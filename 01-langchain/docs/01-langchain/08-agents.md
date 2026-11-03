# LangChain 08：Agents（create_agent 与旧 API 迁移）

> 1.x 里构建 agent 的标准入口是 `langchain.agents.create_agent`。它返回一张 LangGraph 编译图，行为主要靠 `system_prompt`、`tools`、`middleware` 来定制。原来的 `AgentExecutor` 已经搬进 `langchain-classic`，`langgraph.prebuilt.create_react_agent` 也弃用了。网上大量教程还是旧写法，所以这篇新旧都讲：先讲 `create_agent`，再讲怎么读懂和迁移老代码。

版本：以 `langchain 1.4.8`、`langgraph 1.2.19` 为准。

---

## 1. Agent 是什么

Agent 让 LLM 自己决定调哪个工具，看到结果后再决定下一步做什么。

它就是上一篇末尾那个 ReAct 循环：

```
模型 → 要调工具？ ──是──→ 执行工具 → 把结果放回消息 → 回到模型
             └──否──→ 结束，输出最终回答
```

早期 LangChain 用 `AgentExecutor` 封装这个循环，后来换成 LangGraph 的 `create_react_agent`，1.0 之后官方的封装是 `create_agent`。

---

## 2. 新 API：create_agent

### 2.1 最小例子

```python
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气。"""
    return f"{city} 晴 25℃"

agent = create_agent(
    ChatOpenAI(model="gpt-6-luna", temperature=0),   # 也可以直接写字符串 "openai:gpt-6-luna"
    tools=[get_weather],
    system_prompt="你是一位严谨的助手。",
)

result = agent.invoke({"messages": [{"role": "user", "content": "北京天气？"}]})
print(result["messages"][-1].content)
```

输入和输出都是 `{"messages": [...]}`，旧 `AgentExecutor` 那种 `{"input": ..., "output": ...}` 已经没有了。

### 2.2 常用参数

| 参数 | 作用 |
|------|------|
| `model` | 模型对象或 `"provider:model"` 字符串 |
| `tools` | 工具列表（`@tool` 函数、MCP 工具等） |
| `system_prompt` | 系统提示词（字符串）。旧的 `prompt=` / `state_modifier=` 都没有了 |
| `middleware` | 中间件列表，是 1.x 定制 agent 的主要方式（下一节） |
| `response_format` | 结构化最终输出，结果在 `result["structured_response"]`（见 [LangChain 04：Output Parsers 与结构化输出](./04-output-parsers.md)） |
| `checkpointer` | 持久化多轮对话，配合 `thread_id`（见 LangChain 09 篇、LangGraph 05 篇） |
| `store` | 跨线程长期记忆 |
| `context_schema` | 运行时只读上下文的类型，工具里用 `runtime.context` 读 |
| `state_schema` | 自定义状态（继承 `AgentState`） |
| `name` | agent 名字，作为子图/多 agent 里的节点名 |
| `interrupt_before` / `interrupt_after` | 静态断点（调试用；生产用 `HumanInTheLoopMiddleware`） |

### 2.3 中间件：定制 agent 循环

以前想改循环，只能下到 LangGraph 里自己改图。1.x 把常见需求做成了 middleware，挂在循环的几个钩子上（调模型前后、调工具前后）：

```python
from langchain.agents import create_agent
from langchain.agents.middleware import (
    SummarizationMiddleware,     # 对话太长时自动摘要
    HumanInTheLoopMiddleware,    # 指定工具调用前暂停等人批准
    ToolRetryMiddleware,         # 工具失败自动重试
    ToolErrorMiddleware,         # 把某些工具异常变成给模型的反馈
    ToolCallLimitMiddleware,     # 限制工具调用次数
    ModelRetryMiddleware,        # 模型调用失败重试
    ModelFallbackMiddleware,     # 主模型失败切备用模型
    PIIMiddleware,               # 个人信息脱敏
)

agent = create_agent(
    "openai:gpt-6-luna",
    tools=[...],
    middleware=[
        SummarizationMiddleware(model="openai:gpt-6-luna", trigger=("tokens", 4000), keep=("messages", 20)),
        ToolCallLimitMiddleware(run_limit=10),
    ],
)
```

也可以自己写，用 `@before_model`、`@after_model`、`@wrap_model_call`、`@wrap_tool_call`、`@dynamic_prompt` 这些装饰器。比如根据调用时传入的上下文动态生成 system prompt：

```python
from langchain.agents.middleware import dynamic_prompt, ModelRequest

@dynamic_prompt
def role_prompt(request: ModelRequest) -> str:
    user = request.runtime.context.get("user_name", "用户")
    return f"你是 {user} 的助手，回答要简洁。"

agent = create_agent(model, tools=[...], middleware=[role_prompt])
agent.invoke({"messages": [...]}, context={"user_name": "小明"})
```

### 2.4 看每一步

返回的是 LangGraph 图，直接 `.stream` 就能看到每一步，节点名固定是 `model` 和 `tools`：

```python
for ev in agent.stream({"messages": [...]}, stream_mode="updates"):
    for node, value in ev.items():
        print(node, "->", value["messages"][-1])
```

### 2.5 防止无限循环

模型一直调工具停不下来时，有两处可以拦：

- `ToolCallLimitMiddleware(run_limit=..., thread_limit=...)`：限制工具调用次数；
- LangGraph 的 `recursion_limit`（调用时传 `config={"recursion_limit": 30}`）：限制图的总步数。当前默认值是 10007，可以用环境变量 `LANGGRAPH_DEFAULT_RECURSION_LIMIT` 调整。

默认值这么大，等于几乎没有限制，生产环境一定要自己设一个小的值。

---

## 3. 旧 API：AgentExecutor（已在 langchain-classic）

看到下面这种代码，基本可以确定是 1.0 以前的项目：

```python
# pip install langchain-classic
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一位严谨的助手。"),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=10)

print(executor.invoke({"input": "LangChain 最新版本是多少？"})["output"])
```

> 导入路径是 `langchain_classic.agents`，不再是 `langchain.agents`。直接写 `from langchain.agents import AgentExecutor` 会报 `ImportError`。

`AgentExecutor` 的循环是个黑盒，除了塞 callback，循环本身改不了。想在步骤之间存点中间变量，得自己想办法。每一步都要人确认的 Human-in-loop 做不到；进程挂了也没法断点续跑，前面几步等于白跑。流式能力也比较弱。

### 3.1 其他 `create_*_agent` 与 Toolkit Agent

| 函数 | 现状 |
|------|------|
| `create_tool_calling_agent` / `create_openai_tools_agent` | 在 langchain-classic，用 `create_agent` 替代 |
| 文本版 `create_react_agent`（langchain-classic） | 纯文本 ReAct，只有模型不支持 tool calling 时才有意义 |
| `create_xml_agent`、`create_self_ask_with_search_agent` | 同上，历史遗留 |
| `create_sql_agent`、`create_pandas_dataframe_agent` | 还在 community/experimental，了解即可；新代码用 `SQLDatabaseToolkit` 的工具配 `create_agent` 自己组合，更好控制 |

---

## 4. 新旧对照表

| 能力 | AgentExecutor（classic） | create_agent |
|------|---------------|------------------------------|
| 工具调用循环 | 有 | 有 |
| 输入/输出 | `{"input"}` → `{"output"}` | `{"messages"}` → `{"messages"}` |
| system prompt | `ChatPromptTemplate` + `agent_scratchpad` | `system_prompt="..."` |
| Verbose / 中间步骤 | `verbose=True`、`intermediate_steps` | `stream(stream_mode="updates")` |
| 持久化 / Thread | 无 | `checkpointer` |
| Human-in-loop | 无 | `HumanInTheLoopMiddleware` / `interrupt` |
| Time-travel 回放 | 无 | 有 |
| 细粒度流式 | 弱 | `stream_mode="messages"` / `astream_events` |
| 状态扩展 | 无 | `state_schema` |
| 结构化最终输出 | 需自己解析 | `response_format` |
| 插件式扩展 | callback | middleware |
| 部署 / Studio | 无 | LangSmith Deployment |

---

## 5. 迁移对照

### 5.1 旧代码

```python
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
result = executor.invoke({"input": "..."})
print(result["output"])
```

### 5.2 等价新代码

```python
from langchain.agents import create_agent

agent = create_agent(llm, tools=tools, system_prompt="你是一个 helpful assistant。")
result = agent.invoke({"messages": [{"role": "user", "content": "..."}]})
print(result["messages"][-1].content)
```

prompt 模板和 `agent_scratchpad` 都不需要了。LangGraph 用消息列表做状态，工具调用的过程本来就记录在消息里。

### 5.3 从 `langgraph.prebuilt.create_react_agent` 迁移

0.x 时推荐的 `from langgraph.prebuilt import create_react_agent` 在 1.2 里还能导入，但已经弃用，会给出警告。参数对应关系如下：

| `create_react_agent` | `create_agent` |
|------|------|
| `prompt=` / `state_modifier=` | `system_prompt=`（动态内容用 `@dynamic_prompt`） |
| `pre_model_hook` / `post_model_hook` | `@before_model` / `@after_model` 中间件 |
| `response_format` | `response_format`（支持 `ToolStrategy` / `ProviderStrategy`） |
| `state_schema`（TypedDict） | `state_schema`（继承 `AgentState`） |
| 节点名 `agent` / `tools` | 节点名 `model` / `tools` |
| 返回 `{"messages": [...]}` | 相同 |

### 5.4 工具错误处理差异

`create_agent` 里，工具执行期的异常默认直接抛出并中断运行；参数校验失败是例外，会反馈给模型。想让模型在出错后自己修正，就挂上 `ToolErrorMiddleware`，见 [LangChain 07：Tools 工具系统](./07-tools.md) 第 6 节。

---

## 6. 更上层：Deep Agents

如果要做长任务 agent，需要自动规划待办、读写文件、拆分子 agent，官方在 `create_agent` 之上还提供了 Deep Agents（`pip install deepagents`，入口是 `create_deep_agent`）。从上到下三层依次是：Deep Agents（开箱即用的 harness）、`create_agent`（可控的通用循环）、LangGraph（手写图）。本书主要讲下面两层。

---

## 7. 何时还应该用 AgentExecutor

只有一种情况：老代码暂时迁不动。装上 `langchain-classic` 让它先跑着，新项目一律用 `create_agent`。

---

## 8. 本章 demo

完整代码：[`demos/langchain/08_agent_compare.py`](../../demos/langchain/08_agent_compare.py)

```python
# demos/langchain/08_agent_compare.py（节选）
import os
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")

@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气"""
    return f"{city} 晴 25℃"

@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))

agent = create_agent(
    ChatOpenAI(model=MODEL, temperature=0),
    tools=[get_weather, calc],
    system_prompt="你是一位严谨的助手。",
)
out = agent.invoke({"messages": [{"role": "user", "content": "查询北京天气，再算 (3+5)*2"}]})
print(out["messages"][-1].content)

for ev in agent.stream({"messages": [{"role": "user", "content": "上海天气"}]}, stream_mode="updates"):
    for node, value in ev.items():
        print(node, "->", value["messages"][-1].content or value["messages"][-1].tool_calls)
```

完整 demo 里还留了一份旧 `AgentExecutor`（从 `langchain_classic` 导入）的写法，方便对照。

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `from langchain.agents import AgentExecutor` 报 ImportError | 1.x 已移到 langchain-classic | `pip install langchain-classic`，`from langchain_classic.agents import AgentExecutor` |
| `from langchain import create_agent` 报错 | 它不在顶层 | `from langchain.agents import create_agent` |
| `create_agent(..., prompt=...)` / `model_kwargs=` 报 TypeError | 这些参数不存在 | 用 `system_prompt=`；模型参数写在模型对象上 |
| 返回里找不到 `output` | 返回结构变了 | 取 `result["messages"][-1].content` |
| 工具抛异常整个运行挂掉 | 执行期异常默认直接抛出 | 挂 `ToolErrorMiddleware`，或工具内自己 try/except |
| agent 停不下来，费用飙升 | 默认 `recursion_limit` 是 10007，几乎等于不限 | `ToolCallLimitMiddleware` + 显式 `recursion_limit` |
| `create_react_agent` 报弃用警告 | 这是旧入口 | 迁到 `create_agent` |

---

下一篇：[LangChain 09：Memory 记忆机制](09-memory.md)
