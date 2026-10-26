from agent.load_prompt import sub_agents_config
from tools.mysql_tools import list_sql_tables,get_table_data,execute_sql_query

# 本地数据库查询的subagent
db_sub_agent = {
    "name":sub_agents_config["db"].get("name",""),
    "description":sub_agents_config["db"].get("description",""),
    "system_prompt":sub_agents_config["db"].get("system_prompt",""),
    "tools": [list_sql_tables,get_table_data,execute_sql_query]
}
