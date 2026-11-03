# MCP 04：协议生命周期（JSON-RPC、版本发现与能力协商）

> 在协议层，MCP 由三部分组成：JSON-RPC 2.0 消息、每个请求在 `_meta` 里自带的版本与能力，以及一个可选的 `server/discover` 探测。2025-11-25 及更早的版本用的是三步握手加会话，2026-07-28 把握手和会话整个拿掉了。线上两代 Client 会同时存在很长时间，所以这篇两代都讲。

---

## 1. JSON-RPC 2.0 速览

MCP 原样沿用 JSON-RPC 2.0 的消息格式，没有任何改动。一共四种消息。

### 1.1 Request（请求）
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": { /* 可选 */ }
}
```

- `jsonrpc` 必须是字符串 `"2.0"`
- `id` 可以是数字或字符串，同一会话里要唯一，用来匹配响应
- `method` 用斜杠分层的命名空间（`tools/list`、`resources/read`、`notifications/cancelled`）

### 1.2 Response（成功响应）
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { /* 任意结构 */ }
}
```

`id` 必须和请求里的 `id` 相同。

### 1.3 Error（错误响应）
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": { /* 可选附加信息 */ }
  }
}
```

JSON-RPC 规范定义的标准错误码：

| code | 含义 |
|------|------|
| -32700 | Parse error（JSON 解析失败） |
| -32600 | Invalid Request（不是合法 JSON-RPC） |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |
| -32000 ~ -32099 | Server 自定义错误区段 |

2026-07-28 给 MCP 单独划了一段：`-32000 ~ -32019` 留给各实现自定义，`-32020 ~ -32099` 由 MCP 规范保留。目前已经用到的有：

| code | 含义 |
|------|------|
| -32020 | HeaderMismatch（HTTP 头与 body 不一致） |
| -32021 | MissingRequiredClientCapability（Server 需要 Client 没声明的能力） |
| -32022 | UnsupportedProtocolVersion（`data.supported` 里列出 Server 支持的版本） |

另外，「资源不存在」从 `-32002` 改成了 `-32602`（Invalid Params）。应用层错误见[错误处理与参数校验](../02-server/08-errors-validation.md)。

### 1.4 Notification（通知）
```json
{
  "jsonrpc": "2.0",
  "method": "notifications/cancelled",
  "params": { /* 可选 */ }
}
```

通知和请求的区别在于没有 `id`，发送方不等响应。MCP 里 `notifications/*` 命名空间下的消息都是通知。

### 1.5 批处理
JSON-RPC 2.0 允许把多条消息打包成数组发送：

```json
[
  {"jsonrpc":"2.0","id":1,"method":"tools/list"},
  {"jsonrpc":"2.0","id":2,"method":"resources/list"}
]
```

不过 MCP 只在 2025-03-26 版里支持过批处理，2025-06-18 起又把它移除了，平时写代码不用考虑。

---

## 2. 两代协议：有状态握手与无状态请求

JSON-RPC 本身是无状态的，MCP 的两代规范在它上面做了不同的选择：

| | Legacy（≤ 2025-11-25） | Modern（2026-07-28 起） |
|---|---|---|
| 连接建立 | `initialize` → result → `notifications/initialized` 三步握手 | 没有握手；可选 `server/discover` 探测 |
| 版本 / 能力 / 身份 | 握手时协商一次，之后绑定在会话上 | 每个请求的 `_meta` 里都带 |
| HTTP 会话 | `Mcp-Session-Id` 头 | 没有，任何副本都能处理任何请求 |
| Server → Client 请求 | 独立请求（`sampling/createMessage`、`elicitation/create`、`roots/list`、`ping`） | 删除，改为结果里带 `inputRequests`、Client 重试（MRTR） |
| 订阅 | `resources/subscribe` + 独立 GET 流 | `subscriptions/listen` 单条长连接 |
| 日志级别 | `logging/setLevel`（会话级） | 每个请求的 `_meta` 里的 `io.modelcontextprotocol/logLevel`（协议层 Logging 本身也已弃用） |
| 心跳 | `ping` | 删除 |
| Tasks | 核心协议里的实验特性 | 移出核心，成为官方扩展 `io.modelcontextprotocol/tasks` |

> 为什么要这么改？有了握手和会话，每个连接都会粘在某个 Server 进程上，水平扩展就得共享会话存储或者做粘性路由。2026 版让每个请求自包含，负载均衡用普通的 round-robin 就行。代价是 Server 不能再反过来问 Client，于是有了 MRTR。

两代协议会共存很长时间，你手上的 Claude Code、Cursor 未必都升到了 2026 版。Python SDK 2.x 的 `MCPServer` 能同时应答两代 Client，`Client` 则会自动选择（见第 8 节）。后面讲到握手时，指的都是 Legacy 路径。

---

## 3. Modern：没有握手，只有 `_meta` 和 `server/discover`

### 3.1 每个请求自带的元数据

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": { "a": 1, "b": 2 },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/clientInfo": { "name": "raw", "version": "0" }
    }
  }
}
```

| `_meta` 键 | 必需 | 含义 |
|------------|------|------|
| `io.modelcontextprotocol/protocolVersion` | 是 | 这个请求使用的协议版本 |
| `io.modelcontextprotocol/clientCapabilities` | 是 | 与这个请求相关的 Client 能力 |
| `io.modelcontextprotocol/clientInfo` | 否（SHOULD） | Client 身份 |
| `io.modelcontextprotocol/logLevel` | 否 | 本请求要的最低日志级别 |
| `traceparent` / `tracestate` / `baggage` | 否 | OpenTelemetry 链路上下文 |

Server 不支持请求里的版本时，返回 `-32022 UnsupportedProtocolVersion`，并在 `data.supported` 里列出自己支持的版本，Client 从中选一个重试。

### 3.2 `server/discover`

Server 必须实现这个 RPC。Client 可以在发第一个请求之前调用它，一次拿到支持的版本、能力、身份和使用说明。下面是本手册 `06_first_server.py` 在 stdio 上的一次往返（一行一条消息）：

```json
{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "resultType":"complete",
  "supportedVersions":["2026-07-28"],
  "capabilities":{"prompts":{"listChanged":true},"resources":{"listChanged":true,"subscribe":true},"tools":{"listChanged":true}},
  "ttlMs":0,"cacheScope":"private",
  "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"hello-mcp","version":""}}}}
```

有几点要留意：

- `serverInfo` 不在结果体里，而是在 `_meta` 里，每个 2026 版结果都会带；身份属于可选元数据
- 所有结果都有必填的 `resultType`：`"complete"`（普通结果）或 `"input_required"`（MRTR 的中间结果，见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）。老 Server 的结果没有这个字段，Client 要当成 `"complete"` 处理
- `tools/list`、`resources/list`、`prompts/list`、`resources/read` 等结果要带缓存提示 `ttlMs`（新鲜期，毫秒）和 `cacheScope`（`public` / `private`）
- stdio 上的向后兼容：Client 先发 `server/discover`，老 Server 会报方法不存在，Client 再回退到 `initialize` 握手

### 3.3 Operation 阶段

Modern 路径上没有阶段的概念，任何请求随时都能发，Server 逐个请求校验版本和能力。Server 只能使用 Client 在 `_meta` 里声明过的能力：如果 Client 没声明 elicitation，Server 就不能给它返回 elicitation 请求，否则属于协议违规（缺能力时返回 `-32021`）。

### 3.4 Shutdown

和以前一样，MCP 没有定义 shutdown 消息，关闭交给传输层：

- stdio：Client 关闭 Server 的 stdin，等 Server 退出；超时后发 SIGTERM，再超时发 SIGKILL
- HTTP：关闭 HTTP 连接即可，2026 路径上没有会话需要释放

---

## 4. Legacy：三步握手（2025-11-25 及更早）

老客户端还在走这条路，Python SDK 的 `MCPServer` 也照常应答。完整流程如下。

### Step 1: Client → Server: initialize Request

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {
      "roots": { "listChanged": true },
      "sampling": {},
      "elicitation": { "form": {}, "url": {} }
    },
    "clientInfo": {
      "name": "claude-code",
      "title": "Claude Code",
      "version": "1.0.0"
    }
  }
}
```

| 字段 | 含义 |
|------|------|
| `protocolVersion` | Client 支持的最新版本（YYYY-MM-DD 格式） |
| `capabilities` | Client 能力清单 |
| `clientInfo` | Client 的身份。`icons` / `websiteUrl` 是 2025-11-25 新增的 |

### Step 2: Server → Client: initialize Result

`06_first_server.py` 对 `initialize` 的回包如下：

```json
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2025-11-25",
  "capabilities":{"prompts":{"listChanged":false},"resources":{"listChanged":false,"subscribe":false},"tools":{"listChanged":false}},
  "serverInfo":{"name":"hello-mcp","version":""}}}
```

- 可选的 `instructions` 是 Server 给 Host 的使用说明，Host 可以把它放进 system prompt
- 如果 Server 支持 Client 请求的版本，返回的 `protocolVersion` 就等于它；否则返回 Server 支持的最新版本，由 Client 决定是否断开
- 这里的 `listChanged` 全是 `false`：在老路径上，`MCPServer` 对 Legacy 会话如实声明，不像 Modern 路径那样总是声明 `true`

### Step 3: Client → Server: notifications/initialized

```json
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

这条发出去之后，会话才算可用，双方都可以发请求了（包括 Server → Client 的 `sampling/createMessage`、`ping`）。走 HTTP 时，Server 会在响应头里返回一个 `Mcp-Session-Id`，Client 之后的每个请求都要带上，不带会得到 `400 Missing session ID`。

---

## 5. Capabilities 全表

协商的核心是 `capabilities` 对象。某个能力只有在对应一方声明过之后，相关消息才能用。

### 5.1 Client capabilities

| 字段 | 子字段 | 含义 |
|------|--------|------|
| `elicitation` | `form: {}`、`url: {}` | 能向用户索取信息（form 是结构化表单，url 是跳到外部 URL，2025-11-25 新增） |
| `sampling` | `tools: {}`（可选） | 能替 Server 调 LLM（已弃用） |
| `roots` | `listChanged: bool`（2026 里这个子字段随通知一起删除） | 能告诉 Server 可访问的根目录（已弃用） |
| `extensions` | `{ "<扩展 ID>": {...} }` | 支持的官方或自定义扩展，如 `io.modelcontextprotocol/ui`（MCP Apps） |
| `tasks` | （2025 版）`requests: {...}` | 2025 版核心里的实验特性；2026 起移到 `io.modelcontextprotocol/tasks` 扩展 |
| `experimental` | (任意) | 非标准的实验能力 |

### 5.2 Server capabilities

| 字段 | 子字段 | 含义 |
|------|--------|------|
| `tools` | `listChanged: bool` | 有工具，工具列表变化时会通知 |
| `resources` | `listChanged: bool`、`subscribe: bool` | 有资源，资源变更可订阅 |
| `prompts` | `listChanged: bool` | 有 Prompts |
| `completions` | 无 | 支持参数补全 |
| `logging` | 无 | 能发日志通知（已弃用） |
| `extensions` | `{ "<扩展 ID>": {...} }` | 如 `io.modelcontextprotocol/tasks`、`io.modelcontextprotocol/ui` |

### 5.3 一个协商例子（Modern）

Client 在请求里声明：
```json
{"io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}}}
```
Server 在 `server/discover` 里声明：
```json
{"capabilities": {"tools": {"listChanged": true}, "resources": {"subscribe": true}}}
```

于是：
- Server 可以在 `tools/call` 的结果里要求 Client 做 `elicitation/create`，因为 Client 声明了 elicitation
- Client 可以用 `subscriptions/listen` 订阅 `toolsListChanged` 和资源更新，因为 Server 声明了
- Server 不能要求 `sampling/createMessage`，因为 Client 没声明 sampling，硬要发就是 `-32021`

---

## 6. 版本

`protocolVersion` 用 YYYY-MM-DD 日期格式，不是 SemVer。截至 2026 年 11 月：

| 版本 | 发布内容 |
|------|---------|
| `2024-11-05` | 第一版正式 spec |
| `2025-03-26` | Streamable HTTP 取代 HTTP+SSE 双端点；加入基于 OAuth 2.1 的 authorization 章节、工具注解、JSON-RPC 批处理 |
| `2025-06-18` | 结构化工具输出、Elicitation、Server 定位为 OAuth 资源服务器（RFC 9728）、移除批处理 |
| `2025-11-25` | 引入 Tasks（实验）、URL 模式 elicitation、Sampling 带工具、扩展机制雏形 |
| `2026-07-28` | 当前最新：去掉握手和会话（无状态）、`server/discover`、MRTR、`subscriptions/listen`、Tasks 变成扩展、Roots/Sampling/Logging 弃用、`resultType`、缓存提示 `ttlMs` |

Modern 路径上其实没有版本协商，只有校验：每个请求声明自己的版本，Server 不支持就返回 `-32022` 和 `supported` 列表，Client 从中挑一个重试。Legacy 路径还是老办法：Client 报自己支持的最新版本，Server 同意或者回一个自己支持的最新版本，Client 不接受就断开。

> 已经在用 MCP 的项目升级时，可以对照官方 changelog：https://modelcontextprotocol.io/specification/2026-07-28/changelog

---

## 7. 超时、重试与取消

### 7.1 超时
每个请求都应该设超时：
- 工具调用单独配置，爬虫、调大模型这类慢工具放宽到 5–10 分钟
- 收到对应请求的 `notifications/progress` 时可以重置超时，但要设一个上限，防止被滥用
- Python SDK 2.x 的超时参数是以秒为单位的 `float`（v1 是 `timedelta`）。超时会抛 `-32001`（`REQUEST_TIMEOUT`），而且会真正取消 Server 端的处理函数

超时后 Client 应该发一条取消通知（SDK 会自动发）：

```json
{
  "method": "notifications/cancelled",
  "params": {"requestId": 42, "reason": "timeout"}
}
```

### 7.2 重试
- 幂等请求（list、read、get）可以放心重试
- 有副作用的工具调用不要自动重试，交给用户决定
- Modern 下流断了：2026 版删掉了 SSE 断点续传，流一断这次请求就丢了，Client 必须作为新请求（新 id）重发。对写操作来说，这意味着要靠幂等键，不能盲目重试
- MRTR 的重试属于协议内的重试：用新 id，原样带回 `requestState`

### 7.3 心跳：`ping` 已被删除
2025 版有双向 `ping`，2026-07-28 把它整个删了，因为已经没有需要探活的长连接会话。想知道 Server 是否可达，可以调 `server/discover`（带缓存）或者一个开销很小的 `tools/list`，HTTP 层用普通的健康检查就行。对 2025 版的老连接，`ping` 依然可用，`{"method":"ping"}` 返回 `{"result":{}}`。

---

## 8. 在 Python SDK 里的样子

SDK 2.x 把握手和发现都封装进了 `Client`：

```python
import asyncio
from mcp import Client, StdioServerParameters

async def main():
    params = StdioServerParameters(command="python", args=["server.py"])

    # 进入 async with 时：发一次 server/discover，失败则回退 initialize 握手
    async with Client(params) as client:
        print(f"协议版本: {client.protocol_version}")        # '2026-07-28'
        print(f"Server: {client.server_info}")                # Implementation | None
        print(f"Server 能力: {client.server_capabilities}")
        print(f"Server 提示: {client.instructions}")

        tools = await client.list_tools()
        result = await client.call_tool("add", {"a": 1, "b": 2})

asyncio.run(main())
```

`Client(..., mode=...)` 决定协商方式：

| 写法 | 协商流量 | 得到 |
|------|---------|------|
| `Client(target)`（默认 `mode="auto"`） | 一次 `server/discover`，失败则回退 `initialize` | 双方都支持的最新版本 |
| `Client(target, mode="legacy")` | `initialize` 握手 | 2025 版会话（需要 Server 发反向请求时用） |
| `Client(target, mode="2026-07-28")` | 无 | 直接按该版本发请求，但 `server_info`、`server_capabilities` 为空 |
| `Client(target, mode="2026-07-28", prior_discover=saved)` | 无 | 复用上次保存的 `client.session.discover_result`，不需要任何协商往返 |

Client 的 capabilities 根据你传入的回调推导出来：传了 `elicitation_callback` 就自动声明 elicitation，传了 `sampling_callback` 就声明 sampling。

写 Server 也一样：

```python
from mcp.server import MCPServer

mcp = MCPServer("demo")

@mcp.tool()
def hello(name: str) -> str:
    return f"Hello, {name}!"

# MCPServer 自动应答 server/discover，自动按请求版本声明能力，
# 同时对老客户端自动应答 initialize
mcp.run()
```

平时几乎不用写协议层代码。不过知道协议层在做什么，调试时能快很多。

---

## 9. 用 Inspector 看实际流量

启动 Server：

```bash
npx @modelcontextprotocol/inspector python server.py
```

浏览器打开后切到 Notifications / Console 标签，就能看到完整的 JSON-RPC 流量。新版 Inspector 两代协议都能处理（官方文档里叫 "protocol eras"），流量大致如下：

```
→ server/discover  {_meta: {protocolVersion: "2026-07-28", clientCapabilities: ...}}
← {supportedVersions: ["2026-07-28"], capabilities: ..., resultType: "complete"}
→ tools/list
← {tools: [...], ttlMs: 0, cacheScope: "private"}
→ tools/call {name: "add", arguments: {a:1,b:2}}
← {content: [{type:"text",text:"3"}], structuredContent: {result: 3}}
```

调试时碰到卡住的情况，先在 Inspector 里看下一条请求有没有发出去、Server 是不是在等什么。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| Server 不响应任何请求 | 最常见的原因是 Server 进程的 stdout 被 `print()` 污染了 |
| 老代码要先 `session.initialize()` 才能用 | v2 的 `Client` 会自动协商；直接用 `ClientSession` 的底层写法见官方迁移指南 |
| method not found | Client 调用的能力 Server 没在 capabilities 里声明；另外 `ping`、`logging/setLevel` 在 2026 版中已移除，对 2026 连接会返回 `-32601` |
| `400 Missing session ID` | 用 curl 手写 2025 版请求时没带 `Mcp-Session-Id`；2026 版请求不需要它，但必须带 `MCP-Protocol-Version` 头 |
| `-32022` 版本不兼容 | 看 `data.supported`；SDK 的 Client 会自动重选，手写 Client 要自己处理 |
| `NoBackChannelError` | 在 2026 连接上调用了 `ctx.elicit()` / `create_message()`。Server 不能主动问 Client，改用 `Resolve(...)`（见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)） |
| id 冲突 | 同一连接里的 id 必须唯一。SDK 用自增计数；MRTR 重试必须换新 id |
| HTTP 请求没带 `MCP-Protocol-Version` 头 | 远程 MCP 服务会直接拒绝 |

---

## 11. 下一步

- 安装 SDK 和 Inspector：[安装 Python SDK 与 Inspector](./05-installation.md)
- Hello World Server：[写第一个 MCP Server 和 Client](./06-first-server.md)
- 传输层细节：[传输方式 stdio、Streamable HTTP 与进程内](../03-client/02-transports.md)
- Server 反向提问（MRTR）：[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)
- 错误处理：[错误处理与参数校验](../02-server/08-errors-validation.md)

## 参考资料

- Versioning 与兼容：https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- `server/discover`：https://modelcontextprotocol.io/specification/2026-07-28/server/discover
- 变更清单：https://modelcontextprotocol.io/specification/2026-07-28/changelog
- 上一版 Lifecycle（握手）：https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- JSON-RPC 2.0：https://www.jsonrpc.org/specification
- Python SDK 协议版本：https://py.sdk.modelcontextprotocol.io/protocol-versions/
