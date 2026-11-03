# Pydantic AI 进阶 07：HTTP 重试与 ModelRetry

> Pydantic AI 的重试分布在七个层面，各层预算互不共享。传输层（`AsyncHTTPX2TenacityTransport`，处理 429/5xx）、provider SDK、持久执行引擎、模型回退（`ModelFallback`）这四层，模型是看不到的；工具、输出、模型请求 hook 这三层通过 `ModelRetry` 把错误交给模型。每一层单独看都有用，叠在一起次数会相乘，所以得想清楚由哪一层负责重试。

以 `pydantic-ai 2.64.0` 为准。基于 httpx 的 `AsyncTenacityTransport` / `TenacityTransport` 已弃用，改用基于 `httpx2` 的 `AsyncHTTPX2TenacityTransport` + `create_async_httpx2_client()`。

---

## 1. 为什么 LLM 应用必须有重试

直接调 LLM，生产环境里大多会碰到这五种错误：

| 错误 | 层 | 是否可重试 |
|------|----|-----------|
| 网络抖动、`ConnectionError` | HTTP | 可以，立即重试 |
| 429 Rate Limit | HTTP | 可以，按 `Retry-After` header 等待后重试 |
| 5xx 服务端错误 | HTTP | 可以，指数退避重试 |
| 模型返回不符合 schema | 模型 | 可以，用 `ModelRetry` 让它再试一次 |
| 模型返回的字段值业务上不合法（如不存在的用户 ID） | 业务 | 可以，在工具里 raise `ModelRetry` |

前三类是传输问题，对 LLM 来说只是 SDK 报了个错；后两类是语义问题，需要把错误信息交回模型，让它重新生成。

Pydantic AI 把这两层完全分开：HTTP 层用 tenacity 重试，模型层用 `ModelRetry` 异常重试。

---

## 2. 整体架构：七个层面

| 层面 | 重试的是什么 | 配置 | 模型看得到吗 |
|------|-------------|------|-------------|
| 传输层 | 同一个 HTTP 请求 | `AsyncHTTPX2TenacityTransport` | 看不到 |
| provider SDK | 同一个 HTTP 请求，由 SDK 重发 | SDK 自带（OpenAI / Anthropic 默认 3 次尝试；Google / Mistral / HF 默认 1 次；Bedrock 5 次）；`model_settings={'max_retries': N}` | 看不到 |
| 持久执行引擎 | 整个模型请求（Temporal 默认无上限重试） | 引擎自己的 `retry_policy` | 看不到 |
| 模型回退 | 同一个请求换一个模型 | `ModelFallback` / `FallbackModel` | 看不到 |
| 模型请求尝试 | 同一步骤再试一次（hook 里抛 `RetryModelRequest`，上限 100 次，不耗预算） | hook | 看不到 |
| 工具 | 一次工具调用：告诉模型让它改 | `retries={'tools': N}` / 单工具 | 看得到（`ToolReturnPart(outcome='retried')`） |
| 输出 | 最终答案：告诉模型让它改 | `retries={'output': N}` / `ToolOutput(max_retries=N)` | 看得到（`RetryFeedbackPart`） |
| 模型请求 hook | hook 里抛 `ModelRetry` | 耗输出预算 | 看得到 |

表里「模型请求尝试」和「模型请求 hook」是同一层的两种写法（一个不耗预算、对模型不可见，一个耗输出预算、模型看得到），所以官方说的是七个层面，表里有八行。

只有最后三类属于 agent 层面的重试，每次都要多付一轮模型调用；其余各层对模型都不可见。

### 重试相乘

各层不共享预算，但次数会叠加。举个例子：输出重试 2 次（最终答案最多 3 次请求），乘以 OpenAI SDK 默认的 2 次重试（每个请求 3 次尝试），再乘以传输层 2 次重试（又是 3 次尝试），最坏情况下，光为了一个最终答案就可能在网络上发出 3 × 3 × 3 = 27 个请求。所以：

- **只让一层负责重试**：自己装了传输层重试，就用 `model_settings={'max_retries': 0}` 关掉 SDK 层；
- `UsageLimits` 和各层的超时要按最坏情况来定，而不是按正常情况；
- 模型回退（`ModelFallback`）只处理「这个模型这会儿用不了」，瞬时故障留给传输层或 SDK 层。

---

## 3. HTTP 层重试

### 3.1 装依赖

HTTP 重试用的 tenacity 是可选依赖：

```bash
pip install 'pydantic-ai-slim[retries]'
```

会把 `tenacity` 和 Pydantic AI 的 `retries` 子模块一起装上。

### 3.2 最小配置

```python
import httpx2
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from pydantic_ai.models import create_async_httpx2_client
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig, wait_retry_after


def raise_for_retryable(response):
    # 只有 429 / 5xx 才触发重试，其它状态码原样返回
    if response.status_code in (429, 502, 503, 504):
        response.raise_for_status()      # 抛 httpx2.HTTPStatusError，让 tenacity 接手


transport = AsyncHTTPX2TenacityTransport(
    config=RetryConfig(
        retry=retry_if_exception_type((httpx2.HTTPStatusError, httpx2.ConnectError)),
        wait=wait_retry_after(
            fallback_strategy=wait_exponential(multiplier=1, max=60),
            max_wait=300,
        ),
        stop=stop_after_attempt(5),
        reraise=True,
    ),
    validate_response=raise_for_retryable,
)

client = create_async_httpx2_client(transport=transport)   # 保留 Pydantic AI 默认的超时 / User-Agent
```

参数说明：

- `retry=`：哪些异常要重试。`HTTPStatusError` 要配合 `validate_response` 才会产生；
- `wait=wait_retry_after(...)`：先看响应里的 `Retry-After` header，没有的话走 fallback（指数退避）；
- `stop=stop_after_attempt(5)`：最多 5 次。`reraise=True` 表示重试完还失败时原样抛出原异常（默认会包成 `RetryError`）；
- 传给 `create_async_httpx2_client(transport=...)` 的 transport 会替换客户端自己创建的那个，所以连接池也由它负责（默认 1000 连接 / 100 保活）。要走代理，就给重试 transport 传一个自己创建的 `wrapped=httpx2.AsyncHTTPTransport(proxy=...)`。

> 旧写法 `from httpx import AsyncClient` + `AsyncTenacityTransport` 在 V2 里会触发弃用警告（`will be removed in v3; use AsyncHTTPX2TenacityTransport with httpx2.AsyncClient instead`）。

### 3.3 接到 Pydantic AI

```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel(
    'qwen-flash',
    provider=OpenAIProvider(http_client=client),   # ← 把带重试的 client 喂进去
)
agent = Agent(model, model_settings={'max_retries': 0})   # ← 让传输层成为唯一的重试方
```

千问等 OpenAI 兼容端点、Anthropic、Google 都是同样的接法，前提是它的 SDK 接受 `httpx2` 客户端；Bedrock 走 boto3，是个例外。

### 3.4 同步版本

同步场景用 `HTTPX2TenacityTransport`，配 `httpx2.Client`。Agent 一般都是异步的，用 async 版本就行，不要混用。

---

## 4. 遵守 Retry-After

OpenAI / Anthropic 限流返回 429 时会带上：

```
HTTP/1.1 429 Too Many Requests
Retry-After: 23
```

或者 HTTP 日期格式：

```
Retry-After: Wed, 21 Oct 2026 07:28:00 GMT
```

`wait_retry_after()` 能自动解析这两种格式，避免盲目重试反而被封。服务端没给这个 header 时，走 `fallback_strategy=wait_exponential(...)`。

这比自己手写重试强很多。像 `time.sleep(2)` 这种固定间隔的退避，在限流场景下只会一次次再撞上限流。

---

## 5. 模型层重试：`ModelRetry`

### 5.1 Agent 的 `retries` 参数

```python
from pydantic_ai import Agent

agent = Agent(
    'openai-chat:qwen-flash',
    retries=3,  # 模型输出不合规时，最多重试 3 次
)
```

默认值是 1。`retries` 控制的是结构化输出校验失败、工具调用失败时，让模型重新生成的次数。它不重试 HTTP 错误，那是 transport 层的事。

更细的写法：

```python
agent = Agent(
    'openai-chat:qwen-flash',
    retries={'tools': 3, 'output': 2},
)
```

分别控制工具失败和输出失败的重试次数。

### 5.2 在工具里主动 raise ModelRetry

模型经常给出看起来合法、但业务上没用的参数，比如查一个不存在的用户名：

```python
from pydantic_ai import Agent, ModelRetry, RunContext


class DatabaseConn:
    users = {'Alice': 1, 'Bob': 2}


agent = Agent('openai-chat:qwen-flash', deps_type=DatabaseConn)


@agent.tool(retries=2)
def get_user_by_name(ctx: RunContext[DatabaseConn], name: str) -> int:
    """Get a user's ID from their full name."""
    user_id = ctx.deps.users.get(name)
    if user_id is None:
        raise ModelRetry(
            f'No user found with name {name!r}, '
            f'please use the full name like "Alice" or "Bob".'
        )
    return user_id
```

过程是这样的：

1. 模型调用 `get_user_by_name(name="alice")`（小写）
2. 工具 raise `ModelRetry('No user found with name "alice", ...')`
3. Pydantic AI 把这条消息放进 `ToolReturnPart`，再次调用模型
4. 模型读到错误信息，改成 `get_user_by_name(name="Alice")` 重试

把业务校验的结果反馈给模型，让它自己修正，这是 LLM 应用里非常实用的一种自我纠错方式。

### 5.3 output_validator 触发的重试

```python
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic import BaseModel


class Issue(BaseModel):
    title: str
    severity: str


agent = Agent('openai-chat:qwen-flash', output_type=Issue, retries=3)


@agent.output_validator
def validate_issue(ctx: RunContext, issue: Issue) -> Issue:
    if issue.severity not in {'low', 'medium', 'high'}:
        raise ModelRetry(
            f'severity must be one of low/medium/high, got {issue.severity!r}'
        )
    return issue
```

输出通不过校验时，错误信息会交回模型让它改，最多重试 `retries` 次（这里是 3 次），用完还不过就抛 `UnexpectedModelBehavior`。

### 5.4 拿到当前重试次数

```python
@agent.tool(retries=3)
def search(ctx: RunContext, query: str) -> str:
    print(f'第 {ctx.retry} 次重试')
    ...
```

可以在最后一次时降级，比如换一个更宽松的查询，或者直接返回降级结果。

---

## 6. 退避策略

`tenacity` 提供了好几种 wait 策略，可以组合使用：

```python
from tenacity import (
    wait_exponential,
    wait_random,
    wait_fixed,
    wait_combine,
)

# 指数退避 1, 2, 4, 8, ... 上限 60s
wait_exponential(multiplier=1, max=60)

# 固定 5s
wait_fixed(5)

# 指数 + 随机抖动（防雪崩）
wait_combine(wait_exponential(max=60), wait_random(0, 1))
```

我在生产里常用 `wait_retry_after(fallback_strategy=wait_combine(wait_exponential(), wait_random(0, 2)))`：既遵守服务端的要求，又加了随机抖动，避免大量客户端同时重试。

---

## 7. 模型回退：`ModelFallback` / `FallbackModel`

OpenAI 整体宕机时，单纯重试没有意义，应该直接切到另一家。V2 推荐用 `ModelFallback` capability，`FallbackModel` 也还能用：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.models.fallback import FallbackModel

agent = Agent('openai:gpt-6.1-sol', capabilities=[ModelFallback('anthropic:claude-sonnet-5-5', 'google:gemini-3.8-flash')])
# 或
agent = Agent(FallbackModel('openai:gpt-6.1-sol', 'anthropic:claude-sonnet-5-5', 'google:gemini-3.8-flash'))
```

触发条件：

- 默认：请求抛出 `ModelAPIError`（4xx/5xx）时切到下一个；
- 自定义：`fallback_on=` 可以是异常类型元组、异常处理函数、响应处理函数（拒绝一个成功返回但内容不合格的响应），或者它们组成的列表。

```python
from pydantic_ai import ModelAPIError
from pydantic_ai.messages import ModelResponse

def truncated(response: ModelResponse) -> bool:      # 首参标注为 ModelResponse → 当作响应处理函数
    return response.finish_reason in ('length', 'content_filter')

agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[ModelFallback('anthropic:claude-sonnet-5-5', fallback_on=[ModelAPIError, truncated])],
)
```

只传响应处理函数会替换默认的 `(ModelAPIError,)`，这时 API 错误会直接抛出，不再回退。两者都要的话，像上面这样放进同一个列表。所有模型都失败时抛 `FallbackExceptionGroup`，里面是每个模型各自的异常。还要注意，回退和重试不一样：下一个模型一般用不上前一个已经建好的提示词缓存，答案和价格也可能不同。demo 07 里用 `BoomModel` 抛 `ModelHTTPError(503)` 演示了回退，由备用的 `TestModel` 接手。

---

## 8. SDK 自带重试 vs 传输层重试

OpenAI 和 Anthropic 的 SDK 内部都有 `max_retries`（默认 2，即每个请求最多尝试 3 次）。如果你又装了传输层重试，就是两层叠在一起，带来两个问题：

1. 响应变慢：SDK 等几秒，传输层又等几秒；
2. 模型回退变慢：SDK 重试完才把异常往外抛，回退这时才生效。

V2 里关掉 SDK 重试要通过模型设置，而不是 provider 的构造参数：

```python
agent = Agent(model, model_settings={'max_retries': 0})     # 运行时也可以单次覆盖
```

各 provider 的 SDK 默认重试次数、能否降到 1 次都不一样：大多数是 3 次；Google / Mistral / Hugging Face 默认 1 次；Bedrock 是 5 次，走 boto3，不经过传输层。详见官方 *Retries* 页。

---

## 9. 重试与流式

重试和流式基本上合不来：

- HTTP 层的 transport 重试：流式响应一旦开始读，数据已经吐出一半，再重试就没意义了
- 模型层的 `ModelRetry`：要等输出完整收到才能校验，这和边收边返回给用户是矛盾的

所以，要重试、要可靠，就用非流式的 `agent.run()`；要流式、能接受偶尔失败，就用 `agent.run_stream()`，重试只能在外层做成整体失败后重新开一次流。

---

## 10. 重试带来的 token 成本

每次重试都是一次完整的 LLM 调用，token 照样计费。如果 schema 复杂、模型经常重试 3 次，实际花费可能是预期的 3-4 倍。

应对办法：

1. 给 schema 写清楚的 `description`，降低第一次就失败的概率
2. 给字段加 `examples`
3. 用 `NativeOutput`（OpenAI 严格模式），第一次的命中率高得多
4. 用 `usage_limits` 设上限：

```python
from pydantic_ai.usage import UsageLimits

agent.run_sync(
    'xxx',
    usage_limits=UsageLimits(request_limit=5, total_tokens_limit=10000),
)
```

超过限制就直接抛错，不会失控。

---

## 11. 实战：一个生产可用的完整配置

```python
import httpx2
import logfire
from tenacity import (
    retry_if_exception_type, stop_after_attempt,
    wait_exponential, wait_combine, wait_random,
)

from pydantic_ai import Agent
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.models import create_async_httpx2_client
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig, wait_retry_after
from pydantic_ai.usage import UsageLimits


def raise_for_retryable(response):
    if response.status_code in (429, 502, 503, 504):
        response.raise_for_status()


def build_retry_client():
    transport = AsyncHTTPX2TenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((httpx2.HTTPStatusError, httpx2.ConnectError)),
            wait=wait_retry_after(
                fallback_strategy=wait_combine(wait_exponential(multiplier=1, max=60), wait_random(0, 2)),
                max_wait=120,
            ),
            stop=stop_after_attempt(4),
            reraise=True,
        ),
        validate_response=raise_for_retryable,
    )
    return create_async_httpx2_client(transport=transport)


primary = OpenAIChatModel('qwen-flash', provider=OpenAIProvider(http_client=build_retry_client()))

agent = Agent(
    primary,
    capabilities=[ModelFallback('anthropic:claude-haiku-5-5')],   # 主模型整体不可用时切备用
    retries={'tools': 2, 'output': 2},                            # agent 层重试
    model_settings={'max_retries': 0},                            # SDK 层关掉，传输层是唯一的重试方
)

logfire.configure()
logfire.instrument_pydantic_ai()

result = agent.run_sync(
    'Hello',
    usage_limits=UsageLimits(request_limit=5, total_tokens_limit=20_000),
)
```

这份配置包括：

- 传输层：遵守 Retry-After，指数退避加随机抖动，最多 4 次；
- agent 层：工具和输出失败各重试 2 次；
- 模型回退：主模型不可用时切到 Anthropic；
- 用量上限：防止重试失控；
- 可观测：Logfire 全链路 trace。

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `AsyncHTTPX2TenacityTransport` 报 ImportError | 没装 retries 依赖 | `pip install 'pydantic-ai-slim[retries]'`（带 tenacity） |
| `PydanticAIDeprecationWarning: AsyncTenacityTransport is deprecated` | 用的是旧的 httpx 传输类 | 换成 `AsyncHTTPX2TenacityTransport` + `create_async_httpx2_client` |
| 4xx 没触发重试 | 没设 `validate_response`（httpx 默认不抛异常） | 对 429/5xx 调 `response.raise_for_status()` |
| 429 一重试反而更快被封 | 用了 `wait_exponential` 而不是 `wait_retry_after` | 改成 `wait_retry_after` |
| `agent.run` 偶尔特别慢 | SDK 自带重试，传输层又重试一遍（次数相乘） | `model_settings={'max_retries': 0}` |
| 回退不触发 | 第一个模型抛的不是 `ModelAPIError`（比如自定义异常）；或者 `fallback_on` 只给了响应处理函数，替换掉了默认值 | 用 `fallback_on=[ModelAPIError, handler]` |
| `ModelRetry` 没生效 | `retries` 设成了 0 | 至少设 1；或用 `retries={'tools': N, 'output': N}` |
| 重试过程中 token 用超 | 没设 `UsageLimits` | 加 `total_tokens_limit` |
| 流式时 `ModelRetry` 不起作用 | 流式和重试基本互斥 | 流式场景别依赖 retry，在业务层包装 |
| 长 prompt 重试时超时 | tenacity 之外的 HTTP 超时太小 | `create_async_httpx2_client(timeout=..., transport=...)` 调大超时，或用 `model_settings={'timeout': ...}` |
| 所有模型都失败时异常不好处理 | 没正确解开 `FallbackExceptionGroup` | 用 `except* ModelAPIError as eg:` 解包 |

---

## 13. 与 LangChain 对比

LangChain 1.x 的重试分散在几个地方：

- 模型层：`ChatModel(max_retries=...)`，以及 `ModelRetryMiddleware` / `ModelFallbackMiddleware`；
- 工具层：`ToolRetryMiddleware`、`ToolErrorMiddleware`（工具异常默认中断运行，要反馈给模型得挂上它）；
- 结构化输出：校验错误可以回传给模型；
- HTTP 层：完全依赖 SDK 自带的 retry。

Pydantic AI 的好处是抽象统一，官方也把七个层面写在了同一页文档里：传输层在一处声明，工具和输出的重试直接和 `ModelRetry` 结合，`ModelFallback` 是一个 capability，重试预算就是构造参数 `retries`。对比一下：

```python
# LangChain
create_agent(model, tools, middleware=[ModelRetryMiddleware(max_retries=3), ModelFallbackMiddleware("anthropic:claude-sonnet-5-5")])

# Pydantic AI
Agent(model, retries={'tools': 3, 'output': 2}, capabilities=[ModelFallback('anthropic:claude-sonnet-5-5')],
      model_settings={'max_retries': 0})
```

---

## 14. 本章 demo

完整可运行代码：[`demos/advanced/07_retries_http.py`](../../demos/advanced/07_retries_http.py)

demo 包括：
- 在工具里 raise ModelRetry，让模型自己修正
- output_validator 触发的重试
- 打印重试计数
- HTTP transport 的配置（只展示构造，不真发请求）
- FallbackModel 配多个模型
- 没有 key 时用 TestModel 演示重试机制

下一篇：[Pydantic AI 进阶 08：Deferred Tools 与人审批](08-deferred-tools.md)，讲怎么让 Agent 暂停下来等人审批。
