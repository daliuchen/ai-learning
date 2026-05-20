"""
02_chat_models.py
=================
Chat Models 完整演示：
- 多供应商切换
- 同步/异步/批量/流式
- with_retry + with_fallbacks
- bind_tools
- with_structured_output
- 缓存
"""
import asyncio
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()
set_llm_cache(InMemoryCache())


@tool
def get_weather(city: str) -> str:
    """查询城市天气。"""
    return f"{city} 晴 25℃"


class Person(BaseModel):
    """一个人的基本信息"""
    name: str = Field(description="姓名")
    age: int = Field(description="年龄")
    hobbies: list[str] = Field(default_factory=list, description="爱好")


def demo_basic() -> None:
    print("\n========== 1. 基本调用 ==========")
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    resp = model.invoke([
        SystemMessage(content="你是简洁的技术作家。"),
        HumanMessage(content="一句话讲清楚什么是 LCEL。"),
    ])
    print("content:", resp.content)
    print("usage:", resp.usage_metadata)


def demo_retry_fallback() -> None:
    print("\n========== 2. 重试 + 回退 ==========")
    # 故意写错的 base_url 触发回退
    bad = ChatOpenAI(model="gpt-4o-mini", base_url="https://invalid.example/v1", timeout=3, max_retries=0)
    good = ChatOpenAI(model="gpt-4o-mini")
    robust = bad.with_fallbacks([good])
    resp = robust.invoke("说一句你好")
    print("content:", resp.content)


def demo_tool_call() -> None:
    print("\n========== 3. bind_tools ==========")
    model = ChatOpenAI(model="gpt-4o-mini").bind_tools([get_weather])
    resp = model.invoke("北京和上海今天天气怎么样？")
    print("content:", resp.content)
    print("tool_calls:", resp.tool_calls)


def demo_structured() -> None:
    print("\n========== 4. with_structured_output ==========")
    model = ChatOpenAI(model="gpt-4o-mini").with_structured_output(Person)
    p = model.invoke("我叫小明，今年 20 岁，喜欢编程和篮球")
    print(p)
    print("type:", type(p).__name__)


def demo_stream() -> None:
    print("\n========== 5. 流式 ==========")
    model = ChatOpenAI(model="gpt-4o-mini")
    for chunk in model.stream("用三句话讲讲 Python 的优势"):
        print(chunk.content, end="", flush=True)
    print()


async def demo_async() -> None:
    print("\n========== 6. 异步 batch ==========")
    model = ChatOpenAI(model="gpt-4o-mini")
    results = await model.abatch(
        ["北京天气", "上海天气", "广州天气"],
        config={"max_concurrency": 3},
    )
    for r in results:
        print("-", r.content)


def demo_cache() -> None:
    print("\n========== 7. 缓存 ==========")
    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    import time
    for i in range(2):
        t = time.time()
        model.invoke("用一个字回答：地球是几角形的？")
        print(f"call {i+1} 用时 {time.time()-t:.2f}s")


if __name__ == "__main__":
    demo_basic()
    demo_retry_fallback()
    demo_tool_call()
    demo_structured()
    demo_stream()
    asyncio.run(demo_async())
    demo_cache()
