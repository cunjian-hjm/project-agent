# [核心] 智能体组装与执行逻辑
import asyncio
import shutil
from pathlib import Path

import aiosqlite
from langchain_core.messages import AIMessage
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import ContextEditingMiddleware, TodoListMiddleware, ToolErrorMiddleware
from langchain.agents.middleware.context_editing import ClearToolUsesEdit
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.llm import llm
from agent.load_prompt import main_config
from agent.sub_agents.rag_sub_agent import rag_sub_agent
from agent.sub_agents.db_sub_agent import db_sub_agent
from agent.sub_agents.internet_sub_agent import internet_sub_agent
from api.approval import register_approval
from api.context import set_thread_context, set_session_context, reset_session_context
from api.monitor import monitor
from tools.markdown_tools import generate_markdown
from tools.mysql_tools import execute_sql_write
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content
from utils.logger_utils import logger

# 项目根目录（deep-search 项目根，用于定位 output/checkpoints.db）
_project_root = Path(__file__).resolve().parents[1]

# 会话记忆 checkpointer：用 SQLite 持久化每个 thread_id 的对话状态，实现同一会话的多轮追问记忆。
# 注意：astream 走异步路径，必须用 AsyncSqliteSaver（同步 SqliteSaver 的异步方法会抛 NotImplementedError）。
_checkpoint_db = _project_root / "output" / "checkpoints.db"
_checkpoint_db.parent.mkdir(parents=True, exist_ok=True)

# 主 Agent 延迟装配：aiosqlite 连接只能在异步上下文中创建，因此首次调用时再构建（单例）。
_main_agent = None
_agent_lock = asyncio.Lock()


# 上下文压缩用的结构化摘要提示词（档位3：结构化摘要 + 历史卸载）。
# 触发压缩时，SummarizationMiddleware 会调用模型把「旧消息」压成下面这份结构化摘要，用于替换原文；
# 被压缩掉的原文会卸载到 backend（StateBackend，随 checkpointer 持久化），可从摘要里找回。
# 注意：必须保留 {messages} 占位符（middleware 用 .format(messages=...) 填充待压缩消息）。
_COMPRESSION_SUMMARY_PROMPT = """你是对话历史压缩助手。目标：从下方对话历史中提取最重要的上下文，用于替换原始历史，释放上下文空间。

请严格按以下四个小节输出，信息密集、条目化；某小节没有内容就明确写"无"：

## 会话目标
用户的核心诉求，以及要完成的整体任务。

## 关键结论
已查到的关键事实、数据、结论，重要决策及其依据；被否决的方案及原因。

## 已产出文件
本会话创建、修改或访问过的文件路径及其用途（防止文件信息丢失）。

## 后续步骤
为完成会话目标还剩哪些具体任务、下一步该做什么。

只输出上述四小节，不要输出任何额外文字。

<messages>
待压缩消息：
{messages}
</messages>"""


# TodoListMiddleware 注入的中文规划提示词（阶段三：显式规划）。
# 让模型在复杂任务开始前先调用 write_todos 输出结构化计划，并在执行中维护状态。
_TODO_SYSTEM_PROMPT = """## write_todos 任务规划

你有一个 write_todos 工具，用于对复杂任务做显式规划与进度跟踪。

使用规则：
- 复杂任务（≥3 步）开始前，必须先调用 write_todos 输出结构化计划；每条计划写清楚「做什么」和「为什么（用哪个工具/子Agent）」。
- 开始某一步前把它的 status 设为 in_progress，完成后立即设为 completed；发现新任务或放弃旧任务时及时更新列表。
- 除非全部完成，至少保持一条 in_progress。
- 简单任务（几步内能完成）直接做，不必用该工具。
- write_todos 只用于规划跟踪，不负责回答用户；最终答案必须在最后一次 write_todos 之后以文字形式给出。"""


# 阶段五（人机审批）：写 SQL 工具 execute_sql_write 执行前必须经用户审批。
# 通过 create_deep_agent 的 interrupt_on 参数交给内置 HumanInTheLoopMiddleware：
# execute_sql_write 每次调用都会中断，用户 approve 后执行、reject 则不执行。
_SQL_WRITE_INTERRUPT = {
    "execute_sql_write": {
        "allowed_decisions": ["approve", "reject"],
        "description": "检测到数据库写操作（可能修改数据），需要您审批后才能执行。",
    }
}


async def _tool_error_handler(error: Exception, _request):
    """工具抛出未捕获异常时，转成错误提示交给模型自省，而不是让整个任务崩溃。"""
    return (f"工具调用异常（{type(error).__name__}）：{error}。"
            f"请判断是否需要换一个工具、调整参数重试，或按降级链改用其他数据源；"
            f"若仍无法获取，如实告知用户未找到，不要编造。")


def _build_main_agent(checkpointer):
    # 中间件装配（阶段二压缩 + 阶段三规划 + 阶段四自省）：
    #   1) SummarizationMiddleware（档位3）—— 对话历史超过 trigger（约 4000 token）时，用模型把「旧消息」
    #      压成结构化摘要，只保留最近 keep 条消息原文；被压掉的原文卸载到 backend 可回溯。
    #   2) ContextEditingMiddleware(ClearToolUsesEdit)（档位5）—— 上下文再涨到 trigger（约 8000 token）时，
    #      把最旧的工具返回结果清成占位符（保留最近 keep 个），防止数据库/知识库/搜索返回的大结果永久占用上下文。
    #   3) TodoListMiddleware（显式规划）—— 注入 write_todos 工具，让模型对复杂任务先输出结构化计划并维护状态。
    #   4) ToolErrorMiddleware（自省兜底）—— 工具抛出未捕获异常时转成错误提示交给模型，而非整个任务崩溃。
    #   backend 统一用一个 StateBackend 实例，与内置文件工具共享，保持既有行为不变。
    backend = StateBackend()
    custom_middleware = [
        SummarizationMiddleware(
            model=llm,
            backend=backend,
            trigger=("tokens", 4000),        # 触发阈值：历史消息约 4000 token
            keep=("messages", 6),            # 保留最近 6 条消息原文
            summary_prompt=_COMPRESSION_SUMMARY_PROMPT,
            trim_tokens_to_summarize=3000,   # 生成摘要时最多取 3000 token，控制摘要调用成本
        ),
        ContextEditingMiddleware(
            edits=[
                ClearToolUsesEdit(
                    trigger=8000,            # 触发阈值：历史消息约 8000 token
                    keep=3,                  # 保留最近 3 个工具返回结果
                    clear_tool_inputs=True,  # 同时清理对应工具调用参数
                )
            ]
        ),
        TodoListMiddleware(system_prompt=_TODO_SYSTEM_PROMPT),
        ToolErrorMiddleware(aon_error=_tool_error_handler),  # 阶段四：工具异常不崩溃，转错误提示交给模型自省
    ]
    return create_deep_agent(
        model=llm,
        system_prompt=main_config['system_prompt'],
        subagents=[rag_sub_agent, db_sub_agent, internet_sub_agent],
        tools=[read_file_content, generate_markdown, convert_md_to_pdf, execute_sql_write],
        checkpointer=checkpointer,
        backend=backend,                 # 与 SummarizationMiddleware 共享同一 backend
        middleware=custom_middleware,    # 按 .name 覆盖内置 SummarizationMiddleware，其余作为新中间件追加
        interrupt_on=_SQL_WRITE_INTERRUPT,  # 阶段五：写 SQL 工具触发人机审批
    )


async def get_main_agent():
    """懒加载并返回装配了 checkpointer 的主 Agent（单例）。"""
    global _main_agent
    if _main_agent is None:
        async with _agent_lock:
            if _main_agent is None:
                conn = await aiosqlite.connect(str(_checkpoint_db))
                _main_agent = _build_main_agent(AsyncSqliteSaver(conn))
    return _main_agent


def _report_agent_process(chunk):
    """把 astream 的每个 chunk 转成前端可渲染的事件。

    chunk 是 LangGraph 各节点的一次增量更新，常见键：
      - "model"  : 模型这一轮输出（可能同时含 content 推理文字 + tool_calls）
      - "tools"  : 工具/子Agent 执行结果（体量大，不推给前端）
      - "todos"  : TodoListMiddleware 维护的结构化计划（write_todos 写入）
    """
    reported_final = False
    for key, value in chunk.items():
        if not value:
            continue

        # 1) 结构化计划（todos 状态更新）→ 前端「执行计划」清单
        if key == "todos" and isinstance(value, list):
            monitor.report_todos(value)
            continue
        if isinstance(value, dict) and "todos" in value:
            monitor.report_todos(value["todos"])

        if not isinstance(value, dict) or "messages" not in value:
            continue
        message = value["messages"][-1]

        if key == "model":
            if message.tool_calls:
                # 2) 可见思考：模型在调用工具前/过程中的文字推理
                if message.content:
                    monitor.report_reasoning(message.content)
                # 3) 工具/子Agent 调用轨迹
                for tool_call in message.tool_calls:
                    if tool_call["name"] == "write_todos":
                        continue  # 计划已在 todos 里单独展示，不再作为工具调用重复显示
                    if tool_call["name"] == "task":
                        monitor.report_assistant(
                            tool_call['args'].get('subagent_type'),
                            tool_call['args'].get('description'),
                        )
                    else:
                        monitor.report_tool(tool_call['name'], tool_call['args'])
            elif message.content:
                # 4) 无 tool_calls 的纯文本 = 最终答案
                monitor.report_task_result(message.content)
                reported_final = True

    return reported_final


def _last_ai_text(state) -> str:
    """取最终状态下最后一条有文字的 AI 消息内容（模型空回复时的兜底展示）。"""
    messages = (getattr(state, "values", None) or {}).get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
    return ""


async def _stream_with_hitl(agent, config, first_input):
    """执行 agent 流式输出，遇到人工审批中断则挂起等决策后 resume，直到任务完成。

    LangGraph 中断语义：astream 遇到 interrupt() 会正常结束（不抛异常），
    之后 agent.aget_state(config) 的 interrupts 字段列出待审批项；
    用 Command(resume={"decisions": [...]}) 再次 astream 即可恢复执行。
    """
    current = first_input
    reported_final = False
    while True:
        async for chunk in agent.astream(current, config=config):
            if _report_agent_process(chunk):
                reported_final = True

        state = await agent.aget_state(config)
        interrupts = getattr(state, "interrupts", None) or ()
        if not interrupts:
            break  # 无待审批中断，任务已正常结束

        # 取第一个待审批中断，其 value 即 HumanInTheLoopMiddleware 构造的 HITLRequest
        hitl_request = interrupts[0].value
        decisions = await _request_human_approval(config, hitl_request)
        current = Command(resume={"decisions": decisions})

    # 兜底：整轮执行结束却没有给出任何最终文字（例如模型回了空消息），
    # 也必须发一个结束事件，否则前端会一直停在"运行中"，表现为卡死。
    if not reported_final:
        fallback = _last_ai_text(state) or "任务已结束：模型本轮未返回任何内容，请重试或换个更明确的问法。"
        monitor.report_task_result(fallback)


async def _request_human_approval(config, hitl_request):
    """把一个 HITL 中断请求推给前端，挂起等待用户决策，返回 HITL 决策列表。"""
    thread_id = config["configurable"]["thread_id"]
    action_requests = hitl_request.get("action_requests", [])

    monitor.report_approval_request({
        "thread_id": thread_id,
        "requests": [
            {
                "tool": r.get("name"),
                "args": r.get("args", {}),
                "description": r.get("description", ""),
            }
            for r in action_requests
        ],
    })

    # 挂起等 WebSocket 回传决策；超时自动拒绝（安全默认）
    fut = register_approval(thread_id)
    try:
        decision = await asyncio.wait_for(fut, timeout=300)
    except asyncio.TimeoutError:
        decision = {"approve": False, "reason": "审批超时，自动拒绝"}

    if decision.get("approve"):
        return [{"type": "approve"} for _ in action_requests]
    reason = decision.get("reason") or "用户拒绝了该数据库写操作"
    return [{"type": "reject", "message": reason} for _ in action_requests]


async def run_deep_agent(query: str, thread_id: str):
    # 在调用agent之前，保存thread_id, session_dir_str
    # 保存thread_id
    thread_token = set_thread_context(thread_id)
    # 生成session_dir_str， 再保存session_dir_str，, 并向前端发送session_dir_str
    project_dir = Path(__file__).parents[1].resolve()  # 项目根目录
    session_dir = project_dir / "output" / f"session_{thread_id}"  # 拼接生成sesion目录path对象
    session_dir.mkdir(parents=True, exist_ok=True)  # 确保输出目录存在
    session_dir_str = str(session_dir).replace("\\", "/")  # 生成完整路径字符串
    session_token = set_session_context(session_dir_str)  # 保存session_dir_str
    monitor.report_session_dir(session_dir_str)  # 告诉前端，当前会话的输出目录, 浏览器才能在右侧显示会话文件列表

    # 将upload中的文件拷贝到session目录中
    upload_dir = project_dir / "upload" / f"session_{thread_id}"
    uploaded_info = ''  # 关于要优先读取上传的文件的提示词
    if upload_dir.exists():
        # 得到所有文件的列表
        file_names = [item.name for item in upload_dir.iterdir() if item.is_file()]
        for name in file_names:
            shutil.copy2(upload_dir / name, session_dir / name)
        uploaded_info = (f"\n    [已上传文件] 已加载到工作目录:\n" +
                         "\n".join([f"    - {f}" for f in file_names]) +
                         "\n    请优先使用工具读取并参考这些文件。")

    # 得到工作区目录的相对路径
    relative_session_dir_str = str(session_dir.relative_to(project_dir)).replace("\\", "/")
    path_instruction = f"""
            【工作环境指令】
            工作目录: {relative_session_dir_str}

            {uploaded_info}

            规则：
            1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}'
            2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
            3. 使用相对路径，禁止使用绝对路径
            4. 若存在上传文件，请先分析内容
            """

    # 会话配置：thread_id 作为记忆隔离键，同一会话的多轮对话共用同一份持久化状态
    config = {"configurable": {"thread_id": thread_id}}
    main_agent = await get_main_agent()

    try:
        # 判断是否首次会话：首次注入工作环境指令，后续轮次的环境信息已从 checkpointer 历史中继承，
        # 避免每轮重复注入 path_instruction 造成上下文膨胀。
        state = await main_agent.aget_state(config)
        existing_messages = (state.values or {}).get("messages") or []
        is_first_turn = len(existing_messages) == 0
        user_content = f"问题：{query}"
        if is_first_turn:
            user_content = f"问题：{query}\n\n{path_instruction}"

        # 执行agent：只传本轮新消息，历史由 checkpointer 自动恢复；遍历过程数据推送到前端。
        # 若执行中触发人工审批（写 SQL 工具），会中断并等待前端决策后 resume，循环直到完成。
        await _stream_with_hitl(
            main_agent, config,
            {"messages": [{"role": "user", "content": user_content}]},
        )
    except Exception as e:
        logger.error(f"智能体执行最终失败：{e}")
        monitor._emit("error", "智能体执行最终失败了")
    finally:  # 最终都要重置保存的thread_id和session_dir
        reset_session_context(session_token, thread_token)


