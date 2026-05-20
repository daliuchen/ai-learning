"""
01_hello_lcel.py
================
LangChain Hello World：演示最基础的三件套 prompt | model | parser

运行：
    python demos/langchain/01_hello_lcel.py
"""
from dotenv import load_dotenv

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()


def main() -> None:
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一位 {role}，请用 {style} 的语气回答。"),
        ("human", "{question}"),
    ])

    model = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    parser = StrOutputParser()

    chain = prompt | model | parser

    # 1) 同步 invoke
    print("===== invoke =====")
    print(chain.invoke({
        "role": "Python 资深工程师",
        "style": "简洁",
        "question": "Python 的 GIL 是什么？一句话讲清楚。",
    }))

    # 2) batch 批量执行
    print("\n===== batch =====")
    for r in chain.batch([
        {"role": "鲁迅", "style": "讽刺", "question": "如何评价 996？"},
        {"role": "李白", "style": "豪放", "question": "如何评价高速公路？"},
    ]):
        print("-", r, "\n")

    # 3) stream 流式
    print("===== stream =====")
    for chunk in chain.stream({
        "role": "幼儿园老师",
        "style": "耐心",
        "question": "什么是 Python？",
    }):
        print(chunk, end="", flush=True)
    print()


if __name__ == "__main__":
    main()
