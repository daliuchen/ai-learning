# LangChain 08：Agents（旧 API 与 LangGraph 迁移）

> **一句话**：传统的 `AgentExecutor` 已被官方标记为 legacy，所有新项目应该用 **LangGraph 的 `create_react_agent`**。本章既讲老 API 帮你看懂老代码，也讲为什么以及怎样迁到 LangGraph。

---

## 1. Agent 是什么

Agent = "让 LLM 决定调什么工具，然后看到结果继续决定下一步"。

它的本质就是上一章末尾那个 ReAct 循环。早期 LangChain 提供了 `AgentExecutor` 来封装这个循环。

---

## 2. 老 API：AgentExecutor + create_xxx_agent

### 2.1 创建一个 ReAct agent（旧式）

```python
from langchain import hub
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults

tools = [TavilySearchResults(max_results=3)]
prompt = hub.pull("hwchase17/openai-tools-agent")

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
agent = create_openai_tools_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

print(executor.invoke({"input": "LangChain 最新版本是多少？"}))
```

### 2.2 还有这些 create_*_agent

| 函数 | 适用模型 |
|------|----------|
| `create_openai_tools_agent` | 支持 OpenAI tool calling 的模型 |
| `create_tool_calling_agent` | 通用 tool calling（推荐替代上面的） |
| `create_react_agent`（**old**） | 纯文本 ReAct |
| `create_self_ask_with_search_agent` | 特定提示风格 |
| `create_xml_agent` | Claude 友好的 XML 输出格式 |
| `create_sql_agent` | SQL toolkit 专用 |
| `create_pandas_dataframe_agent` | pandas 探索 |

注意 LangChain 老版本里有同名 `create_react_agent`，与 **LangGraph 的 `create_react_agent` 不是同一个东西**，后者才是现代版。

### 2.3 AgentExecutor 主要参数

```python
AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,
    max_iterations=10,              # 最多循环几轮
    max_execution_time=60,          # 最多跑多久
    early_stopping_method="force",  # 超限后强制结束
    handle_parsing_errors=True,     # 解析失败时让模型重试
    return_intermediate_steps=True, # 返回中间步骤
)
```

返回结构：

```python
{
    "input": "...",
    "output": "最终答案",
    "intermediate_steps": [
        (AgentAction(...), tool_observation_str),
        ...
    ],
}
```

---

## 3. 为什么官方推荐迁到 LangGraph

`AgentExecutor` 的局限：

1. **黑盒循环**：你只能塞 callback，循环本身的细节不可控
2. **难加状态**：想存中间变量？得自己想办法
3. **不能 Human-in-loop**：每一步要人确认？做不到
4. **不能断点续跑**：进程挂了，前面 5 步白跑
5. **流式语义弱**：无法细粒度控制每步流式

LangGraph 把循环变成**显式图**，每一步是 node，你完全掌控。

---

## 4. LangGraph 的 create_react_agent（推荐用法）

LangGraph 内置了与 LangChain 老 API 命名相同但能力强很多的 `create_react_agent`：

```python
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city} 晴"

model = ChatOpenAI(model="gpt-4o-mini")
agent = create_react_agent(model, [get_weather])

result = agent.invoke({"messages": [("human", "北京天气？")]})
print(result["messages"][-1].content)
```

短短 5 行，得到一个**支持持久化、流式、人在回路、中断恢复**的 Agent。

第 15 章会详细展开 LangGraph，本章只点到为止。

---

## 5. 新旧对照表

| 能力 | AgentExecutor | LangGraph create_react_agent |
|------|---------------|------------------------------|
| 工具调用循环 | ✅ | ✅ |
| Verbose / 中间步骤 | ✅ verbose=True | ✅ stream() / events |
| 持久化 / Thread | ❌ | ✅ checkpointer |
| Human-in-loop | ❌ | ✅ interrupt |
| Time-travel 回放 | ❌ | ✅ |
| 流式细粒度 | ❌ chunk 不可控 | ✅ astream_events |
| 状态扩展 | ❌ | ✅ 自定义 schema |
| 多 Agent 编排 | 难 | ✅ |
| 部署 / Studio | 无 | ✅ LangGraph Cloud |

---

## 6. 把旧 AgentExecutor 迁到 LangGraph

### 6.1 旧代码

```python
from langchain import hub
from langchain.agents import AgentExecutor, create_tool_calling_agent

prompt = hub.pull("hwchase17/openai-tools-agent")
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
result = executor.invoke({"input": "..."})
print(result["output"])
```

### 6.2 等价新代码

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,
    tools=tools,
    state_modifier="你是一个 helpful assistant。",   # 可选 system prompt
)

result = agent.invoke({"messages": [("human", "...")]})
print(result["messages"][-1].content)
```

少了 prompt 拉取一步，因为 LangGraph 用消息列表作为状态，不需要 ReAct 文本模板。

### 6.3 拿中间步骤

```python
for chunk in agent.stream({"messages": [...]}, stream_mode="updates"):
    for node, value in chunk.items():
        print(node, "→", value)
```

---

## 7. 何时还应该用 AgentExecutor

唯一保留它的理由：**老代码无法迁移**。新项目无脑选 LangGraph。

---

## 8. SQL Agent 与 Pandas Agent

这两个特殊 Toolkit Agent 在数据探索类应用里很常用：

```python
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

db = SQLDatabase.from_uri("sqlite:///chinook.db")
agent = create_sql_agent(llm, db=db, verbose=True)
agent.invoke("有多少张表？")
```

`create_sql_agent` 内部会做：列表表 → 看 schema → 写 SQL → 执行 → 总结。

新写法用 LangGraph 自己组合 SQL Toolkit 即可，更可控。

---

## 9. demo

```python
# demos/langchain/08_agent_compare.py
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import create_react_agent

load_dotenv()

@tool
def get_weather(city: str) -> str:
    """查询天气"""
    return f"{city} 晴 25℃"

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
tools = [get_weather]

# ===== 老 API =====
print("\n----- 旧 AgentExecutor -----")
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 helpful 助手"),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])
agent = create_tool_calling_agent(llm, tools, prompt)
ex = AgentExecutor(agent=agent, tools=tools, verbose=True)
print(ex.invoke({"input": "北京天气？"})["output"])

# ===== 新 API =====
print("\n----- 新 LangGraph -----")
graph = create_react_agent(llm, tools)
out = graph.invoke({"messages": [("human", "北京天气？")]})
print(out["messages"][-1].content)
```

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `create_react_agent` 找不到 | 装的是 langchain 旧版 | `from langgraph.prebuilt import create_react_agent` |
| `AgentExecutor` 不停循环 | 没设 `max_iterations` | 加上限 / 切 LangGraph |
| `intermediate_steps` 为空 | 没开 `return_intermediate_steps=True` | 显式开 |
| stream 拿不到 token | AgentExecutor 流式语义弱 | 切 `astream_events` / LangGraph |

---

## 11. 本章 demo

[`demos/langchain/08_agent_compare.py`](../../demos/langchain/08_agent_compare.py)

下一篇：[09-memory.md](09-memory.md)
