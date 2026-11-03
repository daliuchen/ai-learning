# MCP 学习手册

> 这是一套对照 [Model Context Protocol 官方文档](https://modelcontextprotocol.io) 写的中文教程。MCP 是 Anthropic 在 2024 年牵头推出的开放协议，用来把 AI 应用和外部工具、数据连起来，现在 OpenAI、Google、Microsoft、JetBrains、Cursor、Continue 等都已支持，官方把它比作「AI 应用的 USB-C」。手册基于 MCP 规范 2026-07-28（也会讲它和 2025-11-25 的差异）与官方 Python SDK `mcp` 2.3.0，2026 年 11 月更新。

---

## 一、适合谁读

我写的时候主要想着这几类读者：

- 不满足于「会改 `mcp.json`」、想从协议层弄懂 MCP 的工程师
- 想用 Python 写 MCP Server，把内部系统接进 Claude Code、Cursor、VS Code 等客户端的开发者
- 用 LangChain 或 Pydantic AI 写过 Agent，想知道 MCP 在工具链里处在什么位置的人
- 要在生产环境部署远程 MCP、需要考虑 OAuth、企业鉴权、可观测和安全的架构师

官网把内容拆成很多短页面，这里换了个组织方式：每个主题写成一篇长文，大部分配有能跑的 demo。我尽量做到几件事：讲 API 的同时讲清楚协议报文和背后的原因；每个特性标出对应的规范章节，方便回去查原文；在合适的地方和 Function Calling、OpenAPI、LangChain Tools 做对比，说明各自适合什么场景；最后几章专门讲远程部署、OAuth、Prompt 注入、Tool 投毒和可观测性这些上线后才会碰到的问题。

---

## 二、目录结构

```
03-mcp/
├── README.md                                # 你正在看的总览
├── requirements.txt                         # 所有依赖（2026 年 11 月的版本基线）
├── .env.example                             # 环境变量模板
├── docs/
│   ├── 01-basics/                           # 基础概念（6 篇）
│   │   ├── 01-overview.md                   # MCP 是什么 / 为什么需要
│   │   ├── 02-architecture.md               # Host / Client / Server 三角
│   │   ├── 03-primitives.md                 # Tools / Resources / Prompts 三大原语
│   │   ├── 04-protocol-lifecycle.md         # JSON-RPC + server/discover + 每请求 _meta（附 2025 握手对照）
│   │   ├── 05-installation.md               # Python SDK 2.x + Inspector 安装
│   │   └── 06-first-server.md               # 第一个 Server 和 Client
│   ├── 02-server/                           # 构建 Server（8 篇）
│   │   ├── 01-tools.md                      # 工具：参数 schema / 错误返回
│   │   ├── 02-resources.md                  # 资源：URI / ResourceTemplate / 订阅
│   │   ├── 03-prompts.md                    # 提示：模板与参数
│   │   ├── 04-lifespan-context.md           # 生命周期 + 上下文注入
│   │   ├── 05-completion-pagination.md      # 补全 + 分页
│   │   ├── 06-logging-progress-ping.md      # 日志（已弃用）/ 进度 / Ping（已删除）/ 取消
│   │   ├── 07-tasks.md                      # Tasks 扩展（已变成官方扩展；Python SDK 暂未实现，给句柄模式）
│   │   └── 08-errors-validation.md          # 错误处理 + 参数校验
│   ├── 03-client/                           # 构建 Client（5 篇）
│   │   ├── 01-client-basics.md              # 自写 Client：连接 / 列工具 / 调工具
│   │   ├── 02-transports.md                 # stdio / SSE / Streamable HTTP
│   │   ├── 03-sampling.md                   # Sampling（已弃用）与替代方案
│   │   ├── 04-roots-elicitation.md          # Elicitation / Roots + 多轮请求 MRTR
│   │   └── 05-multi-server-best-practices.md# 多 Server 聚合 + 最佳实践
│   ├── 04-integration/                      # 生态集成（5 篇）
│   │   ├── 01-claude-code.md                # Claude Code 接 MCP
│   │   ├── 02-cursor-vscode.md              # Cursor / VS Code / 其他客户端
│   │   ├── 03-langchain-mcp.md              # langchain.mcp.MCPAdapter / langchain-mcp-adapters
│   │   ├── 04-pydantic-ai-mcp.md            # Pydantic AI 2.x：MCP capability / MCPToolset
│   │   └── 05-comparison.md                 # vs Function Calling / OpenAPI
│   ├── 05-production/                       # 生产化（5 篇）
│   │   ├── 01-remote-mcp.md                 # 远程部署：Streamable HTTP
│   │   ├── 02-auth-oauth.md                 # OAuth 2.1 + RFC 9728 + TokenVerifier
│   │   ├── 03-enterprise-auth.md            # Client Credentials / 企业管理授权（ID-JAG）/ 网关
│   │   ├── 04-security.md                   # Prompt 注入 / Tool 投毒防御
│   │   └── 05-debugging-inspector.md        # MCP Inspector + 可观测性
│   ├── 06-advanced/                         # 进阶 & 新特性（3 篇）
│   │   ├── 01-mcp-apps.md                   # MCP Apps：SDK 内置 Apps 扩展
│   │   ├── 02-agent-skills.md               # Agent Skills + Skills over MCP 扩展
│   │   └── 03-registry.md                   # MCP Registry 发布
│   └── 07-practice/                         # 实战（3 篇）
│       ├── 01-project-internal-kb.md        # 内部知识库 MCP Server（千问 embedding）
│       ├── 02-project-db-mcp.md             # 只读数据库 MCP（应用层 + 数据库层两层防御）
│       └── 03-project-claude-code-tool.md   # PR 评审助手（Claude Code 自定义 MCP）
└── demos/                                   # 所有可运行 demo
    ├── basics/
    ├── server/
    ├── client/
    ├── integration/
    ├── production/
    ├── advanced/
    └── practice/
```

---

## 三、怎么读

### 路径 A：零基础入门（最快半天）

```
01-basics/01-overview
  → 01-basics/03-primitives
  → 01-basics/06-first-server
  → 02-server/01-tools
  → 04-integration/01-claude-code
```

走完这条线，你就能写一个可以被 Claude Code 调用的本地 MCP Server。

### 路径 B：系统学一遍（约 1–2 周）

按目录顺序读，每篇都把 demo 跑一下。参考节奏：

1. 第 1 天：基础部分全部 6 篇（第一篇：[MCP 是什么，解决什么问题](docs/01-basics/01-overview.md)）
2. 第 2–3 天：Server 部分全部 8 篇（第一篇：[用 Tools 暴露可调用的操作](docs/02-server/01-tools.md)）
3. 第 4 天：Client 部分全部 5 篇（第一篇：[自己写 Client（连接、发现与调用工具）](docs/03-client/01-client-basics.md)）
4. 第 5 天：集成部分全部 5 篇（第一篇：[在 Claude Code 中接入 MCP](docs/04-integration/01-claude-code.md)）
5. 第 6–7 天：生产部分全部 5 篇（第一篇：[用 Streamable HTTP 部署远程 MCP](docs/05-production/01-remote-mcp.md)）
6. 第 8 天：进阶部分（第一篇：[用 MCP Apps 在客户端中嵌入交互式 UI](docs/06-advanced/01-mcp-apps.md)）和实战部分（第一篇：[内部知识库 MCP Server](docs/07-practice/01-project-internal-kb.md)）

### 路径 C：直接给 Claude Code 写 MCP

```
01-basics/03-primitives
  → 01-basics/06-first-server
  → 02-server/01-tools
  → 04-integration/01-claude-code
  → 07-practice/03-project-claude-code-tool
```

### 路径 D：从架构角度弄清协议本身

```
01-basics/02-architecture
  → 01-basics/04-protocol-lifecycle
  → 03-client/02-transports
  → 03-client/03-sampling
  → 05-production/02-auth-oauth
```

---

## 四、版本与依赖

> 以 2026 年 11 月的版本为准，代码在 Python 3.12 加下表版本下都能运行。

手册基于 MCP Specification 2026-07-28 与 Python SDK `mcp` 2.3.0：

| 包 | 版本 | 用途 |
|----|------|------|
| `mcp` | `>=2.3.0`（基于 2.3.0） | 官方 Python SDK：`MCPServer` + `Client`；实现 2026-07-28，同时能应答 2025 版的老客户端 |
| `mcp-types` | 随 `mcp` 安装 | 协议类型（`from mcp.types import ...` 仍然可用） |
| `fastmcp`（独立第三方包） | `4.1.x` | 不是官方 SDK；LangChain `MCPAdapter` 和 Pydantic AI `MCPToolset` 建在它上面，与 `mcp` 2.x 兼容 |
| `langchain` | `1.4.x` | `langchain.mcp.MCPAdapter`（内置） |
| `langchain-mcp-adapters` | `0.3.2` | 依赖 `mcp<2`，不能和 `mcp` 2.x 装进同一个环境（见[在 LangChain 中使用 MCP](docs/04-integration/03-langchain-mcp.md)） |
| `pydantic-ai-slim[mcp]` | `2.64.0` | `MCP` capability / `MCPToolset`（原来的 `MCPServerStdio` 等类已移除） |
| `uvicorn` / `starlette` | `0.5x` / `1.x` | 远程 MCP 部署 |
| `openai` | `>=3.0` | 调千问（OpenAI 兼容接口）的 demo |

完整依赖见 `requirements.txt`。

> 2026-07-28 版规范相对 2025-11-25 改动很大（去掉握手和协议层会话、Tasks 改成扩展等），Python SDK 跟着升到 2.0，`FastMCP` 改名 `MCPServer`。具体变化在各篇正文里讲；还在用 v1 的代码请钉住 `mcp>=1.28,<2`。官方迁移指南：https://py.sdk.modelcontextprotocol.io/migration/

---

## 五、环境准备

### 1. 创建虚拟环境

```bash
cd 03-mcp
conda create -n mcp-learn python=3.12 -y && conda activate mcp-learn     # 或 python -m venv .venv
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入你的 key
```

大多数 demo 根本不需要模型，用进程内的 `Client(mcp)` 就能跑。需要 Key 的情况如下：

- `OPENAI_API_KEY` + `OPENAI_BASE_URL`：用于需要真实模型的 demo（`demos/client/01_minimal_agent.py`、04-integration 的两篇、`03_sampling_flight.py` 的直连部分、07-practice 的 embedding 和 `auto_review`）。任意 OpenAI 兼容服务都可以，手册默认用阿里云百炼的千问 `qwen-flash`
- mcp 2.x 不再自动读取 `.env` 和 `MCP_*` 环境变量，需要自己 `export`

### 3. 跑第一个 demo

```bash
python demos/basics/06_first_client.py          # Client 会自己起 Server 子进程
python demos/basics/06_first_server.py          # 单独启动 Server（stdio，等待 Host 连接）
```

然后用 Inspector 连上去：

```bash
npx @modelcontextprotocol/inspector python demos/basics/06_first_server.py     # 需要 Node 22.19+
```

终端会打印一个带 token 的 URL，在浏览器里打开就能可视化调试（Inspector 还有 `--cli` / `--tui` 形态，见[调试与可观测（Inspector、日志与追踪）](docs/05-production/05-debugging-inspector.md)）。

---

## 六、写作约定

1. 每篇的结构大致相同：开场导语、概念、最小可用代码、进阶用法、生产建议、常见坑、demo 入口
2. 代码都能单独运行：每段 Python 的 `import` 写全，不依赖前文
3. 代码块都标语言：`` ```python ``、`` ```json ``、`` ```bash ``
4. 关键 API 写完整路径，例如 `mcp.server.MCPServer`（v1 叫 `mcp.server.fastmcp.FastMCP`），而不是只写 `MCPServer`
5. 错误示范用 `# ❌ 错误写法` 和 `# ✅ 正确写法` 标出来对比
6. 每篇结尾给出对应的 `modelcontextprotocol.io` 链接
7. 在合适的地方放上 LangChain、Pydantic AI、Function Calling 的对照代码

---

## 七、版权与反馈

内容对照 MCP 官方文档（https://modelcontextprotocol.io）独立编写，示例都是原创，可以自由复用。

问题和勘误可以在 GitHub 仓库提 issue。

从第一篇开始：[MCP 是什么，解决什么问题](docs/01-basics/01-overview.md)
