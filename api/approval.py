"""人工审批（Human-in-the-loop）的待决策注册表。

run_deep_agent 执行中遇到危险写操作中断时，通过 register_approval 挂起一个 Future；
前端通过 WebSocket 回传决策时，server.py 调用 resolve_approval 唤醒该挂起任务。
两者运行在同一事件循环内，故直接用 asyncio.Future 即可，无跨线程问题。
"""
import asyncio

# thread_id -> 等待用户决策的 Future
_pending_approvals: dict[str, asyncio.Future] = {}


def register_approval(thread_id: str) -> asyncio.Future:
    """为 thread_id 注册一个待决策 Future（须在运行中的事件循环内调用）。"""
    fut = asyncio.get_running_loop().create_future()
    _pending_approvals[thread_id] = fut
    return fut


def resolve_approval(thread_id: str, decision: dict) -> bool:
    """WebSocket 收到决策后调用；命中则唤醒挂起任务并返回 True，否则返回 False。"""
    fut = _pending_approvals.pop(thread_id, None)
    if fut is None or fut.done():
        return False
    fut.set_result(decision)
    return True
