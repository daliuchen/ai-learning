# MCP 02：架构总览 —— Host / Client / Server 与两层模型

> **一句话**：MCP 是 **Host / Client / Server 三角架构** + **数据层（JSON-RPC 2.0）+ 传输层（stdio / Streamable HTTP）双层分离** 的设计。理解这两件事，整个协议就只是"在 JSON-RPC 上加几个约定"。

---

## 1. 三个角色的精确定义

官方反复强调一点：**Host ≠ Client**。这是新手最容易混的概念，先把它彻底分清。

| 角色 | 中文 | 是什么 | 例子 |
|------|------|--------|------|
| **MCP Host** | 宿主 | AI 应用本身，用户面对的产品 | Claude Code / Claude Desktop / Cursor / VS Code / ChatGPT Desktop |
| **MCP Client** | 客户端 | Host **内部**的一个对象/组件，**一对一**绑定一个 Server | VS Code 里负责"和 Sentry MCP Server 通信"的那个对象 |
| **MCP Server** | 服务器 | 提供能力的程序，可以是本地子进程也可以是远程服务 | filesystem / postgres / Sentry / GitHub MCP Server |

关键事实：**一个 Host 内会有多个 Client，每个 Client 各管一个 Server 连接。**

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

> "一个 Server 对应一个 Client"是协议约束。同一个 Host 想连 4 个 Server 就开 4 个 Client 实例。这是为了让连接状态、能力协商、消息 ID 序列都被严格隔离。

---

## 2. Host 干什么 / Client 干什么 / Server 干什么

### Host 的职责

- **UI 与用户交互**：渲染对话、显示工具调用、申请权限
- **模型调用**：和 OpenAI / Anthropic / Gemini API 通信
- **Client 生命周期管理**：根据配置启动/关闭 Client，崩溃重连
- **跨 Server 统一**：把多个 Server 提供的工具合并、解决命名冲突、按权限过滤
- **人在回路**：用户确认弹窗、敏感操作审批

### Client 的职责

- **JSON-RPC 消息收发**：序列化、ID 管理、超时
- **生命周期管理**：发 `initialize`、协商 capability、维持心跳、关闭
- **Server 能力代理**：当 Server 反向请求 sampling / elicitation / roots 时，转交给 Host 处理
- **错误恢复**：Server 进程退出时报告 Host

Client 不做产品逻辑，**它只是个协议适配器**。

### Server 的职责

- **暴露原语**：注册 Tools / Resources / Prompts
- **执行业务逻辑**：真正干活的代码（查数据库、调 API、读文件）
- **发起反向请求**（可选）：当声明了 sampling/elicitation 能力时，可以反过来问 LLM 或问用户

Server **不知道**对面是 Claude Code 还是 Cursor，**不知道**用的是哪个 LLM，**不知道**用户名是谁。它只跟 Client 协议讲话。

---

## 3. 本地 vs 远程：同一个 Server，两种部署形态

MCP Server 这个词指的是"程序"，不是"远程服务"。它有两种典型形态：

### 本地 Server（local server）

- 作为 Host 的**子进程**启动（典型：`python my-server.py`）
- 通过 **stdio**（标准输入输出）和 Host 内对应的 Client 通信
- **一对一**：每个用户启动一份自己的 Server 实例
- 典型例子：filesystem、git、本地数据库

```
   Host 进程              Server 子进程
   ┌─────────┐  stdin    ┌─────────┐
   │ Client  │──────────>│ Server  │
   │         │<──────────│         │
   └─────────┘  stdout   └─────────┘
```

### 远程 Server（remote server）

- 独立部署的 HTTP 服务（典型：跑在自家 K8s 里）
- 通过 **Streamable HTTP**（POST + 可选 SSE）和 Client 通信
- **多对一**：同一个 Server 实例同时服务多个用户
- 需要鉴权（OAuth 2.1 是官方推荐方案）
- 典型例子：Sentry / Linear / Notion / 企业内部 SaaS 集成

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

### 选型对照

| 维度 | 本地 Server（stdio） | 远程 Server（HTTP） |
|------|---------------------|---------------------|
| 延迟 | 最低（无网络） | 网络 RTT |
| 鉴权 | 进程隔离 + 文件权限 | OAuth / API Key / Bearer |
| 升级 | 用户更新 | 后端发版即生效 |
| 多用户共享状态 | ❌ 不能 | ✅ 天然支持 |
| 离线可用 | ✅ | ❌ |
| 适合 | 工具型 / 文件系统 / 本地工具链 | SaaS 集成 / 企业内部数据 |

> 一个 Server 项目既可以编出本地版本也可以编出远程版本（同一份业务代码，换个传输层启动）。Python SDK 用 `mcp.run(transport="stdio")` 还是 `mcp.run(transport="streamable-http")` 一行切换。

---

## 4. 两层模型：数据层 + 传输层

这是 MCP 设计的最关键决策——**协议分层**。

```
┌──────────────────────────────────────────┐
│  Data Layer（数据层）                     │
│  - JSON-RPC 2.0                          │
│  - lifecycle: initialize / initialized   │
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

**数据层定义"说什么"，传输层定义"怎么传"。** 它俩正交：

- 同一份 `initialize` 请求消息，stdio 下走子进程 stdin，HTTP 下走 POST body，内容一字不变
- 加一个新传输（比如 WebSocket）不用动数据层
- 加一个新原语（比如 Tasks 扩展）不用动传输层

这跟 HTTP 的"应用层 / 传输层"分离思路一模一样。

---

## 5. 数据层：你只需要记住四件事

详细一篇放到 `04-protocol-lifecycle.md`，这里只列大纲：

### 5.1 消息格式：JSON-RPC 2.0
四种消息：

| 类型 | 例子 | 是否要响应 |
|------|------|----------|
| Request | `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` | ✅ |
| Response | `{"jsonrpc":"2.0","id":1,"result":{...}}` | — |
| Error | `{"jsonrpc":"2.0","id":1,"error":{...}}` | — |
| Notification | `{"jsonrpc":"2.0","method":"notifications/initialized"}` | ❌（没有 id） |

### 5.2 生命周期：三步握手 + 操作 + 关闭

```
Client                                Server
  │  initialize request                 │
  ├────────────────────────────────────>│
  │                                     │
  │              initialize result      │
  │<────────────────────────────────────┤
  │                                     │
  │  notifications/initialized          │
  ├────────────────────────────────────>│
  │                                     │
  │   ← 操作阶段，任何方向任何请求 →     │
  │                                     │
  │  (关闭由传输层负责)                 │
```

握手时双方交换 **capabilities**（能力清单），决定整个会话里哪些功能可用。

### 5.3 服务端原语
- `tools/list`、`tools/call`
- `resources/list`、`resources/templates/list`、`resources/read`、`resources/subscribe`
- `prompts/list`、`prompts/get`

### 5.4 客户端原语
- `sampling/createMessage`（Server → Client 要 LLM 调用）
- `elicitation/create`（Server → Client 要用户输入）
- `roots/list`、`notifications/roots/list_changed`
- `notifications/message`（日志）

---

## 6. 传输层 1：stdio

stdio 是最简单也最常用的传输。Host 启动 Server 子进程，两边读写自己的 stdin/stdout：

```
Host process                     Server process
  │                                   │
  │  spawn("python server.py")        │
  ├──────────────────────────────────>│
  │                                   │
  │  write stdin  (LSP-style frame)   │
  ├──────────────────────────────────>│
  │                                   │
  │  read stdout (LSP-style frame)    │
  │<──────────────────────────────────┤
  │                                   │
  │  (Server 必须把日志写 stderr，    │
  │   不能污染 stdout 上的 JSON)      │
```

帧格式与 LSP（Language Server Protocol）一致：

```
Content-Length: 123\r\n
\r\n
{"jsonrpc": "2.0", ...}   ← 紧接着 123 字节
```

> Python SDK 自动处理帧格式，你不需要自己拼。但**注意**：本地 Server 里千万别 `print()` 调试，会污染 stdout。要用 `logging` 输出到 stderr 或用 MCP 的 `ctx.log` 方法。

### 关闭流程
1. Client 关闭 Server 的 stdin
2. 等 Server 自己退出
3. 超时 → `SIGTERM` → `SIGKILL`

---

## 7. 传输层 2：Streamable HTTP

这是 2025-03 spec 引入、取代了早期"HTTP+SSE 双端点"的新传输。**远程 MCP 的事实标准**。

核心：**一个端点（如 `/mcp`）**，同时接受 POST（发请求）和 GET（建 SSE 流接收 Server→Client 消息）。

```
        ① Client POST  (一次性请求-响应)
Client ────────────────────────────> Server
       <────────────────────────────
        JSON response (Content-Type: application/json)


        ② Client POST  (Server 要发流式响应)
Client ────────────────────────────> Server
       <══════════════════════════════════════
        SSE stream  (Content-Type: text/event-stream)
        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}


        ③ Client GET  (订阅 Server → Client 消息)
Client ────────────────────────────> Server
       <══════════════════════════════════════
        SSE stream（长连接，Server 主动 push 通知 / 反向请求）
```

特点：

- **单端点**（早期 SSE 版本要两个端点：一个 POST、一个 GET stream）
- **可选 SSE**：Server 想流式返回时升级到 SSE，否则就普通 JSON
- **支持 Resumable Stream**：通过 `Last-Event-Id` 头恢复中断的 SSE 流
- **支持鉴权**：标准 HTTP 鉴权头 + 推荐 OAuth 2.1

### HTTP Header 约定
- `MCP-Protocol-Version: 2025-11-25` ← initialize 后所有请求必须带
- `Authorization: Bearer ...` ← 鉴权
- `Mcp-Session-Id: ...` ← Server 给的会话 ID（如果 Server 选择有状态）

> 详细的传输层实现（含 stdio frame 解析、SSE 续传、stateless vs stateful 服务端）放到 `03-client/02-transports.md`。

---

## 8. 谁向谁发请求？双向 RPC

很多人以为 MCP 是单向的（Client → Server），其实**两个方向都能发请求**。这是 MCP 区别于 REST 的关键。

```
Client → Server 的请求（最常见）:
  - initialize
  - tools/list, tools/call
  - resources/list, resources/read
  - prompts/list, prompts/get
  - ping

Server → Client 的请求（也很重要）:
  - sampling/createMessage      ← 我要调一次 LLM
  - elicitation/create          ← 我要问用户一个问题
  - roots/list                  ← 我能操作哪些目录？
  - ping
```

也就是说 Client 同时是 RPC Server，Server 也同时是 RPC Client。MCP 把它叫做 **"对称"协议**。

为什么这么设计？因为 MCP Server 写在外部、不能持有 LLM Key、不能弹用户 UI。它要 LLM 帮忙时（比如"分析 47 个航班选哪个最好"），就得反过来求 Host 帮它做这件事——这就是 **Sampling**。

---

## 9. 完整连接流程（一图流）

下面把上面所有的概念串成一张图，从 Host 启动到第一次工具调用：

```
[1] 用户在 Claude Code 设置里配了 my-server
    ↓
[2] Claude Code (Host) 启动 my-server 子进程，
    实例化一个 Client 对象，stdin/stdout 接上
    ↓
[3] Client 发 initialize 请求
    {protocolVersion, clientCapabilities, clientInfo}
    ↓
[4] Server 回 initialize 响应
    {protocolVersion, serverCapabilities, serverInfo, instructions}
    ↓
[5] Client 发 notifications/initialized
    ↓
[6] Client 调 tools/list 拉到所有工具的 JSON Schema
    Host 把这些工具的描述存起来，标注来源是 my-server
    ↓
[7] 用户在 Claude Code 发起对话："帮我加 2 和 3"
    ↓
[8] Host 把所有 MCP 工具 + 内置工具 一起塞给 Anthropic API
    ↓
[9] Claude 模型返回 tool_use：{name:"add", input:{a:2, b:3}}
    ↓
[10] Host 把它路由到 my-server 的 Client
    ↓
[11] Client 发 tools/call 请求
    ↓
[12] Server 执行 add() 函数返回 5
    ↓
[13] Client 收到 5，转给 Host
    ↓
[14] Host 把 5 作为 tool_result 给模型再要一次 completion
    ↓
[15] 模型回复："2 + 3 = 5"，展示给用户
```

第 [3]-[5] 步是**握手**，第 [11]-[13] 步是**工具调用**。中间还可能有 Server → Client 的 sampling/elicitation/log 通知，是双向流量。

---

## 10. 一段 Python 代码看清整套架构

下面这段把"Host 模拟器（Client 端）+ Server"全跑通，让你看清协议在代码里长什么样：

```python
# server.py - 简单的 MCP Server
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-server")

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
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["server.py"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. 握手
            init_result = await session.initialize()
            print(f"Server: {init_result.serverInfo.name}")
            print(f"Capabilities: {init_result.capabilities}")

            # 2. 发现
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"工具: {t.name} - {t.description}")

            # 3. 调用
            result = await session.call_tool("add", {"a": 2, "b": 3})
            print(f"结果: {result.content[0].text}")

asyncio.run(main())
```

跑起来：

```bash
python client.py
# 输出：
# Server: demo-server
# Capabilities: ServerCapabilities(tools=...)
# 工具: add - 两数相加
# 结果: 5
```

这就是 MCP 架构的最小完整实现——14 行 Client + 6 行 Server。

---

## 11. 常见坑

| 坑 | 怎么避免 |
|----|----------|
| **把 Host 和 Client 搞混** | Host 是 VS Code 这种应用，Client 是 VS Code 内部连某个 Server 的对象，**一个 Server 一个 Client** |
| **本地 Server 里 `print()` 调试** | stdout 是协议通道，污染会让 Client 崩。要用 logging→stderr 或 `ctx.log` |
| **以为远程 MCP = REST API** | 是 Streamable HTTP（POST + 可选 SSE），不是普通 REST |
| **跨 Server 工具命名冲突** | 不同 Server 可能都有 `search` 工具，Host 要做 namespace（如 `github__search`） |
| **忘了 Server → Client 反向请求** | 写 Server 时 sampling/elicitation 能力很容易被忽略，但很强大（见 03-client/03 和 04） |

---

## 12. 下一步

- 📖 三大原语具体是什么、各自适用场景 → [03-primitives.md](./03-primitives.md)
- 📖 完整协议握手与能力协商 → [04-protocol-lifecycle.md](./04-protocol-lifecycle.md)
- 🛠️ 跑通本篇示例 → [05-installation.md](./05-installation.md) + [06-first-server.md](./06-first-server.md)

## 参考资料

- 官方架构总览：https://modelcontextprotocol.io/docs/learn/architecture
- 传输层 spec：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Python SDK：https://github.com/modelcontextprotocol/python-sdk
