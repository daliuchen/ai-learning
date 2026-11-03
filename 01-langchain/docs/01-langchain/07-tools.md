# LangChain 07：Tools 工具系统

> Tool 就是 LLM 可以调用的函数。LangChain 的 `@tool` 装饰器能把任意 Python 函数变成工具，并根据签名和 docstring 自动生成 JSON Schema。agent 能做什么事，全看你给它哪些工具，所以这篇会多讲一些错误处理和运行时注入的细节。

---

## 1. 工具调用的全流程

```
1. 你定义工具：@tool def get_weather(city: str) -> str
2. bind 到模型：model.bind_tools([get_weather])
3. 用户输入：HumanMessage("北京天气")
4. 模型决定调工具：AIMessage(tool_calls=[{name:"get_weather", args:{"city":"北京"}}])
5. 你执行工具：result = get_weather.invoke({"city": "北京"})
6. 把结果回填：ToolMessage(content=result, tool_call_id=...)
7. 模型基于 tool 结果再回复：AIMessage("北京今天晴...")
```

用 OpenAI SDK 的话，这 7 步的解析和循环都得自己写。LangChain 把每一步都封装好了，剩下要做的是定义工具，然后自己写循环，或者交给 LangGraph 去循环。

---

## 2. 定义工具的三种方式

### 2.1 @tool 装饰器（推荐）

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气。"""
    return f"{city} 今天晴，25℃"
```

LangChain 会从函数签名、类型注解和 docstring 生成 schema：

```python
print(get_weather.name)         # "get_weather"
print(get_weather.description)  # "根据城市名查询天气。"
print(get_weather.args_schema.model_json_schema())
# {
#   "type": "object",
#   "properties": {"city": {"type": "string"}},
#   "required": ["city"]
# }
```

调用工具：

```python
get_weather.invoke({"city": "北京"})
# 或
get_weather.invoke("北京")    # 单参数时支持位置参数
```

### 2.2 用 Pydantic 显式声明 schema

```python
from pydantic import BaseModel, Field

class WeatherInput(BaseModel):
    city: str = Field(description="城市中文名")
    unit: str = Field(default="celsius", description="温度单位 celsius/fahrenheit")

@tool(args_schema=WeatherInput)
def get_weather(city: str, unit: str = "celsius") -> str:
    """查询某城市天气。"""
    return f"{city} 25°{unit[0].upper()}"
```

参数多、需要把每个参数描述清楚时用这种。

### 2.3 StructuredTool.from_function

不想用装饰器时：

```python
from langchain_core.tools import StructuredTool

def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b

add_tool = StructuredTool.from_function(
    func=add,
    name="add",
    description="计算两数之和",
    args_schema=...,  # 可选
)
```

---

## 3. 异步工具

```python
@tool
async def fetch_url(url: str) -> str:
    """异步抓取 URL 内容。"""
    import httpx
    async with httpx.AsyncClient() as c:
        r = await c.get(url)
        return r.text[:500]
```

`@tool` 能识别 async 函数，调用时用 `ainvoke`：

```python
result = await fetch_url.ainvoke({"url": "https://example.com"})
```

工具只有异步实现时，不能用同步 `invoke` 调用，会抛 `NotImplementedError: StructuredTool does not support sync invocation.`。两种调用方式都要支持的话，用 `StructuredTool.from_function(func=..., coroutine=...)` 同时提供同步和异步实现。

---

## 4. bind_tools 与模型

```python
from langchain_openai import ChatOpenAI

@tool
def get_weather(city: str) -> str:
    """查询天气。"""
    return f"{city} 晴"

@tool
def search_web(query: str) -> str:
    """搜索互联网。"""
    return "..."

model_with_tools = ChatOpenAI(model="gpt-6-luna").bind_tools([get_weather, search_web])

resp = model_with_tools.invoke("北京天气")
print(resp.tool_calls)
# [{
#   "name": "get_weather",
#   "args": {"city": "北京"},
#   "id": "call_xxx",
#   "type": "tool_call"
# }]
```

`resp.content` 可能是空字符串，因为模型只决定了要调工具，没有生成文字。

### 4.1 强制使用某工具

```python
model.bind_tools([t1, t2], tool_choice="t1")     # 必须用 t1
model.bind_tools([t1, t2], tool_choice="auto")   # 模型自己决定（默认）
model.bind_tools([t1, t2], tool_choice="any")    # 必须用某个工具
model.bind_tools([t1, t2], tool_choice="none")   # 禁用工具
```

### 4.2 并行工具调用

主流模型（GPT、Claude 等）都支持一次返回多个 tool_call：

```python
resp = model.invoke("查一下北京和上海的天气")
print(resp.tool_calls)
# [
#   {"name": "get_weather", "args": {"city": "北京"}, ...},
#   {"name": "get_weather", "args": {"city": "上海"}, ...},
# ]
```

每个调用有自己的 `id`，回填结果时按 id 对应。

关闭并行：

```python
model.bind_tools([...], parallel_tool_calls=False)
```

---

## 5. 完整工具调用循环（不用 Agent）

```python
from langchain_core.messages import HumanMessage, ToolMessage

tools = [get_weather, search_web]
tools_by_name = {t.name: t for t in tools}
model = ChatOpenAI(model="gpt-6-luna").bind_tools(tools)

messages = [HumanMessage(content="北京天气怎么样？")]

while True:
    resp = model.invoke(messages)
    messages.append(resp)
    if not resp.tool_calls:
        print("最终：", resp.content)
        break
    for call in resp.tool_calls:
        result = tools_by_name[call["name"]].invoke(call["args"])
        messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
```

这就是一个手写的 ReAct 循环。`langchain.agents.create_agent` 是官方对这段逻辑的封装，底层跑在 LangGraph 上，更稳，也更好观测。详见 LangChain 08 篇和 LangGraph 04 篇。

---

## 6. 错误处理

工具出错有两种情况，默认的处理方式不一样（以 `langchain 1.4` 为准）。

参数不符合 schema 时，比如模型把 `int` 填成了字符串，agent 会自动生成一条 `status="error"` 的 `ToolMessage` 交回模型，让它改了参数再试。

工具函数体里抛出的异常，默认会直接向上抛，整个运行中断。这是故意这么设计的：执行期异常可能是真 bug，不应该默认让模型再试一次把它掩盖掉。

如果想把某类异常变成给模型的反馈，有下面几种做法。

### 6.1 让异常向上抛（默认）

```python
@tool
def divide(a: int, b: int) -> float:
    """除法"""
    return a / b

divide.invoke({"a": 1, "b": 0})  # 直接 ZeroDivisionError
```

### 6.2 ToolException + handle_tool_error

网上常见的 `@tool(handle_tool_errors=True)` 写法在 1.x 里并不存在，会报 `unexpected keyword argument`。正确做法是在工具里主动抛 `ToolException`，再给工具设置 `handle_tool_error`（末尾没有 s）：

```python
from langchain_core.tools import tool, ToolException

@tool
def divide(a: int, b: int) -> float:
    """除法"""
    if b == 0:
        raise ToolException("除数不能为 0")
    return a / b

divide.handle_tool_error = True                       # 返回异常信息本身
# 或自定义：
divide.handle_tool_error = lambda e: f"工具失败：{e}"

divide.invoke({"a": 1, "b": 0})   # -> "工具失败：除数不能为 0"
```

这样只有 `ToolException` 会被接住，其他异常（往往是真 bug）照常抛出。

### 6.3 在 agent 里：ToolErrorMiddleware

```python
from langchain.agents import create_agent
from langchain.agents.middleware import ToolErrorMiddleware

def on_error(exc: Exception, request) -> str | None:
    if isinstance(exc, ValueError):
        return f"`{request.tool_call['name']}` 失败：{exc}"
    return None            # 返回 None = 其它异常照常抛出

agent = create_agent(model, tools=[...], middleware=[ToolErrorMiddleware(on_error)])
```

**别图省事把所有异常都兜住。** 那样真 bug 也会变成一句「请检查输入再试」，模型会反复重试、不停消耗 token，你却看不到报错。需要重试就配 `ToolRetryMiddleware`，需要限制次数就配 `ToolCallLimitMiddleware`。

被接住的错误会以 `ToolMessage` 的形式回到模型，模型通常会道个歉，改了参数再试。

---

## 7. ArtifactTool：返回大对象 + 摘要

有时模型只需要看摘要，界面或业务代码却要拿完整数据：

```python
from langchain_core.tools import tool

@tool(response_format="content_and_artifact")
def search(query: str):
    """搜索"""
    rows = [{"id": i, "title": f"row {i}"} for i in range(100)]
    summary = f"找到 {len(rows)} 条"
    return summary, rows   # (content, artifact)
```

调用结果是 `ToolMessage(content=summary, artifact=rows)`。模型只看到 content，前端可以读 artifact 去渲染。

---

## 8. 注入运行时上下文（InjectedToolArg）

user_id、数据库连接这类参数不该让 LLM 看到，可以用 `InjectedToolArg`：

```python
from typing_extensions import Annotated
from langchain_core.tools import tool, InjectedToolArg

@tool
def query_orders(
    keyword: str,
    user_id: Annotated[str, InjectedToolArg],
) -> list[dict]:
    """根据关键词查询订单。"""
    return db.query(user_id=user_id, q=keyword)
```

LLM 看到的 schema 里没有 user_id，执行时再由你手动注入：

```python
for call in resp.tool_calls:
    args = {**call["args"], "user_id": current_user_id}
    result = query_orders.invoke(args)
```

只有手写循环时才需要自己注入。用 `create_agent` 的话有更好的办法：给工具加一个 `ToolRuntime` 参数，它对模型不可见，由框架负责注入：

```python
from langchain.tools import tool, ToolRuntime

@tool
def query_orders(keyword: str, runtime: ToolRuntime) -> list[dict]:
    """根据关键词查询当前用户的订单。"""
    user_id = runtime.context["user_id"]      # 本次 invoke 传入的只读上下文
    return db.query(user_id=user_id, q=keyword)
```

`runtime.context` 是本次调用的只读输入，`runtime.state` 是跟着对话线程走、可以更新的状态，`runtime.store` 是跨会话的长期存储，此外还有 `runtime.tool_call_id`、`runtime.stream_writer` 等。工具也可以返回 `Command(update={...})` 写回状态，`Command` 的用法见 LangGraph 08 篇。

---

## 9. 内置工具

LangChain Community 里有几十个现成的工具：

```python
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper
from langchain_community.tools import ShellTool
from langchain_tavily import TavilySearch          # pip install langchain-tavily

tavily = TavilySearch(max_results=3)               # 需要 TAVILY_API_KEY；老的 TavilySearchResults 已被它取代
wiki   = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
ddg    = DuckDuckGoSearchRun()
shell  = ShellTool()  # ⚠️ 危险，仅本地用
```

`PythonREPLTool` 已从 community 移到 `langchain_experimental.tools`（`pip install langchain-experimental`）。1.x 之后，接入外部工具主要靠 MCP：`langchain.mcp.MCPAdapter`（1.4 起内置，需要 `pip install "langchain[mcp]"`，目前是 beta，导入时会有一次 `LangChainBetaWarning`；旧包 `langchain-mcp-adapters` 仍可用）可以把任意 MCP server 的工具转成 LangChain 工具，见官方 MCP 文档。

ShellTool 和 PythonREPLTool 会执行代码，生产环境一定要放进沙箱。

---

## 10. 把工具集合包装成 Toolkit

```python
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase

db = SQLDatabase.from_uri("sqlite:///example.db")
toolkit = SQLDatabaseToolkit(db=db, llm=model)
tools = toolkit.get_tools()
```

Toolkit 是一组相关工具的打包，比如上面这个就带了 list tables、schema、query、checker 几个 SQL 工具。常见的 Toolkit 有：

- `SQLDatabaseToolkit`：SQL 数据库
- `FileManagementToolkit`：文件读写
- `PlayWrightBrowserToolkit`：浏览器自动化
- `GmailToolkit`：Gmail
- `JsonToolkit`：JSON 探索

---

## 11. 综合 demo：手写 ReAct 循环

完整代码：[`demos/langchain/07_tools.py`](../../demos/langchain/07_tools.py)

```python
# demos/langchain/07_tools.py
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, ToolMessage

load_dotenv()

@tool
def get_weather(city: str) -> str:
    """查询城市天气"""
    return {"北京": "晴 25℃", "上海": "雨 22℃"}.get(city, "未知")

@tool
def calc(expr: str) -> str:
    """计算表达式，支持 + - * / **"""
    import math
    return str(eval(expr, {"__builtins__": {}}, {"math": math}))

tools = [get_weather, calc]
tools_by_name = {t.name: t for t in tools}

model = ChatOpenAI(model="gpt-6-luna").bind_tools(tools)

messages = [HumanMessage(content="北京气温是多少？再帮我算一下 2*3+4")]

while True:
    resp = model.invoke(messages)
    messages.append(resp)
    if not resp.tool_calls:
        print("\n最终回答：", resp.content)
        break
    for call in resp.tool_calls:
        print(f"调用 {call['name']}({call['args']})")
        result = tools_by_name[call["name"]].invoke(call["args"])
        messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
```

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `bind_tools` 后模型不调工具 | 工具描述写得不清楚 | 优化 docstring 和参数 description |
| 工具参数顺序乱 | OpenAI function call 不保证顺序 | 从 `args` dict 按名字取，不要解包后按位置传 |
| 中文工具名报错 | OpenAI 工具名限定 ASCII | 用 ASCII 名，description 写中文 |
| 模型反复调同一个工具 | 没有终止条件 | 设循环上限，或交给 LangGraph 控制 |
| 异步工具用 `invoke` 调用报 `NotImplementedError` | 只有 async 实现的工具不支持同步调用 | 全程用 `ainvoke`，或同时提供同步实现 |
| `@tool(handle_tool_errors=...)` 报 unexpected keyword | 该参数不存在 | 抛 `ToolException` + 设 `tool.handle_tool_error`，或用 `ToolErrorMiddleware` |
| 工具抛异常整个 agent 中断 | 1.x 默认执行期异常直接抛出 | 见第 6 节 |
| 工具参数起名叫 `config` / `runtime` | 这两个是保留参数名 | 换个名字 |

---

下一篇：[LangChain 08：Agents（create_agent 与旧 API 迁移）](08-agents.md)
