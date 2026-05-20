# Pydantic AI 进阶 08：Deferred Tools 与人审批

> **一句话**：Deferred Tools 让工具调用"延后"到 Agent run 之外去执行 —— 等人审批、跨进程异步、跨网络调度，都可以让 Agent 先暂停，业务层处理完再把结果塞回来继续 run。

---

## 1. 为什么需要"延迟工具"

普通 `@agent.tool` 是**当场执行 + 立刻返回**的同进程函数：

```python
@agent.tool
def get_weather(ctx, city: str) -> str:
    return requests.get(...).text   # 当场调
```

但生产里很多工具**不能/不该**当场执行：

1. **危险操作要人审批**：删库、退款、发邮件给客户 —— Agent 不能自己决定
2. **跨进程异步**：工具是别的 worker 在跑，要排队 + 等回调
3. **跨网络协议**：工具实际是浏览器端调的（如前端按钮触发），Agent 拿不到
4. **超长任务**：跑 30 分钟的批处理，HTTP 连接撑不住

这四类场景的共同特征：**Agent 决定"调哪个工具+用什么参数"，但执行权要交出去**。Pydantic AI 用 `DeferredToolRequests` + `DeferredToolResults` 这套机制把它做成一等公民。

---

## 2. 基本工作流

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

注意三点：

1. **Agent run 暂停时是"正常返回"**，不是抛异常
2. 两轮之间靠 `message_history` 串联，**不能跨进程丢消息**
3. `DeferredToolResults` 里的 `tool_call_id` 必须严格对应

---

## 3. 两种延迟模式

Pydantic AI 把"延迟"分成两种语义不同的场景：

| 模式 | 触发方式 | 用途 |
|------|---------|------|
| **Approval（审批）** | 工具加 `requires_approval=True` 或运行时 raise `ApprovalRequired()` | 危险操作要人点头 |
| **External（外部执行）** | 工具里 raise `CallDeferred(metadata=...)` | 工具实际不在这进程跑，比如前端 / 后台 worker |

两种都通过 `DeferredToolRequests` 返回，你按 `requests.approvals` 和 `requests.calls` 分别处理。

---

## 4. Approval 模式：标记需要审批的工具

```python
from pydantic_ai import Agent

agent = Agent('openai:gpt-5.2')


@agent.tool_plain(requires_approval=True)
def delete_file(path: str) -> str:
    """Delete a file by path."""
    return f'File {path!r} deleted'
```

`requires_approval=True` 告诉 Pydantic AI：**模型决定要调这个工具时，先停下来问人**。

也可以**条件审批**（只有敏感场景才暂停）：

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

`ctx.tool_call_approved` 在审批通过后**重新进入这个工具**时是 `True`，于是直接执行真实逻辑。

---

## 5. 接收 DeferredToolRequests

如果 Agent 的某次 run 触发了延迟，`result.output` **不再是你的输出类型**，而是 `DeferredToolRequests`：

```python
from pydantic_ai import Agent, DeferredToolRequests


agent = Agent('openai:gpt-5.2', output_type=[str, DeferredToolRequests])

result = agent.run_sync('请把 config.py 删了')

if isinstance(result.output, DeferredToolRequests):
    requests = result.output
    print(requests.approvals)  # list[ToolCallPart]，等待审批
    print(requests.calls)      # list[ToolCallPart]，等待外部执行
    print(requests.metadata)   # dict，CallDeferred 传过来的 metadata
else:
    print('正常输出:', result.output)
```

**关键**：要把 `DeferredToolRequests` 加进 `output_type` 列表里。这是 Pydantic AI 让你显式声明"我能处理延迟"。

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

`results.approvals` 字典值可以是：

- `True` —— 批准
- `False` —— 拒绝（默认错误信息）
- `ToolApproved(...)` —— 批准并可附加 metadata
- `ToolDenied('原因')` —— 拒绝并告诉模型为啥

被拒的工具，模型读到 `ToolDenied` 后会决定下一步，比如"那我换个安全操作"或"我向用户道歉并停下"。

---

## 7. External 模式：跨进程异步执行

工具实际由别的服务执行时，用 `CallDeferred`：

```python
import asyncio
from pydantic_ai import Agent, CallDeferred, RunContext

agent = Agent('openai:gpt-5.2')

tasks: dict[str, asyncio.Task] = {}


@agent.tool
async def compute_heavy(ctx: RunContext, payload: str) -> str:
    """Submit a long computation to background worker."""
    task_id = f'task_{len(tasks)}'
    tasks[task_id] = asyncio.create_task(do_heavy_work(payload))
    raise CallDeferred(metadata={'task_id': task_id})
```

raise 之后 Agent 把这个调用塞进 `requests.calls`，metadata 一起带出来。

外部 worker 跑完后：

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

`results.calls` 字典值可以是：

- `str | dict | BaseModel` —— 真实工具返回
- `ModelRetry('...')` —— 让模型重试

---

## 8. 一图看懂两种模式的字段对照

| 字段 | Approval | External |
|------|----------|----------|
| 触发方式 | `tool_plain(requires_approval=True)` / `raise ApprovalRequired()` | `raise CallDeferred(metadata={...})` |
| 出现在 | `requests.approvals` | `requests.calls` |
| 回填位置 | `results.approvals[id] = True/False/ToolApproved/ToolDenied` | `results.calls[id] = 真实返回 / ModelRetry` |
| 业务含义 | "可不可以做" | "做完了，结果是什么" |
| 是否一定要 metadata | 否 | 通常需要（记 task_id） |

---

## 9. 实战：电商下单 Agent

需求：

- 用户："帮我把购物车结算了，最贵的那件如果超 5000 就先放着"
- Agent 自动调 `get_cart_items`、`calculate_total`、`apply_coupon` —— 都自动执行
- Agent 调 `submit_order`、`refund` —— 必须人审批

```python
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied, RunContext


class Order(BaseModel):
    order_id: str
    total: float


agent = Agent(
    'openai:gpt-5.2',
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

**Round 1**：

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

**Round 2**（人点了"确认"）：

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

如果用户同意，`final.output` 就是 `Order(order_id='ORD-001', total=9997.0)`。
如果拒绝，Agent 会读到 `ToolDenied`，可能回复"已为您取消下单"。

---

## 10. 与 message_history 的关系

**这是最容易踩坑的地方**：第二轮 run **必须**传 `message_history`，否则模型完全不知道之前发生过什么，可能直接重新调用工具，或者完全不调用：

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
- 模型决定调工具的 `ToolCallPart`
- (空位置，等你填回结果)

Pydantic AI 拿 `deferred_tool_results` 里的 `tool_call_id` 去匹配历史里的 `ToolCallPart`，对上号才能继续。**ID 错就报错，所以别自己造**。

---

## 11. 跨进程持久化

如果 Round 1 和 Round 2 跨**HTTP 请求 / 跨服务 / 跨数据库**（很常见），你要把 `messages` 序列化存下来：

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

`ModelMessagesTypeAdapter` 是 Pydantic AI 提供的 TypeAdapter，**保证消息体跨版本兼容**。

---

## 12. Inline Handler 模式（高级）

如果你的"延迟"其实是**当下能解决的**（比如 mock 一个审批流程），可以用 `HandleDeferredToolCalls` capability 让 Agent 内部就处理掉：

```python
from pydantic_ai.capabilities import HandleDeferredToolCalls


async def handle_deferred(ctx, requests):
    approvals = {}
    for call in requests.approvals:
        approvals[call.tool_call_id] = await ask_via_slack(call)
    return requests.build_results(approvals=approvals)


agent = Agent(
    'openai:gpt-5.2',
    capabilities=[HandleDeferredToolCalls(handler=handle_deferred)],
)

# 这次 run 不会暂停，handler 内部处理掉了所有 approval
result = agent.run_sync('删 config.py')
print(result.output)  # 直接是最终输出
```

适合的场景：**审批流不需要长时间等待**，但你又想保持"延迟工具"的语义清晰。

---

## 13. 与 LangChain / LangGraph 对比

LangChain 没有原生"延迟工具"概念，通常用 LangGraph 的 **interrupt** 来实现：

```python
# LangGraph
from langgraph.types import interrupt
def approval_node(state):
    answer = interrupt({'tool': 'delete', 'args': ...})
    if answer == 'yes':
        do_delete()
```

LangGraph 的 interrupt 是图节点级别的，更通用但更重；Pydantic AI 的延迟工具是工具级别，**写起来比 LangGraph 直接得多**：你不需要画图、不需要 checkpointer，只需要把工具标个 `requires_approval=True`。

如果你的"人审批"只覆盖几个敏感工具，Pydantic AI 是更简单的选择。如果你的整个工作流都需要"中断 + 恢复"，LangGraph 的 interrupt 更合适。

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `result.output` 拿到的不是 Pydantic 模型而是 `DeferredToolRequests` | 模型决定调要审批的工具 | 用 `isinstance(result.output, DeferredToolRequests)` 判断 |
| 第二轮 run 报"unknown tool_call_id" | 没传 `message_history` 或 id 不对 | 必须 `message_history=result.all_messages()` |
| Agent 第二轮直接再次问用户 | 历史里 ToolCallPart 没被"喂结果" | 检查 `results.approvals` 是否填全所有 `requests.approvals` 的 id |
| `output_type=Issue` 报错 | 没声明 `DeferredToolRequests` 是合法输出 | `output_type=[Issue, DeferredToolRequests]` |
| `requires_approval=True` 没生效 | 用了 `@agent.tool` 但参数名打错 | 改成 `@agent.tool_plain(requires_approval=True)` 或 `@agent.tool(requires_approval=True)` |
| 跨进程 messages 反序列化失败 | 自己 `json.dumps()` 了 | 用 `ModelMessagesTypeAdapter` |
| `CallDeferred.metadata` 在第二轮拿不到 | 没去 `requests.metadata[id]` 取 | 它是 dict，按 tool_call_id 索引 |
| 多个 deferred 工具同时挂起，只处理了一个 | 没遍历 `approvals + calls` 全部 | 两个列表都要遍历填值 |
| 用 `ToolDenied('xxx')` 但模型没读到原因 | 写成 `False` 了 | 显式用 `ToolDenied('原因')` |
| Round 2 后又触发新一轮 deferred | 这是正常的（agent 想调另一个敏感工具） | 循环处理直到没有 `DeferredToolRequests` |

---

## 15. 多轮循环范式

如果 Agent 可能多次需要审批，写一个循环：

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

这是**生产环境的标准范式**，把 deferred 当成"协作信号"而不是"一次性中断"。

---

## 16. 本章 demo

完整可运行代码：[`demos/advanced/08_deferred_tools.py`](../../demos/advanced/08_deferred_tools.py)

demo 涵盖：
- 电商 Agent：自动工具 + 审批工具混用
- 审批通过 / 拒绝 两条路径
- 多轮 deferred 循环
- 跨进程序列化 messages 演示
- 无 key 时用 TestModel 模拟模型选择审批工具

---

## 17. 章节小结

到这里你已经走完 03-advanced 的 8 篇，掌握了 Pydantic AI 的核心进阶能力：

| 章节 | 主题 |
|------|------|
| 01 | 流式响应 |
| 02 | 多模态输入 |
| 03 | 思维链 |
| 04 | Hooks |
| 05 | 直接调模型（Direct Requests）|
| 06 | Capabilities |
| 07 | 重试机制 |
| 08 | Deferred Tools（本篇）|

下一阶段：[`04-modules/`](../04-modules/) —— MCP、Evals、Graph、Logfire 等配套模块。
