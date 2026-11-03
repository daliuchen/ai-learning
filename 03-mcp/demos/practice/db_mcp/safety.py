"""SQL 安全过滤层（第一道防线：应用层）。第二道防线（更强）在 server.py：只读连接 + authorizer。

应用层过滤的价值是：给模型一个**可读、可纠正**的错误信息，并挡住绝大多数明显的坏 SQL；
但它做不到「证明一条 SQL 安全」——真正的安全靠数据库层（只读 role / mode=ro / authorizer）。
"""
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
