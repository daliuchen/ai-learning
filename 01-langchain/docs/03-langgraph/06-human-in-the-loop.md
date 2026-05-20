# LangGraph 06：Human-in-the-loop 与 Time-travel

> **一句话**：LangGraph 的 `interrupt` + checkpointer 让你在任意节点暂停，把状态推给前端，等用户审/改/批，再用 `Command` 继续执行。这是构建"AI 助手 + 人工兜底"应用的核心能力。

---

## 1. 什么时候要 HITL

- **高风险操作**：删数据、发邮件、付款，必须人审
- **不确定时**：模型 confidence 低，让人选
- **多步任务**：每个里程碑点确认
- **训练样本**：人改 → 自动入数据集

---

## 2. 三种实现方式

### 2.1 编译时 interrupt_before / interrupt_after（旧/静态）

```python
app = g.compile(
    checkpointer=memory,
    interrupt_before=["dangerous_action"],
    interrupt_after=["plan"],
)
```

执行到这些 node 前/后会自动暂停。

### 2.2 节点内 `interrupt()`（新/动态）

LangGraph 0.2+ 推荐写法：在节点内调用 `interrupt()`，传入要展示给人的 payload，并接收人的回复：

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

最底层：在外面 `app.get_state()` / `app.update_state(...)` 然后 `invoke(None, ...)` 继续。三种方式互相组合用。

---

## 3. 第一个 HITL：审批工具调用

```python
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件"""
    return f"邮件已发给 {to}"

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model="gpt-4o-mini").bind_tools([send_email])

def agent(state):
    return {"messages": [model.invoke(state["messages"])]}

g = StateGraph(State)
g.add_node("agent", agent)
g.add_node("tools", ToolNode([send_email]))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", tools_condition)
g.add_edge("tools", "agent")

memory = MemorySaver()
# ⚠️ 工具节点前自动暂停
app = g.compile(checkpointer=memory, interrupt_before=["tools"])
```

跑起来：

```python
cfg = {"configurable": {"thread_id": "t1"}}

# 1. 第一次跑
for ev in app.stream({"messages": [("human", "发邮件给 boss@x.com 标题 '请假'")]}, config=cfg):
    print(ev)
# 输出 agent 节点结果（含 tool_calls），但 tools 节点没执行 — interrupted

# 2. 检查
snap = app.get_state(cfg)
print("next nodes:", snap.next)         # ('tools',)
print("待执行的工具:", snap.values["messages"][-1].tool_calls)

# 3. 人审通过 → 续跑（None 输入表示从断点继续）
for ev in app.stream(None, config=cfg):
    print(ev)
```

如果想**否决**这次工具调用，用 `update_state` 把消息改成 ToolMessage：

```python
last = app.get_state(cfg).values["messages"][-1]
app.update_state(cfg, {
    "messages": [ToolMessage(content="人工拒绝", tool_call_id=last.tool_calls[0]["id"])],
}, as_node="tools")
```

然后 `app.invoke(None, config=cfg)` 继续，模型会看到"拒绝"消息再作答。

---

## 4. interrupt() + Command（推荐）

`interrupt` 把信息抛出来等待用户：

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

获得用户输入后用 `Command(resume=...)` 续跑：

```python
final = app.invoke(Command(resume="批准"), config=cfg)
print(final)
```

这套写法比 `interrupt_before` 更灵活，因为可以**任意 node 任意时刻**调 `interrupt`，还能携带任意 payload。

---

## 5. Time-travel：回到历史改变路线

```python
hist = list(app.get_state_history(cfg))
old = hist[3]   # 想回去的那个 checkpoint

# 改个值
app.update_state(old.config, {"some_field": "new value"})

# 从这里重跑
app.invoke(None, config=old.config)
```

可以做：
- 用户后悔了，回到上一步重选
- 调试时手动改 state 看结果
- 训练数据采集：跑到关键点 fork 多个分支

---

## 6. 把 HITL 接到前端：典型架构

```
前端                后端 API                LangGraph
 │                    │                       │
 │── POST /chat ─────▶│ invoke(input)          │
 │                    │─────────────────────▶ │
 │                    │     state to interrupt │
 │                    │ ◀───────────────────── │
 │                    │ HTTP response 200 +   │
 │                    │ interrupt payload     │
 │ ◀────显示审核 UI ──│                       │
 │                    │                       │
 │── POST /resume ──▶│ invoke(Command(resume))│
 │                    │─────────────────────▶ │
 │                    │ final result          │
 │                    │ ◀───────────────────── │
 │ ◀── 显示最终结果 ──│                       │
```

后端要用 `thread_id` 关联前后两次请求（前端发起新对话时生成 UUID，每次都带）。

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

一个 graph 里可以多次中断：

```python
def step1(s): 
    a = interrupt({"q": "审核步骤1"})
    return {"a": a}

def step2(s): 
    b = interrupt({"q": "审核步骤2"})
    return {"b": b}
```

每次 `Command(resume=...)` 只续跑到下一个 interrupt。

---

## 8. 流式 + HITL

stream 一直跑到 interrupt 然后停：

```python
for ev in app.stream({"...": "..."}, config=cfg):
    if "__interrupt__" in ev:
        print("等待审核:", ev["__interrupt__"][0].value)
        break
```

再次 stream(Command(resume=...))。

---

## 9. demo

```python
# demos/langgraph/06_hitl.py
from typing_extensions import Annotated, TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
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

model = ChatOpenAI(model="gpt-4o-mini").bind_tools([send_email])

def agent(state):
    return {"messages": [model.invoke(state["messages"])]}

def human_approval(state):
    last = state["messages"][-1]
    if not last.tool_calls:
        return state
    decision = interrupt({
        "question": "是否批准以下工具调用？",
        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in last.tool_calls],
    })
    if decision != "yes":
        from langchain_core.messages import ToolMessage
        return {"messages": [
            ToolMessage(content="人工拒绝", tool_call_id=c["id"])
            for c in last.tool_calls
        ]}
    return state

def needs_tool(state):
    return "tools" if state["messages"][-1].tool_calls else END

g = StateGraph(S)
g.add_node("agent", agent)
g.add_node("review", human_approval)
g.add_node("tools", ToolNode([send_email]))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", needs_tool, {"tools": "review", END: END})
g.add_edge("review", "tools")
g.add_edge("tools", "agent")

memory = MemorySaver()
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
| interrupt 后再次 invoke 又走第一步 | 没传同一个 thread_id | 用同一个 cfg |
| Command(resume=...) 报错 | compile 没 checkpointer | 必须有 |
| 多次 interrupt 拿不到 prompt | stream 没等下次 | 每次 `__interrupt__` 出现立刻停 |
| update_state 改了但没生效 | `as_node` 与 reducer 不匹配 | 仔细看 reducer 行为 |
| 跨进程 HITL（异步审批） | thread state 必须共享 | 用 Postgres/Sqlite Checkpointer |

---

## 11. 本章 demo

[`demos/langgraph/06_hitl.py`](../../demos/langgraph/06_hitl.py)

下一篇：[07-streaming.md](07-streaming.md)
