# 1. list_sql_tables: 列出数据库中所有可用的表，这是了解数据库结构的第一步。
# 2. get_table_data: 读取指定表的前100行数据，用于快速预览数据内容。
# 3. execute_sql_query: 执行自定义SQL查询。当需要复杂的筛选、联接或聚合时使用此工具。

import os
from dotenv import load_dotenv
from api.monitor import monitor
from mysql.connector import connect, Error
from typing import Annotated, List
from langchain_core.tools import tool

load_dotenv()

# 加载配置文件方便后续使用
def get_db_config():
    """Get database configuration from environment variables."""
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL")
    }
    # 移除 None 值（核心必要操作）
    config = {k: v for k, v in config.items() if v is not None}

    # 补充：校验核心配置是否存在（可选但推荐）
    required_keys = ["user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")
    return config

# 定义查看数据库表的工具
"""
【mysql.connector 核心 API 说明（针对 connect/cursor）】
1. connect 函数：
   - 作用：建立与 MySQL 数据库的连接，返回一个 Connection 对象；
   - 使用方式：connect(**config)，config 为包含 host/user/password 等的字典；
   - 上下文管理器：推荐用 with 语句（with connect(**config) as conn），自动关闭连接，避免资源泄露；
   - 核心属性/方法：
     - conn.cursor(): 创建游标对象（执行 SQL 的核心）；
     - conn.commit(): 提交事务（autocommit=True 时无需手动调用）；
     - conn.close(): 关闭连接（with 语句自动执行）。
2. cursor 游标对象：
   - 作用：执行 SQL 语句、获取查询结果的核心对象；
   - 创建方式：conn.cursor()；
   - 上下文管理器：with conn.cursor() as cursor，自动关闭游标；
   - 核心方法：
     - cursor.execute(sql): 执行单条 SQL 语句（如 SHOW TABLES/SELECT/INSERT）；
     - cursor.executemany(sql, params): 批量执行 SQL 语句（如批量插入）；
     - cursor.close(): 关闭游标（with 语句自动执行）。
3. 【重点】cursor 执行 DQL/DML 后的结果解析：
   ▶ DQL（数据查询语言，如 SELECT/SHOW）：查询类操作，返回「数据结果集」
     - 核心方法：
       1. cursor.fetchall(): 获取所有结果（返回列表，每个元素是元组，如 [(1, '张三'), (2, '李四')]）；
       2. cursor.fetchone(): 获取一条结果（返回元组，如 (1, '张三')，多次调用可遍历所有结果）；
       3. cursor.fetchmany(n): 获取前 n 条结果（返回列表）；
       4. cursor.column_names: 获取查询结果的列名（列表，如 ['id', 'name']）；
     - 解析技巧：将「列名 + 元组结果」转为字典（更易读），如 {'id': 1, 'name': '张三'}。
   ▶ DML（数据操作语言，如 INSERT/UPDATE/DELETE）：修改类操作，无「数据结果集」
     - 核心属性：
       1. cursor.rowcount: 返回受影响的行数（整数，如 INSERT 1 条返回 1，UPDATE 3 条返回 3）；
       2. cursor.lastrowid: INSERT 操作后，返回新增记录的自增 ID（仅对有自增主键的表有效）；
     - 解析技巧：通过 rowcount 判断操作是否生效，lastrowid 获取新增数据的主键。
4. 异常处理：
   - Error: mysql.connector 专属异常类，捕获所有数据库操作异常（如连接失败、SQL 语法错误）；
   - 推荐方式：try-except Error as e 捕获异常，返回友好提示。
"""

# 1. list_sql_tables: 列出数据库中所有可用的表，这是了解数据库结构的第一步。

@tool
def list_sql_tables() -> str:
    """
    查询当前库中有哪些可用表!
        对应的sql: show tables;
    :return: 可用表:1,2,3   /  没有可用
    """
    monitor.report_tool(tool_name="搜索表名工具")
    try:
        # 创建连接,并且使用完毕就关闭
        with connect(**get_db_config()) as conn:
            # 创建游标
            with conn.cursor() as cursor:
                # 执行SQL语句
                #  1
                #  2
                #  3
                sql = "show tables;"
                # [(1,),(2,),(3,)]
                cursor.execute(sql)
                select_result = cursor.fetchall()
                if not select_result or len(select_result) == 0:
                    return "没有可用表"
                table_name_list = [item[0] for item in select_result]
                return f"可用表:{",".join(table_name_list)}"
    except Error as e:
        return "数据库连接失败: " + str(e)


# 2. get_table_data: 读取指定表的前100行数据，用于快速预览数据内容。
# 参数 table_name:表名
# 返回
"""
   表格形式
   id   name    age 
    1  二狗子    18
    2  驴蛋蛋    19
"""
@tool
def get_table_data(table_name: str) -> str:
    """
    查询指定表名的数据,参数就是表名! 为了让助手理解表的结构!
    我们返回的格式参照表格格式 如下:
            id   name    age
            1  二狗子    18
            2  驴蛋蛋    19
    """
    monitor.report_tool(tool_name="搜索指定表名的数据工具", args={"table_name": table_name})
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                sql = f"select * from {table_name} limit 100;"
                cursor.execute(sql)
                # 获取结果 [1.判断有没有数据 2. 获取表头  3. 获取表数据]
                # description获取表的元数据 (表头)  1. 判断是不是有效表或者有数据  2. 获取表头信息
                # [
                #   (id , 第一列的描述 , ....) ,
                #   (name , 第一列的描述 , ....)
                # ]
                description = cursor.description
                if  not description:
                    return f"当前表{table_name}没有数据"
                table_header = [column_tuple[0] for column_tuple in description]
                # [(1,name,18),(),()]
                result = cursor.fetchall()
                # (1,name,18) -> "1,name,18"
                # [(1,name,18),(),()] -> ["1,name,18","2,hehe,20"]
                data_list = [ ",".join(map(str ,row_tuple)) for row_tuple in result]
                # id,name,age
                table_header_str = ",".join(table_header)
                # "1,name,18"\n"2,hehe,20\n
                data_list_str = "\n".join(data_list)
                return table_header_str + "\n" + data_list_str
    except Error as e:
        return "数据库连接失败: " + str(e)


# SQL 只读白名单：只允许查询类语句，杜绝 DROP/UPDATE/DELETE/INSERT 等写操作（阶段五：危险工具防护）。
_READONLY_SQL_KEYWORDS = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")


def _is_readonly_sql(sql: str) -> bool:
    """判断 SQL 是否为只读查询语句（SELECT/SHOW/DESCRIBE/EXPLAIN），其余一律拒绝。"""
    stripped = sql.strip()
    # 去掉前导注释（-- / # 行注释、/* */ 块注释），防止用注释绕过白名单
    while stripped.startswith(("--", "#", "/*")):
        if stripped.startswith("--") or stripped.startswith("#"):
            newline = stripped.find("\n")
            stripped = stripped[newline + 1:] if newline != -1 else ""
        else:  # /* */
            end = stripped.find("*/")
            stripped = stripped[end + 2:] if end != -1 else ""
        stripped = stripped.strip()
    if not stripped:
        return False
    keyword = stripped.split(None, 1)[0].upper()
    return keyword in _READONLY_SQL_KEYWORDS


# 3. execute_sql_query: 执行自定义SQL查询。当需要复杂的筛选、联接或聚合时使用此工具。
# 参数: sql
# 响应: 查询数据 表格形式
@tool
def execute_sql_query(sql: str) -> str:

    """
    执行自定义SQL查询（仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN 等只读查询）
    :param sql: 自定义SQL查询语句
    :return: 查询数据 表格形式
    """
    monitor.report_tool(tool_name="执行自定义SQL查询工具", args={"sql": sql})
    if not _is_readonly_sql(sql):
        return (f"已拒绝执行（只读白名单）：仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN 查询。"
                f"当前语句以 '{sql.strip()[:30]}' 开头，疑似写操作，请改用只读查询。")
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                # 获取结果 [1.判断有没有数据 2. 获取表头  3. 获取表数据]
                # description获取表的元数据 (表头)  1. 判断是不是有效表或者有数据  2. 获取表头信息
                # [
                #   (id , 第一列的描述 , ....) ,
                #   (name , 第一列的描述 , ....)
                # ]
                description = cursor.description
                if  not description:
                    return f"当前语句{sql}没有数据"
                table_header = [column_tuple[0] for column_tuple in description]
                # [(1,name,18),(),()]
                result = cursor.fetchall()
                # (1,name,18) -> "1,name,18"
                # [(1,name,18),(),()] -> ["1,name,18","2,hehe,20"]
                data_list = [ ",".join(map(str ,row_tuple)) for row_tuple in result]
                # id,name,age
                table_header_str = ",".join(table_header)
                # "1,name,18"\n"2,hehe,20\n
                data_list_str = "\n".join(data_list)
                return table_header_str + "\n" + data_list_str
    except Error as e:
        return "数据库连接失败: " + str(e)


# 4. execute_sql_write: 执行数据库写操作（INSERT/UPDATE/DELETE 等）。
# 危险工具：每次调用都会在主 Agent 层触发人机审批（HumanInTheLoopMiddleware），
# 用户批准后才真正执行；拒绝则不执行并告知模型。
# 参数: sql
# 响应: 执行结果（受影响行数）
@tool
def execute_sql_write(sql: str) -> str:
    """
    执行数据库写操作（INSERT/UPDATE/DELETE 等），会修改数据。此操作需要用户人工审批，批准后才会真正执行。
    :param sql: 写操作 SQL 语句
    :return: 执行结果（受影响行数）
    """
    monitor.report_tool(tool_name="执行写操作SQL工具", args={"sql": sql})
    if _is_readonly_sql(sql):
        return "该语句是只读查询（SELECT/SHOW/DESCRIBE），请改用 execute_sql_query 工具，不要用本写操作工具。"
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                return f"写操作已执行成功，影响 {cursor.rowcount} 行。"
    except Error as e:
        return "数据库写操作失败: " + str(e)

if __name__ == '__main__':
    # print(list_sql_tables.invoke({}))
    # print(get_table_data.invoke({"table_name": "drugs"}))
    print(execute_sql_query.invoke({"sql": "select * from drugs limit 100"}))