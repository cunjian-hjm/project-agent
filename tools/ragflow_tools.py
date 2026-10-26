import os
from langchain_core.tools import tool
from api.monitor import monitor
from ragflow_sdk import RAGFlow

_ragflow_client = RAGFlow(
    api_key=os.getenv("RAGFLOW_API_KEY"),
    base_url=os.getenv("RAGFLOW_BASE_URL")
)

@tool
def get_assistant_list() -> str:
    """
      获取ragflow服务中所有的聊天助手信息! 用于后续具体提问的筛选!
      返回格式: 助手名称:xxx , 描述: xx , 关联的知识库: x,x,x,x \n
              助手名称:xxx , 描述: xx , 关联的知识库: x,x,x,x \n
    """
    # todo 注意:  ragflow  .list_chats [chat对象的列表]   datasets  [知识库信息字典的列表]
    monitor.report_tool(tool_name="查询ragflow助手列表信息")
    try:

        # 1. 查询所有的聊天助手
        chat_list = _ragflow_client.list_chats()
        if not chat_list or len(chat_list) == 0:
            return "未找到可用任何助手"
        # 2. 查询每个助手的关联的知识库
        final_result = ""
        for chat in chat_list:
            # chat -> 名字 和 描述  助手关联的知识库 (dataset) [属性 对象]
            name = chat.name
            description = chat.description
            datasets = chat.datasets # -> [{知识库的信息 name ....},{},{}]
            dataset_name_list = [dataset['name'] for dataset in datasets]
            final_result += f"助手名称:{name} , 描述: {description} , 关联的知识库: {','.join(dataset_name_list)} \n"
        # 3. 返回拼接结果
        return final_result
    except Exception as e:
        return f"查询助手列表失败：{str(e)}"


# `create_ask_delete`: 创建一个新会话，提问一次，然后删除该会话，并返回答案。当需要向特定的 RAGFlow 助手提问时，使用此工具。
#  目标 向指定助手进行提问  创建会话  提问 获取结果 删除会话
# 参数: 助手的名称 [get_assistant_list工具返回]  || question 问题
# 响应: 就是对话返回的结果

@tool
def create_ask_delete(chat_name:str,question:str) -> str:
    """
      向指定的助手提问,并获取返回结果!
      参数 chat_name就是助手的名称,名称需要get_assistant_list查询和确认
          question是本次提问的问题
      返回结果就是提问的回答
    """
    # todo 注意:  ragflow  .list_chats [chat对象的列表]   datasets  [知识库信息字典的列表]
    monitor.report_tool(tool_name=f"向ragflow提问工具",args={"chat_name":chat_name})
    try:
        # 1. 查询指定名称的助手
        list_chats = _ragflow_client.list_chats(name=chat_name)
        if not list_chats or len(list_chats) == 0:
            return f"未找到名称为{chat_name}的助手"
        # 获取要使用的助手
        chat = list_chats[0]
        # ragflow  chat  datasets  session
        # 2. 向助手进行提问
        session = chat.create_session(name="temp_session")
        stream = session.ask(question=question, stream=True)
        final_result = ""
        # 3. 获取结果数据
        for chunk in  stream:
            # 遍历的chunk本身就是累加的，所有取最后一个就可以了
            final_result = chunk.content
        # 4. 关闭助手会话
        chat.delete_sessions([session.id])
        # 5. 返回提问结果
        return final_result
    except Exception as e:
        return f"向{chat_name}提问失败：{str(e)}"

if __name__ == "__main__":
    # print(get_assistant_list.invoke({}))
    print(create_ask_delete.invoke({"chat_name":"test","question":"空调的绝热工作怎么做？"}))