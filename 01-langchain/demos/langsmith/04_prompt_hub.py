"""
04_prompt_hub.py
================
Prompt Hub：pull 公开 prompt（无需 API Key）+ push 私有 prompt（需要 Key，默认关闭）。
注意：langchain.hub 已移除，改用 langsmith.Client().pull_prompt / push_prompt。
"""
import os

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import Client

load_dotenv()

MODEL = os.environ.get("QWEN_MODEL", "gpt-4o-mini")
client = Client()


def demo_pull_public():
    print("\n=== 1. pull rlm/rag-prompt ===")
    # 拉别人公开的 prompt 必须显式确认（prompt 里是序列化的 LangChain 对象，可能不可信）
    p = client.pull_prompt("rlm/rag-prompt", dangerously_pull_public_prompt=True)
    print("input_variables:", p.input_variables)
    chain = p | ChatOpenAI(model=MODEL)
    out = chain.invoke({
        "context": "LCEL 是 LangChain Expression Language",
        "question": "LCEL 是什么？",
    })
    print("answer:", out.content[:200])


def demo_push_private():
    print("\n=== 2. 私有 push（默认关闭，自行打开） ===")
    if os.getenv("LANGSMITH_HUB_PUSH") != "1" or not os.getenv("LANGSMITH_API_KEY"):
        print("跳过：需要 LANGSMITH_API_KEY 且设置 LANGSMITH_HUB_PUSH=1")
        return
    p = ChatPromptTemplate.from_messages([("system", "你是 {role}"), ("human", "{q}")])
    # 不带 owner 前缀，推到当前账号下；每次 push 产生一个新 commit
    url = client.push_prompt("role-qa", object=p, tags=["demo"], commit_tags=["staging"])
    print("已 push:", url)


if __name__ == "__main__":
    demo_pull_public()
    demo_push_private()
