"""
06_streaming.py
===============
Streaming 全方位演示：stream / astream / astream_events / 部分 JSON 流式
"""
import asyncio

from dotenv import load_dotenv

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

load_dotenv()
model = ChatOpenAI(model="gpt-4o-mini", stream_usage=True)


def demo_stream():
    print("\n=== sync stream ===")
    chain = ChatPromptTemplate.from_template("用中文讲两句关于 {x} 的诗") | model | StrOutputParser()
    for chunk in chain.stream({"x": "春天"}):
        print(chunk, end="", flush=True)
    print()


def demo_json_partial():
    print("\n=== JsonOutputParser 部分流式 ===")
    parser = JsonOutputParser()
    chain = (
        ChatPromptTemplate.from_messages([
            ("system", "返回 JSON: {{\"fruits\": [...5 个水果...]}}"),
            ("human", "go"),
        ])
        | model
        | parser
    )
    for p in chain.stream({}):
        print(p)


async def demo_events():
    print("\n=== astream_events ===")

    async def fake_retrieve(q: str):
        await asyncio.sleep(0.3)
        return [f"文档 {i}：关于 {q} 的内容" for i in range(3)]

    retriever = RunnableLambda(fake_retrieve).with_config(run_name="my_retriever")
    prompt = ChatPromptTemplate.from_messages([
        ("system", "根据：\n{ctx}"),
        ("human", "{q}"),
    ])
    chain = (
        {"ctx": retriever | (lambda d: "\n".join(d)), "q": lambda x: x["q"]}
        | prompt
        | model
        | StrOutputParser()
    )
    async for ev in chain.astream_events({"q": "Python 优势"}, version="v2"):
        kind = ev["event"]
        name = ev["name"]
        if kind == "on_chain_end" and name == "my_retriever":
            print(f"\n[retriever 完成] 返回 {len(ev['data']['output'])} 段")
        elif kind == "on_chat_model_stream":
            print(ev["data"]["chunk"].content, end="", flush=True)
    print()


if __name__ == "__main__":
    demo_stream()
    demo_json_partial()
    asyncio.run(demo_events())
