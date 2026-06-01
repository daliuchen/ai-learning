# State vs Context：不是所有状态都该进上下文

> **一句话**：Agent 要记的东西分两类——「模型必须**看到**才能决策的」放上下文，「程序**记着就行**、模型用时才需要」放外部 state（变量、数据库、scratchpad），按需才注入。把待办清单、中间变量反复挂在窗口里，是上下文累积的隐形大头。

---

## 1. 一个根本区分：看到 vs 记着

新手 Agent 最常见的浪费：把所有状态都当对话历史塞进上下文。但很多状态模型**根本不需要每步都看到**：

| 状态 | 该放哪 | 理由 |
|------|--------|------|
| 当前子目标 / 下一步该做什么 | 上下文 | 模型每步决策都要它 |
| 已确认的关键事实 | 上下文 | 直接影响下一步推理 |
| 完整待办清单（20 项，已完成 15 项） | 外部 state | 模型只需知道「下一项」，不必每步重读全部 |
| 计数器 / 循环次数 / 重试次数 | 外部 state | 纯程序逻辑，模型不需要看 |
| 大段中间产物（抓回的网页全文） | 外部 state（句柄） | 用时才取回（见 [02-tool-results.md](./02-tool-results.md)） |
| 工具调用的原始日志 | 外部 state | 调试用，模型不需要 |

判断口诀：**「这个东西，模型这一步推理时真的要读它的内容吗？」** 否 → 进外部 state，别占窗口。

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

scratchpad（草稿纸）是一块 Agent 能读写、但**不必每步全量进上下文**的外部存储。Agent 把中间笔记、计划、已知事实写进去，需要时再读相关片段进窗口。

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

好处：Agent 跑到第 30 步还能 `scratchpad_read("初始需求")` 把最初目标拉回来——而不靠把它一直挂在窗口里（那样早被后面的轨迹淹没了）。

---

## 3. LangGraph 的 state：一等公民的外部状态

LangGraph 把这个理念做成了框架核心：图里流转的不是「一条 messages」，而是一个**结构化 state 对象**。哪些字段进 LLM、哪些只在程序里流转，由你显式控制。

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage
import operator

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]  # 进 LLM 的对话上下文
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

精髓：**`messages` 字段才是上下文窗口，其他字段（todo / retry_count / artifacts）是程序状态，模型看不到，也不该看到。** LangGraph 让你把这条边界画得清清楚楚。

---

## 4. State 外置的三类典型对象

| 对象 | 为什么外置 | 上下文里留什么 |
|------|-----------|----------------|
| **待办 / 计划清单** | 几十项全量重发是纯浪费 | 只留「当前项」+「剩余几项」 |
| **大中间产物**（网页、文件、查询结果） | 体积大、不一定再用 | 摘要 + 句柄，按需取回 |
| **控制流变量**（循环数、状态机、标志位） | 模型决策完全不需要 | 什么都不留，纯程序持有 |

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

- **默认外置**：新增一个要"记住"的东西时，先问"模型每步推理需要读它吗"，默认放 state，确实需要看再放上下文。
- **上下文只放"目录 + 当前焦点"**：清单留当前项，产物留句柄目录，让模型知道"有什么、去哪取"，而非把全文常驻。
- **控制流绝不进窗口**：计数、标志、状态机切换是程序的事，一个 token 都别花。
- **用框架画边界**：LangGraph 的 state schema、或自己的 `Scratchpad`，把"进窗口"和"留程序"显式分开，避免随手 append 导致窗口失控。

这正是上一篇隔离窗口之间的协调方式——子 Agent 不靠共享上下文，而靠读写共享的外部 state 传递信息。

---

## 下一步

- [06-failure-modes.md](./06-failure-modes.md)：state 用不好会怎样——脏 state 导致的失败模式
- [02-tool-results.md](./02-tool-results.md)：句柄机制是 state 外置在工具结果上的应用
- [04-isolation.md](./04-isolation.md)：隔离窗口靠外部 state 协调
- 跨章：[../05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md) 上下文确实满了之后的压缩裁剪
