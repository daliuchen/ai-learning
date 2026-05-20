# MCP Practice 02：只读数据库 MCP Server（带 SQL 安全过滤）

> **一句话**：把内部 PostgreSQL / SQLite 暴露给 Claude Code 用——只读、限表、限行数、必须经过 SQL 解析白名单。本项目能让你在对话里安全地查数据，不用担心 LLM "突发奇想" drop 表。

---

## 1. 项目目标

- **输入**：一个数据库（SQLite / PostgreSQL / MySQL）
- **输出**：MCP Server 暴露
  - Tool: `query(sql)` — 执行 SELECT，强校验
  - Tool: `describe_table(name)` — 看 schema
  - Resource: `db://schema/{table}` — 完整列定义
  - Resource: `db://tables` — 所有可用表
- **安全约束**：
  - 只允许 SELECT
  - 禁止 `;`（多语句）
  - 禁止 `--` / `/* */`（注释）
  - LIMIT 自动加上限
  - 表白名单
  - 慢查询超时

---

## 2. 设计决策

| 问题 | 决定 |
|------|------|
| DB | SQLite（demo，生产换 SQLAlchemy 接 PG） |
| SQL 解析 | sqlparse |
| 表白名单 | 配置文件 |
| 行数限制 | 强制 LIMIT 100（覆盖用户给的） |
| 超时 | 5 秒（PG 用 `SET statement_timeout`） |
| 鉴权 | stdio 模式不需要；HTTP 用 Bearer |

---

## 3. 目录结构

```
demos/practice/db_mcp/
├── server.py
├── safety.py             # SQL 安全过滤
├── sample.db             # demo SQLite（运行时生成）
└── README.md
```

---

## 4. 完整代码

### 4.1 `safety.py` — SQL 安全过滤

```python
# demos/practice/db_mcp/safety.py
"""SQL 安全过滤层"""
from __future__ import annotations

import re

import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import Keyword, DDL, DML


class SQLValidationError(ValueError):
    pass


# 默认白名单：根据自己业务调整
DEFAULT_ALLOWED_TABLES = {"users", "orders", "products", "logs"}
MAX_LIMIT = 100


def validate_sql(sql: str, allowed_tables: set[str] = DEFAULT_ALLOWED_TABLES) -> str:
    """校验并返回安全后的 SQL"""
    # 0. 删除前后空白
    sql = sql.strip()
    if not sql:
        raise SQLValidationError("SQL 不能为空")

    # 1. 禁止注释
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise SQLValidationError("SQL 不允许注释")

    # 2. 禁止多语句
    if sql.rstrip(";").count(";") > 0:
        raise SQLValidationError("SQL 不允许多语句")

    # 3. 解析
    parsed = sqlparse.parse(sql)
    if len(parsed) != 1:
        raise SQLValidationError("只允许单条 SQL")
    stmt: Statement = parsed[0]

    # 4. 必须是 SELECT
    if stmt.get_type() != "SELECT":
        raise SQLValidationError(f"只允许 SELECT，你的是 {stmt.get_type()}")

    # 5. 不允许 DDL / DML keyword
    forbidden = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                 "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE"}
    for token in stmt.flatten():
        if (token.ttype in (Keyword, DDL, DML)
                and token.normalized.upper() in forbidden):
            raise SQLValidationError(f"禁止关键字: {token.normalized}")

    # 6. 检查表名白名单（粗暴但有效：找 FROM/JOIN 后的标识）
    sql_upper = sql.upper()
    table_refs = re.findall(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql_upper)
    table_refs += re.findall(r"\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql_upper)
    for tbl in table_refs:
        if tbl.lower() not in allowed_tables:
            raise SQLValidationError(
                f"表 '{tbl}' 不在白名单内。可用：{sorted(allowed_tables)}"
            )

    # 7. 强制 LIMIT
    safe_sql = sql.rstrip(";").strip()
    if "LIMIT" not in safe_sql.upper():
        safe_sql += f" LIMIT {MAX_LIMIT}"
    else:
        # 把现有 LIMIT 截到 MAX_LIMIT
        safe_sql = re.sub(
            r"\bLIMIT\s+(\d+)",
            lambda m: f"LIMIT {min(int(m.group(1)), MAX_LIMIT)}",
            safe_sql,
            flags=re.IGNORECASE,
        )

    return safe_sql
```

### 4.2 `server.py`

```python
# demos/practice/db_mcp/server.py
"""只读数据库 MCP Server"""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from safety import DEFAULT_ALLOWED_TABLES, SQLValidationError, validate_sql

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).parent / "sample.db"))


def init_sample_db():
    """如果 sample.db 不存在，建一份 demo 数据"""
    if DB_PATH.exists():
        return
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, tier TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT);
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL);
        INSERT INTO users (name, tier) VALUES ('Alice','vip'),('Bob','standard'),('Carol','vip');
        INSERT INTO orders (user_id, amount, status) VALUES
          (1, 199.0, 'paid'), (1, 299.0, 'shipped'),
          (2, 99.0,  'paid'), (3, 599.0, 'refunded');
        INSERT INTO products (name, price) VALUES ('Book',49),('Laptop',999),('Mouse',29);
    """)
    con.commit()
    con.close()


@dataclass
class AppCtx:
    conn: sqlite3.Connection


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    init_sample_db()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield AppCtx(conn=conn)
    finally:
        conn.close()


mcp = FastMCP("db-readonly", lifespan=app_lifespan)


def _ctx(ctx: Context) -> AppCtx:
    return ctx.request_context.lifespan_context


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False}
)
async def query(sql: str, ctx: Context) -> list[dict]:
    """对内部数据库执行只读 SQL 查询。

    安全约束：
    - 只允许 SELECT
    - 禁止注释 / 多语句 / DDL/DML 关键字
    - 表必须在白名单：users / orders / products / logs
    - 自动强制 LIMIT 100

    Args:
        sql: 标准 SQL SELECT 语句，例如 'SELECT * FROM users WHERE tier=\\'vip\\''
    """
    try:
        safe = validate_sql(sql)
    except SQLValidationError as e:
        raise ToolError(f"SQL 校验失败: {e}")

    await ctx.info(f"执行: {safe}")
    app = _ctx(ctx)
    try:
        cur = app.conn.execute(safe)
    except sqlite3.Error as e:
        raise ToolError(f"SQL 执行失败: {e}")
    rows = [dict(r) for r in cur.fetchall()]
    return rows


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False}
)
async def describe_table(name: str, ctx: Context) -> list[dict]:
    """查看某张表的列定义。

    Args:
        name: 表名（必须在白名单内）
    """
    if name.lower() not in DEFAULT_ALLOWED_TABLES:
        raise ToolError(f"表 '{name}' 不在白名单内")
    app = _ctx(ctx)
    cur = app.conn.execute(f"PRAGMA table_info({name})")
    return [dict(r) for r in cur.fetchall()]


@mcp.resource("db://tables", mime_type="application/json")
def list_tables() -> list[str]:
    """列出所有可访问的表"""
    return sorted(DEFAULT_ALLOWED_TABLES)


@mcp.resource("db://schema/{table}", mime_type="text/plain")
def table_schema(table: str) -> str:
    """读某张表的 schema 描述"""
    if table.lower() not in DEFAULT_ALLOWED_TABLES:
        raise FileNotFoundError(table)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        lines = [f"Table: {table}"]
        for row in cur.fetchall():
            cid, name, type_, notnull, dflt, pk = row
            tag = " PRIMARY KEY" if pk else " NOT NULL" if notnull else ""
            lines.append(f"  - {name} {type_}{tag}")
        return "\n".join(lines)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
```

---

## 5. 测试 safety.py

```python
# tests/test_safety.py
import pytest
from safety import validate_sql, SQLValidationError


def test_basic_select_ok():
    sql = "SELECT * FROM users WHERE tier='vip'"
    assert "LIMIT 100" in validate_sql(sql)


def test_reject_drop():
    with pytest.raises(SQLValidationError, match="禁止关键字"):
        validate_sql("DROP TABLE users")


def test_reject_insert():
    with pytest.raises(SQLValidationError, match="只允许 SELECT"):
        validate_sql("INSERT INTO users VALUES (4, 'X', 'vip')")


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
    sql = "SELECT * FROM users LIMIT 9999"
    assert "LIMIT 100" in validate_sql(sql)


def test_limit_preserved_if_smaller():
    sql = "SELECT * FROM users LIMIT 5"
    assert "LIMIT 5" in validate_sql(sql)
```

跑：

```bash
pytest demos/practice/db_mcp/tests/
```

---

## 6. 跑起来

```bash
python demos/practice/db_mcp/server.py
# 首次会自动建 sample.db

# 用 Inspector
npx @modelcontextprotocol/inspector python demos/practice/db_mcp/server.py
```

试试这些 query：

```
query("SELECT * FROM users WHERE tier='vip'")
query("SELECT u.name, COUNT(o.id) cnt FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.id")

# 这些应该被拦：
query("DROP TABLE users")          → ToolError: 禁止关键字 DROP
query("SELECT * FROM credit_cards") → ToolError: 不在白名单
query("SELECT 1; DROP TABLE users") → ToolError: 多语句
```

---

## 7. 接到 Claude Code

```json
{
  "mcpServers": {
    "db": {
      "command": "python",
      "args": ["/abs/path/db_mcp/server.py"],
      "env": {
        "DB_PATH": "/abs/path/db_mcp/sample.db"
      }
    }
  }
}
```

```
[user]: 找一下我们 VIP 用户的订单总额
[claude]: 调用 db__query
         SQL: SELECT u.name, SUM(o.amount) FROM users u
              JOIN orders o ON u.id = o.user_id
              WHERE u.tier='vip' GROUP BY u.id
[claude]: Alice 总额 $498，Carol 总额 $599...
```

---

## 8. 改成 PostgreSQL

把 sqlite3 换成 asyncpg：

```python
import asyncpg

@asynccontextmanager
async def app_lifespan(server):
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"],
        min_size=2, max_size=10,
        command_timeout=5,
        server_settings={"statement_timeout": "5000"},  # 5s
    )
    try:
        yield AppCtx(pool=pool)
    finally:
        await pool.close()
```

PG 比 SQLite 多用 `statement_timeout` 防慢查询。

---

## 9. 加只读用户（数据库层防御）

**最好的安全**是**数据库层**给 MCP Server 一个**只读 role**：

```sql
-- PostgreSQL
CREATE USER mcp_readonly WITH PASSWORD 'xxx';
GRANT CONNECT ON DATABASE mydb TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON users, orders, products TO mcp_readonly;
-- 不 GRANT 别的
```

这样即使应用层的 SQL 过滤被绕过，DB 也拒绝写。

---

## 10. 扩展方向

| 想加 | 怎么做 |
|------|--------|
| 查询缓存 | Tool 里加 TTL 缓存（同 SQL 5 分钟内复用） |
| 查询审计 | logging 每条 SQL + user + 结果行数 |
| 多 schema | 配 `db://schema/{schema}/{table}` |
| 自然语言转 SQL | 加一个 Tool `nl2sql(question)`，里头用 sampling 让 Host LLM 翻译 |
| 慢查询保护 | 配置 statement_timeout |
| 解释执行计划 | Tool `explain(sql)` 返回 EXPLAIN |

---

## 11. 安全清单

- [x] 只允许 SELECT
- [x] 表白名单
- [x] LIMIT 上限
- [x] 禁止注释 / 多语句
- [x] 禁止 DDL/DML 关键字
- [ ] DB 层只读 role（生产必加）
- [ ] 慢查询超时（PG 用 statement_timeout）
- [ ] 速率限制（每用户每分钟 N 次）
- [ ] 审计日志（结果行数、SQL hash、user id）

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **sqlparse 解析嵌套子查询有 bug** | 加单元测试覆盖 |
| **LIMIT 后还能 OFFSET 大** | 同时限 OFFSET 上限 |
| **`(SELECT ...) UNION (SELECT ...)`** | sqlparse type 还是 SELECT，但要小心 column 数量 |
| **CTE (WITH 子句)** | 解析 type 仍是 SELECT，但表名识别要扩展正则 |
| **UDF / 函数注入** | 禁用 `pg_read_file` 等危险函数 |
| **大查询 OOM** | LIMIT 100 通常够，但 SELECT JSON 列要小心 |

---

## 13. 下一步

- 📖 给 Claude Code 写自定义 MCP → [03-project-claude-code-tool.md](./03-project-claude-code-tool.md)
- 📖 鉴权（远程数据库 MCP）→ 05-production/02-auth-oauth

## 参考资料

- sqlparse：https://sqlparse.readthedocs.io
- PostgreSQL statement_timeout：https://www.postgresql.org/docs/current/runtime-config-client.html
- SQL 注入防御：https://owasp.org/www-community/attacks/SQL_Injection
