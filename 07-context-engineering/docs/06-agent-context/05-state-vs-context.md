# State vs Context：不是所有状态都该进上下文

> Agent 要记的东西分两类。模型做决策时必须读到的，放进上下文；程序记着就行、模型偶尔才用的，放到外部 state（变量、数据库、scratchpad），用时再注入。待办清单、中间变量每一步都挂在窗口里，是上下文累积里很容易被忽略的一大块。

---

## 1. 先分清：模型要看到，还是程序记着就行

刚上手写 Agent 时最常见的浪费，是把所有状态都当成对话历史塞进上下文。其实很多状态模型并不需要每一步都看到：

| 状态 | 该放哪 | 理由 |
|------|--------|------|
| 当前子目标 / 下一步该做什么 | 上下文 | 模型每步决策都要它 |
| 已确认的关键事实 | 上下文 | 直接影响下一步推理 |
| 完整待办清单（20 项，已完成 15 项） | 外部 state | 模型只需知道「下一项」，不必每步重读全部 |
| 计数器 / 循环次数 / 重试次数 | 外部 state | 纯程序逻辑，模型不需要看 |
| 大段中间产物（抓回的网页全文） | 外部 state（句柄） | 用时才取回（见[工具结果的上下文管理](./02-tool-results.md)） |
| 工具调用的原始日志 | 外部 state | 调试用，模型不需要 |

判断时问自己一句：模型这一步推理，要不要读这个东西的内容？不要，就放外部 state，别占窗口。

```python
# ❌ 反模式：把整个待办清单每步重发进上下文
messages.append({"role": "user", "content":
    "待办：\n[x]调研A\n[x]调研B\n[x]...(共15项已完成)\n[ ]写第16项\n[ ]..."})
# 15 项已完成的内容每步都重发，纯噪声，还稀释注意力

# ✅ 正解：清单存外部 state，上下文只放「当前这一项」
todo.mark_done("调研B")
current = todo.next_pending()          # 程序逻辑，不进窗口
messages.append({"role": "user", "content": f"当前任务：{current.title}"})
```

---

## 2. Scratchpad 模式：把思考外置成可读写的草稿纸

scratchpad（草稿纸）是一块 Agent 能读能写、但不必每一步都整份放进上下文的外部存储。Agent 把中间笔记、计划、已知事实写进去，需要时再把相关的片段读回窗口。

```python
class Scratchpad:
    """Agent 的外部草稿纸：读写不直接占满上下文。"""
    def __init__(self):
        self._notes: dict[str, str] = {}

    def write(self, key: str, value: str):      # 工具：让 Agent 存笔记
        self._notes[key] = value

    def read(self, key: str) -> str:            # 工具：用时才读回
        return self._notes.get(key, "(无)")

    def keys(self) -> list[str]:                # 上下文里只放「目录」，不放全文
        return list(self._notes.keys())

# 注册成工具：scratchpad_write / scratchpad_read / scratchpad_keys
# 上下文里常驻的只是「草稿纸里有哪些条目」，全文按需 read 回来
```

这样 Agent 跑到第 30 步，还能用 `scratchpad_read("初始需求")` 把最初的目标拉回来。要是靠把目标一直挂在窗口里，早就被后面的轨迹淹没了。

---

## 3. LangGraph 的 state：框架内建的外部状态

LangGraph 把这个思路做成了框架的核心。图里流转的是一个结构化的 state 对象，不只是一列 messages。哪些字段送进 LLM、哪些只在程序里流转，由你显式决定。

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # 进 LLM 的对话上下文（add_messages 会把 ("user", ...) 元组转成消息对象）
    todo: list[str]            # 待办清单 —— 程序读写，不必整个进 prompt
    retry_count: int           # 计数器 —— 纯控制流，模型永远看不到
    artifacts: dict[str, str]  # 落盘句柄 —— 按需注入

def planner(state: AgentState) -> dict:
    next_item = state["todo"][0] if state["todo"] else None
    # 关键：只把「当前这一项」塞进 messages，todo 全表留在 state 里
    return {"messages": [("user", f"执行：{next_item}")]}

def should_continue(state: AgentState) -> str:
    if state["retry_count"] > 3:    # 控制流判断，不消耗任何 token
        return END
    return "planner"

graph = StateGraph(AgentState)
graph.add_node("planner", planner)
# ... 边和条件略
```

只有 `messages` 字段是上下文窗口，todo、retry_count、artifacts 都是程序状态，模型看不到，也不该看到。用 LangGraph 写，这条边界会清楚地落在 state schema 里。

---

## 4. 适合外置的三类对象

| 对象 | 为什么外置 | 上下文里留什么 |
|------|-----------|----------------|
| 待办 / 计划清单 | 几十项全量重发是纯浪费 | 只留「当前项」+「剩余几项」 |
| 大中间产物（网页、文件、查询结果） | 体积大、不一定再用 | 摘要 + 句柄，按需取回 |
| 控制流变量（循环数、状态机、标志位） | 模型决策完全不需要 | 什么都不留，纯程序持有 |

```
       上下文窗口（贵、有限、模型每步重读）
       ┌─────────────────────────────────┐
       │ system │ 当前目标 │ 关键事实 │ 句柄目录 │
       └─────────────────────────────────┘
                      ↕ 按需读写
       外部 state（便宜、持久、模型看不到）
       ┌─────────────────────────────────┐
       │ 完整待办 │ 中间产物全文 │ 计数器 │ 日志 │
       └─────────────────────────────────┘
```

---

## 5. 落地准则

- 默认外置。新增一个要记住的东西时，先问模型每步推理是否需要读它，默认放 state，确实需要才进上下文。
- 上下文里只放目录和当前焦点。清单留当前项，产物留句柄目录，让模型知道有什么、去哪取，不必把全文常驻。
- 控制流不进窗口。计数、标志、状态机切换是程序的事，一个 token 都不用花。
- 用框架把边界画出来。LangGraph 的 state schema 或者自己写的 `Scratchpad`，都能把「进窗口」和「留在程序里」分开，避免随手 append 让窗口失控。

上一篇讲的隔离窗口之间也是这样协调的：子 Agent 不共享上下文，靠读写共享的外部 state 传递信息。

---

## 下一步

- [上下文相关的 Agent 失败模式](./06-failure-modes.md)：state 管得不好会出什么问题，比如脏 state 引起的失败
- [工具结果的上下文管理](./02-tool-results.md)：句柄机制是 state 外置在工具结果上的应用
- [上下文隔离](./04-isolation.md)：隔离的窗口靠外部 state 协调
- 跨章：[为什么要压缩上下文](../05-compaction/01-why-compact.md)，上下文确实满了以后怎么压缩裁剪
