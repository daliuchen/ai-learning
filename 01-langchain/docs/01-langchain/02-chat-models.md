# LangChain 02：Chat Models 与 Messages

> **一句话**：Chat Model 是 LangChain 里的"模型抽象"，把 OpenAI/Anthropic/Google/DeepSeek 等供应商抹平成一个统一接口，输入永远是 `List[BaseMessage]`，输出永远是 `AIMessage`。

---

## 1. 为什么不直接调 SDK

直接用 `openai.chat.completions.create(...)` 没问题，但当你想：

- 换成 Claude / Gemini → 整套调用要改
- 切到流式 → 又写一遍
- 加重试 / 限流 / 回退到备用模型 → 又写一遍
- 接入 LangSmith 监控 → 又改一遍

LangChain 的 `ChatModel` 抽象解决了这些事。**核心承诺：所有 ChatModel 实现 `Runnable` 接口**，自带：

```python
.invoke()  / .ainvoke()   # 同步 / 异步
.batch()   / .abatch()    # 批量
.stream()  / .astream()   # 流式
.with_retry()             # 自动重试
.with_fallbacks()         # 多模型回退
.with_structured_output() # 结构化输出
.bind_tools()             # 工具调用
```

---

## 2. 安装与第一次调用

每家供应商有独立的 partner 包：

```bash
pip install langchain-openai langchain-anthropic langchain-google-genai langchain-deepseek langchain-ollama
```

最简调用：

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

model = ChatOpenAI(model="gpt-4o-mini")
resp = model.invoke([HumanMessage(content="你好")])
print(resp.content)
```

注意输入是 `List[BaseMessage]`，不是字符串。但 LangChain 做了**鸭子类型容错**：

```python
model.invoke("你好")                           # ✅ 字符串自动包成 HumanMessage
model.invoke([("system", "..."), ("human", "...")])  # ✅ 元组列表也行
model.invoke([{"role": "user", "content": "..."}])   # ✅ OpenAI 格式 dict 也行
```

工程实践建议**显式使用 Message 对象**，代码清晰、IDE 提示友好。

---

## 3. 六种核心 Message 类型

```python
from langchain_core.messages import (
    HumanMessage,      # 用户消息
    AIMessage,         # 助手消息
    SystemMessage,     # 系统指令
    ToolMessage,       # 工具调用结果（Tool Calling 用）
    FunctionMessage,   # 旧 Function Calling（已废弃，保留兼容）
    ChatMessage,       # 自定义 role 的消息（很少用）
)
```

最常用是前四个，对应一次完整工具调用流程：

```
HumanMessage      → "查一下北京天气"
AIMessage(        → 模型决定调用工具
  tool_calls=[
    {"name":"get_weather","args":{"city":"北京"},"id":"call_1"}
  ]
)
ToolMessage(      → 工具执行后回填结果
  tool_call_id="call_1",
  content="晴 25℃"
)
AIMessage         → "北京今天晴，25 度"
```

---

## 4. 模型参数

`ChatOpenAI` 的常用参数（其他供应商类似）：

```python
model = ChatOpenAI(
    model="gpt-4o-mini",          # 模型名
    temperature=0.3,              # 创造性 0~2
    max_tokens=1024,              # 最大输出 tokens
    top_p=1.0,                    # 核采样
    timeout=30,                   # 单次请求超时
    max_retries=2,                # SDK 层重试
    streaming=True,               # 默认是否流式
    base_url="https://...",       # 自定义 API 地址（兼容 OpenAI 协议的服务）
    api_key="sk-xxx",             # 也可读环境变量 OPENAI_API_KEY
    organization="...",           # OpenAI org
    default_headers={"X-...": ""},# 自定义 header
    model_kwargs={                # 透传给底层 SDK 的额外参数
        "frequency_penalty": 0.5,
        "seed": 42,
    },
)
```

`temperature=0` 不等于完全确定性，OpenAI 的 `seed` 配合 `system_fingerprint` 才更接近确定输出。

---

## 5. 多供应商切换

LangChain 真正的价值之一：**换模型只换一行**。

```python
# OpenAI
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4o-mini")

# Anthropic
from langchain_anthropic import ChatAnthropic
model = ChatAnthropic(model="claude-3-5-sonnet-latest")

# Google
from langchain_google_genai import ChatGoogleGenerativeAI
model = ChatGoogleGenerativeAI(model="gemini-1.5-pro")

# DeepSeek（兼容 OpenAI 协议）
from langchain_deepseek import ChatDeepSeek
model = ChatDeepSeek(model="deepseek-chat")

# 本地 Ollama
from langchain_ollama import ChatOllama
model = ChatOllama(model="llama3.1:8b")
```

后续 chain 代码完全相同：

```python
chain = prompt | model | parser
```

---

## 6. 通用初始化：init_chat_model

LangChain 提供工厂函数让"模型选择"也参数化：

```python
from langchain.chat_models import init_chat_model

model = init_chat_model("gpt-4o-mini", model_provider="openai", temperature=0)
# 或一行式
model = init_chat_model("openai:gpt-4o-mini", temperature=0)
model = init_chat_model("anthropic:claude-3-5-sonnet-latest")
model = init_chat_model("ollama:llama3.1:8b")
```

适合做"配置驱动"的项目，把 model 名写在 YAML / 环境变量里。

---

## 7. 异步与并发

每个方法都有 `a` 前缀的异步版本：

```python
import asyncio
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model="gpt-4o-mini")

async def main():
    # 单条异步
    resp = await model.ainvoke("Hi")
    print(resp.content)

    # 批量并发
    qs = ["北京天气", "上海天气", "广州天气"]
    results = await model.abatch(qs, config={"max_concurrency": 3})
    for r in results:
        print(r.content)

    # 流式
    async for chunk in model.astream("讲个笑话"):
        print(chunk.content, end="", flush=True)

asyncio.run(main())
```

**注意**：`.batch()` 内部其实是 `asyncio.gather`，所以哪怕同步代码也享受并发。`max_concurrency` 是非常重要的限流参数，默认不限。

---

## 8. Tool Calling（极重要）

`bind_tools` 把工具描述附加到模型上，模型在合适时会"决定"调用工具：

```python
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气。"""
    return f"{city} 晴 25℃"

model = ChatOpenAI(model="gpt-4o-mini").bind_tools([get_weather])

resp = model.invoke("北京今天天气怎么样？")
print(resp.tool_calls)
# [{'name': 'get_weather', 'args': {'city': '北京'}, 'id': 'call_xxx', 'type': 'tool_call'}]
```

`resp.tool_calls` 是 LangChain 标准化后的格式（数组），不需要你解析 OpenAI 原始 JSON。

完整工具调用循环见第 7 篇 Tools。

---

## 9. 结构化输出

`with_structured_output` 让模型直接产出指定 schema 的 Python 对象：

```python
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

class Person(BaseModel):
    """一个人的基本信息"""
    name: str = Field(description="姓名")
    age: int = Field(description="年龄")
    hobbies: list[str] = Field(default_factory=list, description="爱好")

model = ChatOpenAI(model="gpt-4o-mini")
structured = model.with_structured_output(Person)

p = structured.invoke("我叫小明，今年 20 岁，喜欢编程和篮球")
print(p)         # Person(name='小明', age=20, hobbies=['编程', '篮球'])
print(p.name)    # '小明'
```

底层根据供应商自动选最优实现（OpenAI 用 JSON Schema 模式 / function call，Anthropic 用 tool）。第 4 篇会展开讲。

---

## 10. Streaming

最朴素的流式：

```python
for chunk in model.stream("写一首关于春天的诗"):
    print(chunk.content, end="", flush=True)
```

每个 `chunk` 是 `AIMessageChunk`，多个 chunk 用 `+` 累加：

```python
acc = None
for chunk in model.stream("..."):
    acc = chunk if acc is None else acc + chunk
print(acc.content)
```

如果只想拼最终 `AIMessage`，直接用 `invoke` 即可，`stream` 主要用于 UI 实时显示。

第 6 篇会专门讲 `astream_events`（细粒度事件流）。

---

## 11. 配置：with_config / config_kwargs

`Runnable` 支持运行时配置：

```python
chain = prompt | model | parser

result = chain.invoke(
    {"question": "..."},
    config={
        "run_name": "weather_query",          # LangSmith trace 名字
        "tags": ["prod", "v1"],
        "metadata": {"user_id": "u123"},
        "callbacks": [my_handler],
        "max_concurrency": 5,
    },
)
```

`tags` / `metadata` 会一路传递到 LangSmith，方便事后筛选 trace。

---

## 12. 错误处理三连：retry / fallback / rate-limit

### 12.1 自动重试

```python
robust_model = model.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
    retry_if_exception_type=(TimeoutError, ConnectionError),
)
```

### 12.2 模型回退

主模型挂了/被限流时，自动跑备用模型：

```python
primary = ChatOpenAI(model="gpt-4o")
fallback = ChatAnthropic(model="claude-3-5-sonnet-latest")

model = primary.with_fallbacks([fallback])
```

### 12.3 限速

```python
from langchain_core.rate_limiters import InMemoryRateLimiter

rate_limiter = InMemoryRateLimiter(
    requests_per_second=10,
    check_every_n_seconds=0.1,
    max_bucket_size=10,
)
model = ChatOpenAI(model="gpt-4o-mini", rate_limiter=rate_limiter)
```

适合本地批处理避免触发供应商 RPM 限制。

---

## 13. Token 与费用统计

每个 `AIMessage.usage_metadata` 都自动带 token 信息（前提是供应商返回）：

```python
resp = model.invoke("你好")
print(resp.usage_metadata)
# {'input_tokens': 8, 'output_tokens': 12, 'total_tokens': 20}
```

回调里也能拿到，第 14 篇会讲到 callback 维度的成本统计。

---

## 14. 缓存：避免重复调用

```python
from langchain_core.globals import set_llm_cache
from langchain_core.caches import InMemoryCache

set_llm_cache(InMemoryCache())

# 第一次正常调用
model.invoke("Hi")
# 第二次完全相同的输入 → 命中缓存，0 token
model.invoke("Hi")
```

生产环境用 Redis / SQLite cache：

```python
from langchain_community.cache import SQLiteCache, RedisCache
set_llm_cache(SQLiteCache(database_path=".cache.db"))
```

注意缓存按"完整请求体"为 key，包括 system 提示、temperature 等，参数变化即 miss。

---

## 15. 一个综合 demo：多供应商 + 回退 + 重试

```python
# demos/langchain/02_chat_models.py
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

primary = ChatOpenAI(model="gpt-4o-mini", temperature=0).with_retry(stop_after_attempt=2)
fallback = ChatAnthropic(model="claude-3-5-haiku-latest")
model = primary.with_fallbacks([fallback])

msgs = [
    SystemMessage(content="你是一位简洁的技术作家。"),
    HumanMessage(content="用 80 字以内解释什么是 LCEL。"),
]

resp = model.invoke(msgs)
print(resp.content)
print("tokens:", resp.usage_metadata)
```

跑完到 LangSmith 应该能看到这次调用的具体 trace（如果配了 `LANGSMITH_TRACING=true`）。

---

## 16. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 返回的 `AIMessage.content` 是空字符串但 `tool_calls` 有值 | 模型决定先调工具，没生成文字 | 检查 `resp.tool_calls`，按规范执行工具 |
| 流式没流起来，全部一起出现 | 上游 chain 把 chunk 缓存了 | 检查中间是否有 `RunnableLambda` 没传 chunk |
| 一调用就 429 | 没限速 | 加 `InMemoryRateLimiter` 或 `with_retry(stop_after_delay=..)` |
| 切换模型后输出格式乱了 | Prompt 对模型敏感 | 用 `with_structured_output` 用 schema 兜底 |
| `model_kwargs` 里写 `tools` 不生效 | 应该用 `bind_tools` 接口 | 见第 7 篇 |

---

## 17. 本章 demo

完整代码：[`demos/langchain/02_chat_models.py`](../../demos/langchain/02_chat_models.py)

下一篇专门讲 Prompt：[03-prompts.md](03-prompts.md)
