# Pydantic AI 进阶 07：HTTP 重试与 ModelRetry

> **一句话**：Pydantic AI 把重试分成两层 —— **HTTP 层**用 `AsyncTenacityTransport` 处理 429/5xx 这种"传输错误"，**模型层**用 `ModelRetry` 处理"模型输出不合规"，加上 `FallbackModel` 兜底，构成一套完整的"抗抖动 + 抗幻觉"防护。

---

## 1. 为什么 LLM 应用必须有重试

裸调 LLM 在生产里大概率挂在这五种错误上：

| 错误 | 层 | 是否可重试 |
|------|----|-----------|
| 网络抖动、`ConnectionError` | HTTP | ✅ 立即重试 |
| 429 Rate Limit | HTTP | ✅ 看 `Retry-After` header 重试 |
| 5xx 服务端错误 | HTTP | ✅ 指数退避重试 |
| 模型返回不符 schema | 模型 | ✅ `ModelRetry` 让它再试一次 |
| 模型返回的字段值业务上不合法（如不存在的用户 ID） | 业务 | ✅ 工具内 raise `ModelRetry` |

第 1-3 类是"传输问题"，对 LLM 而言只是 SDK 报错；
第 4-5 类是"语义问题"，要把错误信息回传给模型让它重试。

Pydantic AI 把这两层完全解耦：**HTTP 层重试用 tenacity，模型层重试用 ModelRetry 异常**。

---

## 2. 整体架构

```
                ┌──────────────────────────────────────┐
                │           Agent.run                  │
                └──────────────────────────────────────┘
                              │
              ┌───────────────┴──────────────┐
              ▼                              ▼
        ┌──────────┐                  ┌──────────────┐
        │ 模型层重试 │ ← ModelRetry     │ HTTP 层重试   │
        │ Agent retries=N             │ Tenacity       │
        │ 工具/校验失败时再调一次 LLM    │ 429/5xx/网络抖动  │
        └──────────┘                  └──────────────┘
              │                              │
              └────────┬─────────────────────┘
                       ▼
              ┌──────────────────┐
              │  FallbackModel   │  ← 整个链都挂，切到备用模型
              └──────────────────┘
```

三层各管各的，组合起来才是生产可用。

---

## 3. HTTP 层重试

### 3.1 装依赖

HTTP 重试用 tenacity，是 optional dependency：

```bash
pip install 'pydantic-ai-slim[retries]'
```

它会把 `tenacity` 和 Pydantic AI 的 `retries` 子模块一起装上。

### 3.2 最小配置

```python
from httpx import AsyncClient, HTTPStatusError
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after

transport = AsyncTenacityTransport(
    config=RetryConfig(
        retry=retry_if_exception_type((HTTPStatusError, ConnectionError)),
        wait=wait_retry_after(
            fallback_strategy=wait_exponential(multiplier=1, max=60),
            max_wait=300,
        ),
        stop=stop_after_attempt(5),
        reraise=True,
    ),
    validate_response=lambda r: r.raise_for_status(),
)

client = AsyncClient(transport=transport)
```

关键点：

- **`retry=`**：什么异常要重试。`HTTPStatusError` 是 httpx 在 4xx/5xx 时抛的（需要配合 `validate_response`）
- **`wait=wait_retry_after(...)`**：先看响应里的 `Retry-After` header，没有就走 fallback（指数退避）
- **`stop=stop_after_attempt(5)`**：最多 5 次
- **`reraise=True`**：跑完还失败，原样抛出原异常（默认会包成 `RetryError`）
- **`validate_response=lambda r: r.raise_for_status()`**：让 4xx/5xx 触发异常，否则 httpx 默认不抛

### 3.3 接到 Pydantic AI

```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel(
    'gpt-4o-mini',
    provider=OpenAIProvider(http_client=client),  # ← 把带重试的 client 喂进去
)
agent = Agent(model)
```

Anthropic / Google / 任何 OpenAI-compatible provider 都是同一招：把 `http_client` 参数填上。

### 3.4 同步版本

`AsyncTenacityTransport` 用于异步 httpx；同步用 `TenacityTransport`。日常 Agent 都是异步，HTTP transport 用 async 即可，不要混用。

---

## 4. 关键：尊重 Retry-After

OpenAI / Anthropic 在 429 限流时会返回：

```
HTTP/1.1 429 Too Many Requests
Retry-After: 23
```

或 HTTP 日期格式：

```
Retry-After: Wed, 21 Oct 2026 07:28:00 GMT
```

`wait_retry_after()` 会**自动解析这两种格式**，避免你瞎重试反而被 ban。如果 server 没给 header，就走 `fallback_strategy=wait_exponential(...)`。

这一点比手撸 retry 强很多 —— 自己写 `time.sleep(2)` 这种固定退避在限流场景里会反复触发限流。

---

## 5. 模型层重试：`ModelRetry`

### 5.1 Agent 的 `retries` 参数

```python
from pydantic_ai import Agent

agent = Agent(
    'openai:gpt-4o-mini',
    retries=3,  # 模型输出不合规时，最多重试 3 次
)
```

默认值是 1。`retries` 控制的是**结构化输出校验失败、工具调用失败时**重新让模型生成的次数。注意它**不重试** HTTP 错误，那是上一层 transport 的事。

更细的写法：

```python
agent = Agent(
    'openai:gpt-4o-mini',
    retries={'tools': 3, 'output': 2},
)
```

分别控制工具失败和输出失败的次数。

### 5.2 工具里主动 raise ModelRetry

模型经常给你**"看起来合法但业务上没用"**的参数，比如查询一个不存在的用户名：

```python
from pydantic_ai import Agent, ModelRetry, RunContext


class DatabaseConn:
    users = {'Alice': 1, 'Bob': 2}


agent = Agent('openai:gpt-4o-mini', deps_type=DatabaseConn)


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

工作原理：

1. 模型调用 `get_user_by_name(name="alice")`（小写）
2. 工具 raise `ModelRetry('No user found with name "alice", ...')`
3. Pydantic AI 把 retry 消息塞进 `ToolReturnPart`，再次调模型
4. 模型读到错误信息，重新尝试 `get_user_by_name(name="Alice")`

**这是 LLM 应用最强大的自愈能力**，把"业务校验"反馈给模型让它自己改。

### 5.3 output_validator 触发的重试

```python
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic import BaseModel


class Issue(BaseModel):
    title: str
    severity: str


agent = Agent('openai:gpt-4o-mini', output_type=Issue, retries=3)


@agent.output_validator
def validate_issue(ctx: RunContext, issue: Issue) -> Issue:
    if issue.severity not in {'low', 'medium', 'high'}:
        raise ModelRetry(
            f'severity must be one of low/medium/high, got {issue.severity!r}'
        )
    return issue
```

模型输出过校验前，会反复让它修。

### 5.4 拿到当前重试次数

```python
@agent.tool(retries=3)
def search(ctx: RunContext, query: str) -> str:
    print(f'第 {ctx.retry} 次重试')
    ...
```

适合在最后一次时降级（比如改用更宽松的查询、或返回降级结果）。

---

## 6. 退避策略

`tenacity` 提供多种 wait 策略，混搭：

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

生产里常用：**`wait_retry_after(fallback_strategy=wait_combine(wait_exponential(), wait_random(0, 2)))`**，既尊重 server 又防群体雪崩。

---

## 7. FallbackModel：换条腿走路

当 OpenAI 整体宕机时，单纯重试没意义。`FallbackModel` 让你直接切到另一家：

```python
from pydantic_ai import Agent
from pydantic_ai.models.fallback import FallbackModel

fallback_model = FallbackModel(
    'openai:gpt-4o',
    'anthropic:claude-sonnet-4-5',
    'google-gla:gemini-2.0-flash',
)
agent = Agent(fallback_model)
result = agent.run_sync('什么是 GIL？')
```

触发条件：

- **默认**：第一个模型抛 `ModelAPIError`（4xx/5xx）时切下一个
- **自定义**：传 `fallback_on=callable`，按业务规则切

```python
from pydantic_ai.messages import ModelResponse


def bad_finish_reason(response: ModelResponse) -> bool:
    return response.finish_reason in ('length', 'content_filter')


fallback_model = FallbackModel(
    'openai:gpt-4o',
    'anthropic:claude-sonnet-4-5',
    fallback_on=bad_finish_reason,
)
```

如果所有模型都挂了，抛 `FallbackExceptionGroup`，里面装着每个模型的异常。

---

## 8. 注意：SDK 自带重试 vs Pydantic AI 重试

OpenAI 和 Anthropic 的 SDK 内部都有 `max_retries`（默认 2）。这会**抢在** Pydantic AI 之前重试，导致两个问题：

1. **响应慢**：SDK 等几秒、Pydantic AI 又等几秒，叠加起来很久
2. **FallbackModel 切换变慢**：SDK 重试完才把异常往外抛，Fallback 才生效

生产里强烈建议：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel(
    'gpt-4o',
    provider=OpenAIProvider(
        api_key='sk-...',
        max_retries=0,  # ← 关掉 SDK 自带重试
    ),
)
```

把重试统一交给 `AsyncTenacityTransport` 管，避免双层重试。

---

## 9. 重试与流式

**重要**：重试机制和流式 **基本不兼容**。

- HTTP 层 transport 重试：流式响应一旦开始读，重试就失去意义（数据已经吐出来一半了）
- 模型层 `ModelRetry`：要等输出完整收到才能校验，与"边收边返给用户"矛盾

实操建议：

- **要重试 + 要可靠** → 用 `agent.run()`，非流式
- **要流式 + 接受可能失败** → 用 `agent.run_stream()`，重试只能在外层包一层"整体失败再重新流"

---

## 10. 重试导致的 token 翻倍计费

每次重试都是一次完整的 LLM 调用，token 计费照算。如果你的 schema 复杂、模型经常 retry 3 次，**实际花费可能是预期的 3-4 倍**。

应对：

1. 给 schema 写清晰的 `description`，降低首发失败率
2. 给字段加 `examples`
3. 用 `NativeOutput`（OpenAI 严格模式），首发命中率高得多
4. 监控 `usage_limits`：

```python
from pydantic_ai.usage import UsageLimits

agent.run_sync(
    'xxx',
    usage_limits=UsageLimits(request_limit=5, total_tokens_limit=10000),
)
```

超过限制直接抛错，防止失控。

---

## 11. 实战配方：生产可用的"全副武装" Agent

```python
import logfire
from httpx import AsyncClient, HTTPStatusError
from tenacity import (
    retry_if_exception_type, stop_after_attempt,
    wait_exponential, wait_combine, wait_random,
)

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from pydantic_ai.usage import UsageLimits


def build_retry_client() -> AsyncClient:
    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((HTTPStatusError, ConnectionError)),
            wait=wait_retry_after(
                fallback_strategy=wait_combine(
                    wait_exponential(multiplier=1, max=60),
                    wait_random(0, 2),
                ),
                max_wait=120,
            ),
            stop=stop_after_attempt(4),
            reraise=True,
        ),
        validate_response=lambda r: r.raise_for_status(),
    )
    return AsyncClient(transport=transport, timeout=60.0)


client = build_retry_client()

primary = OpenAIChatModel(
    'gpt-4o-mini',
    provider=OpenAIProvider(http_client=client, max_retries=0),  # SDK 重试关掉
)
backup = AnthropicModel(
    'claude-haiku-4-5',
    provider=AnthropicProvider(http_client=client, max_retries=0),
)

agent = Agent(
    FallbackModel(primary, backup),
    retries=2,  # 模型层重试 2 次
    instrument=True,
)

logfire.configure()
logfire.instrument_pydantic_ai()

result = agent.run_sync(
    'Hello',
    usage_limits=UsageLimits(request_limit=5, total_tokens_limit=20_000),
)
```

这个配方覆盖：

- HTTP 层：尊重 Retry-After + 指数退避 + 抖动 + 最多 4 次
- 模型层：输出/工具失败重试 2 次
- 厂商切换：OpenAI 挂了切 Anthropic
- 用量上限：防止重试失控
- 可观测：Logfire 全链路 trace

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `AsyncTenacityTransport` 报 ImportError | 没装 retries 依赖 | `pip install 'pydantic-ai-slim[retries]'` |
| 4xx 没触发重试 | 没设 `validate_response=lambda r: r.raise_for_status()` | 加上 |
| 429 重试反而更快被 ban | 用了 `wait_exponential` 而不是 `wait_retry_after` | 改成 `wait_retry_after` |
| `agent.run` 偶尔特别慢 | SDK 自带 max_retries=2 + tenacity 又重试一遍 | provider 里设 `max_retries=0` |
| FallbackModel 不切换 | 第一个模型抛的不是 `ModelAPIError`（如自定义异常） | 用 `fallback_on=callable` 自定义条件 |
| `ModelRetry` 没生效 | Agent 的 `retries=0` 或工具的 `retries=0` | 至少给 1 |
| 重试中 token 用爆 | 没设 `UsageLimits` | 加 `total_tokens_limit` |
| 流式时 `ModelRetry` 不起作用 | 流式与重试基本互斥 | 流式场景下不要依赖 retry，业务层包装 |
| 长 Prompt 重试时 timeout | tenacity 之外的 httpx timeout 太小 | `AsyncClient(timeout=60.0)` |
| 所有模型都失败 | 用了 `FallbackExceptionGroup` 没正确解包 | `except* ModelAPIError as eg:` 解 group |

---

## 13. 与 LangChain 对比

LangChain 的重试相对碎片化：

- `Runnable.with_retry(stop_after_attempt=3)` —— 简单但不够细
- `RetryOutputParser` —— 仅用于 parser 失败
- HTTP 层完全依赖 SDK 自带 retry

Pydantic AI 的优势在于**统一抽象**：

- HTTP 层通过 `AsyncTenacityTransport` 一处声明，所有 Agent 共享
- 模型层 `ModelRetry` 直接和工具/校验一体化
- `FallbackModel` 是一等公民

对比一下：

```python
# LangChain
chain.with_retry(stop_after_attempt=3, wait_exponential_jitter=True)

# Pydantic AI
Agent(model_with_tenacity_client, retries=3)
```

LangChain 一行更短，但实际生产你会发现 Pydantic AI 的分层更清晰、可观测性更好。

---

## 14. 本章 demo

完整可运行代码：[`demos/advanced/07_retries_http.py`](../../demos/advanced/07_retries_http.py)

demo 涵盖：
- ModelRetry 在工具里 raise，让模型自愈
- output_validator 触发的重试
- 演示 retry 计数
- HTTP transport 配置（不真发请求，仅展示构造）
- FallbackModel 配多个模型
- 无 key 时用 TestModel 演示重试机制

下一篇：[`08-deferred-tools.md`](08-deferred-tools.md) —— Deferred Tools 让 Agent 暂停等"人审批"。
