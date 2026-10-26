from agent.load_prompt import sub_agents_config
from tools.internet_search_tool import internet_search

# 网络请求subagent
internet_sub_agent ={
    "name": sub_agents_config['tavily']['name'],
    "description":sub_agents_config['tavily']['description'],
    "tools": [internet_search],
    "system_prompt":sub_agents_config['tavily']['system_prompt']
}