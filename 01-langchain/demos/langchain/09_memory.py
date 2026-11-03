"""
09_memory.py
============
RunnableWithMessageHistory + trim_messages 演示
"""
import os
from dotenv import load_dotenv

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import trim_messages
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI

load_dotenv()
MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")  # 任意 OpenAI 兼容模型；默认千问，base_url 读 OPENAI_BASE_URL

model = ChatOpenAI(model=MODEL)
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是助手，记住用户告诉你的信息。"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])


def trimmer(msgs):
    return trim_messages(
        msgs,
        max_tokens=6,  # 最多保留最近 6 条消息
        token_counter=len,  # 非 OpenAI 模型没法精确数 token；len 表示按「消息条数」裁剪
        strategy="last",
        include_system=True,
        start_on="human",
    )


chain = (
    {
        "input": lambda x: x["input"],
        "history": lambda x: trimmer(x["history"]),
    }
    | prompt
    | model
)

_store: dict[str, InMemoryChatMessageHistory] = {}


def get_history(session_id: str) -> InMemoryChatMessageHistory:
    return _store.setdefault(session_id, InMemoryChatMessageHistory())


bot = RunnableWithMessageHistory(
    chain,
    get_history,
    input_messages_key="input",
    history_messages_key="history",
)


def main():
    cfg = {"configurable": {"session_id": "u1"}}
    for q in [
        "我叫小明，喜欢猫。",
        "我喜欢什么动物？",
        "推荐一只适合上班族的猫。",
    ]:
        a = bot.invoke({"input": q}, config=cfg).content
        print(f"\n用户: {q}\n助手: {a}")


if __name__ == "__main__":
    main()
