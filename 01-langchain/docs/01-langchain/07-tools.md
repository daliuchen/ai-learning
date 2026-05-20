# LangChain 07：Tools 工具系统

> **一句话**：Tool = "LLM 可以调用的函数"。LangChain 用 `@tool` 装饰器把任意 Python 函数变成 LLM 可以理解和调用的工具，自动生成 JSON Schema 描述。

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

这 7 步在传统 OpenAI SDK 里你得手写一遍解析、循环。LangChain 把每一步都做了抽象，你只关心：**定义工具 + 写循环 / 用 LangGraph 自动循环**。

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

LangChain 自动从函数签名、类型注解、docstring 生成 schema：

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

适合参数多、需要严谨描述的场景。

### 2.3 StructuredTool.from_function

不想用装饰器：

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

`@tool` 自动识别 async 函数，invoke 时用 `ainvoke`：

```python
result = await fetch_url.ainvoke({"url": "https://example.com"})
```

如果工具只有异步实现，同步 `invoke` 会用 `asyncio.run` 兜底。

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

model_with_tools = ChatOpenAI(model="gpt-4o-mini").bind_tools([get_weather, search_web])

resp = model_with_tools.invoke("北京天气")
print(resp.tool_calls)
# [{
#   "name": "get_weather",
#   "args": {"city": "北京"},
#   "id": "call_xxx",
#   "type": "tool_call"
# }]
```

`resp.content` 可能是空字符串（模型只决定调工具，没生成文字）。

### 4.1 强制使用某工具

```python
model.bind_tools([t1, t2], tool_choice="t1")     # 必须用 t1
model.bind_tools([t1, t2], tool_choice="auto")   # 模型自己决定（默认）
model.bind_tools([t1, t2], tool_choice="any")    # 必须用某个工具
model.bind_tools([t1, t2], tool_choice="none")   # 禁用工具
```

### 4.2 并行工具调用

GPT-4 / Claude 3.5 等模型支持一次返回多个 tool_call：

```python
resp = model.invoke("查一下北京和上海的天气")
print(resp.tool_calls)
# [
#   {"name": "get_weather", "args": {"city": "北京"}, ...},
#   {"name": "get_weather", "args": {"city": "上海"}, ...},
# ]
```

每个并行调用一个 `id`，回填时按 id 对齐。

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
model = ChatOpenAI(model="gpt-4o-mini").bind_tools(tools)

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

这本质就是手写 ReAct 循环。**LangGraph 的 `create_react_agent` 是这段代码的官方封装版**，更稳健、可观测，详见 LangGraph 04 章。

---

## 6. 错误处理

工具可能抛异常，LangChain 给了三种策略：

### 6.1 让异常向上抛（默认）

```python
@tool
def divide(a: int, b: int) -> float:
    """除法"""
    return a / b

divide.invoke({"a": 1, "b": 0})  # 直接 ZeroDivisionError
```

### 6.2 用 handle_tool_errors 把异常变成消息

```python
@tool(handle_tool_errors=True)
def divide(a: int, b: int) -> float:
    """除法"""
    return a / b

# divide.invoke({"a": 1, "b": 0}) 返回 "ZeroDivisionError: division by zero"
```

### 6.3 自定义 handler

```python
def handler(e: Exception) -> str:
    return f"工具失败：{type(e).__name__}"

@tool(handle_tool_errors=handler)
def divide(a, b): return a / b
```

工具错误以 `ToolMessage` 形式回到模型，模型一般会自己道歉/重试。

---

## 7. ArtifactTool：返回大对象 + 摘要

模型只需要看到摘要，UI / 业务想拿到完整数据时：

```python
from langchain_core.tools import tool

@tool(response_format="content_and_artifact")
def search(query: str):
    """搜索"""
    rows = [{"id": i, "title": f"row {i}"} for i in range(100)]
    summary = f"找到 {len(rows)} 条"
    return summary, rows   # (content, artifact)
```

调用得到 `ToolMessage(content=summary, artifact=rows)`，模型只看 content，前端可读取 artifact 渲染。

---

## 8. 注入运行时上下文（InjectedToolArg）

有些参数不该让 LLM 看到（如 user_id、db connection），用 `InjectedToolArg`：

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

LLM 看到的 schema 里**没有 user_id**，但你在执行时手动注入：

```python
for call in resp.tool_calls:
    args = {**call["args"], "user_id": current_user_id}
    result = query_orders.invoke(args)
```

LangGraph 的 `create_react_agent` 接 `state` 时常用这套机制传 thread-scoped 数据。

---

## 9. 内置工具

LangChain Community 自带几十个常用工具：

```python
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper
from langchain_community.tools import ShellTool, PythonREPLTool
from langchain_community.tools import RequestsGetTool

tavily = TavilySearchResults(max_results=3)
wiki   = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
ddg    = DuckDuckGoSearchRun()
shell  = ShellTool()  # ⚠️ 危险，仅本地用
py     = PythonREPLTool()
```

挑选注意：**ShellTool/PythonREPLTool 涉及代码执行，生产环境必须沙箱**。

---

## 10. 把工具集合包装成 Toolkit

```python
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase

db = SQLDatabase.from_uri("sqlite:///example.db")
toolkit = SQLDatabaseToolkit(db=db, llm=model)
tools = toolkit.get_tools()
```

Toolkit 是"一组相关工具的捆绑"，自带几个 SQL 工具（list tables / schema / query / checker）。常见 Toolkit：

- `SQLDatabaseToolkit`：SQL 数据库
- `FileManagementToolkit`：文件读写
- `PlayWrightBrowserToolkit`：浏览器自动化
- `GmailToolkit`：Gmail
- `JsonToolkit`：JSON 探索

---

## 11. 综合 demo：手撸 ReAct 循环

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

model = ChatOpenAI(model="gpt-4o-mini").bind_tools(tools)

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
| `bind_tools` 后模型不调工具 | 描述不清楚 | 优化 docstring 和参数 description |
| 工具参数顺序乱 | OpenAI function call 不保证 | 用 `args` dict 取，不要 unpack 后位置传 |
| 中文工具名报错 | OpenAI 工具名限定 ASCII | 用 ASCII 名，description 写中文 |
| 模型疯狂调同一个工具 | 缺少终止条件 | 设循环上限 / 用 LangGraph 控制 |
| 异步工具同步调用慢 | 同步 invoke 用 asyncio.run 启停 | 全程用 ainvoke |

---

## 13. 本章 demo

[`demos/langchain/07_tools.py`](../../demos/langchain/07_tools.py)

下一篇：[08-agents.md](08-agents.md)
