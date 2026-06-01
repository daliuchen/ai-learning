"""
07_messages_history.py
======================
消息与对话历史 demo：
1) 拿 all_messages / new_messages
2) 多轮聊天，传 message_history
3) 序列化 / 反序列化（ModelMessagesTypeAdapter）
4) SQLite 持久化
5) 检查工具调用历史

没有 API key 时使用 TestModel。

运行：
    python demos/basics/07_messages_history.py
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model():
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-haiku-latest"
    print("[warn] 未检测到 API key，使用 TestModel\n")
    return TestModel()


MODEL = pick_model()


# ---------- 1) 拿到消息 ----------
def demo_inspect_messages() -> None:
    print("===== 1) 拿到消息（all_messages） =====")
    agent = Agent(MODEL, system_prompt="你是一位简洁的助手。")
    r = agent.run_sync("你好")
    print(f"output       : {r.output}")
    print(f"消息条数      : {len(r.all_messages())}")
    for i, m in enumerate(r.all_messages()):
        kinds = [type(p).__name__ for p in m.parts]
        print(f"  msg[{i}] = {type(m).__name__} parts={kinds}")
    print()


# ---------- 2) 多轮聊天 ----------
def demo_multi_turn() -> None:
    print("===== 2) 多轮聊天 =====")
    agent = Agent(MODEL, system_prompt="你是一位会记住用户名字的助手。")

    r1 = agent.run_sync("我叫 Ethan")
    print(f"turn 1: {r1.output}")

    r2 = agent.run_sync("我刚刚说我叫什么？", message_history=r1.new_messages())
    print(f"turn 2: {r2.output}")

    r3 = agent.run_sync("再确认一遍我的名字", message_history=r2.all_messages())
    print(f"turn 3: {r3.output}")
    print()


# ---------- 3) 序列化 / 反序列化 ----------
def demo_serialize() -> None:
    print("===== 3) 序列化 / 反序列化 =====")
    agent = Agent(MODEL, system_prompt="一句话回答。")
    r = agent.run_sync("地球的卫星叫什么？")

    blob: bytes = ModelMessagesTypeAdapter.dump_json(r.all_messages())
    print(f"dump 后 {len(blob)} 字节")

    restored = ModelMessagesTypeAdapter.validate_json(blob)
    print(f"restore 后 {len(restored)} 条消息")
    assert len(restored) == len(r.all_messages())

    # 用 restore 后的历史继续聊
    r2 = agent.run_sync("那它有多大？", message_history=restored)
    print(f"接续 : {r2.output}")
    print()


# ---------- 4) SQLite 持久化 ----------
def demo_sqlite() -> None:
    print("===== 4) SQLite 持久化 =====")
    db_path = Path(tempfile.gettempdir()) / "pydantic_ai_chat_demo.db"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, messages BLOB)"
    )
    conn.commit()

    agent = Agent(MODEL, system_prompt="你是一位简洁的助手，记住用户说过的话。")

    def chat(conv_id: str, prompt: str) -> str:
        row = conn.execute(
            "SELECT messages FROM conversations WHERE id=?", (conv_id,)
        ).fetchone()
        history = ModelMessagesTypeAdapter.validate_json(row[0]) if row else []
        r = agent.run_sync(prompt, message_history=history)
        new_blob = ModelMessagesTypeAdapter.dump_json(r.all_messages())
        conn.execute(
            "INSERT INTO conversations(id, messages) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET messages=excluded.messages",
            (conv_id, new_blob),
        )
        conn.commit()
        return r.output

    print("turn 1 :", chat("u1", "我最喜欢的语言是 Python"))
    print("turn 2 :", chat("u1", "我刚刚说我喜欢什么？"))

    # 模拟"重启程序"：新建连接读出来
    conn.close()
    conn2 = sqlite3.connect(db_path)
    blob = conn2.execute("SELECT messages FROM conversations WHERE id=?", ("u1",)).fetchone()[0]
    history = ModelMessagesTypeAdapter.validate_json(blob)
    print(f"重启后 history 含 {len(history)} 条消息")
    conn2.close()
    db_path.unlink(missing_ok=True)
    print()


# ---------- 5) 检查工具调用历史 ----------
def demo_tool_history() -> None:
    print("===== 5) 检查工具调用历史 =====")
    agent = Agent(MODEL, system_prompt="你是一位天气助手，必要时调用 get_weather。")

    @agent.tool_plain
    def get_weather(city: str) -> str:
        """查询城市天气"""
        db = {"北京": "晴 26°C", "上海": "多云 24°C"}
        return db.get(city, "未知")

    r = agent.run_sync("北京和上海的天气")
    for m in r.all_messages():
        for p in m.parts:
            if isinstance(p, ToolCallPart):
                print(f"  CALL  {p.tool_name}({p.args!r})")
            elif isinstance(p, ToolReturnPart):
                content = p.content
                print(f"  RETURN {p.tool_name} → {content!r}")
    print()


def main() -> None:
    demo_inspect_messages()
    demo_multi_turn()
    demo_serialize()
    demo_sqlite()
    demo_tool_history()


if __name__ == "__main__":
    main()
