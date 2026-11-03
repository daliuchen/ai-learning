# Pydantic AI 进阶 05：Direct Model Requests 直接调模型

> 不需要工具循环、输出校验、历史拼装和重试的时候，可以绕开 Agent，直接用 `pydantic_ai.direct.model_request_sync()` 调一次模型。这时 Pydantic AI 就是一个与具体模型无关的 SDK。这篇讲 `direct` 模块的几个函数、消息结构，以及什么时候该用它、什么时候还是该回到 Agent。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. 为什么需要直接调模型

`Agent` 是 Pydantic AI 的主入口，它替你做了不少事：

- 拼 system prompt、历史消息和当前的 user prompt
- 注册工具、生成 JSON Schema、跑工具循环
- 校验结构化输出，失败时自动 `ModelRetry`
- 管理 `usage_limits`、`hooks`、`instrumentation`

写 Agent 应用时这些都很有用，但在下面几种场景里反而是负担：

1. 只想跑一次推理，比如做 prompt 实验、写评测脚本
2. 把 Pydantic AI 当成统一的模型 SDK，屏蔽 OpenAI、Anthropic、Google 各家的差异
3. 接入现有系统时，消息列表已经在别处构造好了，不希望 Agent 再改写
4. 自己写更上层的抽象（比如自家的 Agent 框架），只想借用 Pydantic AI 的 Model 实现

`pydantic_ai.direct` 就是为这些场景准备的。官方的说法是：

> These methods are thin wrappers around the Model implementations, offering a simpler interface when you don't need the full functionality of an Agent.

也就是在 Model 实现外面套了一层很薄的壳，只做一次纯推理。

---

## 2. 核心函数

`pydantic_ai.direct` 有四个基本函数，覆盖同步 / 异步和非流式 / 流式的四种组合。另外还有一个 `model_request_batch`，用 provider 的批处理 API 一次提交一批请求，价格约为一半，适合不赶时间的离线任务：

| 函数 | 同步 / 异步 | 流式 | 用途 |
|------|-------------|------|------|
| `model_request` | async | 否 | 异步一次性返回 |
| `model_request_sync` | sync | 否 | 同步一次性返回（脚本最常用） |
| `model_request_stream` | async | 是 | 异步流式，返回 `StreamedResponse` |
| `model_request_stream_sync` | sync | 是 | 同步流式，少用 |
| `model_request_batch` | async | — | 批处理 API，便宜、有延迟 |

它们的签名基本一样：

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

返回 `ModelResponse`，结构后面会讲。

---

## 3. 最小例子

```python
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_sync

response = model_request_sync(
    'anthropic:claude-haiku-5-5',
    [ModelRequest.user_text_prompt('What is the capital of France?')],
)

print(response.text)               # "The capital of France is Paris."（等价于拼起所有 TextPart）
print(response.usage)              # RequestUsage(input_tokens=15, output_tokens=8)
print(response.model_name)         # 'claude-haiku-5-5'
print(response.timestamp)
```

几点说明：

1. model 直接写字符串就行，和 Agent 一样支持 `'provider:model'` 简写
2. messages 是 ModelMessage 列表，最常用的工厂方法是 `ModelRequest.user_text_prompt()`
3. `response.parts` 是一个列表，因为模型可能返回多段文本，也可能夹着工具调用

异步版本只是多了 `async/await`：

```python
import asyncio
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request

async def main():
    response = await model_request(
        'openai-chat:qwen-flash',
        [ModelRequest.user_text_prompt('What is 123 / 456?')],
    )
    print(response.parts[0].content)

asyncio.run(main())
```

---

## 4. ModelMessage 结构

用 `direct` 之前得先弄清楚 Pydantic AI 的消息结构。消息只有两类对象：

```
ModelMessage = ModelRequest | ModelResponse

ModelRequest（你发给模型的）
   └── parts: list[
         SystemPromptPart    # 系统提示
         UserPromptPart      # 用户输入（可含文本 / 图片 / 音频）
         ToolReturnPart      # 工具返回
         RetryFeedbackPart   # 重试反馈（V2；旧的 RetryPromptPart 已废弃）
       ]

ModelResponse（模型回给你的）
   └── parts: list[
         TextPart            # 文本
         ToolCallPart        # 工具调用请求
         ThinkingPart        # 思维链
       ]
```

最常见的两种构造方式：

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

工厂方法 `user_text_prompt` 只能带一段可选的 `instructions=`（`ModelRequest.user_text_prompt('...', instructions='回答控制在 30 字以内')`）；要放 `SystemPromptPart`、拼多轮历史，还是用方式 A。

多轮对话就是 `ModelRequest` 和 `ModelResponse` 交替排列：

```python
messages = [
    ModelRequest(parts=[
        SystemPromptPart(content='你是翻译助手'),
        UserPromptPart(content='hello'),
    ]),
    ModelResponse(parts=[TextPart(content='你好')]),
    ModelRequest(parts=[UserPromptPart(content='good morning')]),
]
response = model_request_sync('openai-chat:qwen-flash', messages)
```

---

## 5. 传 ModelSettings

`direct` 不读 `Agent` 的配置，所有参数都通过 `model_settings` 传：

```python
from pydantic_ai import ModelRequest
from pydantic_ai.settings import ModelSettings
from pydantic_ai.direct import model_request_sync

response = model_request_sync(
    'openai-chat:qwen-flash',
    [ModelRequest.user_text_prompt('写一首五言绝句')],
    model_settings=ModelSettings(
        temperature=0.9,
        max_tokens=200,
        timeout=30.0,
    ),
)
```

要用 provider 专有的选项（比如 OpenAI 的 `openai_reasoning_effort`），就换成 `OpenAIChatModelSettings` 这类子类。结构化输出不走 settings，而是通过 `model_request_parameters` 的输出模式控制。

---

## 6. 直接调模型也能用工具

`direct` 不会像 Agent 那样自动跑工具循环，但可以让模型决定调哪个工具，再由你来执行：

```python
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import ModelRequest, ToolDefinition
from pydantic_ai.direct import model_request
from pydantic_ai.messages import TextPart
from pydantic_ai.models import ModelRequestParameters


class Divide(BaseModel):
    """Divide two numbers."""
    numerator: float
    denominator: float
    on_inf: Literal['error', 'infinity'] = 'infinity'


async def main():
    response = await model_request(
        'openai-chat:qwen-flash',
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

工具调用循环得自己写：

1. 取出 `ToolCallPart`
2. 执行对应的函数
3. 把结果包成 `ToolReturnPart`，放进新一轮的 `ModelRequest`
4. 再调一次 `model_request`

这些正是 Agent 替你做的事。所以我的建议是：有工具就用 Agent，没工具的纯推理才用 direct。

---

## 7. 直接调模型 vs Agent

| 维度 | `Agent` | `direct.model_request*` |
|------|---------|--------------------------|
| 抽象层级 | 高 | 低 |
| 是否跑工具循环 | 自动 | 自己写 |
| 是否做输出校验 | Pydantic 校验 + ModelRetry | 不校验 |
| 是否管理 message history | `message_history` 参数 | 全部手动构造 |
| 是否触发 `output_validator` | 是 | 否 |
| 是否注入 `system_prompt` | 自动拼 | 自己放 `SystemPromptPart` |
| 是否触发 hooks / events | 是 | 否 |
| 是否做模型重试 | `retries=` | 自己写 try/except |
| 适合场景 | 业务 Agent / RAG / 多轮对话 | prompt 实验 / 评测 / 轻量 SDK |
| 代码量 | 多 | 很少 |

怎么选：要一个能调工具、能结构化输出、能多轮对话的助手，用 Agent；只想用统一接口调各家模型、拿到 ModelResponse 就结束，用 direct。如果你发现自己在用 direct 一点点拼出工具循环和重试，那最后多半是重写了一个 Agent。

---

## 8. 实战：轻量级翻译 SDK

把翻译、自动识别语种、切换 provider 封装成一个不到 30 行的小 SDK：

```python
from typing import Literal
from pydantic_ai import ModelRequest
from pydantic_ai.messages import SystemPromptPart, UserPromptPart
from pydantic_ai.direct import model_request_sync
from pydantic_ai.settings import ModelSettings

Provider = Literal['openai', 'anthropic', 'google']

PROVIDER_MODELS: dict[Provider, str] = {
    'openai': 'openai-chat:qwen-flash',
    'anthropic': 'anthropic:claude-haiku-5-5',
    'google': 'google:gemini-3.8-flash',
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
    # Claude 5.x 收到 temperature 会返回 400，只给其他家设
    settings = ModelSettings() if provider == 'anthropic' else ModelSettings(temperature=temperature)
    resp = model_request_sync(
        PROVIDER_MODELS[provider],
        messages,
        model_settings=settings,
    )
    return resp.parts[0].content


print(translate('今天天气真好', 'en'))               # "The weather is great today."
print(translate('Bonjour', 'zh', provider='anthropic'))  # "你好"
```

几个细节：

1. 调用方只选 provider，不用关心具体的模型名；各家能接受的参数不同（比如 Claude 5.x 不接受 `temperature`），差异也收在函数里面
2. 这是无状态的单次推理，用 Agent 反而更重
3. 对外只暴露 str，`ModelResponse` 这类内部类型留在 SDK 里面

---

## 9. 流式直接调用

`model_request_stream` 返回一个 async context manager，里面是 `StreamedResponse`。它本身是模型事件的异步迭代器，文本增量要自己从 `PartStartEvent` / `PartDeltaEvent` 里取：

```python
import asyncio
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_stream
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

async def main():
    async with model_request_stream(
        'openai-chat:qwen-flash',
        [ModelRequest.user_text_prompt('用三句话讲清楚什么是 GIL')],
    ) as stream:
        async for event in stream:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                print(event.part.content, end='', flush=True)
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                print(event.delta.content_delta, end='', flush=True)
        print()
        # 流式结束后还能拿完整 response
        full = stream.get()
        print('\nusage:', full.usage)

asyncio.run(main())
```

> `stream.stream_text()` 在 `direct` 的流上并不存在，会报 `AttributeError: 'OpenAIStreamedResponse' object has no attribute 'stream_text'`。`stream_text` / `stream_output` 是 `agent.run_stream()` 返回的 `StreamedRunResult` 才有的方法。

---

## 10. 加 Instrumentation

`direct` 同样支持 logfire 观测：

```python
import logfire
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_sync

logfire.configure()
logfire.instrument_pydantic_ai()  # 全局开

response = model_request_sync(
    'anthropic:claude-haiku-5-5',
    [ModelRequest.user_text_prompt('What is the capital of France?')],
)
```

也可以只对某一次调用开：

```python
response = model_request_sync(
    'openai-chat:qwen-flash',
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
│  └─ ✅ direct.model_request_sync 最合适
├─ 集成到自家框架，需要完全掌控 messages？
│  └─ ✅ direct.model_request（异步版）
└─ 想流式但只是单次问答？
   └─ ✅ direct.model_request_stream
```

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 模型好像没读 system prompt | `user_text_prompt()` 只生成用户消息 | 传 `instructions=`，或手动构造 `ModelRequest(parts=[SystemPromptPart(...), UserPromptPart(...)])` |
| `response.parts[0]` 是 `ToolCallPart` 而不是文本 | 模型决定调工具，但 direct 不会自动执行 | 要工具循环就用 Agent，或者自己处理 ToolCallPart |
| 多轮对话越聊越乱 | 没有严格按 `ModelRequest ↔ ModelResponse` 交替 | 每轮先 append 上一轮的 response，再 append 新的 request |
| `model_settings` 不生效 | 以为 direct 会用 Agent 的配置 | direct 不读 Agent 配置，要显式传 `model_settings=` |
| 拿不到 Pydantic 对象形式的结构化输出 | direct 不做输出校验 | 用 Agent 的 `output_type`，或自己 `model_validate_json` |
| `output_validator` 没被触发 | direct 不读 Agent 的装饰器 | 同上 |
| Logfire 看不到 trace | 没调 `instrument_pydantic_ai()`，也没传 `instrument=True` | 二选一开启 |
| 工具调用拿到的 args 是字符串 | OpenAI 返回的 `arguments` 是 JSON 字符串 | 用 `part.args_as_dict()` |
| 流式时 `stream.stream_text()` 报 AttributeError | direct 的流是事件迭代器 | `async for event in stream`，再判断 `PartDeltaEvent` |

---

## 13. 与 LangChain 对比

LangChain 里对应的位置是 `BaseChatModel.invoke()`：

```python
# LangChain
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model='gpt-6-luna')
ai_msg = model.invoke([HumanMessage('你好')])

# Pydantic AI direct
from pydantic_ai import ModelRequest
from pydantic_ai.direct import model_request_sync
resp = model_request_sync('openai-chat:qwen-flash', [ModelRequest.user_text_prompt('你好')])
```

两者都是绕开高层抽象直接调模型。区别在于：LangChain 的 `BaseChatModel` 本身就是它统一的最小抽象；Pydantic AI 把这层最小抽象放在 `direct` 模块里，Agent 建在它上面。

如果要做模型评测平台，我更愿意用 Pydantic AI 的 direct，因为它返回的 `ModelResponse` 是强类型的 Pydantic 对象。

---

## 14. 本章 demo

完整可运行代码：[`demos/advanced/05_direct_requests.py`](../../demos/advanced/05_direct_requests.py)

demo 包括：
- 同步、异步、流式三种调用
- 手动拼多轮对话的 messages
- 工具调用，自己跑循环
- 没有 API key 时用 `TestModel` 演示

下一篇：[Pydantic AI 进阶 06：Capabilities（能力）与 ModelProfile](06-capabilities.md)，讲 V2 的核心原语 capability 和 ModelProfile。
