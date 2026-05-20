"""
04_prompt_hub.py
================
Prompt Hub 演示：pull 公开 prompt + push 私有 prompt
"""
import os

from dotenv import load_dotenv

from langchain import hub
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()
if not os.getenv("LANGSMITH_API_KEY"):
    raise SystemExit("请先配置 LANGSMITH_API_KEY")


def demo_pull_public():
    print("\n=== 1. pull rlm/rag-prompt ===")
    p = hub.pull("rlm/rag-prompt")
    print("input_variables:", p.input_variables)
    chain = p | ChatOpenAI(model="gpt-4o-mini")
    out = chain.invoke({
        "context": "LCEL 是 LangChain Expression Language",
        "question": "LCEL 是什么？",
    })
    print("answer:", out.content[:200])


def demo_push_private():
    print("\n=== 2. 私有 push（默认关闭，自行打开） ===")
    if os.getenv("LANGSMITH_HUB_PUSH") != "1":
        print("跳过：设置 LANGSMITH_HUB_PUSH=1 启用")
        return
    p = ChatPromptTemplate.from_messages([
        ("system", "你是 {role}"),
        ("human", "{q}"),
    ])
    handle = os.getenv("LANGSMITH_HANDLE")
    if not handle:
        print("跳过：设置 LANGSMITH_HANDLE=your-handle")
        return
    hub.push(f"{handle}/role-qa", p)
    print("已 push", f"{handle}/role-qa")


if __name__ == "__main__":
    demo_pull_public()
    demo_push_private()
