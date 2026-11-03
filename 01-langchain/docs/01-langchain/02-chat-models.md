# LangChain 02：Chat Models 与 Messages

> Chat Model 是 LangChain 对模型的抽象。OpenAI、Anthropic、Google、DeepSeek 这些供应商的差异被收进同一个接口：输入是 `List[BaseMessage]`，输出是 `AIMessage`。后面所有篇章都建立在它上面，所以这篇把消息类型、参数、多供应商切换、重试回退和缓存一次讲完。

---

## 1. 为什么不直接调 SDK

直接用 `openai.chat.completions.create(...)` 当然可以。麻烦在后面：换成 Claude 或 Gemini，整套调用要改；改成流式，要再写一遍；加重试、限流、备用模型，又是一遍；接 LangSmith 监控，还得再改。

LangChain 的做法是让所有 ChatModel 都实现 `Runnable` 接口，下面这些能力是现成的：

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

> 1.x 之后 `langchain` 主包不再替你转发各家集成。用哪家就装哪家的 partner 包，再直接 `from langchain_openai import ChatOpenAI` 这样导入。

最简调用：

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

model = ChatOpenAI(model="gpt-6-luna")
resp = model.invoke([HumanMessage(content="你好")])
print(resp.content)
```

标准输入是 `List[BaseMessage]`，不过 LangChain 对其他几种写法也做了兼容：

```python
model.invoke("你好")                           # ✅ 字符串自动包成 HumanMessage
model.invoke([("system", "..."), ("human", "...")])  # ✅ 元组列表也行
model.invoke([{"role": "user", "content": "..."}])   # ✅ OpenAI 格式 dict 也行
```

实际项目里我建议显式用 Message 对象，代码意图清楚，IDE 提示也更好。

> 1.x 里消息类也可以从 `langchain.messages` 导入（`from langchain.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage`），和 `langchain_core.messages` 是同一批类，二选一即可。取回答文字用 `resp.text`（属性）；`resp.content` 在多模态或推理模型下可能是一个 block 列表。

---

## 3. 六种核心 Message 类型

```python
from langchain_core.messages import (   # 或 from langchain.messages import ...
    HumanMessage,      # 用户消息
    AIMessage,         # 助手消息
    SystemMessage,     # 系统指令
    ToolMessage,       # 工具调用结果（Tool Calling 用）
    FunctionMessage,   # 旧 Function Calling（已废弃，保留兼容）
    ChatMessage,       # 自定义 role 的消息（很少用）
)
```

### 3.1 content_blocks：跨供应商的统一内容结构

1.x 给 `AIMessage` 加了标准化的 `content_blocks`。不管底层是 OpenAI 还是 Anthropic，思考过程、文本、工具调用、图片都用同一种 block 结构表示：

```python
resp = model.invoke("为什么天空是蓝色的？")
for block in resp.content_blocks:
    if block["type"] == "reasoning":      # 推理模型的思考内容
        print("[思考]", block["reasoning"])
    elif block["type"] == "text":
        print(block["text"])
```

只想拿最终文字时，`resp.text` 就够了。

回到上面六种消息，最常用的是前四个。一次完整的工具调用会依次用到它们：

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
    model="gpt-6-luna",          # 模型名
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

### 4.1 接入 OpenAI 兼容接口（千问 / DeepSeek / 本地 vLLM 等）

国内常用的模型大多提供 OpenAI 兼容端点，用 `ChatOpenAI` 改一下 `base_url` 就能接，不需要专门的包（`ChatOpenAI` 也会自动读环境变量 `OPENAI_API_KEY` / `OPENAI_BASE_URL`）：

```python
import os
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="qwen-flash",   # 千问；换 qwen-plus / qwen-max 同理
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key=os.environ["DASHSCOPE_API_KEY"],
)
```

本书的 demo 都这样写：模型名从环境变量 `QWEN_MODEL` 读，默认 `qwen-flash`，`base_url` 来自 `OPENAI_BASE_URL`。

> `langchain-openai` 还会读 `OPENAI_API_BASE`，而且它的优先级高于 `OPENAI_BASE_URL`。shell 里如果残留了这个旧变量，请求会被悄悄发到错的地址，表现是 401 或「无效令牌」。遇到这种情况先 `env | grep OPENAI` 查一下。

`temperature=0` 并不保证输出完全确定。在 OpenAI 上，`seed` 配合 `system_fingerprint` 才更接近确定输出。Claude 5.x 则不接受 `temperature`、`top_p`、`top_k`，传了会返回 400；用 Claude 时去掉这几个参数，想要输出稳定就靠结构化输出，或者多次采样取多数结果。

---

## 5. 多供应商切换

换模型只需要改一行，这是 LangChain 很实用的一点。

```python
# OpenAI
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-6-luna")

# Anthropic
from langchain_anthropic import ChatAnthropic
model = ChatAnthropic(model="claude-sonnet-5-5")      # 模型名随厂商更新，以官方列表为准

# Google
from langchain_google_genai import ChatGoogleGenerativeAI
model = ChatGoogleGenerativeAI(model="gemini-4-argon")

# DeepSeek（兼容 OpenAI 协议）
from langchain_deepseek import ChatDeepSeek
model = ChatDeepSeek(model="deepseek-chat")

# 本地 Ollama
from langchain_ollama import ChatOllama
model = ChatOllama(model="llama3.1:8b")
```

后面的 chain 代码一个字都不用动：

```python
chain = prompt | model | parser
```

---

## 6. 通用初始化：init_chat_model

LangChain 还提供了一个工厂函数，把选哪个模型也变成参数：

```python
from langchain.chat_models import init_chat_model

model = init_chat_model("gpt-6-luna", model_provider="openai", temperature=0)
# 或一行式
model = init_chat_model("openai:gpt-6-luna", temperature=0)
model = init_chat_model("anthropic:claude-sonnet-5-5")
model = init_chat_model("ollama:llama3.1:8b")
```

如果项目靠配置驱动，模型名可以写在 YAML 或环境变量里。`create_agent("openai:gpt-6-luna", ...)` 里直接传的那个字符串，内部也是交给它解析的。

不指定型号的话，还能在运行时再决定用哪个：

```python
configurable = init_chat_model()                         # 不指定 model
configurable.invoke("...", config={"configurable": {"model": "gpt-6-luna"}})
configurable.invoke("...", config={"configurable": {"model": "claude-sonnet-5-5"}})
```

这里构造时没有传 `temperature`：同一个对象既要发给 GPT 又要发给 Claude，而 Claude 5.x 收到 `temperature` 会返回 400。

---

## 7. 异步与并发

每个方法都有带 `a` 前缀的异步版本：

```python
import asyncio
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model="gpt-6-luna")

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

同步的 `.batch()` 默认用线程池并发执行，`.abatch()` 用 `asyncio.gather`，所以同步代码调 `batch` 也能并发。`max_concurrency` 默认不限，**批量调用时一定要设**，否则很容易被供应商限流。

---

## 8. Tool Calling

这是 agent 的基础。`bind_tools` 把工具描述附加到模型上，模型觉得需要时就会发起调用：

```python
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气。"""
    return f"{city} 晴 25℃"

model = ChatOpenAI(model="gpt-6-luna").bind_tools([get_weather])

resp = model.invoke("北京今天天气怎么样？")
print(resp.tool_calls)
# [{'name': 'get_weather', 'args': {'city': '北京'}, 'id': 'call_xxx', 'type': 'tool_call'}]
```

`resp.tool_calls` 是 LangChain 标准化之后的数组，不用自己去解析 OpenAI 的原始 JSON。完整的工具调用循环放在第 7 篇讲。

---

## 9. 结构化输出

`with_structured_output` 让模型直接返回符合指定 schema 的 Python 对象：

```python
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

class Person(BaseModel):
    """一个人的基本信息"""
    name: str = Field(description="姓名")
    age: int = Field(description="年龄")
    hobbies: list[str] = Field(default_factory=list, description="爱好")

model = ChatOpenAI(model="gpt-6-luna")
structured = model.with_structured_output(Person)

p = structured.invoke("我叫小明，今年 20 岁，喜欢编程和篮球")
print(p)         # Person(name='小明', age=20, hobbies=['编程', '篮球'])
print(p.name)    # '小明'
```

底层会按供应商选合适的实现：OpenAI 用 JSON Schema 模式或 function call，Anthropic 用 tool。第 4 篇详细讲。

---

## 10. Streaming

最简单的流式写法：

```python
for chunk in model.stream("写一首关于春天的诗"):
    print(chunk.content, end="", flush=True)
```

每个 `chunk` 是一个 `AIMessageChunk`，可以用 `+` 累加起来：

```python
acc = None
for chunk in model.stream("..."):
    acc = chunk if acc is None else acc + chunk
print(acc.content)
```

如果最后只要完整的 `AIMessage`，直接 `invoke` 就行，`stream` 主要用在需要界面实时显示的场景。细粒度的事件流 `astream_events` 留到第 6 篇。

---

## 11. 配置：with_config / config_kwargs

调用 `Runnable` 时可以传运行时配置：

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

`tags` 和 `metadata` 会一路带到 LangSmith，事后可以按它们筛选 trace。

---

## 12. 错误处理：重试、回退与限速

### 12.1 自动重试

```python
robust_model = model.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
    retry_if_exception_type=(TimeoutError, ConnectionError),
)
```

### 12.2 模型回退

主模型出错或被限流时，自动切到备用模型：

```python
primary = ChatOpenAI(model="gpt-6.1-sol")
fallback = ChatAnthropic(model="claude-sonnet-5-5")

model = primary.with_fallbacks([fallback])
```

如果想让 `create_agent` 里整个 agent 的模型自动降级，用 1.x 的中间件更省事：`ModelFallbackMiddleware("anthropic:claude-sonnet-5-5")`、`ModelRetryMiddleware(...)`（见 LangChain 08 篇）。

### 12.3 限速

```python
from langchain_core.rate_limiters import InMemoryRateLimiter

rate_limiter = InMemoryRateLimiter(
    requests_per_second=10,
    check_every_n_seconds=0.1,
    max_bucket_size=10,
)
model = ChatOpenAI(model="gpt-6-luna", rate_limiter=rate_limiter)
```

本地跑批处理时用它，可以避免撞上供应商的 RPM 限制。（也可以 `from langchain.rate_limiters import InMemoryRateLimiter`，是同一个类。）

### 12.4 异常类型统一了

主流集成包现在会把各家供应商的异常转成 `langchain_core.exceptions` 里的统一类型，比如 `ModelRateLimitError`、`ModelTimeoutError`、`ModelAuthenticationError`、`ContextOverflowError` 等，每个都带 `is_retryable` 属性。换供应商时，`except` 那部分不用跟着改。

---

## 13. Token 与费用统计

只要供应商返回了用量，`AIMessage.usage_metadata` 里就会有 token 数：

```python
resp = model.invoke("你好")
print(resp.usage_metadata)
# {'input_tokens': 8, 'output_tokens': 12, 'total_tokens': 20}
```

回调里也能拿到这些数据，第 14 篇会讲怎么用 callback 做成本统计。

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

生产环境换成 Redis 或 SQLite 缓存：

```python
from langchain_community.cache import SQLiteCache, RedisCache
set_llm_cache(SQLiteCache(database_path=".cache.db"))
```

缓存的 key 是完整的请求体，system 提示、temperature 这些都算在内，任何参数变了都不会命中。

---

## 15. 一个综合 demo：多供应商 + 回退 + 重试

完整代码：[`demos/langchain/02_chat_models.py`](../../demos/langchain/02_chat_models.py)

```python
# demos/langchain/02_chat_models.py
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

primary = ChatOpenAI(model="gpt-6-luna", temperature=0).with_retry(stop_after_attempt=2)
fallback = ChatAnthropic(model="claude-haiku-5-5")
model = primary.with_fallbacks([fallback])

msgs = [
    SystemMessage(content="你是一位简洁的技术作家。"),
    HumanMessage(content="用 80 字以内解释什么是 LCEL。"),
]

resp = model.invoke(msgs)
print(resp.content)
print("tokens:", resp.usage_metadata)
```

如果设置了 `LANGSMITH_TRACING=true`，跑完可以在 LangSmith 里看到这次调用的 trace。

---

## 16. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `AIMessage.content` 是空字符串，`tool_calls` 却有值 | 模型决定先调工具，没生成文字 | 检查 `resp.tool_calls`，执行对应工具 |
| 流式没生效，内容一次性全出来 | 上游 chain 把 chunk 攒住了 | 检查中间有没有不透传 chunk 的 `RunnableLambda` |
| 一调用就 429 | 没限速 | 加 `InMemoryRateLimiter`，或 `with_retry(stop_after_attempt=..)` 配合指数退避 |
| 换模型后输出格式乱了 | 不同模型对同一 Prompt 的反应不同 | 用 `with_structured_output` 让 schema 兜底 |
| 在 `model_kwargs` 里写 `tools` 不生效 | 工具要通过 `bind_tools` 传 | 见 [LangChain 07：Tools 工具系统](./07-tools.md) |
| 千问/其他兼容接口报 401 | shell 里有残留的 `OPENAI_API_BASE`，它优先于 `OPENAI_BASE_URL` | `unset OPENAI_API_BASE` 或显式传 `base_url=` |
| `from langchain.chat_models import ...` 找不到某供应商类 | 1.x 不再转发各集成 | 装对应 partner 包并从 `langchain_xxx` 导入 |

---

下一篇讲 Prompt：[LangChain 03：Prompt Templates 提示模板](03-prompts.md)
