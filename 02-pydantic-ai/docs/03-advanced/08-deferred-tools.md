# Pydantic AI 进阶 08：Deferred Tools 与人审批

> 有些工具调用不能在 Agent 运行中当场执行：要等人审批，要交给别的进程异步处理，或者要跨网络调度。Deferred Tools 让 Agent 先停下来，把调用请求交给业务层，处理完再带着结果接着跑。这篇讲审批和外部执行两种模式、怎么恢复运行，以及审批在安全上的边界。

以 `pydantic-ai 2.64.0` 为准。想在运行中途就地处理延迟调用，用 `HandleDeferredToolCalls` capability（见第 12 节）。

---

## 1. 为什么需要延迟工具

普通的 `@agent.tool` 是在同一进程里当场执行、立刻返回的函数：

```python
@agent.tool
def get_weather(ctx, city: str) -> str:
    return requests.get(...).text   # 当场调
```

但生产环境里，很多工具不能或者不该当场执行：

1. 危险操作要人审批：删库、退款、给客户发邮件，不能让 Agent 自己决定
2. 跨进程异步：工具由别的 worker 执行，要排队、等回调
3. 跨网络：工具实际在浏览器端调用（比如由前端按钮触发），Agent 那边拿不到
4. 超长任务：跑 30 分钟的批处理，HTTP 连接撑不了那么久

这几种情况有一个共同点：Agent 决定调用哪个工具、传什么参数，但执行要交给别人。Pydantic AI 用 `DeferredToolRequests` + `DeferredToolResults` 这套机制专门处理它。

---

## 2. 基本流程

```
   ┌─────────────────────────────────────────────────┐
   │ Round 1:  agent.run_sync("删除 config.py")       │
   └─────────────────────────────────────────────────┘
                       │
                       ▼
   ┌─────────────────────────────────────────────────┐
   │ Agent → 决定调 delete_file(path="config.py")    │
   │ delete_file 标了 requires_approval=True         │
   │ Agent 暂停，返回 DeferredToolRequests           │
   └─────────────────────────────────────────────────┘
                       │
                       ▼
   ┌─────────────────────────────────────────────────┐
   │ 你的业务层：弹个对话框/钉钉审批，等用户回复       │
   │ 拿到 approved=True 后构造 DeferredToolResults    │
   └─────────────────────────────────────────────────┘
                       │
                       ▼
   ┌─────────────────────────────────────────────────┐
   │ Round 2:  agent.run_sync(                       │
   │     message_history=messages,                    │
   │     deferred_tool_results=results,               │
   │ )                                                │
   │ Agent 拿到批准结果，继续跑工具 → 给最终答案       │
   └─────────────────────────────────────────────────┘
```

有三点要注意：

1. Agent 暂停时是正常返回，不是抛异常
2. 两轮之间靠 `message_history` 串起来，跨进程时消息不能丢
3. `DeferredToolResults` 里的 `tool_call_id` 必须和请求一一对应

---

## 3. 两种延迟模式

Pydantic AI 把延迟分成两种含义不同的情况：

| 模式 | 触发方式 | 用途 |
|------|---------|------|
| Approval（审批） | 工具加 `requires_approval=True`，或运行时 raise `ApprovalRequired()` | 危险操作要人同意 |
| External（外部执行） | 工具里 raise `CallDeferred(metadata=...)` | 工具实际不在当前进程执行，比如前端或后台 worker |

两种都通过 `DeferredToolRequests` 返回，分别在 `requests.approvals` 和 `requests.calls` 里处理。

---

## 4. Approval 模式：标记需要审批的工具

```python
from pydantic_ai import Agent

agent = Agent('openai:gpt-6.1-sol')


@agent.tool_plain(requires_approval=True)
def delete_file(path: str) -> str:
    """Delete a file by path."""
    return f'File {path!r} deleted'
```

`requires_approval=True` 的意思是：模型决定调用这个工具时，先停下来问人。

也可以按条件审批，只在敏感场景暂停：

```python
from pydantic_ai import Agent, ApprovalRequired, RunContext

SENSITIVE = {'config.py', '.env', 'secrets.json'}


@agent.tool
def update_file(ctx: RunContext, path: str, content: str) -> str:
    if path in SENSITIVE and not ctx.tool_call_approved:
        raise ApprovalRequired()  # ← 运行时决定要审批
    write_file(path, content)
    return f'Updated {path}'
```

审批通过后再次进入这个工具时，`ctx.tool_call_approved` 是 `True`，于是直接执行实际逻辑。

---

## 5. 接收 DeferredToolRequests

某次运行触发了延迟时，`result.output` 就不再是你声明的输出类型，而是 `DeferredToolRequests`：

```python
from pydantic_ai import Agent, DeferredToolRequests


agent = Agent('openai:gpt-6.1-sol', output_type=[str, DeferredToolRequests])

result = agent.run_sync('请把 config.py 删了')

if isinstance(result.output, DeferredToolRequests):
    requests = result.output
    print(requests.approvals)  # list[ToolCallPart]，等待审批
    print(requests.calls)      # list[ToolCallPart]，等待外部执行
    print(requests.metadata)   # dict，CallDeferred 传过来的 metadata
else:
    print('正常输出:', result.output)
```

`DeferredToolRequests` 必须加进 `output_type` 列表，相当于你显式声明「我能处理延迟」。

---

## 6. 构造 DeferredToolResults 并恢复

```python
from pydantic_ai import DeferredToolResults, ToolDenied

messages = result.all_messages()  # 保存历史，下一轮要传回

# 业务层：拿到用户的审批结果
results = DeferredToolResults()
for call in requests.approvals:
    if call.tool_name == 'delete_file' and ask_user(call.args):
        results.approvals[call.tool_call_id] = True
    else:
        results.approvals[call.tool_call_id] = ToolDenied('用户拒绝了删除操作')

# 第二轮：把 results 传回去继续跑
final = agent.run_sync(
    message_history=messages,
    deferred_tool_results=results,
)
print('最终回答:', final.output)
```

`results.approvals` 的值可以是：

- `True`：批准
- `False`：拒绝（用默认的错误信息）
- `ToolApproved(override_args={...})`：批准，并可以用 `override_args` 替换模型给的参数
- `ToolDenied('原因')`：拒绝，并告诉模型原因

需要给工具带额外信息时，写在 `results.metadata[tool_call_id]` 里，工具执行时从 `ctx.tool_call_metadata` 读到。

工具被拒后，模型读到 `ToolDenied`，自己决定下一步，比如换一个安全的操作，或者向用户说明情况后停下。

---

## 7. External 模式：跨进程异步执行

工具实际由别的服务执行时，用 `CallDeferred`：

```python
import asyncio
from pydantic_ai import Agent, CallDeferred, RunContext

agent = Agent('openai:gpt-6.1-sol')

tasks: dict[str, asyncio.Task] = {}


@agent.tool
async def compute_heavy(ctx: RunContext, payload: str) -> str:
    """Submit a long computation to background worker."""
    task_id = f'task_{len(tasks)}'
    tasks[task_id] = asyncio.create_task(do_heavy_work(payload))
    raise CallDeferred(metadata={'task_id': task_id})
```

raise 之后，Agent 把这次调用放进 `requests.calls`，metadata 也一起带出来。

外部 worker 执行完后：

```python
from pydantic_ai import DeferredToolResults

results = DeferredToolResults()
for call in requests.calls:
    task_id = requests.metadata[call.tool_call_id]['task_id']
    task = tasks[task_id]
    result_text = await task          # 等 worker 完成
    results.calls[call.tool_call_id] = result_text

final = await agent.run(
    message_history=messages,
    deferred_tool_results=results,
)
```

`results.calls` 的值可以是：

- 任意可序列化的值（`str`、`dict`、`BaseModel` 等）：当作工具的实际返回，也可以显式包成 `ToolReturn`
- `ModelRetry('...')`：把错误信息回给模型，让它重试
- `ToolFailed(...)`：告诉模型这次调用失败了

---

## 8. 两种模式的字段对照

| 字段 | Approval | External |
|------|----------|----------|
| 触发方式 | `tool_plain(requires_approval=True)` / `raise ApprovalRequired()` | `raise CallDeferred(metadata={...})` |
| 出现在 | `requests.approvals` | `requests.calls` |
| 回填位置 | `results.approvals[id] = True/False/ToolApproved/ToolDenied` | `results.calls[id] = 真实返回 / ModelRetry / ToolFailed` |
| 业务含义 | 能不能做 | 做完了，结果是什么 |
| 是否一定要 metadata | 否 | 通常需要（记 task_id） |

---

## 9. 实战：电商下单 Agent

需求：

- 用户说：「帮我把购物车结算了」
- `get_cart_items`、`calculate_total` 自动执行
- `submit_order`、`refund` 必须经人审批

```python
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied, RunContext


class Order(BaseModel):
    order_id: str
    total: float


agent = Agent(
    'openai:gpt-6.1-sol',
    output_type=[Order, DeferredToolRequests],
    instructions='你是电商助理，对涉及金额/支付/退款的操作要谨慎。',
)


@agent.tool_plain
def get_cart_items() -> list[dict]:
    return [
        {'sku': 'A1', 'name': 'iPhone 17', 'price': 7999, 'qty': 1},
        {'sku': 'B2', 'name': 'AirPods', 'price': 999, 'qty': 2},
    ]


@agent.tool_plain
def calculate_total(items: list[dict]) -> float:
    return sum(i['price'] * i['qty'] for i in items)


@agent.tool_plain(requires_approval=True)
def submit_order(items: list[dict], total: float) -> Order:
    """提交订单（需要用户确认）"""
    return Order(order_id='ORD-001', total=total)


@agent.tool_plain(requires_approval=True)
def refund(order_id: str, amount: float) -> str:
    """退款（需要用户确认）"""
    return f'退款 ¥{amount} → {order_id} 完成'
```

第一轮：

```python
result = agent.run_sync('帮我结算购物车')

if isinstance(result.output, DeferredToolRequests):
    requests = result.output
    messages = result.all_messages()

    print('===== 待审批 =====')
    for call in requests.approvals:
        print(f'  {call.tool_name}({call.args})')
```

输出可能是：

```
===== 待审批 =====
  submit_order({'items': [...], 'total': 9997.0})
```

第二轮（用户点了确认）：

```python
results = DeferredToolResults()
for call in requests.approvals:
    user_say_yes = input(f'同意 {call.tool_name}? (y/n): ') == 'y'
    if user_say_yes:
        results.approvals[call.tool_call_id] = True
    else:
        results.approvals[call.tool_call_id] = ToolDenied('用户取消了订单')

final = agent.run_sync(
    message_history=messages,
    deferred_tool_results=results,
)
print('最终:', final.output)
```

用户同意的话，`final.output` 就是 `Order(order_id='ORD-001', total=9997.0)`；拒绝的话，Agent 会读到 `ToolDenied`，可能回复「已为您取消下单」。

---

## 10. 与 message_history 的关系

这里最容易出错：第二轮必须传 `message_history`。框架要在历史里找到最后一条带未处理工具调用的 `ModelResponse`，才知道这些结果填给谁；不传历史会直接报 `UserError`（"Tool call results were provided, but the message history is empty."）：

```python
# ❌ 错误：丢历史
agent.run_sync(deferred_tool_results=results)

# ✅ 正确
agent.run_sync(
    message_history=result.all_messages(),
    deferred_tool_results=results,
)
```

历史里包含：

- 用户的原始 prompt
- 模型决定调用工具的 `ToolCallPart`
- 一个空位，等你把结果填回来

Pydantic AI 用 `deferred_tool_results` 里的 `tool_call_id` 去匹配历史里的 `ToolCallPart`，所以 ID 要原样取自 `requests.approvals` / `requests.calls`，不要自己编。用 `requests.build_results(...)` 构造结果时，ID 对不上会直接抛 `ValueError`。

---

## 11. 跨进程持久化

第一轮和第二轮分属不同的 HTTP 请求、不同服务，或者中间要落库，这种情况很常见。这时要把 `messages` 序列化存起来：

```python
import json
from pydantic_ai.messages import ModelMessagesTypeAdapter

# 存
serialized = ModelMessagesTypeAdapter.dump_json(messages)
save_to_db(user_session_id, serialized)

# 取
serialized = load_from_db(user_session_id)
messages = ModelMessagesTypeAdapter.validate_json(serialized)
```

`ModelMessagesTypeAdapter` 是 Pydantic AI 提供的 TypeAdapter，按框架自己的消息 schema 序列化和反序列化，`ToolCallPart`、时间戳这些类型都能原样还原。

---

## 12. Inline Handler 模式

如果延迟的事情当下就能解决（比如审批流程很快就有结果），可以用 `HandleDeferredToolCalls` capability 在 Agent 内部处理掉：

```python
from pydantic_ai.capabilities import HandleDeferredToolCalls


async def handle_deferred(ctx, requests):
    approvals = {}
    for call in requests.approvals:
        approvals[call.tool_call_id] = await ask_via_slack(call)
    return requests.build_results(approvals=approvals)


agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[HandleDeferredToolCalls(handler=handle_deferred)],
)

# 这次 run 不会暂停，handler 内部处理掉了所有 approval
result = agent.run_sync('删 config.py')
print(result.output)  # 直接是最终输出
```

适合审批不需要长时间等待、又想保留延迟工具这层清晰语义的场景。

---

## 13. 与 LangChain / LangGraph 对比

LangChain 本身没有延迟工具的概念，一般用 LangGraph 的 interrupt 实现：

```python
# LangGraph
from langgraph.types import interrupt
def approval_node(state):
    answer = interrupt({'tool': 'delete', 'args': ...})
    if answer == 'yes':
        do_delete()
```

LangGraph 的 interrupt 在图节点这一级，更通用，也更重。LangChain 1.x 里还有 `HumanInTheLoopMiddleware`，挂在 `create_agent` 上对指定工具中断，需要 checkpointer 和 `thread_id`。Pydantic AI 的延迟工具在工具这一级，状态就是消息历史：不用画图，也不用 checkpointer，给工具标上 `requires_approval=True`，什么时候拿到审批结果，就什么时候带着历史接着跑。代价是持久化，以及「客户端传回来的历史不可信」这件事，都要你自己负责。

如果只是少数几个敏感工具需要人审批，我会选 Pydantic AI 这套，简单得多；如果整个工作流都要中断和恢复，LangGraph 的 interrupt 更合适。

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `result.output` 不是 Pydantic 模型，而是 `DeferredToolRequests` | 模型决定调用要审批的工具 | 用 `isinstance(result.output, DeferredToolRequests)` 判断 |
| 第二轮报 `UserError: Tool call results were provided, but the message history is empty.` | 没传 `message_history` | 必须 `message_history=result.all_messages()` |
| 第二轮 Agent 又一次停下来要审批 | 历史里的 ToolCallPart 没拿到结果 | 检查 `results.approvals` 是否覆盖了 `requests.approvals` 里所有 id |
| `output_type=Issue` 报错 | 没声明 `DeferredToolRequests` 是合法输出 | `output_type=[Issue, DeferredToolRequests]` |
| 某个工具没有停下来等审批 | 工具是用 `Agent(tools=[fn])` 直接传函数注册的，没有地方写 `requires_approval`（装饰器里把参数写成 `require_approval` 不会静默失效，会直接报 `TypeError`） | 改成 `Agent(tools=[Tool(fn, requires_approval=True)])`，或用 `@agent.tool(requires_approval=True)` / `@agent.tool_plain(requires_approval=True)` |
| 跨进程反序列化 messages 失败 | 自己用了 `json.dumps()` | 用 `ModelMessagesTypeAdapter` |
| 第二轮拿不到 `CallDeferred.metadata` | 没从 `requests.metadata[id]` 取 | 它是 dict，按 tool_call_id 索引 |
| 多个延迟工具同时挂起，只处理了一个 | 没把 `approvals` 和 `calls` 都遍历一遍 | 两个列表都要遍历并填值 |
| 用了 `ToolDenied('xxx')`，模型却没读到原因 | 实际写成了 `False` | 显式用 `ToolDenied('原因')` |
| 第二轮之后又出现新的延迟请求 | 这是正常的，agent 想调用另一个敏感工具 | 循环处理，直到不再返回 `DeferredToolRequests` |

---

## 15. 安全边界：审批不等于授权

Pydantic AI 的服务端是无状态的：延迟工具的结果（`deferred_tool_results`）和消息历史都是随请求带进来的，框架既不签名也不校验。所以：

- 通过 UI 适配器（Vercel AI / AG-UI）或自己的 HTTP 接口对外服务时，审批结果由客户端随请求提交，**任何能访问这个接口的客户端都能批准** `requires_approval=True` 的工具。不要把「历史里有审批记录」当成授权凭据；
- 高风险操作要在服务端重新校验调用方的权限，按调用方权限构建工具集；来自浏览器的历史先用 `sanitize_messages` 清洗（见[消息与对话历史（Messages & History）](../01-basics/07-messages-history.md)）；
- 内置的网页聊天 UI（`agent.to_web()`）把审批显示成「批准 / 拒绝」按钮。官方明确说它只用于本地开发调试，只接受 `application/json` 以防跨站请求，但绑定 localhost 本身并不构成安全边界。

V2 还有几个细节：工具参数必须先校验通过才能延迟，因为处理延迟的人需要看到参数；`ApprovalRequired` / `CallDeferred` 也可以在 `before_tool_execute` 等执行 hook 里抛，给一批工具统一加审批；`toolset.approval_required(predicate)` 可以按条件批量加审批。

---

## 16. 多轮循环写法

Agent 可能多次需要审批时，写成一个循环：

```python
from pydantic_ai import DeferredToolRequests, DeferredToolResults

result = agent.run_sync('完成所有清理任务')
messages = result.all_messages()

while isinstance(result.output, DeferredToolRequests):
    requests = result.output
    results = DeferredToolResults()
    for call in requests.approvals:
        results.approvals[call.tool_call_id] = ask_user(call)
    for call in requests.calls:
        results.calls[call.tool_call_id] = run_external(call)

    result = agent.run_sync(
        message_history=messages,
        deferred_tool_results=results,
    )
    messages = result.all_messages()

print('全部完成:', result.output)
```

生产环境里一般就这么写：把延迟看作 Agent 和业务层之间反复来回的协作，不要当成只发生一次的中断。

---

## 17. 本章 demo

完整可运行代码：[`demos/advanced/08_deferred_tools.py`](../../demos/advanced/08_deferred_tools.py)

demo 包括：
- 电商 Agent：自动执行的工具和需要审批的工具混用
- 审批通过、拒绝两条路径
- 多轮延迟循环
- 跨进程序列化 messages
- 没有 key 时用 TestModel 模拟模型选中审批工具

---

## 18. 进阶部分回顾

进阶部分的 8 篇：

| 章节 | 主题 |
|------|------|
| 01 | [流式响应](01-streaming.md) |
| 02 | [多模态输入](02-multimodal.md) |
| 03 | [思维链](03-thinking.md) |
| 04 | [Hooks](04-hooks.md) |
| 05 | [直接调模型（Direct Requests）](05-direct-requests.md) |
| 06 | [Capabilities](06-capabilities.md) |
| 07 | [重试机制](07-retries-http.md) |
| 08 | Deferred Tools（本篇）|

接下来是[配套模块](../04-modules/)：MCP、Evals、Graph、Logfire 等。
