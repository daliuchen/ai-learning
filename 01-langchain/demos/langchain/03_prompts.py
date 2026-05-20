"""
03_prompts.py
=============
Prompt Templates 完整演示。
"""
from dotenv import load_dotenv

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import (
    ChatPromptTemplate,
    FewShotChatMessagePromptTemplate,
    MessagesPlaceholder,
)
from langchain_openai import ChatOpenAI

load_dotenv()


def demo_basic():
    print("\n=== 1. 最基础 ===")
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是 {role}。"),
        ("human", "{question}"),
    ])
    pv = prompt.invoke({"role": "Python 老师", "question": "什么是装饰器？"})
    print(pv.to_string())


def demo_placeholder():
    print("\n=== 2. MessagesPlaceholder ===")
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是友好助手"),
        MessagesPlaceholder("history"),
        ("human", "{question}"),
    ])
    msgs = prompt.invoke({
        "history": [("human", "我叫小明"), ("ai", "你好小明")],
        "question": "我叫什么？",
    }).to_messages()
    for m in msgs:
        print(type(m).__name__, ":", m.content)


def demo_partial():
    print("\n=== 3. partial ===")
    from datetime import datetime
    prompt = ChatPromptTemplate.from_messages([
        ("system", "当前时间 {now}"),
        ("human", "{q}"),
    ]).partial(now=lambda: datetime.now().isoformat())
    print(prompt.invoke({"q": "几点了？"}).to_string())


def demo_few_shot():
    print("\n=== 4. Few-shot ===")
    example_prompt = ChatPromptTemplate.from_messages([
        ("human", "{input}"),
        ("ai", "{output}"),
    ])
    few_shot = FewShotChatMessagePromptTemplate(
        example_prompt=example_prompt,
        examples=[
            {"input": "2+2", "output": "4"},
            {"input": "3*4", "output": "12"},
        ],
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", "数学计算器，仅输出数字"),
        few_shot,
        ("human", "{input}"),
    ])
    chain = prompt | ChatOpenAI(model="gpt-4o-mini", temperature=0) | StrOutputParser()
    print("6*7 =", chain.invoke({"input": "6*7"}))


if __name__ == "__main__":
    demo_basic()
    demo_placeholder()
    demo_partial()
    demo_few_shot()
