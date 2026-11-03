"""只读数据库 MCP Server（mcp 2.x）：应用层 SQL 过滤 + 数据库层只读强制（两层防御）

跑法:
    python demos/practice/db_mcp/server.py              # stdio
    python demos/practice/db_mcp/server.py --selftest   # 进程内自测：正常查询 + 一批攻击输入
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from safety import DEFAULT_ALLOWED_TABLES, SQLValidationError, validate_sql  # noqa: E402

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver import Context  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

logger = logging.getLogger("db_mcp")
DB_PATH = Path(os.getenv("DB_PATH", Path(tempfile.gettempdir()) / "mcp_demo_sample.db"))
QUERY_TIMEOUT_S = 5.0


def init_sample_db() -> None:
    """建一份 demo 数据（用可写连接，只在初始化时用一次）"""
    if DB_PATH.exists():
        return
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, tier TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT);
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL);
        CREATE TABLE secrets (id INTEGER PRIMARY KEY, token TEXT);          -- 不在白名单里的敏感表
        INSERT INTO users (name, tier) VALUES ('Alice','vip'),('Bob','standard'),('Carol','vip');
        INSERT INTO orders (user_id, amount, status) VALUES (1,199.0,'paid'),(1,299.0,'shipped'),(2,99.0,'paid'),(3,599.0,'refunded');
        INSERT INTO products (name, price) VALUES ('Book',49),('Laptop',999),('Mouse',29);
        INSERT INTO secrets (token) VALUES ('sk-should-never-leak');
        """
    )
    con.commit()
    con.close()


def authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    """数据库层的最后一道门：SQLite 在**编译每条语句**时回调它，只放行 SELECT 和白名单表的读取。
    不管应用层的正则被怎么绕过（子查询、CTE、别名……），这里都兜得住。"""
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:  # arg1 = 表名
        return sqlite3.SQLITE_OK if (arg1 or "").lower() in DEFAULT_ALLOWED_TABLES else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:  # 只放行聚合 / 常见标量函数
        return sqlite3.SQLITE_OK if (arg2 or "").lower() in {"count", "sum", "avg", "min", "max", "lower", "upper", "round", "length", "coalesce"} else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def open_readonly() -> sqlite3.Connection:
    # 1) mode=ro：文件级只读；2) query_only：连接级只读；3) authorizer：语句级白名单
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(authorizer)
    return conn


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[sqlite3.Connection]:
    init_sample_db()
    conn = open_readonly()
    try:
        yield conn
    finally:
        conn.close()


mcp = MCPServer("db-readonly", lifespan=app_lifespan)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _run_with_timeout(conn: sqlite3.Connection, sql: str) -> list[dict]:
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)  # 返回非 0 就中断查询
    try:
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.set_progress_handler(None, 0)


@mcp.tool(annotations=READ_ONLY)
def query(sql: str, ctx: Context[sqlite3.Connection]) -> list[dict]:
    """对内部数据库执行只读 SQL 查询。

    安全约束：只允许 SELECT；禁止注释 / 多语句 / DDL/DML 关键字；表必须在白名单（users / orders / products）；
    自动强制 LIMIT 100；超过 5 秒会被中断。

    Args:
        sql: 标准 SQL SELECT，例如 "SELECT * FROM users WHERE tier='vip'"
    """
    try:
        safe = validate_sql(sql)
    except SQLValidationError as e:
        raise ToolError(f"SQL 校验失败: {e}") from None  # 模型能读到并纠正
    logger.info("执行: %s", safe)  # 标准库日志，写 stderr；审计里记 SQL 和调用者
    conn = ctx.request_context.lifespan_context
    try:
        return _run_with_timeout(conn, safe)
    except sqlite3.Error as e:  # 含 authorizer 拒绝（not authorized）、超时中断（interrupted）、语法错误
        raise ToolError(f"SQL 执行失败: {e}") from None


@mcp.tool(annotations=READ_ONLY)
def describe_table(name: str, ctx: Context[sqlite3.Connection]) -> list[dict]:
    """查看某张表的列定义（表必须在白名单内）"""
    if name.lower() not in DEFAULT_ALLOWED_TABLES:
        raise ToolError(f"表 '{name}' 不在白名单内，可用：{sorted(DEFAULT_ALLOWED_TABLES)}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # table_info 是 PRAGMA，authorizer 连接里是被禁的，所以单开一个只读连接
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"PRAGMA table_info({name})").fetchall()]  # name 已过白名单，不是用户拼接
    finally:
        conn.close()


@mcp.resource("db://tables", mime_type="application/json")
def list_tables() -> list[str]:
    """列出所有可访问的表"""
    return sorted(DEFAULT_ALLOWED_TABLES)


@mcp.resource("db://schema/{table}", mime_type="text/plain")
def table_schema(table: str) -> str:
    """读某张表的 schema 描述"""
    if table.lower() not in DEFAULT_ALLOWED_TABLES:
        raise ResourceNotFoundError(f"表不存在或不可访问: {table}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        lines = [f"Table: {table}"]
        for _cid, name, type_, notnull, _dflt, pk in conn.execute(f"PRAGMA table_info({table})"):
            lines.append(f"  - {name} {type_}{' PRIMARY KEY' if pk else ' NOT NULL' if notnull else ''}")
        return "\n".join(lines)
    finally:
        conn.close()


async def selftest() -> None:
    from mcp import Client

    attacks = [
        "DROP TABLE users",
        "SELECT * FROM secrets",
        "SELECT 1; DROP TABLE users",
        "SELECT * FROM users -- 注释",
        "SELECT name FROM users WHERE id IN (SELECT id FROM secrets)",  # 子查询绕过应用层表名正则
        "WITH s AS (SELECT token AS name FROM secrets) SELECT name FROM s",  # CTE 绕过
        "SELECT users.name, secrets.token FROM users, secrets",  # 逗号连接：应用层的 FROM/JOIN 正则看不见 secrets，由 authorizer 兜底
        "SELECT load_extension('x')",
    ]
    async with Client(mcp) as client:
        r = await client.call_tool("query", {"sql": "SELECT u.name, SUM(o.amount) total FROM users u JOIN orders o ON u.id = o.user_id WHERE u.tier='vip' GROUP BY u.id"})
        print("正常查询:", r.structured_content["result"])
        for sql in attacks:
            r = await client.call_tool("query", {"sql": sql})
            print(f"{sql[:58]:60}-> {r.content[0].text.replace('Error executing tool query: ', '')[:60]}")
        print("describe_table:", [c["name"] for c in (await client.call_tool("describe_table", {"name": "orders"})).structured_content["result"]])


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        logging.disable(logging.CRITICAL)
        asyncio.run(selftest())
    else:
        mcp.run()
