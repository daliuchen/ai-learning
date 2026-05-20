# MCP 03：三大原语 —— Tools / Resources / Prompts

> **一句话**：MCP Server 通过三个原语对外暴露能力——**Tools** 由模型调（写操作）、**Resources** 由应用提供（只读上下文）、**Prompts** 由用户触发（模板）。这一篇把它们的边界、设计意图、协议方法、Python 落地一次性讲清。

---

## 1. 为什么是三个，不是一个

如果你之前只熟悉 Function Calling，可能会问：「为啥 MCP 要搞三个原语？所有事不都能用 Tool 解决吗？」

答案：**控制权归属不同**。

| 原语 | 控制方 | 典型 UI 体验 |
|------|--------|--------------|
| **Tools** | **Model-controlled**（模型决定何时调） | 模型在对话中自动调用，可能需用户审批 |
| **Resources** | **Application-controlled**（AI 应用决定何时拉） | 用户在 UI 上挑文件、应用自动注入上下文 |
| **Prompts** | **User-controlled**（用户显式触发） | Slash 命令 / 命令面板 / 模板按钮 |

举个具体例子——一个数据库 MCP Server：

- `query_database(sql)` → **Tool**：模型读到用户问题"过去 7 天订单数"，自动决定调它
- `db://schema/orders` → **Resource**：应用启动时拉一次，作为系统提示词的一部分
- `prompts/diagnose-slow-query` → **Prompt**：用户在命令面板挑选"诊断慢查询"模板

同样是"和数据库相关的能力"，三种用法对应三种 UI 与权限模型。**把所有事都做成 Tool，会让模型在不该插手的地方擅自行动。**

---

## 2. 一张表对比三大原语

| 维度 | **Tools** | **Resources** | **Prompts** |
|------|-----------|---------------|-------------|
| **意图** | 执行动作（动词） | 提供上下文（名词） | 提供交互模板（句子） |
| **谁触发** | 模型 | 应用 / 用户 | 用户 |
| **副作用** | 可以有 | 应该没有 | 无 |
| **典型例子** | `send_email`、`create_issue` | 文件内容、DB schema、API 响应 | "总结今天日历"、"写周报模板" |
| **协议方法** | `tools/list`、`tools/call` | `resources/list`、`resources/templates/list`、`resources/read`、`resources/subscribe` | `prompts/list`、`prompts/get` |
| **是否带参数** | 必有（JSON Schema） | URI 模板可参数化 | 可有（arguments） |
| **更新通知** | `notifications/tools/list_changed` | `notifications/resources/list_changed` + `notifications/resources/updated` | `notifications/prompts/list_changed` |
| **类比** | REST API 的 POST/PUT/DELETE | REST API 的 GET | 命令行的 alias / 编辑器的 snippet |

---

## 3. Tools：让模型干活

### 3.1 是什么
**Tools = 模型可以调用的函数**。每个工具有 name、description、JSON Schema 定义的 inputSchema、可选的 outputSchema 和 title。

### 3.2 协议消息

```json
// 列工具
{"jsonrpc":"2.0","id":1,"method":"tools/list"}

// 响应
{
  "jsonrpc":"2.0","id":1,
  "result":{
    "tools":[
      {
        "name":"add",
        "title":"加法",
        "description":"两数相加",
        "inputSchema":{
          "type":"object",
          "properties":{
            "a":{"type":"integer"},
            "b":{"type":"integer"}
          },
          "required":["a","b"]
        }
      }
    ]
  }
}

// 调用
{
  "jsonrpc":"2.0","id":2,"method":"tools/call",
  "params":{
    "name":"add",
    "arguments":{"a":2,"b":3}
  }
}

// 结果
{
  "jsonrpc":"2.0","id":2,
  "result":{
    "content":[
      {"type":"text","text":"5"}
    ]
  }
}
```

### 3.3 Python SDK 落地

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加。

    Args:
        a: 被加数
        b: 加数
    """
    return a + b

@mcp.tool()
def search_orders(
    user_id: str,
    days: int = 7,
    status: str | None = None,
) -> list[dict]:
    """查询某用户最近 N 天的订单。

    Args:
        user_id: 用户 ID（必填）
        days: 查询窗口天数，默认 7
        status: 可选订单状态过滤，如 'paid' / 'shipped'
    """
    # ... 业务代码
    return [{"id": "...", "amount": 100}]
```

FastMCP 自动做这些事：

1. 从函数签名 + 类型注解 + docstring 生成 JSON Schema
2. 把 docstring 拆成工具 description + 各参数 description
3. 处理可选参数 / 默认值
4. 返回值序列化成 MCP content（list[dict] 会变成 JSON 文本）

### 3.4 返回结构化 content

工具可以返回**多种内容类型**：

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent, ImageContent

mcp = FastMCP("demo")

@mcp.tool()
def render_chart(query: str) -> list:
    # 返回文字 + 图片
    return [
        TextContent(type="text", text=f"查询：{query}"),
        ImageContent(type="image", data="<base64>", mimeType="image/png"),
    ]
```

支持的 content 类型：

| 类型 | 用途 |
|------|------|
| `TextContent` | 普通文本 |
| `ImageContent` | 图片（base64） |
| `AudioContent` | 音频 |
| `EmbeddedResource` | 嵌入一个 Resource 引用 |

### 3.5 关键约定

- **命名**：建议带命名空间，如 `github_create_issue` 而不只是 `create_issue`，避免跨 Server 冲突
- **描述**：写给 LLM 看，要明确"什么时候用 / 不该什么时候用 / 输入输出语义"
- **副作用警示**：写操作（删除、发送、扣费）务必在 description 里强调，Client 会用来决定是否弹审批
- **错误**：参数校验失败 vs 业务失败要区分（详见 02-server/08-errors-validation）

---

## 4. Resources：让应用读上下文

### 4.1 是什么
**Resources = 一份只读数据，有 URI、有 MIME type**。

URI 是任意 scheme：`file://`、`db://`、`gh://`、`custom-scheme://...` 都行。客户端拿到 URI 后通过 `resources/read` 获取实际内容。

> Resources **不是给模型调用的**。是 AI 应用（Host）按自己策略决定何时拉、注入到模型上下文里的。模型不会自动"读 resource"，除非通过工具间接触发。

### 4.2 直接资源 vs 资源模板

- **直接 Resource**：固定 URI。例如 `system://config`、`db://schema/orders`
- **Resource Template**：带占位符。例如 `db://tables/{table}`、`gh://repos/{owner}/{repo}/issues/{id}`

模板可以做**参数补全**：用户输入 `gh://repos/anthropic/` 时，Client 可以请求 `completion/complete` 拿到候选 repo 列表。

### 4.3 协议消息

```json
// 列直接 Resource
{"method":"resources/list"}
// 响应
{
  "resources":[
    {
      "uri":"db://schema/orders",
      "name":"orders-schema",
      "title":"orders 表 schema",
      "mimeType":"text/plain"
    }
  ]
}

// 列 Resource Template
{"method":"resources/templates/list"}
// 响应
{
  "resourceTemplates":[
    {
      "uriTemplate":"db://tables/{table}",
      "name":"table-data",
      "title":"按表名读数据",
      "description":"读某张表的前 100 行",
      "mimeType":"application/json"
    }
  ]
}

// 读 Resource
{"method":"resources/read","params":{"uri":"db://schema/orders"}}
// 响应
{
  "contents":[
    {
      "uri":"db://schema/orders",
      "mimeType":"text/plain",
      "text":"id INT, user_id INT, amount DECIMAL, ..."
    }
  ]
}
```

### 4.4 Python SDK 落地

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("db-server")

@mcp.resource("db://schema/orders")
def orders_schema() -> str:
    """orders 表的 schema 描述"""
    return "id INT PRIMARY KEY, user_id INT, amount DECIMAL(10,2), ..."

@mcp.resource("db://tables/{table}")
def table_data(table: str) -> str:
    """读某张表的前 100 行"""
    # 注意：实际生产要做 SQL 注入防御 + 白名单
    rows = db.fetch(f"SELECT * FROM {table} LIMIT 100")
    return "\n".join(map(str, rows))
```

注意几个细节：

- `@mcp.resource("db://schema/orders")` → 直接 Resource，URI 固定
- `@mcp.resource("db://tables/{table}")` → 模板，`{table}` 是参数，自动从 URI 提取
- 返回值可以是 `str`（自动 mimeType=text/plain）或 `bytes`（base64） 或 dict（JSON）

### 4.5 订阅变更

如果 Server 声明了 `resources.subscribe: true` 能力，Client 可以订阅某个 Resource：

```json
{"method":"resources/subscribe","params":{"uri":"db://live-metrics"}}
```

之后 Server 在数据变更时主动推送：

```json
{"method":"notifications/resources/updated","params":{"uri":"db://live-metrics"}}
```

适合监控类、实时类数据。

### 4.6 什么时候用 Resource 而不是 Tool？

判断标准：**这个能力会不会改变外部状态？**

- 「读 schema」「列 issue」「拿配置」→ Resource（应用启动时一次性拉到上下文）
- 「创建 issue」「发邮件」「执行 SQL」→ Tool（模型决定何时调）

**反面例子**：把"查询数据库"做成 Resource 是常见错误。SQL 查询有不确定的副作用（慢查询拖死库、可能改 hint），且需要参数化语义化——这种应该是 Tool。

**正面例子**：`db://schema/orders` 是 Resource——它就是一份静态结构描述，Server 可能本地缓存。

---

## 5. Prompts：让用户启动一段流程

### 5.1 是什么
**Prompts = 参数化的 prompt 模板**，由用户显式触发。

Prompt 不是单条消息，而是**一段完整的对话开局**——可以包含 system message、几个 user/assistant 历史、引用 Resource 作为附件。

### 5.2 协议消息

```json
// 列 Prompt
{"method":"prompts/list"}
// 响应
{
  "prompts":[
    {
      "name":"summarize-prs",
      "title":"总结最近 PR",
      "description":"给定 repo 和时间窗口，总结活跃 PR",
      "arguments":[
        {"name":"repo","description":"owner/name","required":true},
        {"name":"days","description":"天数","required":false}
      ]
    }
  ]
}

// 拿具体 Prompt（带参数）
{
  "method":"prompts/get",
  "params":{"name":"summarize-prs","arguments":{"repo":"anthropic/anthropic-sdk-python","days":"7"}}
}
// 响应
{
  "description":"总结最近 PR",
  "messages":[
    {"role":"user","content":{"type":"text","text":"请总结 anthropic/anthropic-sdk-python 最近 7 天的 PR..."}}
  ]
}
```

### 5.3 Python SDK 落地

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

mcp = FastMCP("github-helper")

@mcp.prompt()
def summarize_prs(repo: str, days: int = 7) -> list[base.Message]:
    """总结最近 N 天的 PR"""
    return [
        base.UserMessage(
            f"请帮我总结 {repo} 最近 {days} 天的 PR：\n"
            f"1. 列出每个 PR 的标题和状态\n"
            f"2. 找出最重要的 3 个\n"
            f"3. 给出每个 PR 的一句话评价"
        )
    ]

@mcp.prompt()
def code_review(file_path: str) -> str:
    """代码审查模板"""
    return (
        f"请对文件 {file_path} 做代码 review，关注：\n"
        f"- 安全性\n- 性能\n- 可读性\n- 测试覆盖率"
    )
```

返回值可以是：

- `str` → 单条 user message
- `list[Message]` → 多轮对话开局

### 5.4 怎么暴露给用户

Claude Code / Cursor / VS Code 通常把 Prompts 暴露成**斜杠命令**：

```
/github-helper:summarize-prs repo=anthropic/sdk days=7
```

或在命令面板：

```
> Prompts: Summarize PRs   [输入 repo, days]
```

> 这一行为意味着：**Prompt 是"封装好的对话开场"，不是"模板字符串"**。它的价值在于让非技术用户也能精确触发一段 Agent 工作流。

---

## 6. 通知系统：让原语动态化

每个原语都有对应的"列表变更通知"，让动态新增/删除能力变得可能：

| 通知 | 时机 | 前提（能力声明） |
|------|------|------------------|
| `notifications/tools/list_changed` | 工具列表变了 | Server 声明 `tools.listChanged: true` |
| `notifications/resources/list_changed` | Resource 列表变了 | Server 声明 `resources.listChanged: true` |
| `notifications/resources/updated` | 订阅的 Resource 内容变了 | Server 声明 `resources.subscribe: true` + Client 调过 subscribe |
| `notifications/prompts/list_changed` | Prompt 列表变了 | Server 声明 `prompts.listChanged: true` |

收到 `*_list_changed` 时，Client 标准做法是**立刻重新调对应的 `*/list`** 拉最新清单。

---

## 7. 一个 Server 同时用三个原语：travel-server 示例

来个综合例子。一个旅行助手 MCP Server，给 AI 应用用三种方式提供能力：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

mcp = FastMCP("travel-server")

# ===== Tools：模型用来"干活" =====
@mcp.tool()
def search_flights(origin: str, destination: str, date: str) -> list[dict]:
    """查询航班。返回航班列表"""
    return [{"flight":"CA1981","price":1280, "dep_time":"08:00"}]

@mcp.tool()
def book_flight(flight_id: str, passenger_name: str) -> str:
    """预订航班。⚠️ 这是写操作，会扣费"""
    return f"已预订 {flight_id}，订单号 ABC123"

# ===== Resources：应用用来"读上下文" =====
@mcp.resource("user://preferences")
def user_prefs() -> str:
    """用户旅行偏好"""
    return "偏好：靠窗座位、晨班、最多 1 次中转"

@mcp.resource("travel://past-trips/{year}")
def past_trips(year: str) -> str:
    """指定年份的历史行程"""
    return f"{year} 年去过：东京、首尔、巴塞罗那"

# ===== Prompts：用户用来"启动模板流程" =====
@mcp.prompt()
def plan_vacation(destination: str, days: int, budget: int = 3000) -> str:
    """帮我规划假期"""
    return (
        f"请帮我规划去 {destination} 的 {days} 天行程，预算 ${budget}。\n"
        f"参考资源：\n"
        f"- user://preferences（我的偏好）\n"
        f"- travel://past-trips/2023（去年行程）\n"
        f"流程：先 search_flights 看航班，再问我选哪个，最后 book_flight 下单。"
    )

if __name__ == "__main__":
    mcp.run()
```

用户在 Claude Code 里的体验：

1. 用户点 `/travel-server:plan-vacation destination=Barcelona days=7`
2. Prompt 展开为一段引导 + 引用了两个 Resource
3. Claude Code 读取这两个 Resource，注入到上下文
4. 模型按引导用 Tools：先 `search_flights` 再 `book_flight`

**三个原语，缺一个体验就降一级。**

---

## 8. 客户端反向能力（预告）

除了 Server 向 Client 暴露的三个原语，Client 也向 Server 暴露三个能力——这些放在 `03-client/03~04` 详细讲，这里先列名字：

| 客户端能力 | 用途 | 详见 |
|-----------|------|------|
| **Sampling** | Server 反向求 Host LLM 做 completion | 03-client/03-sampling |
| **Elicitation** | Server 反向求用户输入 | 03-client/04-roots-elicitation |
| **Roots** | Client 告诉 Server 可访问目录 | 03-client/04-roots-elicitation |

六个原语 + 通知 + 生命周期 = MCP 全部协议表面。

---

## 9. 设计决策对照表

| 问题 | 答案 |
|------|------|
| 我要让模型自动调，会有副作用 | **Tool** |
| 我要让应用注入只读上下文 | **Resource** |
| 我要让用户在 UI 显式启动一段流程 | **Prompt** |
| 我要让 Server 反过来用 LLM | **Sampling**（Client 能力） |
| 我要让 Server 中途问用户 | **Elicitation**（Client 能力） |
| 我要限制 Server 可访问的目录 | **Roots**（Client 能力） |

---

## 10. 常见坑

| 坑 | 怎么避免 |
|----|----------|
| **所有事做成 Tool** | 严肃用 Resource / Prompt——只读上下文用 Resource，模板用 Prompt |
| **Resource 写成动态查询** | Resource 应该是幂等只读的，"按用户问题查库"这种动作请用 Tool |
| **Tool description 给人看而不是给模型看** | LLM 是读者，要写明用法、参数语义、边界条件 |
| **Prompt 当 Tool 用** | Prompt 由用户显式触发，模型不会自动调 |
| **没声明 listChanged 却发通知** | Server 必须在 initialize 阶段声明对应 capability，否则通知被丢弃 |
| **跨 Server 同名工具** | Client 端做命名空间，Server 端可考虑加 prefix |

---

## 11. 下一步

- 📖 协议握手 / 能力协商细节 → [04-protocol-lifecycle.md](./04-protocol-lifecycle.md)
- 🛠️ 安装 SDK + Inspector → [05-installation.md](./05-installation.md)
- 🛠️ 跑通 Hello World → [06-first-server.md](./06-first-server.md)
- 🔍 Tool 深入（参数 schema、错误码）→ 02-server/01-tools
- 🔍 Resource 深入（URI 模板、订阅）→ 02-server/02-resources
- 🔍 Prompt 深入（消息组装、参数补全）→ 02-server/03-prompts

## 参考资料

- 官方 Server Concepts：https://modelcontextprotocol.io/docs/learn/server-concepts
- Tools spec：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Resources spec：https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- Prompts spec：https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
