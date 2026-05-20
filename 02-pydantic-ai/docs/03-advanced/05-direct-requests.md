# Pydantic AI 进阶 05：Direct Model Requests 直接调模型

> **一句话**：当你不需要 Agent 的工具循环 / 输出校验 / 历史拼装 / 重试机制时，可以直接用 `pydantic_ai.direct.model_request_sync()` 一行代码跑一次推理，把 Pydantic AI 当成一个"模型无关 SDK"用。

---

## 1. 为什么需要"直接调模型"

`Agent` 是 Pydantic AI 的主入口，但它做了不少事：

- 拼 system prompt + 历史消息 + 当前 user prompt
- 注册工具、生成 JSON Schema、跑工具循环
- 校验结构化输出、失败时自动 `ModelRetry`
- 维护 `usage_limits`、`hooks`、`instrumentation`

这套机制在写 Agent 应用时无价，但有些场景里它反而是负担：

1. 只是想跑一次推理，比如做 prompt 实验、写评测脚本
2. 把 Pydantic AI 当成"统一的模型 SDK"，绕过 OpenAI/Anthropic/Google 各家的差异
3. 和现有系统集成时，消息列表是别处构造好的，不想让 Agent 改写
4. 自己写更高一层的抽象（比如自家的 Agent 框架），只想借用 Pydantic AI 的 Model 实现

这就是 `pydantic_ai.direct` 存在的理由。官方原话：

> These methods are thin wrappers around the Model implementations, offering a simpler interface when you don't need the full functionality of an Agent.

简单说，它是 **Model 实现的薄壳**，把"一次纯推理"暴露出来。

---

## 2. 四个核心函数

`pydantic_ai.direct` 模块提供四个函数，覆盖同步 / 异步 × 非流式 / 流式四个象限：

| 函数 | 同步 / 异步 | 流式 | 用途 |
|------|-------------|------|------|
| `model_request` | async | 否 | 异步一次性返回 |
| `model_request_sync` | sync | 否 | 同步一次性返回（脚本最常用） |
| `model_request_stream` | async | 是 | 异步流式，返回 `StreamedResponse` |
| `model_request_stream_sync` | sync | 是 | 同步流式，少用 |

它们的签名几乎一样：

```python
from pydantic_ai.direct import model_request_sync
from pydantic_ai import ModelRequest

response = model_request_sync(
    model,                          # str | Model 实例
    messages,                       # list[ModelMessage]
    *,
    model_settings=None,            # ModelSettings，可调温度、max_tokens 等
    model_request_parameters=None,  # 工具定义、输出模式
    instrument=None,                # 是否开启 logfire instrumentation
)
```

返回类型是 `ModelResponse`，结构后面会讲。

---

## 3. 最小例子

```python
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_sync

response = model_request_sync(
    'anthropic:claude-haiku-4-5',
    [ModelRequest.user_text_prompt('What is the capital of France?')],
)

print(response.parts[0].content)   # "The capital of France is Paris."
print(response.usage)              # RequestUsage(input_tokens=15, output_tokens=8)
print(response.model_name)         # 'claude-haiku-4-5'
print(response.timestamp)
```

观察三件事：

1. **model 用字符串就行**：和 Agent 一样支持 `'provider:model'` 简写
2. **messages 是 ModelMessage 列表**：`ModelRequest.user_text_prompt()` 是最常用的工厂
3. **response.parts 是分段**：模型有可能返回多段文本 + 工具调用，所以是 list

异步版本只多了 `async/await`：

```python
import asyncio
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request

async def main():
    response = await model_request(
        'openai:gpt-5-nano',
        [ModelRequest.user_text_prompt('What is 123 / 456?')],
    )
    print(response.parts[0].content)

asyncio.run(main())
```

---

## 4. ModelMessage 结构详解

要会用 `direct`，必须先搞清楚 Pydantic AI 的消息体。整个消息体系两类对象：

```
ModelMessage = ModelRequest | ModelResponse

ModelRequest（你发给模型的）
   └── parts: list[
         SystemPromptPart    # 系统提示
         UserPromptPart      # 用户输入（可含文本 / 图片 / 音频）
         ToolReturnPart      # 工具返回
         RetryPromptPart     # 重试提示
       ]

ModelResponse（模型回给你的）
   └── parts: list[
         TextPart            # 文本
         ToolCallPart        # 工具调用请求
         ThinkingPart        # 思维链
       ]
```

最常见的两种构造：

```python
from pydantic_ai.messages import (
    ModelRequest, ModelResponse,
    SystemPromptPart, UserPromptPart, TextPart,
)

# 方式 A：手撸 parts（最灵活）
messages = [
    ModelRequest(parts=[
        SystemPromptPart(content='你是简洁的助理，回答控制在 30 字以内'),
        UserPromptPart(content='Pydantic 是什么？'),
    ]),
]

# 方式 B：用工厂方法（最省事）
messages = [
    ModelRequest.user_text_prompt('Pydantic 是什么？'),
]
```

**注意**：工厂方法 `user_text_prompt` 不带 system prompt，多轮场景里你大概率要走方式 A。

多轮对话就是把 `ModelRequest` 和 `ModelResponse` 交替排列：

```python
messages = [
    ModelRequest(parts=[
        SystemPromptPart(content='你是翻译助手'),
        UserPromptPart(content='hello'),
    ]),
    ModelResponse(parts=[TextPart(content='你好')]),
    ModelRequest(parts=[UserPromptPart(content='good morning')]),
]
response = model_request_sync('openai:gpt-4o-mini', messages)
```

---

## 5. 加 ModelSettings 与系统参数

`direct` 不读 `Agent` 的配置，所有调参都通过 `model_settings`：

```python
from pydantic_ai import ModelRequest
from pydantic_ai.settings import ModelSettings
from pydantic_ai.direct import model_request_sync

response = model_request_sync(
    'openai:gpt-4o-mini',
    [ModelRequest.user_text_prompt('写一首五言绝句')],
    model_settings=ModelSettings(
        temperature=0.9,
        max_tokens=200,
        timeout=30.0,
    ),
)
```

如果想用更高级的 provider 选项（比如 OpenAI 的 `response_format`），就要用 `OpenAIChatModelSettings` 等子类。

---

## 6. 直接调模型也能用工具

`direct` 不像 Agent 那样自动跑工具循环，但**可以让模型决定调哪个工具**，由你来执行。这就是它"低层"的本质：

```python
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import ModelRequest, ToolDefinition
from pydantic_ai.direct import model_request
from pydantic_ai.models import ModelRequestParameters


class Divide(BaseModel):
    """Divide two numbers."""
    numerator: float
    denominator: float
    on_inf: Literal['error', 'infinity'] = 'infinity'


async def main():
    response = await model_request(
        'openai:gpt-5-nano',
        [ModelRequest.user_text_prompt('What is 123 / 456?')],
        model_request_parameters=ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name=Divide.__name__.lower(),
                    description=Divide.__doc__,
                    parameters_json_schema=Divide.model_json_schema(),
                ),
            ],
            allow_text_output=True,
        ),
    )

    for part in response.parts:
        if isinstance(part, TextPart):
            print('text:', part.content)
        else:
            print('tool call:', part.tool_name, part.args)
```

工具调用循环要你自己写：

1. 取出 `ToolCallPart`
2. 执行真实函数
3. 把结果包成 `ToolReturnPart` 塞进新一轮 `ModelRequest`
4. 再调一次 `model_request`

这正是 Agent 替你做的事，所以**有工具就用 Agent**，没工具的纯推理才用 direct。

---

## 7. 直接调模型 vs Agent

一张对照表帮你做决策：

| 维度 | `Agent` | `direct.model_request*` |
|------|---------|--------------------------|
| 抽象层级 | 高 | 低 |
| 是否跑工具循环 | ✅ 自动 | ❌ 自己写 |
| 是否做输出校验 | ✅ Pydantic 校验 + ModelRetry | ❌ 不校验 |
| 是否管理 message history | ✅ `message_history` 参数 | ❌ 全部手动构造 |
| 是否触发 `output_validator` | ✅ | ❌ |
| 是否走 `system_prompt` 注入 | ✅ 自动拼 | ❌ 自己塞 `SystemPromptPart` |
| 是否触发 hooks / events | ✅ | ❌ |
| 是否做模型重试 | ✅ `retries=` | ❌ 自己写 try/except |
| 适合场景 | 业务 Agent / RAG / 多轮对话 | prompt 实验 / 评测 / 轻量 SDK |
| 代码量 | 多 | 极少 |

**经验法则**：

- ✅ 你想要"一个能调工具、能结构化输出、能多轮对话的助手" → Agent
- ✅ 你想要"用统一接口调任何 LLM，拿到 ModelResponse 就完事" → direct
- ❌ 用 direct 模拟整个 Agent → 你最后会重写一个 Agent

---

## 8. 实战：轻量级翻译 SDK

把"翻译 + 自动检测语种 + 多 provider 切换"封装成一个不到 30 行的 SDK：

```python
from typing import Literal
from pydantic_ai import ModelRequest
from pydantic_ai.messages import SystemPromptPart, UserPromptPart
from pydantic_ai.direct import model_request_sync
from pydantic_ai.settings import ModelSettings

Provider = Literal['openai', 'anthropic', 'google']

PROVIDER_MODELS: dict[Provider, str] = {
    'openai': 'openai:gpt-4o-mini',
    'anthropic': 'anthropic:claude-haiku-4-5',
    'google': 'google-gla:gemini-2.0-flash',
}


def translate(
    text: str,
    target_lang: str = 'en',
    *,
    provider: Provider = 'openai',
    temperature: float = 0.2,
) -> str:
    """轻量翻译：自动检测源语种，输出 target_lang。"""
    messages = [
        ModelRequest(parts=[
            SystemPromptPart(content=(
                f'You are a professional translator. '
                f'Translate the user input into {target_lang}. '
                f'Output the translation only, no extra text.'
            )),
            UserPromptPart(content=text),
        ]),
    ]
    resp = model_request_sync(
        PROVIDER_MODELS[provider],
        messages,
        model_settings=ModelSettings(temperature=temperature),
    )
    return resp.parts[0].content


print(translate('今天天气真好', 'en'))               # "The weather is great today."
print(translate('Bonjour', 'zh', provider='anthropic'))  # "你好"
```

注意几个生产细节：

1. **provider 抽象**：用户不需要知道具体 model 名
2. **不需要 Agent**：这是"无状态单次推理"，Agent 反而更重
3. **只暴露 str 给调用方**：把 `ModelResponse` 这种内部类型挡在 SDK 边界外

---

## 9. 流式直接调用

`model_request_stream` 返回的是 async context manager，里面 yield `StreamedResponse`：

```python
import asyncio
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_stream

async def main():
    async with model_request_stream(
        'openai:gpt-4o-mini',
        [ModelRequest.user_text_prompt('用三句话讲清楚什么是 GIL')],
    ) as stream:
        async for chunk in stream.stream_text():
            print(chunk, end='', flush=True)
        print()
        # 流式结束后还能拿完整 response
        full = stream.get()
        print('\nusage:', full.usage)

asyncio.run(main())
```

流式相关 API（`stream_text`、`stream_output`、`get`）和 `agent.iter()` 里的 `StreamedRunResult` 完全一致。

---

## 10. 加 Instrumentation

`direct` 一样支持 logfire 观测：

```python
import logfire
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_sync

logfire.configure()
logfire.instrument_pydantic_ai()  # 全局开

response = model_request_sync(
    'anthropic:claude-haiku-4-5',
    [ModelRequest.user_text_prompt('What is the capital of France?')],
)
```

或者只对单次开：

```python
response = model_request_sync(
    'openai:gpt-4o-mini',
    [...],
    instrument=True,
)
```

---

## 11. 选型决策树

```
你的需求：
├─ 要工具循环 / 多轮对话 / 输出校验？
│  └─ ✅ 用 Agent，不要折腾 direct
├─ 写评测脚本 / prompt 试验 / 一次性推理？
│  └─ ✅ direct.model_request_sync 最香
├─ 集成到自家框架，需要完全掌控 messages？
│  └─ ✅ direct.model_request（异步版）
└─ 想流式但只是单次问答？
   └─ ✅ direct.model_request_stream
```

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 模型行为不对，好像没读 system prompt | `user_text_prompt()` 工厂不带 system，你以为带了 | 手动构造 `ModelRequest(parts=[SystemPromptPart(...), UserPromptPart(...)])` |
| `response.parts[0]` 拿到的是 `ToolCallPart` 而不是文本 | 模型决定调工具，但 direct 不会自动跑 | 要工具循环就用 Agent；或自己处理 ToolCallPart |
| 多轮对话越聊越乱 | 忘记按 `ModelRequest ↔ ModelResponse` 严格交替 | 每轮必须 append 上一轮 response，再 append 新 request |
| `model_settings` 不生效 | 用了 Agent 的 settings 概念 | direct 没读 Agent 配置，要显式传 `model_settings=` |
| 结构化输出拿不到 Pydantic 对象 | direct 不跑输出校验 | 要结构化 → 用 Agent 的 `output_type`，或自己 `model_validate_json` |
| `output_validator` 没被触发 | direct 不读 Agent 装饰器 | 同上 |
| Logfire 看不到 trace | 没调 `instrument_pydantic_ai()` 或没传 `instrument=True` | 二选一开启 |
| 工具调用拿到的 args 是字符串 | OpenAI 返回的 `arguments` 是 JSON 字符串 | `json.loads(part.args)` 或用 `part.args_as_dict()` |

---

## 13. 与 LangChain 对比

LangChain 里类似的位置是 `BaseChatModel.invoke()`：

```python
# LangChain
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model='gpt-4o-mini')
ai_msg = model.invoke([HumanMessage('你好')])

# Pydantic AI direct
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_sync
resp = model_request_sync('openai:gpt-4o-mini', [ModelRequest.user_text_prompt('你好')])
```

两边都是"绕开高级抽象直接调模型"，区别在：

- LangChain 的 `BaseChatModel` 已经是它统一的最小抽象
- Pydantic AI 把"统一最小抽象"放在 `direct` 模块里，Agent 是建在它之上的更高层

如果你做"模型评测平台"，Pydantic AI 的 direct 比 LangChain 更舒服，因为它的 `ModelResponse` 是强类型 Pydantic 对象。

---

## 14. 本章 demo

完整可运行代码：[`demos/advanced/05_direct_requests.py`](../../demos/advanced/05_direct_requests.py)

demo 涵盖：
- 同步 / 异步 / 流式三种调用
- 多轮对话手撸 messages
- 工具调用 + 自己跑循环
- 无 API key 时用 `TestModel` 演示

下一篇：[`06-capabilities.md`](06-capabilities.md) —— 搞懂模型的 Capabilities 与 ModelProfile。
