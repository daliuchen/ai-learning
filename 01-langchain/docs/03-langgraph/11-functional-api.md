# LangGraph 11：Functional API（@entrypoint / @task）

> **一句话**：Functional API 让你用 Python 装饰器写"看起来像普通函数"的代码，由 LangGraph 自动转换成 graph，**同样具备持久化、HITL、流式所有能力**。它和 StateGraph 是同一引擎的两种风格。

---

## 1. Why Functional API

StateGraph 是声明式：你画图、定义 node 和 edge。
Functional API 是命令式：你写一个普通 Python 函数，里面调用其他 `@task`，控制流就是 `if/for/await`。

两种风格的对比：

| 风格 | 写法 | 适合 |
|------|------|------|
| StateGraph | 画图，节点+边 | 流程清晰、需要可视化 |
| Functional | 函数式，直接代码 | 控制流复杂、习惯 async/await |

两者**功能完全等价**，可混用：一个 entrypoint 里可以调一个 StateGraph，反之亦然。

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

注意：

- `@task` 让函数变成"可重放/可缓存"的任务
- `@entrypoint()` 定义入口，签名就是 invoke 的 input/output
- `step_a(x)` 返回的是 future，`.result()` 拿结果
- 整个 workflow 被自动当成 graph 执行，跨进程可恢复

---

## 3. 并发：不 `.result()` 就是 future

```python
@entrypoint()
def parallel(items: list[int]) -> list[int]:
    futures = [step_a(i) for i in items]   # 并行发起
    return [f.result() for f in futures]
```

类似 `asyncio.gather`，所有 task 并行执行。

---

## 4. 持久化与 HITL

`@entrypoint` 接受 `checkpointer`：

```python
from langgraph.checkpoint.memory import MemorySaver

@entrypoint(checkpointer=MemorySaver())
def workflow(x: int) -> int:
    a = step_a(x).result()
    b = step_b(a).result()
    return b

workflow.invoke(3, config={"configurable": {"thread_id": "t1"}})
```

每个 `@task` 是一个 checkpoint 点，进程挂了重启从上次 task 结果继续。

interrupt 也能用：

```python
from langgraph.types import interrupt, Command

@entrypoint(checkpointer=MemorySaver())
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

`@entrypoint` 与 `@task` 都支持异步。

---

## 6. 与 StateGraph 混用

`@task` 可以直接调用一个编译后的 StateGraph：

```python
sub_app = build_subgraph().compile()

@task
def sub(input):
    return sub_app.invoke(input)
```

反之，StateGraph 的 node 可以调 `@entrypoint` workflow。

---

## 7. 流式

```python
for ev in workflow.stream(3, stream_mode="updates"):
    print(ev)
```

每个 `@task` 完成产生一个 update。

---

## 8. Functional ReAct

LangGraph 给出了 Functional 版的 ReAct 模板：

```python
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.func import entrypoint, task

@task
def call_model(messages):
    return model.invoke(messages)

@task
def call_tool(tool_call):
    return by_name[tool_call["name"]].invoke(tool_call["args"])

@entrypoint(checkpointer=MemorySaver())
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

是不是比 StateGraph 版直观？但失去了 graph 可视化与显式条件边。**适合代码控制流复杂的场景**。

---

## 9. demo

```python
# demos/langgraph/11_functional.py
import asyncio
from dotenv import load_dotenv
from langgraph.func import entrypoint, task
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI

load_dotenv()
model = ChatOpenAI(model="gpt-4o-mini")

@task
def joke(topic: str) -> str:
    return model.invoke(f"讲一个关于 {topic} 的短笑话").content

@task
def poem(topic: str) -> str:
    return model.invoke(f"写一首关于 {topic} 的两行诗").content

@entrypoint(checkpointer=MemorySaver())
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
| 需要 Studio UI 调试 | StateGraph（Functional 支持有限） |
| HITL / 持久化 | 都行 |

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `.result()` 卡住 | task 没装饰器 | `@task` 装饰 |
| 多次 invoke 重新执行已完成 task | 没 checkpointer | 加上 |
| 异常重启后从头跑 | 错误打断了 task | 用 try 包 task 调用 |
| 流式拿不到 token | Functional 颗粒度按 task | 想要 token 级用 `astream_events` |

---

## 12. 本章 demo

[`demos/langgraph/11_functional.py`](../../demos/langgraph/11_functional.py)

下一篇：[12-deployment.md](12-deployment.md)
