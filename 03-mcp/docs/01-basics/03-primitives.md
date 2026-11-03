# MCP 03：三种原语 Tools、Resources 与 Prompts

> MCP Server 用三种原语对外提供能力：Tools 由模型调用，通常带写操作；Resources 由应用决定何时读取，提供只读上下文；Prompts 由用户主动触发，是现成的对话模板。这篇讲清楚三者的边界和设计意图，以及对应的协议方法和 Python 写法。

---

## 1. 为什么要分成三种

如果你之前只用过 Function Calling，大概会问：所有事都用 Tool 做不就行了，MCP 为什么要分三种原语？

区别在于控制权归谁。

| 原语 | 控制方 | 常见的 UI 形式 |
|------|--------|--------------|
| Tools | Model-controlled（模型决定何时调） | 模型在对话中自动调用，可能需要用户审批 |
| Resources | Application-controlled（AI 应用决定何时拉） | 用户在 UI 上挑文件，或应用自动注入上下文 |
| Prompts | User-controlled（用户显式触发） | Slash 命令 / 命令面板 / 模板按钮 |

拿一个数据库 MCP Server 举例：

- `query_database(sql)` 是 Tool：用户问「过去 7 天的订单数」，模型自己决定调用它
- `db://schema/orders` 是 Resource：应用启动时拉一次，作为系统提示词的一部分
- `prompts/diagnose-slow-query` 是 Prompt：用户在命令面板里选「诊断慢查询」模板

三者都和数据库有关，但对应三种不同的 UI 和权限模型。如果全都做成 Tool，模型就可能在不该由它决定的地方自作主张。

---

## 2. 三种原语对比

| 维度 | Tools | Resources | Prompts |
|------|-----------|---------------|-------------|
| 意图 | 执行动作（动词） | 提供上下文（名词） | 提供交互模板（句子） |
| 谁触发 | 模型 | 应用 / 用户 | 用户 |
| 副作用 | 可以有 | 应该没有 | 无 |
| 例子 | `send_email`、`create_issue` | 文件内容、DB schema、API 响应 | 「总结今天的日历」「写周报模板」 |
| 协议方法 | `tools/list`、`tools/call` | `resources/list`、`resources/templates/list`、`resources/read`（订阅走 `subscriptions/listen`）| `prompts/list`、`prompts/get` |
| 是否带参数 | 必须声明 inputSchema（JSON Schema，无参工具给空对象） | URI 模板可参数化 | 可有（arguments） |
| 更新通知 | `notifications/tools/list_changed` | `notifications/resources/list_changed` + `notifications/resources/updated` | `notifications/prompts/list_changed` |
| 类比 | REST API 的 POST/PUT/DELETE | REST API 的 GET | 命令行的 alias / 编辑器的 snippet |

---

## 3. Tools：让模型动手

### 3.1 是什么
Tool 就是模型可以调用的函数。每个工具有 name、description、用 JSON Schema 定义的 inputSchema，以及可选的 outputSchema 和 title。

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

### 3.3 Python SDK 写法

```python
from mcp.server import MCPServer

mcp = MCPServer("demo")

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

`@mcp.tool()` 修饰的函数，它的函数名、docstring 和类型注解就是全部声明。MCPServer 会据此：

1. 从函数签名和类型注解生成 JSON Schema
2. 把整段 docstring 原样作为工具的 description（不会拆出 `Args:` 里的参数说明，参数级说明要用 `Field(description=...)` 写，见[用 Tools 暴露可调用的操作](../02-server/01-tools.md)）
3. 处理可选参数和默认值
4. 把返回值序列化成 MCP content（`list[dict]` 会变成 JSON 文本）；有返回类型注解时，还会生成 `outputSchema` 并返回 `structuredContent`

### 3.4 返回结构化 content

工具可以返回多种类型的内容：

```python
from mcp.server import MCPServer
from mcp.types import TextContent, ImageContent

mcp = MCPServer("demo")

@mcp.tool()
def render_chart(query: str) -> list:
    # 返回文字 + 图片
    return [
        TextContent(type="text", text=f"查询：{query}"),
        ImageContent(type="image", data="<base64>", mime_type="image/png"),
    ]
```

支持的 content 类型：

| 类型 | 用途 |
|------|------|
| `TextContent` | 普通文本 |
| `ImageContent` | 图片（base64） |
| `AudioContent` | 音频 |
| `EmbeddedResource` | 嵌入一个 Resource 引用 |

### 3.5 几条约定

- 命名：最好带上命名空间，比如 `github_create_issue` 而不只是 `create_issue`，避免和别的 Server 冲突
- 描述：读者是 LLM，要写明什么时候该用、什么时候不该用、输入输出各是什么意思
- 副作用：删除、发送、扣费这类写操作要在 description 里写清楚，Client 会据此决定要不要弹审批
- 错误：参数校验失败和业务失败要分开处理（详见[错误处理与参数校验](../02-server/08-errors-validation.md)）

---

## 4. Resources：让应用读取上下文

### 4.1 是什么
Resource 是一份只读数据，有 URI，有 MIME type。

URI 的 scheme 随意，`file://`、`db://`、`gh://`、`custom-scheme://...` 都可以。客户端拿到 URI 后，通过 `resources/read` 取实际内容。

> Resource 不是给模型调用的。AI 应用（Host）按自己的策略决定什么时候拉取、什么时候注入模型上下文。除非通过工具间接触发，模型不会自己去读 resource。

### 4.2 直接资源和资源模板

直接 Resource 的 URI 是固定的，比如 `system://config`、`db://schema/orders`。Resource Template 则带占位符，比如 `db://tables/{table}`、`gh://repos/{owner}/{repo}/issues/{id}`。

模板支持参数补全：用户输入 `gh://repos/anthropic/` 时，Client 可以调 `completion/complete` 拿到候选 repo 列表。

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

### 4.4 Python SDK 写法

```python
from mcp.server import MCPServer

mcp = MCPServer("db-server")

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

几个细节：

- `@mcp.resource("db://schema/orders")` 是直接 Resource，URI 固定
- `@mcp.resource("db://tables/{table}")` 是模板，`{table}` 作为参数从 URI 里自动提取
- 返回值可以是 `str`（自动设 mimeType=text/plain）、`bytes`（base64）或 dict（JSON）

### 4.5 订阅变更

Server 声明了 `resources.subscribe: true` 能力后，Client 就可以订阅某个 Resource。具体怎么订阅取决于协议版本：

- 2025 及更早：发 `{"method":"resources/subscribe","params":{"uri":"db://live-metrics"}}`，之后由 Server 主动推送 `notifications/resources/updated`
- 2026-07-28：`resources/subscribe` 被 `subscriptions/listen` 取代。Client 开一条长连接，声明自己关心 `resourceSubscriptions: ["db://live-metrics"]`，Server 通过这条流推送变更（见[用 Resources 提供上下文数据](../02-server/02-resources.md)）

监控类、实时类数据适合这么做。

### 4.6 什么时候用 Resource，什么时候用 Tool

判断标准是这个能力会不会改变外部状态。读 schema、列 issue、拿配置，适合做成 Resource，应用启动时一次性拉进上下文；创建 issue、发邮件、执行 SQL，应该做成 Tool，由模型决定什么时候调用。

一个常见的错误是把「查询数据库」做成 Resource。SQL 查询的影响并不确定（慢查询可能拖垮数据库，写语句还会改数据），而且需要带参数、带语义，应该做成 Tool。反过来，`db://schema/orders` 适合做 Resource，它就是一份静态的结构描述，Server 还可以在本地缓存。

---

## 5. Prompts：让用户启动一段流程

### 5.1 是什么
Prompt 是参数化的 prompt 模板，由用户显式触发。

它可以是一段完整的对话开局，包含若干条 user/assistant 消息（MCP 的 Prompt 消息只有这两种角色，没有 system），也可以引用 Resource 作为附件。

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

### 5.3 Python SDK 写法

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.prompts import base

mcp = MCPServer("github-helper")

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

- `str`：一条 user message
- `list[Message]`：多轮对话的开局

### 5.4 怎么呈现给用户

Claude Code、Cursor、VS Code 一般把 Prompt 做成斜杠命令。以 Claude Code 为例，Prompt 名就是函数名，参数按位置用空格隔开（详见[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)）：

```
/mcp__github-helper__summarize_prs anthropic/sdk 7
```

或者放在命令面板里：

```
> Prompts: Summarize PRs   [输入 repo, days]
```

> 所以 Prompt 更像一段封装好的对话开场，而不是一个模板字符串。它的用处是让不懂技术的用户也能准确地启动一段 Agent 工作流。

---

## 6. 通知：让能力可以动态变化

每种原语都有对应的列表变更通知，这样 Server 才能在运行中增删能力：

| 通知 | 时机 | 前提（能力声明） |
|------|------|------------------|
| `notifications/tools/list_changed` | 工具列表变了 | Server 声明 `tools.listChanged: true` |
| `notifications/resources/list_changed` | Resource 列表变了 | Server 声明 `resources.listChanged: true` |
| `notifications/resources/updated` | 订阅的 Resource 内容变了 | Server 声明 `resources.subscribe: true`，且 Client 订阅了该 URI（2025 版 `resources/subscribe`，2026 版 `subscriptions/listen`） |
| `notifications/prompts/list_changed` | Prompt 列表变了 | Server 声明 `prompts.listChanged: true` |

收到 `*_list_changed` 后，Client 的标准做法是马上重新调用对应的 `*/list`，拿到最新清单。

---

## 7. 一个 Server 同时用三种原语：travel-server

来看一个综合例子。这个旅行助手 MCP Server 用三种方式给 AI 应用提供能力：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.prompts import base

mcp = MCPServer("travel-server")

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
        f"- travel://past-trips/2025（去年行程）\n"
        f"流程：先 search_flights 看航班，再问我选哪个，最后 book_flight 下单。"
    )

if __name__ == "__main__":
    mcp.run()
```

用户在 Claude Code 里的体验是这样的：

1. 用户输入 `/mcp__travel-server__plan_vacation Barcelona 7`
2. Prompt 展开成一段引导，里面引用了两个 Resource
3. Claude Code 读取这两个 Resource，注入上下文
4. 模型按引导使用 Tools，先 `search_flights`，再 `book_flight`

少了其中任何一种原语，这个流程都会变得更别扭。

---

## 8. 客户端能力（预告）

除了 Server 向 Client 暴露的三种原语，Client 也向 Server 提供能力。这些在 `03-client/03~04` 详细讲，这里先列出来：

| 客户端能力 | 用途 | 状态（2026-07-28） | 详见 |
|-----------|------|------|------|
| Elicitation | Server 请求用户输入 | 保留，传递方式改为多轮请求 | [Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md) |
| Sampling | Server 请求 Host 的 LLM 做 completion | 已弃用（SEP-2577），改为 Server 直连 LLM API | [Sampling 让 Server 借用 Host 的 LLM（已弃用）](../03-client/03-sampling.md) |
| Roots | Client 告诉 Server 可以访问哪些目录 | 已弃用，改为工具参数 / 资源 URI / 配置 | [Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md) |

---

## 9. 按需求选原语

| 需求 | 选什么 |
|------|------|
| 让模型自动调用，会有副作用 | Tool |
| 让应用注入只读上下文 | Resource |
| 让用户在 UI 里显式启动一段流程 | Prompt |
| 让 Server 中途问用户 | Elicitation（Client 能力，用 `Resolve` / MRTR） |
| 让 Server 反过来用 LLM | ~~Sampling~~ 已弃用，Server 直接调 LLM API |
| 告诉 Server 哪些目录可用 | ~~Roots~~ 已弃用，用工具参数 / 资源 URI / 配置 |

---

## 10. 常见坑

| 坑 | 怎么避免 |
|----|----------|
| 什么都做成 Tool | 只读上下文用 Resource，模板用 Prompt |
| Resource 写成动态查询 | Resource 应该幂等、只读；「按用户问题查库」这种动作用 Tool |
| Tool description 写给人看 | 读者是 LLM，要写明用法、参数含义和边界条件 |
| 把 Prompt 当 Tool 用 | Prompt 由用户显式触发，模型不会自动调 |
| 没声明 listChanged 却发通知 | Server 必须在 capabilities 里声明对应能力，否则通知会被丢弃；2026 版的通知只发给开了 `subscriptions/listen` 的 Client |
| 不同 Server 有同名工具 | Client 端做命名空间，Server 端可以考虑加前缀 |

---

## 11. 下一步

- 协议流程与能力协商的细节：[协议生命周期（JSON-RPC、版本发现与能力协商）](./04-protocol-lifecycle.md)
- 安装 SDK 和 Inspector：[安装 Python SDK 与 Inspector](./05-installation.md)
- 跑通 Hello World：[写第一个 MCP Server 和 Client](./06-first-server.md)
- Tool 进阶（参数 schema、错误码）：[用 Tools 暴露可调用的操作](../02-server/01-tools.md)
- Resource 进阶（URI 模板、订阅）：[用 Resources 提供上下文数据](../02-server/02-resources.md)
- Prompt 进阶（消息组装、参数补全）：[用 Prompts 封装可复用的对话模板](../02-server/03-prompts.md)

## 参考资料

- 官方 Server Concepts：https://modelcontextprotocol.io/docs/learn/server-concepts
- Tools spec：https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- Resources spec：https://modelcontextprotocol.io/specification/2026-07-28/server/resources
- Prompts spec：https://modelcontextprotocol.io/specification/2026-07-28/server/prompts
