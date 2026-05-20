# Pydantic AI 进阶 04：Hooks（生命周期钩子）

> **一句话**：Hooks 是 Pydantic AI 给 Agent 装的"AOP 切面"——在 run / model_request / tool_execute / output_validate 等关键节点埋点，统一做日志、限流、审计、重试、token 预算控制，而不用去改 Agent 本身。

---

## 1. 为什么需要 Hooks

写到生产你迟早会遇到这五类需求：

1. **审计**：谁、什么时候、用什么 prompt 调了哪个工具
2. **限流**：单用户每分钟最多 10 次模型调用
3. **预算控制**：单次会话 token 不超过 50k，超了 abort
4. **PII 脱敏**：发给模型前删手机号、身份证号
5. **重试 / 降级**：模型返回包含敏感词时自动重试或换 prompt

不用 Hooks 你只能：

- 在每个工具里手写 logging（散落、易遗漏）
- 在调用 `agent.run()` 外面套 try/finally（拦不到工具调用）
- 监控只能靠 Logfire（只读，没法插手）

Hooks 把"读取 + 改写 + 中断"三件事都给你了。

---

## 2. Hooks vs Logfire

| 维度 | Hooks | Logfire |
|------|-------|---------|
| 定位 | **业务层切面** | **框架层观测** |
| 能力 | 读 + 改 + 拦 | 只读 |
| 注册方式 | `Agent(capabilities=[Hooks(...)])` | `import logfire; logfire.configure()` |
| 典型用途 | 限流 / 脱敏 / 审计 / 重试 | trace / metric / 异常监控 |
| 性能影响 | 微（按钩子写的复杂度算） | 微（异步上报） |
| 互斥吗 | **不互斥，强烈建议同时用** | 同上 |

记住：**Hooks 是写代码的事，Logfire 是开关的事**。两者覆盖的关注点不一样。

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
async def log_done(ctx: RunContext[None]) -> None:
    print(f"[done] tokens={ctx.usage.total_tokens}")

agent = Agent("openai:gpt-4o-mini", capabilities=[hooks])
agent.run_sync("Hello")
```

要点：

1. `Hooks()` 是个收集器，通过装饰器把回调挂上去
2. 挂好后用 `capabilities=[hooks]` 注册到 Agent
3. 钩子签名第一个参数永远是 `ctx: RunContext[...]`

---

## 4. 钩子完整清单

Pydantic AI 把生命周期切得很细，七组钩子覆盖所有关键节点：

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

四种用法对比：

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

@hooks.on.before_model_request
async def trace_model(ctx: RunContext[None], request_context):
    request_context.metadata["__start"] = time.time()
    return request_context

@hooks.on.after_model_request
async def log_model(ctx, *, request_context, response):
    elapsed = time.time() - request_context.metadata["__start"]
    await save_log(
        run_id=ctx.run_id,
        kind="model_request",
        elapsed=elapsed,
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

agent = Agent("openai:gpt-4o-mini", capabilities=[hooks])
```

---

## 7. 实战 B：Token 预算控制

单次会话 token 不超过 50k，超了直接 abort：

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

agent = Agent("openai:gpt-4o-mini", capabilities=[hooks])
```

更优雅的写法是用 Pydantic AI 自带的 `usage_limits` 参数：

```python
from pydantic_ai.usage import UsageLimits
agent.run_sync("...", usage_limits=UsageLimits(total_tokens_limit=50_000))
```

但 Hook 的好处是**可以做更复杂的策略**，比如"快用完时切到便宜模型继续"。

---

## 8. 实战 C：限流（Rate Limit）

按用户 ID 限流，每分钟 10 次模型调用：

```python
from collections import defaultdict, deque
import time
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks

# {user_id: deque of timestamps}
_calls: dict[str, deque] = defaultdict(deque)
WINDOW = 60  # 秒
LIMIT = 10

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

agent = Agent("openai:gpt-4o-mini", deps_type=Deps, capabilities=[hooks])
```

---

## 9. 实战 D：PII 脱敏

发给模型前删除身份证 / 手机号：

```python
import re
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import UserPromptPart

PII_RE = re.compile(r"\d{11}|\d{18}|\d{17}[\dX]")

hooks = Hooks()

@hooks.on.before_model_request
async def scrub(ctx, request_context):
    for msg in request_context.messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                part.content = PII_RE.sub("[REDACTED]", part.content)
    return request_context
```

---

## 10. 实战 E：缓存（wrap_model_request）

`wrap` 钩子最适合做缓存：

```python
import hashlib, json
from pydantic_ai.capabilities import Hooks

_cache: dict[str, object] = {}

hooks = Hooks()

@hooks.on.model_request
async def cache_model(ctx, *, request_context, handler):
    key = hashlib.md5(
        json.dumps([m.model_dump() for m in request_context.messages], default=str).encode()
    ).hexdigest()
    if key in _cache:
        print("[cache hit]", key[:8])
        return _cache[key]
    response = await handler(request_context)
    _cache[key] = response
    return response
```

---

## 11. 针对特定工具的钩子

`before_tool_execute` 等工具相关钩子支持 `tools=[...]` 过滤：

```python
@hooks.on.before_tool_execute(tools=["send_email"])
async def confirm_send(ctx, *, call, tool_def, args):
    # 只在 send_email 工具被调时触发
    if args.get("to") in BLACKLIST:
        raise RuntimeError("黑名单地址不能发邮件")
    return args
```

不带 `tools` 参数则对所有工具生效。

---

## 12. 超时

每个钩子都能加 timeout：

```python
@hooks.on.before_model_request(timeout=2.0)
async def slow_check(ctx, request_context):
    await some_io()
    return request_context
```

超过 2 秒抛 `HookTimeoutError`。**所有 IO 类钩子都该加 timeout**，否则一个阻塞会拖死整个 run。

---

## 13. 注册顺序与执行顺序

| 钩子类型 | 多个时的执行顺序 |
|---------|-----------------|
| `before_*` | 按注册顺序正序 |
| `after_*` | 按注册顺序**倒序**（栈式） |
| `wrap_*` | 先注册的在外层（洋葱式） |

所以注册顺序很重要。**审计 / 限流类放外层，业务转换放内层**。

---

## 14. 同步 vs 异步

Pydantic AI 接受**同步函数**和**async 函数**都作为钩子，但**强烈推荐 async**：

```python
# OK
@hooks.on.before_run
def sync_hook(ctx):
    print("sync")

# 更好
@hooks.on.before_run
async def async_hook(ctx):
    await save_log(...)
```

同步钩子里别做 IO（数据库 / HTTP），会卡住事件循环。

---

## 15. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 钩子里抛错导致整个 run 失败 | Hook 异常默认会传播 | 钩子里用 try/except 兜住，或用 `xxx_error` 钩子做兜底 |
| `before_*` 钩子改了 messages 但没生效 | 忘了 return 修改后的 request_context | 必须 `return request_context` |
| `before_tool_execute` 改了 args 但工具收到原值 | 同上 | 必须 `return args` |
| Hook 不触发 | 没注册到 `capabilities=[hooks]` | 检查 Agent 构造 |
| Hook 触发顺序奇怪 | `after_*` 是栈式倒序 | 按预期顺序调整注册顺序 |
| 多个 `wrap_*` 嵌套乱了 | 先注册的是外层 | 想象成洋葱：外层在前 |
| 同步钩子 IO 阻塞 | 同步钩子直接跑在事件循环里 | 改成 async |
| 钩子里访问 `ctx.deps` 报 None | Agent 没设 `deps_type` / `run` 没传 `deps=` | 检查泛型和参数 |
| 流式时 Hook 不触发 | `run_stream()` 走的是另一套流程 | 用 `event` / `run_event_stream` 钩子 |
| `wrap_*` 里忘了 await handler | 返回了 coroutine 对象 | `result = await handler(...)` |
| 钩子里抛 ModelRetry | 触发自动重试（**这是 feature**） | 想清楚是不是要重试 |

---

## 16. 本章 demo

完整可运行代码：[`demos/advanced/04_hooks.py`](../../demos/advanced/04_hooks.py)

至此进阶四篇全部完成。下一组：[`04-modules/`](../04-modules/) —— Logfire 可观测 / Evals 评测 / Graph 状态机三大独立模块。
