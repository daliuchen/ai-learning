# LangGraph 06：Human-in-the-loop 与 Time-travel

> 有了 `interrupt` 和 checkpointer，图可以在任意节点停下来，把当前状态交给前端，等人审核、修改或批准之后，再用 `Command` 接着跑。凡是需要"AI 先干、人来兜底"的应用，都离不开这套机制。这篇讲几种暂停方式、恢复时的规则、Time-travel，以及怎么接到一个 Web 后端上。

版本：以 `langgraph 1.2.19` 为准。静态断点（`interrupt_before` / `interrupt_after`）官方定位为调试工具，人在回路用节点内的 `interrupt()`；用 `create_agent` 时可以直接挂 `HumanInTheLoopMiddleware`。

---

## 1. 什么时候要 HITL

- 高风险操作：删数据、发邮件、付款，必须有人审
- 模型拿不准时：confidence 低，交给人选
- 多步任务：每到一个里程碑确认一次
- 收集训练样本：人改过的结果自动进数据集

---

## 2. 四种实现方式

### 2.1 编译时 interrupt_before / interrupt_after（静态断点，仅调试）

```python
app = g.compile(
    checkpointer=memory,
    interrupt_before=["dangerous_action"],
    interrupt_after=["plan"],
)
```

执行到这些 node 之前或之后会自动暂停。有几点要注意：停下时 `invoke` 的返回值和正常跑完长得一样，没有 `__interrupt__` 键，只能靠 `app.get_state(cfg).next` 非空来判断停住了；`interrupt_after` 设在最后一个节点上等于没设。官方明确说静态断点不推荐用于人在回路，它更适合在 Studio 或测试里单步查看 state。

### 2.2 节点内 `interrupt()`（动态，推荐）

在节点里调用 `interrupt()`，传入要展示给人的 payload，返回值就是人的回复：

```python
from langgraph.types import interrupt, Command

def review(state):
    answer = interrupt({
        "question": "AI 建议如下，是否批准？",
        "draft": state["draft"],
    })
    # answer 是用户传回的值（继续执行时）
    return {"final": answer}
```

### 2.3 update_state + 手动恢复

最底层的做法：在外面调 `app.get_state()` / `app.update_state(...)`，然后 `invoke(None, ...)` 继续。前三种方式可以组合使用。

### 2.4 create_agent 的 HumanInTheLoopMiddleware

如果 agent 是用 `create_agent` 搭的，审批节点不用自己写：

```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(
    model, tools=[send_email, read_inbox],
    middleware=[HumanInTheLoopMiddleware(interrupt_on={
        "send_email": True,                        # approve / edit / reject / respond 都允许
        "read_inbox": False,                       # 不需要审批
    })],
    checkpointer=InMemorySaver(),                  # 必须有 checkpointer
)
```

决策有 `approve` / `edit` / `reject` / `respond` 四种。要拒绝一个带副作用的动作，用 `reject`；`respond` 的内容会被当作一次成功的工具结果交给模型，用错了模型会以为事情办成了。恢复同样用 `Command(resume=...)`。

---

## 3. 第一个 HITL：审批工具调用

```python
import os
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件"""
    return f"邮件已发给 {to}"

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash")).bind_tools([send_email])

def agent(state):
    return {"messages": [model.invoke(state["messages"])]}

g = StateGraph(State)
g.add_node("agent", agent)
g.add_node("tools", ToolNode([send_email]))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", tools_condition)
g.add_edge("tools", "agent")

memory = InMemorySaver()
# ⚠️ 工具节点前自动暂停
app = g.compile(checkpointer=memory, interrupt_before=["tools"])
```

跑起来：

```python
cfg = {"configurable": {"thread_id": "t1"}}

# 1. 第一次跑
for ev in app.stream({"messages": [("human", "发邮件给 boss@x.com 标题 '请假'")]}, config=cfg):
    print(ev)
# 输出 agent 节点结果（含 tool_calls），但 tools 节点没执行 — 已停下
# （注意：这是静态断点，stream 里不会出现有内容的 __interrupt__，要靠 get_state(cfg).next 判断）

# 2. 检查
snap = app.get_state(cfg)
print("next nodes:", snap.next)         # ('tools',)
print("待执行的工具:", snap.values["messages"][-1].tool_calls)

# 3. 人审通过 → 续跑（None 输入表示从断点继续）
for ev in app.stream(None, config=cfg):
    print(ev)
```

如果要否决这次工具调用，用 `update_state` 塞一条 ToolMessage 进去：

```python
last = app.get_state(cfg).values["messages"][-1]
app.update_state(cfg, {
    "messages": [ToolMessage(content="人工拒绝", tool_call_id=last.tool_calls[0]["id"])],
}, as_node="tools")
```

然后 `app.invoke(None, config=cfg)` 继续，模型会看到"拒绝"消息再作答。

---

## 4. interrupt() + Command（推荐）

`interrupt` 把信息抛出来，等用户回复：

```python
from langgraph.types import interrupt, Command

def collect_feedback(state):
    decision = interrupt({
        "task": "请审核以下回答",
        "answer": state["draft"],
    })
    return {"final": decision}

g.add_node("review", collect_feedback)
```

第一次跑：

```python
out = app.invoke({"draft": "AI 起草的回答..."}, config=cfg)
# 此时 out 里包含 __interrupt__ 字段
print(out)
# {'__interrupt__': [Interrupt(value={'task': '请审核以下回答', 'answer': '...'})], ...}
```

拿到用户输入后，用 `Command(resume=...)` 续跑：

```python
final = app.invoke(Command(resume="批准"), config=cfg)
print(final)
```

新版推荐给 `invoke` 加上 `version="v2"`，返回的是 `GraphOutput`：`.value` 是 state，`.interrupts` 是 `Interrupt` 元组（没中断时为空），不用再去 state 字典里找 `__interrupt__` 键：

```python
r = app.invoke({"draft": "..."}, cfg, version="v2")
print(r.interrupts[0].value)                 # 要展示给人的 payload
r = app.invoke(Command(resume="批准"), cfg, version="v2")
print(r.value)
```

有一条规则必须记住：**恢复时，被中断的节点会从头重跑**（这一次 `interrupt()` 直接返回 resume 的值）。由此带来几条约束：

- 副作用（发邮件、扣款）要放在 `interrupt()` 之后，或者拆到单独的节点里
- 不要用宽泛的 `try/except` 包住 `interrupt()`，它靠抛异常来暂停
- 同一节点里有多个 `interrupt()` 时，调用顺序必须稳定，resume 值是按顺序匹配的
- 传给 `interrupt()` 的值要能序列化

和 `interrupt_before` 相比，这种写法可以在任意 node 的任意位置暂停，还能带上任意 payload，灵活得多。

---

## 5. Time-travel：回到历史改变路线

```python
hist = list(app.get_state_history(cfg))   # 最新的在最前
old = hist[3]   # 想回去的那个 checkpoint

# 改个值：update_state 返回一个新的（分叉出来的）checkpoint 配置
new_cfg = app.update_state(old.config, {"some_field": "new value"})

# 从分叉点重跑
app.invoke(None, config=new_cfg)
```

几个用得上的场景：用户后悔了，回到上一步重选；调试时手动改 state 看结果；采集训练数据时在关键点分出多个分支。

---

## 6. 把 HITL 接到前端：常见架构

```
前端                  后端 API                   LangGraph
 │                        │                          │
 │── POST /chat ─────────▶│invoke(input)             │
 │                        │─────────────────────────▶│
 │                        │      停在 interrupt      │
 │                        │◀─────────────────────────│
 │◀── 200 + interrupt ────│                          │
 │   （前端显示审核 UI）  │                          │
 │                        │                          │
 │── POST /resume ───────▶│invoke(Command(resume))   │
 │                        │─────────────────────────▶│
 │                        │      返回最终结果        │
 │                        │◀─────────────────────────│
 │◀── 显示最终结果 ───────│                          │
```

后端靠 `thread_id` 把前后两次请求关联起来：前端发起新对话时生成一个 UUID，之后每次请求都带上。

FastAPI 例子：

```python
@app.post("/chat")
def chat(req: ChatReq):
    cfg = {"configurable": {"thread_id": req.thread_id}}
    result = lang_app.invoke({"messages": [("human", req.text)]}, config=cfg)
    if "__interrupt__" in result:
        return {"need_review": True, "data": result["__interrupt__"][0].value}
    return {"answer": result["messages"][-1].content}

@app.post("/resume")
def resume(req: ResumeReq):
    cfg = {"configurable": {"thread_id": req.thread_id}}
    result = lang_app.invoke(Command(resume=req.decision), config=cfg)
    return {"answer": result["messages"][-1].content}
```

---

## 7. 多次 interrupt

一个 graph 里可以中断多次：

```python
def step1(s): 
    a = interrupt({"q": "审核步骤1"})
    return {"a": a}

def step2(s): 
    b = interrupt({"q": "审核步骤2"})
    return {"b": b}
```

每次 `Command(resume=...)` 只会跑到下一个 interrupt。多个并行节点同时中断时，`resume` 可以传一个 `{interrupt_id: 值}` 字典，按 id 分别恢复。

---

## 8. 流式 + HITL

stream 会一直跑到 interrupt 处停下：

```python
for ev in app.stream({"...": "..."}, config=cfg, stream_mode="updates"):
    if "__interrupt__" in ev:
        print("等待审核:", ev["__interrupt__"][0].value)
        break
```

拿到回复后再 `stream(Command(resume=...))`。

---

## 9. 本章 demo

完整代码在 [`demos/langgraph/06_hitl.py`](../../demos/langgraph/06_hitl.py)：

```python
# demos/langgraph/06_hitl.py
import os
from typing_extensions import Annotated, TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件"""
    return f"邮件已发给 {to}"

class S(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash")).bind_tools([send_email])

def agent(state):
    return {"messages": [model.invoke(state["messages"])]}

def human_approval(state):
    last = state["messages"][-1]
    if not last.tool_calls:
        return {}
    decision = interrupt({
        "question": "是否批准以下工具调用？",
        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in last.tool_calls],
    })
    if decision != "yes":
        return {"messages": [
            ToolMessage(content="人工拒绝", tool_call_id=c["id"])
            for c in last.tool_calls
        ]}
    return {}

def needs_tool(state):
    return "tools" if state["messages"][-1].tool_calls else END

g = StateGraph(S)
g.add_node("agent", agent)
g.add_node("review", human_approval)
g.add_node("tools", ToolNode([send_email]))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", needs_tool, {"tools": "review", END: END})
def after_review(state):
    last = state["messages"][-1]
    # 被拒绝时最后一条是"人工拒绝"的 ToolMessage，回到 agent 让模型看到拒绝
    return "agent" if isinstance(last, ToolMessage) else "tools"

g.add_conditional_edges("review", after_review, {"tools": "tools", "agent": "agent"})
g.add_edge("tools", "agent")

memory = InMemorySaver()
app = g.compile(checkpointer=memory)

cfg = {"configurable": {"thread_id": "demo"}}
out = app.invoke({"messages": [("human", "给 boss@x.com 发邮件请假")]}, config=cfg)
print(out)
# 应当看到 __interrupt__

# 模拟用户审批
final = app.invoke(Command(resume="yes"), config=cfg)
print(final["messages"][-1].content)
```

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| interrupt 后再次 invoke 又从第一步开始 | 没传同一个 thread_id | 用同一个 cfg |
| Command(resume=...) 报错 | compile 时没挂 checkpointer | 必须挂 |
| 多次 interrupt 拿不到后面的 payload | stream 没在中断处停下 | 每次出现 `__interrupt__` 就停 |
| update_state 改了没生效 | `as_node` 与 reducer 不匹配 | 仔细核对 reducer 的合并行为 |
| 跨进程 HITL（异步审批） | thread state 需要共享 | 用 Postgres/Sqlite Checkpointer |

---

下一篇：[LangGraph 07：Streaming 的各种模式](07-streaming.md)
