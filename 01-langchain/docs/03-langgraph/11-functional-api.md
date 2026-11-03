# LangGraph 11：Functional API（@entrypoint / @task）

> Functional API 让你用装饰器写出跟普通 Python 函数差不多的代码，LangGraph 在背后把它当成 graph 执行，持久化、HITL、流式这些能力一样不少。它和 StateGraph 跑在同一个引擎上，只是写法不同。这篇讲它的基本用法、并发、持久化与中断，以及什么时候该选它。

版本：以 `langgraph 1.2.19` 为准；`MemorySaver` 是 `InMemorySaver` 的别名。

---

## 1. Why Functional API

StateGraph 是声明式的：你画图，定义 node 和 edge。Functional API 是命令式的：你写一个普通 Python 函数，在里面调用其他 `@task`，控制流就是 `if/for/await`。

| 风格 | 写法 | 适合 |
|------|------|------|
| StateGraph | 画图，节点+边 | 流程清晰、需要可视化 |
| Functional | 函数式，直接代码 | 控制流复杂、习惯 async/await |

两者功能等价，也能混用：entrypoint 里可以调一个 StateGraph，反过来也行。

---

## 2. 最小例子

```python
from langgraph.func import entrypoint, task

@task
def step_a(x: int) -> int:
    return x + 1

@task
def step_b(x: int) -> int:
    return x * 2

@entrypoint()
def workflow(x: int) -> int:
    y = step_a(x).result()       # .result() 阻塞拿结果（同步）
    z = step_b(y).result()
    return z

print(workflow.invoke(3))        # 8
```

`@task` 把函数变成可重放、可缓存的任务；`@entrypoint()` 定义入口，函数签名就是 invoke 的输入和输出。`step_a(x)` 返回的是一个 future，调 `.result()` 才拿到值。整个 workflow 会被当成 graph 执行，跨进程也能恢复。

---

## 3. 并发：不 `.result()` 就是 future

```python
@entrypoint()
def parallel(items: list[int]) -> list[int]:
    futures = [step_a(i) for i in items]   # 并行发起
    return [f.result() for f in futures]
```

效果和 `asyncio.gather` 差不多，所有 task 并行执行。

---

## 4. 持久化与 HITL

`@entrypoint` 可以接收 `checkpointer`：

```python
from langgraph.checkpoint.memory import InMemorySaver

@entrypoint(checkpointer=InMemorySaver())
def workflow(x: int) -> int:
    a = step_a(x).result()
    b = step_b(a).result()
    return b

workflow.invoke(3, config={"configurable": {"thread_id": "t1"}})
```

每个 `@task` 都是一个 checkpoint 点，进程挂了重启后，会从上次完成的 task 结果接着跑。

interrupt 也能用：

```python
from langgraph.types import interrupt, Command

@entrypoint(checkpointer=InMemorySaver())
def workflow(x):
    a = step_a(x).result()
    user_choice = interrupt({"q": "继续吗？", "current": a})
    if user_choice == "yes":
        b = step_b(a).result()
        return b
    return a
```

---

## 5. 异步

```python
@task
async def fetch(url: str) -> str:
    ...

@entrypoint()
async def aworkflow(urls: list[str]) -> list[str]:
    results = await asyncio.gather(*[fetch(u) for u in urls])
    return results
```

`@entrypoint` 和 `@task` 都支持异步。

---

## 6. 与 StateGraph 混用

`@task` 里可以直接调用编译好的 StateGraph：

```python
sub_app = build_subgraph().compile()

@task
def sub(input):
    return sub_app.invoke(input)
```

反过来，StateGraph 的 node 里也可以调用 `@entrypoint` workflow。

---

## 7. 流式

```python
for ev in workflow.stream(3, stream_mode="updates"):
    print(ev)
```

每个 `@task` 完成时产生一条 update，最后还有一条以 entrypoint 函数名为 key 的最终返回值。实际输出是 `[{'step_a': 4}, {'step_b': 8}, {'workflow': 8}]`。

---

## 8. Functional ReAct

LangGraph 文档给了一个 Functional 版的 ReAct 模板：

```python
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.func import entrypoint, task

@task
def call_model(messages):
    return model.invoke(messages)

@task
def call_tool(tool_call):
    return by_name[tool_call["name"]].invoke(tool_call["args"])

@entrypoint(checkpointer=InMemorySaver())
def react(messages):
    while True:
        resp = call_model(messages).result()
        messages.append(resp)
        if not resp.tool_calls:
            return resp
        for tc in resp.tool_calls:
            result = call_tool(tc).result()
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
```

读起来比 StateGraph 版直观，但没有了图的可视化和显式条件边。我会在控制流比较绕、用图表达反而别扭的时候选它。

---

## 9. 本章 demo

完整代码：[`demos/langgraph/11_functional.py`](../../demos/langgraph/11_functional.py)

```python
# demos/langgraph/11_functional.py
import asyncio
from dotenv import load_dotenv
from langgraph.func import entrypoint, task
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI

load_dotenv()
model = ChatOpenAI(model="qwen-flash")

@task
def joke(topic: str) -> str:
    return model.invoke(f"讲一个关于 {topic} 的短笑话").content

@task
def poem(topic: str) -> str:
    return model.invoke(f"写一首关于 {topic} 的两行诗").content

@entrypoint(checkpointer=InMemorySaver())
def workflow(topic: str) -> dict:
    f1 = joke(topic)
    f2 = poem(topic)
    return {"joke": f1.result(), "poem": f2.result()}

cfg = {"configurable": {"thread_id": "t1"}}
print(workflow.invoke("猫", config=cfg))

for ev in workflow.stream("狗", config={"configurable": {"thread_id": "t2"}}, stream_mode="updates"):
    print(ev)
```

---

## 10. 何时选 Functional API

| 场景 | 推荐 |
|------|------|
| 简单线性流程 | Functional |
| 大量条件分支 + 循环 | Functional |
| 多 Agent 显式编排 | StateGraph |
| 需要可视化图 / 给 PM 看 | StateGraph |
| 需要 Studio UI 里看节点拓扑 | StateGraph（Functional 没有静态的节点/边可画） |
| HITL / 持久化 | 都行 |

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 调 `.result()` 报 `AttributeError` | 函数没加 `@task`，返回的是普通值 | 用 `@task` 装饰 |
| 出错后重新 invoke，已成功的 task 又跑了一遍 | 传了新输入就是一次新运行；或者没挂 checkpointer | 挂 checkpointer，用同一个 `thread_id` 调 `invoke(None, cfg)` 恢复，已完成的 task 直接用保存的结果 |
| 流式拿不到 token | `updates` 模式的粒度是 task | 要 token 级就用 `stream_mode="messages"` |

---

下一篇：[LangGraph 12：部署与 Studio](12-deployment.md)
