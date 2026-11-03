# MCP Practice 02：只读数据库 MCP Server（两层 SQL 防护）

> 这个项目把内部的 PostgreSQL / SQLite 暴露给 Claude Code 使用：只读、限定表、限定行数、限定时长。应用层的 SQL 过滤只能算第一道防线：它能给模型一个看得懂、能据此修改的错误，却没法证明一条 SQL 是安全的，安全最终要靠数据库层来保证（只读 role、`mode=ro`、authorizer）。这个项目两层都做了，下面用一批常见攻击输入来检验。

---

## 1. 项目目标

输入是一个数据库（demo 用 SQLite，生产环境换成 PostgreSQL / MySQL）。输出是一个 MCP Server，暴露以下能力：

- Tool：`query(sql)`，执行 SELECT，带严格校验
- Tool：`describe_table(name)`，查看表结构
- Resource：`db://schema/{table}`，完整的列定义
- Resource：`db://tables`，所有可用的表

安全约束分两层。应用层（`safety.py`）只允许 SELECT，禁止注释、多语句和 DDL/DML 关键字，限定表名白名单，并给 LIMIT 设上限。数据库层（`server.py`）用 `mode=ro` 做文件级只读，用 `PRAGMA query_only` 做连接级只读，用 `set_authorizer`（在语句编译时回调，只放行 SELECT、白名单里的表和函数）做语句级控制，再加上查询超时（progress handler）。

---

## 2. 设计决策

| 问题 | 决定 |
|------|------|
| 数据库 | SQLite（demo，零依赖）；生产用 PostgreSQL |
| 应用层过滤 | `sqlparse` + 正则（给模型返回友好的错误） |
| 数据库层防御 | `mode=ro` + `query_only` + `authorizer` + 超时；PostgreSQL 用只读 role + `statement_timeout` |
| 表白名单 | 写成常量（生产环境放到配置文件里）；两层都检查 |
| 行数限制 | 强制 `LIMIT 100`（只会把更大的值截小） |
| 超时 | 5 秒（SQLite 用 progress handler，PostgreSQL 用 `statement_timeout`） |
| 鉴权 | stdio 不需要；HTTP 用 `TokenVerifier`（[远程 MCP 的 OAuth 2.1 鉴权](../05-production/02-auth-oauth.md)），审计日志里记录 `get_access_token().subject` |
| 错误 | 校验失败和执行失败都用 `ToolError`（模型能据此纠正）；其他意料之外的异常由 SDK 自动隐藏细节 |

---

## 3. 目录结构

```
demos/practice/db_mcp/
├── server.py             # MCPServer + lifespan + 只读连接 + authorizer
├── safety.py             # 应用层 SQL 过滤
└── (sample.db 运行时生成在临时目录，可用 DB_PATH 覆盖)
```

---

## 4. 完整代码

### 4.1 `safety.py`：应用层 SQL 过滤

```python
# demos/practice/db_mcp/safety.py
from __future__ import annotations

import re

import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import DDL, DML, Keyword


class SQLValidationError(ValueError):
    pass


DEFAULT_ALLOWED_TABLES = {"users", "orders", "products"}
MAX_LIMIT = 100
FORBIDDEN = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "ATTACH", "PRAGMA"}


def validate_sql(sql: str, allowed_tables: set[str] = DEFAULT_ALLOWED_TABLES) -> str:
    """校验并返回加了 LIMIT 上限的安全 SQL；不合格抛 SQLValidationError（消息写给模型看，要能指导它改）"""
    sql = sql.strip()
    if not sql:
        raise SQLValidationError("SQL 不能为空")
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise SQLValidationError("SQL 不允许注释")
    if sql.rstrip(";").count(";") > 0:
        raise SQLValidationError("SQL 不允许多语句")

    parsed = sqlparse.parse(sql)
    if len(parsed) != 1:
        raise SQLValidationError("只允许单条 SQL")
    stmt: Statement = parsed[0]
    if stmt.get_type() != "SELECT":
        raise SQLValidationError(f"只允许 SELECT，你的是 {stmt.get_type()}")

    for token in stmt.flatten():
        if token.ttype in (Keyword, DDL, DML) and token.normalized.upper() in FORBIDDEN:
            raise SQLValidationError(f"禁止关键字: {token.normalized}")

    # 表名白名单（粗暴但有效：找 FROM / JOIN 后的标识符；兜底的是 server.py 里的 authorizer）
    refs = re.findall(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I) + re.findall(r"\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I)
    for tbl in refs:
        if tbl.lower() not in allowed_tables:
            raise SQLValidationError(f"表 '{tbl}' 不在白名单内。可用：{sorted(allowed_tables)}")

    safe = sql.rstrip(";").strip()
    if "LIMIT" not in safe.upper():
        safe += f" LIMIT {MAX_LIMIT}"
    else:
        safe = re.sub(r"\bLIMIT\s+(\d+)", lambda m: f"LIMIT {min(int(m.group(1)), MAX_LIMIT)}", safe, flags=re.I)
    return safe
```

### 4.2 `server.py`：数据库层防御 + MCP Server

```python
# demos/practice/db_mcp/server.py
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
    """数据库层的最后一道门：SQLite 在编译每条语句时回调它，只放行 SELECT 和白名单表的读取。
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
        "SELECT name FROM users WHERE id IN (SELECT id FROM secrets)",  # 子查询：应用层正则能看到 FROM secrets，直接拦下
        "WITH s AS (SELECT token AS name FROM secrets) SELECT name FROM s",  # CTE：同样被应用层拦下
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
```

几个要点：

- 连接上做了三重只读：`file:...?mode=ro`（文件级）、`PRAGMA query_only = ON`（连接级）、`set_authorizer`（语句级：只放行 `SQLITE_SELECT`、白名单表的 `SQLITE_READ`、白名单函数的 `SQLITE_FUNCTION`，其余一律 `SQLITE_DENY`）。authorizer 在编译每一条语句时都会被回调。上面的自测里，子查询和 CTE 中的 `FROM secrets` 已经被应用层正则拦下，逗号连接骗过了正则，由 authorizer 拦住；应用层漏掉的写法，这一层都能兜底
- `PRAGMA` 也会被 authorizer 拦下，所以 `describe_table` 单独开了一个只读连接，表名先过白名单（绝不拼接用户输入）
- 同步函数在 v2 里跑在工作线程上，`def query(...)` 不会阻塞事件循环，因此 SQLite 连接要设 `check_same_thread=False`
- `ToolError(...) from None`：校验失败的消息会原样交给模型，所以要写成能指导它修改的话（比如「表 'x' 不在白名单内。可用：[...]」）；`from None` 避免把内部的异常链带出去
- 日志用标准库 `logging`（写 stderr），审计日志里记录 SQL 和调用者；不要用已弃用的 `ctx.info()`
- 资源不存在时用 `ResourceNotFoundError`（客户端收到 `-32602`）

---

## 5. 测试

```python
# tests/test_safety.py（应用层）
import pytest
from safety import validate_sql, SQLValidationError

def test_basic_select_ok():
    assert "LIMIT 100" in validate_sql("SELECT * FROM users WHERE tier='vip'")

def test_reject_drop():
    with pytest.raises(SQLValidationError, match="只允许 SELECT"):
        validate_sql("DROP TABLE users")

def test_reject_comment():
    with pytest.raises(SQLValidationError, match="不允许注释"):
        validate_sql("SELECT * FROM users -- comment")

def test_reject_multi_statement():
    with pytest.raises(SQLValidationError, match="多语句"):
        validate_sql("SELECT * FROM users; DROP TABLE users")

def test_reject_unknown_table():
    with pytest.raises(SQLValidationError, match="不在白名单"):
        validate_sql("SELECT * FROM credit_cards")

def test_limit_clamped():
    assert "LIMIT 100" in validate_sql("SELECT * FROM users LIMIT 9999")

def test_limit_preserved_if_smaller():
    assert "LIMIT 5" in validate_sql("SELECT * FROM users LIMIT 5")
```

端到端测试直接用进程内的 `Client`，不需要启动子进程：

```python
from mcp import Client
from demos.practice.db_mcp.server import mcp

async def test_authorizer_blocks_secrets():
    async with Client(mcp) as client:
        r = await client.call_tool("query", {"sql": "SELECT users.name, secrets.token FROM users, secrets"})
        assert r.is_error and "prohibited" in r.content[0].text
```

---

## 6. 运行（带攻击输入的自测）

```bash
python demos/practice/db_mcp/server.py --selftest
```

实际输出（`secrets` 表是故意放进库里的敏感表，不在白名单中）：

```
正常查询: [{'name': 'Alice', 'total': 498.0}, {'name': 'Carol', 'total': 599.0}]
DROP TABLE users                                            -> SQL 校验失败: 只允许 SELECT，你的是 DROP
SELECT * FROM secrets                                       -> SQL 校验失败: 表 'secrets' 不在白名单内。可用：['orders', 'products', 'user
SELECT 1; DROP TABLE users                                  -> SQL 校验失败: SQL 不允许多语句
SELECT * FROM users -- 注释                                   -> SQL 校验失败: SQL 不允许注释
SELECT name FROM users WHERE id IN (SELECT id FROM secrets  -> SQL 校验失败: 表 'secrets' 不在白名单内。可用：['orders', 'products', 'user
WITH s AS (SELECT token AS name FROM secrets) SELECT name   -> SQL 校验失败: 表 'secrets' 不在白名单内。可用：['orders', 'products', 'user
SELECT users.name, secrets.token FROM users, secrets        -> SQL 执行失败: access to secrets.token is prohibited
SELECT load_extension('x')                                  -> SQL 执行失败: not authorized to use function: load_extension
describe_table: ['id', 'user_id', 'amount', 'status']
```

注意 `FROM users, secrets` 那一行：逗号连接骗过了应用层的 `FROM/JOIN` 正则（正则只看到了 `users`），最后是数据库层的 authorizer 把它拦了下来（`access to secrets.token is prohibited`）。

用 Inspector 手动试：

```bash
npx @modelcontextprotocol/inspector python demos/practice/db_mcp/server.py
```

---

## 7. 接入 Claude Code

```bash
claude mcp add db --env DB_PATH=/abs/path/to/prod_replica.db --transport stdio -- /abs/path/.venv/bin/python /abs/path/db_mcp/server.py
```

```
[user]: 找一下我们 VIP 用户的订单总额
[claude]: 调用 db 的 query
         SQL: SELECT u.name, SUM(o.amount) FROM users u JOIN orders o ON u.id = o.user_id WHERE u.tier='vip' GROUP BY u.id
[claude]: Alice 总额 $498，Carol 总额 $599...
```

（只读工具设置了 `read_only_hint=True`。按协议规定这只是给客户端的提示，告诉它这个工具不改数据；客户端据此怎么处理（比如少弹确认）由客户端自己决定，Server 不能拿它当安全保证。）

---

## 8. 换成 PostgreSQL

```python
import asyncpg

@asynccontextmanager
async def app_lifespan(server: MCPServer):
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"],                    # 用只读用户的连接串
        min_size=2, max_size=10,
        command_timeout=5,
        server_settings={"statement_timeout": "5000", "default_transaction_read_only": "on"},   # 5 秒；强制只读事务
    )
    try:
        yield pool
    finally:
        await pool.close()
```

PostgreSQL 没有 SQLite 那样的 authorizer，所以数据库层的只读 role 必不可少（见下一节）。v2 里 lifespan 只在启动时运行一次，连接池由所有请求共享，正好合适。

---

## 9. 数据库层防御：只读 role

最可靠的做法是在数据库层给 MCP Server 分配一个只读 role：

```sql
-- PostgreSQL
CREATE USER mcp_readonly WITH PASSWORD 'xxx';
GRANT CONNECT ON DATABASE mydb TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON users, orders, products TO mcp_readonly;     -- 只 GRANT 白名单表；不 GRANT 别的
ALTER ROLE mcp_readonly SET statement_timeout = '5s';
ALTER ROLE mcp_readonly SET default_transaction_read_only = on;
```

这样即使应用层的 SQL 过滤被绕过，数据库仍会拒绝写操作、拒绝读取白名单以外的表、拒绝慢查询。表白名单要在两层各写一次：应用层负责给出友好的错误，数据库层才是真正把关的地方。

---

## 10. 可以扩展的方向

| 想加什么 | 怎么做 |
|------|--------|
| 查询缓存 | 在工具里加一个 TTL 缓存（同样的 SQL 5 分钟内复用结果） |
| 查询审计 | 用 logging 记录每条 SQL、`get_access_token().subject` 和结果行数 |
| 多 schema | `db://schema/{schema}/{table}` |
| 自然语言转 SQL | 不要用已弃用的 Sampling；在 Server 里直接调用自己的 LLM（见[Sampling 让 Server 借用 Host 的 LLM（已弃用）](../03-client/03-sampling.md) 的方案 B），或者让 Agent 的模型自己写 SQL，由你的 `query` 工具负责把关 |
| 慢查询保护 | `statement_timeout`（PostgreSQL）/ progress handler（SQLite） |
| 查看执行计划 | 加一个 Tool `explain(sql)`（同样要过两层校验） |
| 动态修改表白名单 | 改完之后调用 `await ctx.notify_resources_changed()`，让订阅者重新拉取 `db://tables` |

---

## 11. 安全清单

- [x] 只允许 SELECT
- [x] 表白名单（两层都有）
- [x] LIMIT 上限
- [x] 禁止注释和多语句
- [x] 禁止 DDL/DML 关键字
- [x] 数据库层强制只读（SQLite 用 `mode=ro` + `query_only` + authorizer；PostgreSQL 用只读 role）
- [x] 慢查询超时
- [x] 错误消息对模型友好，不泄露内部结构
- [ ] 速率限制（每个用户每分钟 N 次；2026 起网关可以按 `Mcp-Name` 头限流）
- [ ] 审计日志（结果行数、SQL hash、user id）
- [ ] 敏感列脱敏（手机号、邮箱），白名单细化到列一级

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 只靠应用层的正则或 sqlparse 防御 | 正则总有覆盖不到的写法（本篇自测里是逗号连接；换成别的正则，视图、别名等也可能漏）；authorizer 或只读 role 才是最终的把关 |
| `FROM users, secrets`（逗号连接）没检测出来 | 正则只认 `FROM`/`JOIN`，最后是 authorizer 拦下的 |
| sqlparse 解析嵌套子查询有 bug | 用单元测试覆盖，并依靠数据库层兜底 |
| 有了 LIMIT，OFFSET 却可以很大 | OFFSET 也要设上限 |
| 通过 UDF 或函数注入 | SQLite 在 authorizer 里设函数白名单，`load_extension` 这类不在白名单里的函数一律拒绝，`ATTACH` 也走默认拒绝；PostgreSQL 版靠只读 role 收权限，不给 `pg_read_file` 这类函数的执行权限 |
| 同步工具里用 SQLite 连接报线程错误 | v2 里 `def` 工具在工作线程中执行；设置 `check_same_thread=False`，或者改成 `async def` + `asyncio.to_thread` |
| 大查询导致 OOM | 一般 LIMIT 100 就够了，但查询 JSON / TEXT 大字段时要小心，返回结果的大小也要设上限 |
| PRAGMA 被 authorizer 拦下 | 元数据查询单独开一个只读连接，表名走白名单 |

---

## 13. 下一步

- 给 Claude Code 写一个自定义 MCP：[为 Claude Code 编写 PR 评审 MCP Server](./03-project-claude-code-tool.md)
- 远程数据库 MCP 的鉴权：[远程 MCP 的 OAuth 2.1 鉴权](../05-production/02-auth-oauth.md)

## 参考资料

- sqlparse：https://sqlparse.readthedocs.io
- SQLite authorizer：https://www.sqlite.org/c3ref/set_authorizer.html
- SQLite URI `mode=ro`：https://www.sqlite.org/uri.html
- PostgreSQL statement_timeout：https://www.postgresql.org/docs/current/runtime-config-client.html
