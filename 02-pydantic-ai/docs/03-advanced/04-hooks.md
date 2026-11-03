# Pydantic AI 进阶 04：Hooks（生命周期钩子）

> 日志、限流、审计、重试、token 预算这些横切的需求，如果写进每个工具和每次调用里，很快就会散得到处都是。Pydantic AI 的 Hooks 让你在 run、模型请求、工具执行、输出校验等节点挂上回调，统一处理这些事，Agent 本身不用改。这篇先列清楚钩子的签名和执行顺序，再给几个常用的实战写法。

以 `pydantic-ai 2.64.0` 为准，文中钩子签名和 `AbstractCapability` 一致。

---

## 1. 为什么需要 Hooks

项目上了生产，下面这些需求迟早会出现：

1. 审计：谁在什么时候、用什么 prompt 调了哪个工具
2. 限流：单个用户每分钟最多 10 次模型调用
3. 预算：单次会话 token 不超过 50k，超了就中止
4. PII 脱敏：发给模型之前去掉手机号、身份证号
5. 重试和降级：模型返回里有敏感词时自动重试，或者换个 prompt

没有 Hooks，能用的办法都不太好：在每个工具里手写 logging，分散且容易漏；在 `agent.run()` 外面套 try/finally，拦不到工具调用；靠 Logfire 监控，只能看，插不了手。Hooks 让你既能读，也能改，还能中断。

---

## 2. Hooks vs Logfire

| 维度 | Hooks | Logfire |
|------|-------|---------|
| 定位 | 业务层切面 | 框架层观测 |
| 能力 | 读 + 改 + 拦 | 只读 |
| 注册方式 | `Agent(capabilities=[Hooks(...)])` | `import logfire; logfire.configure()` |
| 常见用途 | 限流 / 脱敏 / 审计 / 重试 | trace / metric / 异常监控 |
| 性能影响 | 很小（取决于钩子本身写得多复杂） | 很小（异步上报） |
| 互斥吗 | 不互斥，建议一起用 | 同上 |

Hooks 要写代码，Logfire 基本是打开就能用，两者关注的东西不同。

---

## 3. 第一个 Hook：最小例子

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks

hooks = Hooks()

@hooks.on.before_run
async def log_run(ctx: RunContext[None]) -> None:
    print(f"[start] {ctx.usage}")

@hooks.on.after_run
async def log_done(ctx: RunContext[None], *, result):     # after_run 带 result，并且要把它返回
    print(f"[done] tokens={ctx.usage.total_tokens}")
    return result

agent = Agent("openai-chat:qwen-flash", capabilities=[hooks])
agent.run_sync("Hello")
```

说明：

1. `Hooks()` 是一个收集器，用装饰器把回调挂上去
2. 挂好之后用 `capabilities=[hooks]` 注册到 Agent
3. 钩子的第一个参数总是 `ctx: RunContext[...]`，其余参数因钩子而异：`before_*` 一般是位置参数，`after_*` / `wrap_*` / `*_error` 是关键字参数，写错会报 `TypeError: ... got an unexpected keyword argument`

不用装饰器也行，直接把函数传给构造器：`Hooks(before_model_request=log_request)`。同步、异步函数都可以。

各钩子的签名（直接摘自 `AbstractCapability`）：

```python
before_run(ctx)                                                          -> None
after_run(ctx, *, result)                                                -> result
before_model_request(ctx, request_context)                               -> request_context
after_model_request(ctx, *, request_context, response)                   -> response
wrap_model_request(ctx, *, request_context, handler)                     -> ModelResponse     # hooks.on.model_request
on_model_request_error(ctx, *, request_context, error)                   -> ModelResponse（或重新抛出）  # hooks.on.model_request_error
before_tool_execute(ctx, *, call, tool_def, args)                        -> args
after_tool_execute(ctx, *, call, tool_def, args, result)                 -> result
on_tool_execute_error(ctx, *, call, tool_def, args, error)               -> 结果（或重新抛出）
```

---

## 4. 钩子完整清单

Pydantic AI 的生命周期切得很细，下面七组钩子覆盖了所有关键节点。

### 4.1 Run 级

| 钩子 | 时机 |
|------|------|
| `before_run` | Agent 开始执行前 |
| `after_run` | Agent 执行结束后 |
| `wrap_run` (`hooks.on.run`) | 包裹整个 run（setup/teardown 模式） |
| `run_error` | run 抛错时，可恢复 |

### 4.2 节点级（图节点，对应 `agent.iter()` 看到的节点）

| 钩子 | 时机 |
|------|------|
| `before_node_run` | 每个图节点前 |
| `after_node_run` | 每个图节点后 |
| `wrap_node_run` (`hooks.on.node_run`) | 包裹节点 |
| `node_run_error` | 节点错误 |

### 4.3 模型请求级

| 钩子 | 时机 | 能改什么 |
|------|------|----------|
| `before_model_request` | 调 LLM API 前 | 改 messages / model_settings |
| `after_model_request` | LLM API 返回后 | 改 response |
| `wrap_model_request` (`hooks.on.model_request`) | 包裹 LLM 调用 | 加重试、缓存 |
| `model_request_error` | LLM 请求错误 | 兜底、降级 |

### 4.4 工具校验级

| 钩子 | 时机 |
|------|------|
| `before_tool_validate` | JSON 解析 / 校验前 |
| `after_tool_validate` | 校验通过后 |
| `wrap_tool_validate` | 包裹校验 |
| `tool_validate_error` | 校验失败 |

### 4.5 工具执行级

| 钩子 | 时机 | 能改什么 |
|------|------|----------|
| `before_tool_execute` | 调用工具函数前 | 改 args |
| `after_tool_execute` | 工具函数返回后 | 改返回值 |
| `wrap_tool_execute` | 包裹工具 | 加 timeout / 限流 |
| `tool_execute_error` | 工具抛错 | 重试 / 兜底 |

### 4.6 输出处理级

| 钩子 | 时机 |
|------|------|
| `before_output_validate` | 结构化输出校验前 |
| `after_output_validate` | 校验通过后 |
| `wrap_output_validate` | 包裹校验 |
| `output_validate_error` | 校验失败（可触发 ModelRetry） |
| `before_output_process` / `after_output_process` / `wrap_output_process` / `output_process_error` | 输出值提取阶段 |

### 4.7 其他

| 钩子 | 时机 |
|------|------|
| `prepare_tools` | 准备发给模型的工具定义时 |
| `prepare_output_tools` | 准备输出工具时 |
| `deferred_tool_calls` | 出现延迟工具调用时 |
| `event` | 流式中每个 AgentStreamEvent |
| `run_event_stream` (`hooks.on.run_event_stream`) | 包裹整个事件流 |

---

## 5. before / after / wrap / error 四种语义

每个生命周期点都有四个钩子位：

```
   ┌────────────────────────────────────────────┐
   │  wrap_xxx ─────── 整个阶段包起来          │
   │  ┌──────────────────────────────────────┐ │
   │  │  before_xxx  ← 进入前                 │ │
   │  │      ↓                                │ │
   │  │   核心逻辑 (LLM 请求 / 工具调用 / ...)│ │
   │  │      ↓                                │ │
   │  │  after_xxx   ← 退出前                 │ │
   │  │      ↓ 抛错？                          │ │
   │  │  xxx_error   ← 兜底                    │ │
   │  └──────────────────────────────────────┘ │
   └────────────────────────────────────────────┘
```

四种写法对比：

```python
# before：观察 + 改输入
@hooks.on.before_model_request
async def log(ctx, request_context):
    print(request_context.messages[-1])
    return request_context  # 必须返回（可修改后返回）

# after：观察 + 改输出
@hooks.on.after_model_request
async def check(ctx, *, request_context, response):
    return response

# wrap：包起来加 try/finally / 缓存 / 重试
@hooks.on.model_request
async def wrap(ctx, *, request_context, handler):
    cached = cache.get(key)
    if cached:
        return cached
    response = await handler(request_context)
    cache.set(key, response)
    return response

# error：抛错时兜底
@hooks.on.model_request_error
async def recover(ctx, *, request_context, error):
    # 选项 1: 重新抛出 → 错误继续传播（默认）
    raise error
    # 选项 2: 抛不同错 → 错误转换
    # 选项 3: 返回 ModelResponse → 抑制错误
```

---

## 6. 实战 A：审计日志

把每次 LLM 请求、每次工具调用都写进数据库：

```python
import time
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks

hooks = Hooks()

@hooks.on.model_request            # wrap 钩子：开头计时、结尾记账，不用在 before / after 之间传状态
async def trace_model(ctx: RunContext[None], *, request_context, handler):
    start = time.time()
    response = await handler(request_context)
    await save_log(
        run_id=ctx.run_id,
        kind="model_request",
        elapsed=time.time() - start,
        usage=response.usage,
    )
    return response

@hooks.on.before_tool_execute
async def trace_tool(ctx, *, call, tool_def, args):
    await save_log(
        run_id=ctx.run_id,
        kind="tool_call",
        tool=call.tool_name,
        args=args,
    )
    return args  # 必须返回 args（可改写）

agent = Agent("openai-chat:qwen-flash", capabilities=[hooks])
```

---

## 7. 实战 B：Token 预算控制

单次会话 token 不超过 50k，超了直接中止：

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import UsageLimitExceeded

BUDGET = 50_000

hooks = Hooks()

@hooks.on.after_model_request
async def check_budget(ctx: RunContext[None], *, request_context, response):
    used = ctx.usage.total_tokens
    if used > BUDGET:
        raise UsageLimitExceeded(f"超出预算：{used} > {BUDGET}")
    return response

agent = Agent("openai-chat:qwen-flash", capabilities=[hooks])
```

只是限总量的话，用 Pydantic AI 自带的 `usage_limits` 参数更简单：

```python
from pydantic_ai.usage import UsageLimits
agent.run_sync("...", usage_limits=UsageLimits(total_tokens_limit=50_000))
```

用 Hook 的好处是能写更复杂的策略，比如预算快用完时切到便宜的模型继续。

---

## 8. 实战 C：限流（Rate Limit）

按用户 ID 限流，每分钟 10 次模型调用：

```python
from collections import defaultdict, deque
from dataclasses import dataclass
import time
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks

# {user_id: deque of timestamps}
_calls: dict[str, deque] = defaultdict(deque)
WINDOW = 60  # 秒
LIMIT = 10

@dataclass
class Deps:
    user_id: str

hooks = Hooks()

@hooks.on.before_model_request
async def rate_limit(ctx: RunContext[Deps], request_context):
    uid = ctx.deps.user_id
    now = time.time()
    q = _calls[uid]
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        raise RuntimeError(f"用户 {uid} 触发限流")
    q.append(now)
    return request_context

agent = Agent("openai-chat:qwen-flash", deps_type=Deps, capabilities=[hooks])
```

---

## 9. 实战 D：PII 脱敏

发给模型之前去掉身份证号和手机号：

```python
import dataclasses
import re
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelRequest, UserPromptPart

# 长的在前，否则 18 位身份证会先被 \d{11} 截走前 11 位；中文和数字之间 \b 不成立，用前后不是数字做边界
PII_RE = re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{11})(?!\d)")

hooks = Hooks()

@hooks.on.before_model_request
async def scrub(ctx, request_context):
    cleaned = []
    for msg in request_context.messages:
        if isinstance(msg, ModelRequest):
            parts = [
                dataclasses.replace(p, content=PII_RE.sub("[REDACTED]", p.content))
                if isinstance(p, UserPromptPart) and isinstance(p.content, str) else p
                for p in msg.parts
            ]
            msg = dataclasses.replace(msg, parts=parts)
        cleaned.append(msg)
    request_context.messages = cleaned      # 只改「这次请求」发给模型的消息
    return request_context
```

不要原地改 part（`part.content = ...`）。`request_context.messages` 是一个独立的浅拷贝列表，但里面的 message 和 part 对象可能和 `ctx.messages`（持久历史）是同一批对象，原地修改会把历史和后续请求一起改掉。只想改这一次请求，就像上面那样用 `dataclasses.replace` 构造新对象；想连持久历史一起清洗，就去改 `ctx.messages`。两边都要改的话，得分别显式改。

---

## 10. 实战 E：缓存（wrap_model_request）

`wrap` 钩子最适合做缓存。在 demo 04 里，同一个 prompt 第二次就是 `cache HIT`，不再请求模型：

```python
import hashlib, json
from pydantic_ai.capabilities import Hooks

_cache: dict[str, object] = {}

hooks = Hooks()

@hooks.on.model_request
async def cache_model(ctx, *, request_context, handler):
    # 缓存键只取「用户提示 + 工具返回」的内容：消息里带时间戳 / run_id，整条 dump 的话永远 MISS
    key = hashlib.md5(
        json.dumps(
            [getattr(p, "content", None) for m in request_context.messages for p in m.parts],
            default=str,
        ).encode()
    ).hexdigest()
    if key in _cache:
        print("[cache hit]", key[:8])
        return _cache[key]
    response = await handler(request_context)
    _cache[key] = response
    return response
```

---

## 11. 只针对某些工具的钩子

`before_tool_execute` 这类工具相关的钩子支持用 `tools=[...]` 过滤：

```python
@hooks.on.before_tool_execute(tools=["send_email"])
async def confirm_send(ctx, *, call, tool_def, args):
    # 只在 send_email 工具被调时触发
    if args.get("to") in BLACKLIST:
        raise RuntimeError("黑名单地址不能发邮件")
    return args
```

不传 `tools` 就对所有工具生效。

---

## 12. 用钩子统一转换错误：`ModelRetry` 与 `ToolFailed`

工具校验 / 执行钩子和模型请求钩子里，也可以抛 `ModelRetry`（让模型重试，消耗对应工具或输出的重试预算）和 `ToolFailed`（把这次工具调用作为失败结果告诉模型）。我常用的做法是写一个 `tool_execute_error` 钩子，把第三方库的异常统一翻译成这两种，免得每个工具里重复 `try/except`。钩子还可以抛 `SkipModelRequest(response)`，不调模型直接给出回复，适合在 `before_model_request` 里命中缓存时用；§10 的缓存用的是 wrap 钩子，直接 return 缓存结果、不调 `handler`，效果一样。另外还能抛 `SkipToolValidation(args)` / `SkipToolExecution(result)` 跳过某个阶段。

---

## 13. 超时

每个钩子都能设 timeout：

```python
@hooks.on.before_model_request(timeout=2.0)
async def slow_check(ctx, request_context):
    await some_io()
    return request_context
```

超过 2 秒会抛 `HookTimeoutError`。涉及 IO 的钩子都应该设 timeout，否则一个阻塞就能拖住整个 run。

---

## 14. 注册顺序与执行顺序

| 钩子类型 | 多个时的执行顺序 |
|---------|-----------------|
| `before_*` | 按注册顺序正序 |
| `after_*` / `*_error` | 同一个 `Hooks` 实例内按注册顺序；跨多个 capability 时按 capability 倒序（栈式） |
| `wrap_*` | 先注册的在外层（洋葱式） |

所以注册顺序有讲究：审计、限流类的放外层，业务转换放内层。另外，`RunContext` 上有哪些内容和钩子的触发时机有关。很早触发的 run / node 钩子执行时，当前这一步的工具管理器和模型请求参数还没组装好，`ctx.tools` 之类的可能还不是最终值。

---

## 15. 同步 vs 异步

同步函数和 async 函数都可以当钩子：

```python
# OK
@hooks.on.before_run
def sync_hook(ctx):
    print("sync")

# 更好（要 await 异步 IO 时）
@hooks.on.before_run
async def async_hook(ctx):
    await save_log(...)
```

同步钩子里可能有阻塞代码，所以框架默认把它放进线程池执行，不卡事件循环。这样有两个副作用：同步钩子里设置的 `ContextVar` 在别处读不到；线程池容量有限，别在里面做太重的事。

---

## 16. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 钩子里抛错，整个 run 失败 | 钩子的异常默认会往外传 | 钩子里 try/except，或用 `xxx_error` 钩子兜底 |
| `before_*` 钩子改了 messages 但没生效 | 忘了 return 修改后的 request_context | 必须 `return request_context` |
| `before_tool_execute` 改了 args，工具收到的还是原值 | 同上 | 必须 `return args` |
| 钩子不触发 | 没注册到 `capabilities=[hooks]` | 检查 Agent 构造 |
| 钩子执行顺序和预想的不一样 | `after_*` 跨 capability 是栈式倒序 | 按想要的顺序调整注册顺序 |
| 多个 `wrap_*` 嵌套顺序乱了 | 先注册的在外层 | 按洋葱来想：外层先注册 |
| 同步钩子里设的 `ContextVar` 别处读不到 | 同步钩子跑在线程池里 | 改成 async 钩子，或者别依赖 ContextVar |
| 钩子改了 message 里的 part，历史也被改了 | `request_context.messages` 里的对象和持久历史共享 | 用 `dataclasses.replace` 构造新对象 |
| `TypeError: ... got an unexpected keyword argument 'result'`（或 `args`） | `after_run` / `after_tool_execute` 的签名不全 | `after_run` 补上 `result` 并返回它，`after_tool_execute` 补上 `args` |
| `AttributeError: ... has no attribute 'metadata'`（`request_context.metadata`） | `ModelRequestContext` 没有这个字段 | 用 wrap 钩子在一个函数里完成计时和记账 |
| 钩子里 `ctx.deps` 是 None | Agent 没设 `deps_type`，或 `run` 时没传 `deps=` | 检查泛型和参数 |
| `run_stream()` 下节点钩子的触发时机不同 | `run_stream()` 把节点生命周期拆在流式前后，`before_node_run` 在流打开前触发 | 需要逐事件完整观测时，用 `event` / `run_event_stream` 钩子，或 `run()` + `event_stream_handler` |
| `wrap_*` 里忘了 await handler | 返回的是 coroutine 对象 | `result = await handler(...)` |
| 钩子里抛了 ModelRetry | 会触发自动重试，这是设计好的行为 | 先想清楚是不是真要重试 |

---

## 17. 本章 demo

完整可运行代码：[`demos/advanced/04_hooks.py`](../../demos/advanced/04_hooks.py)

进阶部分后面还有直接调模型、Capabilities、重试、延迟工具四篇；再往后是[独立模块](../04-modules/)：MCP、Evals 评测、Graph 状态机、Logfire 可观测等。
