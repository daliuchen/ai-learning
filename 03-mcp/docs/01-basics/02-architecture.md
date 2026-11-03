# MCP 02：Host、Client、Server 与两层架构

> MCP 的架构可以拆成两件事：Host、Client、Server 三个角色各管什么，以及数据层（JSON-RPC 2.0）和传输层（stdio / Streamable HTTP）怎么分开。把这两件事弄清楚，剩下的协议细节基本都是在 JSON-RPC 上加约定。

---

## 1. 三个角色

官方文档反复强调 Host 和 Client 不是一回事，这也是新手最容易混的地方，先分清楚。

| 角色 | 中文 | 是什么 | 例子 |
|------|------|--------|------|
| MCP Host | 宿主 | AI 应用本身，用户直接面对的产品 | Claude Code / Claude Desktop / Cursor / VS Code / ChatGPT Desktop |
| MCP Client | 客户端 | Host 内部的一个对象或组件，和一个 Server 一对一绑定 | VS Code 里负责和 Sentry MCP Server 通信的那个对象 |
| MCP Server | 服务器 | 提供能力的程序，可以是本地子进程，也可以是远程服务 | filesystem / postgres / Sentry / GitHub MCP Server |

一个 Host 里会有多个 Client，每个 Client 各管一个 Server 连接。

```
┌──────────────────── MCP Host: VS Code ────────────────────┐
│                                                            │
│  ┌─Client A─┐  ┌─Client B─┐  ┌─Client C─┐  ┌─Client D─┐  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
└───────┼─────────────┼─────────────┼─────────────┼─────────┘
        │             │             │             │
   ┌────▼────┐  ┌─────▼────┐  ┌────▼─────┐  ┌────▼──────┐
   │filesystem│  │ postgres │  │  Sentry  │  │  GitHub   │
   │ (本地)   │  │  (本地)  │  │  (远程)  │  │  (远程)   │
   └──────────┘  └──────────┘  └──────────┘  └───────────┘
```

> 一个 Server 对应一个 Client 是协议层面的约束。Host 想连 4 个 Server，就开 4 个 Client 实例，这样各连接的状态、能力协商和消息 ID 序列都互相隔离。

---

## 2. 三个角色各自的职责

### Host

- UI 与用户交互：渲染对话、显示工具调用、申请权限
- 调用模型：和 OpenAI / Anthropic / Gemini API 通信
- 管理 Client 生命周期：按配置启动、关闭 Client，崩溃后重连
- 统一多个 Server：合并各 Server 的工具、解决命名冲突、按权限过滤
- 人在回路：用户确认弹窗、敏感操作审批

### Client

- 收发 JSON-RPC 消息：序列化、ID 管理、超时
- 版本与能力协商：2026-07-28 起没有握手，每个请求都在 `_meta` 里带协议版本和自己的 capabilities，连接前可以用 `server/discover` 探测 Server（遇到老 Server 则回退到 `initialize` 握手）
- 代理 Server 的提问：Server 在结果里要求补充输入时（elicitation，以及已弃用的 sampling / roots），转给 Host 处理，再带着答案重试请求
- 错误恢复：Server 进程退出时报告给 Host

Client 不做产品逻辑，它就是一个协议适配器。

### Server

- 暴露原语：注册 Tools / Resources / Prompts
- 执行业务逻辑：查数据库、调 API、读文件这些实际干活的代码
- 向 Client 要输入（可选）：Client 声明了 elicitation 能力时，Server 可以让它去问用户（2026-07-28 起通过多轮请求实现，见第 8 节）

Server 不知道对面是 Claude Code 还是 Cursor，不知道用的是哪个 LLM，也不知道用户是谁。它只和 Client 按协议通信。

---

## 3. 本地与远程：同一个 Server 的两种部署形态

MCP Server 指的是一个程序，不一定是远程服务。常见的部署形态有两种。

### 本地 Server（local server）

本地 Server 作为 Host 的子进程启动（比如 `python my-server.py`），通过 stdio（标准输入输出）和 Host 里对应的 Client 通信。每个用户各自启动一份实例，是一对一的关系。filesystem、git、本地数据库都属于这一类。

```
   Host 进程              Server 子进程
   ┌─────────┐  stdin    ┌─────────┐
   │ Client  │──────────>│ Server  │
   │         │<──────────│         │
   └─────────┘  stdout   └─────────┘
```

### 远程 Server（remote server）

远程 Server 是独立部署的 HTTP 服务，比如跑在自家的 K8s 里，通过 Streamable HTTP（POST，可选 SSE）和 Client 通信。一个实例同时服务多个用户，所以需要鉴权，官方推荐 OAuth 2.1。Sentry、Linear、Notion 以及企业内部的 SaaS 集成大多是这种形态。

```
   Host A (Alice)            Server (跑在云上)
   ┌─────────┐  HTTPS       ┌─────────────┐
   │ Client  │─────────────>│             │
   │         │<─────────────│             │
   └─────────┘              │   Multi-    │
                            │  tenant     │
   Host B (Bob)             │   Server    │
   ┌─────────┐  HTTPS       │             │
   │ Client  │─────────────>│             │
   │         │<─────────────│             │
   └─────────┘              └─────────────┘
```

### 怎么选

| 维度 | 本地 Server（stdio） | 远程 Server（HTTP） |
|------|---------------------|---------------------|
| 延迟 | 最低（无网络） | 网络 RTT |
| 鉴权 | 进程隔离 + 文件权限 | OAuth / API Key / Bearer |
| 升级 | 用户自己更新 | 后端发版即生效 |
| 多用户共享状态 | 不支持 | 天然支持 |
| 离线可用 | 可以 | 不行 |
| 适合 | 工具型 / 文件系统 / 本地工具链 | SaaS 集成 / 企业内部数据 |

> 同一个 Server 项目既能以本地方式运行，也能以远程方式运行，业务代码不变，只换传输层。在 Python SDK 里就是 `mcp.run(transport="stdio")` 和 `mcp.run(transport="streamable-http")` 的区别。

---

## 4. 两层模型：数据层和传输层

MCP 设计里最重要的一个决定是协议分层。

```
┌──────────────────────────────────────────┐
│  Data Layer（数据层）                     │
│  - JSON-RPC 2.0                          │
│  - lifecycle: server/discover + _meta    │
│  - primitives: tools / resources / ...   │
│  - notifications                         │
└──────────────────────────────────────────┘
              ↓ 复用 ↓
┌──────────────────────────────────────────┐
│  Transport Layer（传输层）                │
│  - stdio                                 │
│  - Streamable HTTP（POST + SSE）         │
│  ... 可扩展                              │
└──────────────────────────────────────────┘
```

数据层规定「说什么」，传输层规定「怎么传」，两者互不依赖：

- 同一条 `tools/call` 请求，在 stdio 下写进子进程的 stdin，在 HTTP 下放进 POST body，内容一个字都不变
- 加新的传输方式（比如 WebSocket）不用改数据层（官方 Python SDK 2.x 反倒把非标准的 WebSocket 传输删掉了）
- 加新的原语（比如 Tasks 扩展）不用改传输层

思路和 HTTP 区分应用层、传输层是一样的。

---

## 5. 数据层要点

详细内容在「协议生命周期」那篇，这里只列大纲。

### 5.1 消息格式：JSON-RPC 2.0
一共四种消息：

| 类型 | 例子 | 是否要响应 |
|------|------|----------|
| Request | `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` | 要 |
| Response | `{"jsonrpc":"2.0","id":1,"result":{...}}` | 不适用 |
| Error | `{"jsonrpc":"2.0","id":1,"error":{...}}` | 不适用 |
| Notification | `{"jsonrpc":"2.0","method":"notifications/cancelled","params":{...}}` | 不要（没有 id） |

### 5.2 生命周期：2026-07-28 起没有握手

```
2025-11-25 及更早（有状态）            2026-07-28（无状态）
Client            Server              Client            Server
  │ initialize ──────>│                │ server/discover ─>│  ← 可选的探测
  │<────── result ────┤                │<────── result ────┤
  │ notifications/    │                │                   │
  │   initialized ───>│                │ tools/call ──────>│  ← 每个请求自带
  │ tools/call ──────>│                │  _meta: 版本 +    │    版本和 capabilities
  │      ...          │                │  capabilities     │
```

2026-07-28 去掉了 `initialize` / `notifications/initialized` 握手和协议层会话（`Mcp-Session-Id`）。现在每个请求都在 `_meta` 里自带协议版本和 Client 能力，Server 的能力和支持的版本通过 `server/discover` 查询。这样任何一个副本都能处理任何请求，远程 MCP 可以直接放在普通负载均衡后面。官方 Python SDK 2.x 两代协议都支持：`Client` 先发 `server/discover`，失败了再回退到 `initialize`（详见「协议生命周期」）。

### 5.3 服务端原语
- `tools/list`、`tools/call`
- `resources/list`、`resources/templates/list`、`resources/read`（订阅走 `subscriptions/listen`，2025 版是 `resources/subscribe`）
- `prompts/list`、`prompts/get`

### 5.4 客户端原语
- `elicitation/create`（让 Client 向用户要输入）
- `sampling/createMessage`（让 Client 调 LLM），已弃用
- `roots/list`，已弃用
- `notifications/message`（协议层日志），已弃用

在 2026-07-28 里，这些客户端原语不再是 Server 主动发给 Client 的独立请求，而是放在 Server 的结果里（`InputRequiredResult`）等 Client 回答，见第 8 节。

---

## 6. 传输层之一：stdio

stdio 最简单，也最常用。Host 启动 Server 子进程，两边各自读写 stdin/stdout：

```
Host process                     Server process
  │                                   │
  │  spawn("python server.py")        │
  ├──────────────────────────────────>│
  │                                   │
  │  write stdin  (一行一条 JSON)     │
  ├──────────────────────────────────>│
  │                                   │
  │  read stdout  (一行一条 JSON)     │
  │<──────────────────────────────────┤
  │                                   │
  │  (Server 必须把日志写 stderr，    │
  │   不能污染 stdout 上的 JSON)      │
```

帧格式是按行分隔的 JSON（newline-delimited JSON）：每条消息占一行，以 `\n` 结尾，消息体里不能有换行。没有 LSP 那种 `Content-Length` 头。

```
{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{...}}\n
{"jsonrpc":"2.0","id":1,"result":{...}}\n
```

> 帧格式由 Python SDK 自动处理，不用自己拼。但本地 Server 里千万别用 `print()` 调试，它会污染 stdout。要用 `logging`（默认输出到 stderr）；协议层的 `ctx.log` 方法已在 2026-07-28 弃用。

### 关闭流程
1. Client 关闭 Server 的 stdin
2. 等 Server 自己退出
3. 超时后依次发 `SIGTERM`、`SIGKILL`

---

## 7. 传输层之二：Streamable HTTP

Streamable HTTP 由 2025-03 版 spec 引入，取代了早期「HTTP+SSE 双端点」的传输（后者在 2026-07-28 被正式标为 Deprecated），现在是远程 MCP 的事实标准。

它只有一个端点（如 `/mcp`），Client 把每条 JSON-RPC 消息 POST 上去：

```
        ① Client POST  (一次性请求-响应)
Client ────────────────────────────> Server
       <────────────────────────────
        JSON response (Content-Type: application/json)


        ② Client POST  (Server 要流式返回：进度通知等)
Client ────────────────────────────> Server
       <══════════════════════════════════════
        SSE stream  (Content-Type: text/event-stream)
        event: message
        data: {"jsonrpc":"2.0","method":"notifications/progress",...}
        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}
```

几个特点：

- 单端点，请求走 POST；响应可以是普通 JSON，也可以升级成这个 POST 的 SSE 响应流
- 2026-07-28 删掉了独立的 GET 流。Server 想推送「列表变了」这类通知，得由 Client 先 POST `subscriptions/listen` 开一条长连接；进度这类请求级通知只出现在对应请求的响应流里
- 2026-07-28 删掉了断点续传（`Last-Event-Id`）。流断了请求就丢了，Client 必须用新的请求 ID 重发
- 鉴权用标准 HTTP 鉴权头，推荐 OAuth 2.1

### HTTP Header 约定（2026-07-28）
- `MCP-Protocol-Version: 2026-07-28`：每个请求都要带
- `Mcp-Method: tools/call`、`Mcp-Name: add`：新增，让网关和限流器不解析 body 也能路由（SEP-2243）
- `Authorization: Bearer ...`：鉴权
- ~~`Mcp-Session-Id`~~：2026 路径上不再有；2025 版老 Client 仍会带，SDK 对它们保持原行为

用 `curl` 直接 POST 到本地 Server 看看（没有握手，也没有 session）：

```bash
curl -si -X POST localhost:8000/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/call' -H 'Mcp-Name: add' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"add","arguments":{"a":1,"b":2},
       "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
# HTTP/1.1 200 OK    content-type: application/json     （没有 mcp-session-id）
# {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"3","type":"text"}],"isError":false,
#   "resultType":"complete","structuredContent":{"result":3},"_meta":{"io.modelcontextprotocol/serverInfo":{...}}}}
```

> 传输层的详细实现（stdio、Streamable HTTP、对老客户端的兼容）见[传输方式 stdio、Streamable HTTP 与进程内](../03-client/02-transports.md)。

---

## 8. 谁向谁发请求：从双向 RPC 到多轮请求

2025-11-25 及更早的版本里，MCP 是对称的：Server 也能主动给 Client 发请求（`sampling/createMessage`、`elicitation/create`、`roots/list`、`ping`），Client 同时扮演 RPC Server。

2026-07-28 把所有 Server → Client 的请求都删了，换成 Multi Round-Trip Requests（MRTR，多轮请求）。Server 不再反过来发问，而是在当前请求的结果里说明还需要哪些信息，Client 收集好答案后重试原请求：

```
Client → Server:  tools/call reserve {title:"Dune"}                       (id=1)
Server → Client:  result {resultType:"input_required",
                         inputRequests:{ qty:{method:"elicitation/create", params:{message:"几本？",...}} },
                         requestState:"<加密的不透明字符串>"}
        （Client 向用户提问，拿到答案）
Client → Server:  tools/call reserve {title:"Dune",                        (id=2，新 ID)
                         inputResponses:{qty:{action:"accept",content:{copies:2}}},
                         requestState:"<原样回传>"}
Server → Client:  result {resultType:"complete", content:[...]}
```

这样改的原因是：Server 没有了回头找 Client 的通道，请求就不用绑定在某条连接或某个 worker 上，任意副本都能处理重试，也就不需要共享存储或粘性会话。2026 版的无状态设计就建立在这一点上。

- 老的反向请求（`ctx.elicit()`）在 2025 版连接上仍然可用，在 2026 版连接上会抛 `NoBackChannelError`
- Python SDK 提供 `Resolve(...)` 依赖，让一份工具代码同时服务两代 Client（见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）
- `ping` 被删除，`logging/setLevel`、`notifications/roots/list_changed` 也一起删了

---

## 9. 完整连接流程

把前面的概念串起来，从 Host 启动到第一次工具调用：

```
[1] 用户在 Claude Code 设置里配了 my-server
    ↓
[2] Claude Code (Host) 启动 my-server 子进程，
    实例化一个 Client 对象，stdin/stdout 接上
    ↓
[3] Client 发 server/discover（可选），拿到 Server 的版本、能力、身份
    （老 Server 不认识它 → 回退到 initialize 握手）
    ↓
[4] Client 之后每个请求都在 _meta 里带上协议版本和自己的 capabilities
    ↓
[5] Client 调 tools/list 拉到所有工具的 JSON Schema
    Host 把这些工具的描述存起来，标注来源是 my-server
    ↓
[6] 用户在 Claude Code 发起对话："帮我加 2 和 3"
    ↓
[7] Host 把所有 MCP 工具 + 内置工具 一起塞给 Anthropic API
    ↓
[8] Claude 模型返回 tool_use：{name:"add", input:{a:2, b:3}}
    ↓
[9] Host 把它路由到 my-server 的 Client
    ↓
[10] Client 发 tools/call 请求
    ↓
[11] Server 执行 add() 函数返回 5
    ↓
[12] Client 收到 5，转给 Host
    ↓
[13] Host 把 5 作为 tool_result 给模型再要一次 completion
    ↓
[14] 模型回复："2 + 3 = 5"，展示给用户
```

第 [3]–[4] 步是发现与协商，第 [10]–[12] 步是工具调用。如果工具执行到一半需要用户补充信息，Server 会返回 `input_required` 结果，Client 问完用户后重试第 [10] 步。

---

## 10. 用一段 Python 代码看整套架构

下面这段同时跑了一个模拟 Host 的 Client 和一个 Server，能看清协议落到代码里是什么样子：

```python
# server.py - 简单的 MCP Server
from mcp.server import MCPServer

mcp = MCPServer("demo-server")

@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b

if __name__ == "__main__":
    mcp.run()  # 默认 stdio
```

```python
# client.py - 一个最小的 Host/Client，启动 server 子进程并调它
import asyncio
from mcp import Client, StdioServerParameters

async def main():
    params = StdioServerParameters(command="python", args=["server.py"])

    # async with 进入时完成协议版本协商（server/discover，必要时回退 initialize）
    async with Client(params) as client:
        # 1. 连接信息
        print(f"Server: {client.server_info.name}  协议: {client.protocol_version}")
        print(f"Capabilities: {client.server_capabilities.model_dump(exclude_none=True)}")

        # 2. 发现
        tools = await client.list_tools()
        for t in tools.tools:
            print(f"工具: {t.name} - {t.description}")

        # 3. 调用
        result = await client.call_tool("add", {"a": 2, "b": 3})
        print(f"结果: {result.content[0].text}")

asyncio.run(main())
```

运行：

```bash
python client.py
# 输出：
# Server: demo-server  协议: 2026-07-28
# Capabilities: {'prompts': {...}, 'resources': {...}, 'tools': {'list_changed': True}}
# 工具: add - 两数相加
# 结果: 5
```

> 在 v1 里，这段要手写三层嵌套（`stdio_client` → `ClientSession` → `await session.initialize()`）；v2 的 `Client` 把它们合成了一个对象，需要底层能力时用 `client.session` 拿到 `ClientSession`。

二十来行 Client 加不到十行 Server，就是 MCP 架构最小的完整实现。

---

## 11. 常见坑

| 坑 | 怎么避免 |
|----|----------|
| 把 Host 和 Client 搞混 | Host 是 VS Code 这样的应用，Client 是 VS Code 内部连某个 Server 的对象，一个 Server 对应一个 Client |
| 本地 Server 里用 `print()` 调试 | stdout 是协议通道，被污染后 Client 会出错。改用 `logging`（默认写 stderr） |
| 以为远程 MCP 就是 REST API | 它是 Streamable HTTP（POST + 可选 SSE 响应流），每个请求自带协议版本等 `_meta`，不是普通 REST |
| 跨 Server 工具重名 | 不同 Server 可能都有 `search` 工具，Host 要加命名空间（如 `github__search`） |
| 还在让 Server 主动发反向请求 | 2026-07-28 起 Server 不能再主动发 `elicitation/create` 等请求，要用 `Resolve` / `InputRequiredResult`（见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）；Sampling 和 Roots 已弃用 |

---

## 12. 下一步

- 三大原语分别是什么、适用什么场景：[三种原语 Tools、Resources 与 Prompts](./03-primitives.md)
- 完整的协议流程与能力协商：[协议生命周期（JSON-RPC、版本发现与能力协商）](./04-protocol-lifecycle.md)
- 跑通本篇示例：[安装 Python SDK 与 Inspector](./05-installation.md)，然后看 [写第一个 MCP Server 和 Client](./06-first-server.md)

## 参考资料

- 官方架构总览：https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture
- 传输层 spec：https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
- 多轮请求（MRTR）：https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr
- Python SDK：https://github.com/modelcontextprotocol/python-sdk
