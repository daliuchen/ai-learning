# MCP 学习手册

> 一套对照 [Model Context Protocol 官方文档](https://modelcontextprotocol.io) 系统编写的中文深度教程。MCP 是 2024 年由 Anthropic 牵头、目前已被 OpenAI / Google / Microsoft / JetBrains / Cursor / Continue 等全行业支持的"AI 与外部世界连接的开放标准协议"，被官方比喻成「AI 应用的 USB-C」。本手册基于 **2025-11-25 规范** 与 **Python SDK** 编写。

---

## 一、教程定位

本系列教程的目标读者是：

- 想从协议层理解 MCP 而不只是"会改 `mcp.json`"的工程师
- 想用 Python 自己写 MCP Server、把内部系统接入 Claude Code / Cursor / VS Code 等客户端的开发者
- 已经用 LangChain / Pydantic AI 写过 Agent，想搞懂 MCP 在 Agent 工具链中位置的人
- 想做生产级远程 MCP 部署（OAuth、企业鉴权、可观测、安全）的架构师

不同于官网文档把内容拆成大量短页面，本系列把每个主题写成"一篇长文 + 一份可运行 demo"，重点放在：

1. **协议-代码-原理**三层结合，不只是 SDK API 调用
2. **官方规范对照**：每个特性都标注对应的 spec 章节，方便顺着溯源
3. **横向对比**（vs Function Calling / OpenAPI / LangChain Tools），告诉你什么时候该用什么
4. **生产经验**：远程部署、OAuth、Prompt 注入防御、Tool 投毒、可观测性

---

## 二、目录结构

```
03-mcp/
├── README.md                                # 你正在看的总览
├── requirements.txt                         # 所有依赖
├── .env.example                             # 环境变量模板
├── docs/
│   ├── 01-basics/                           # 基础概念（6 篇）
│   │   ├── 01-overview.md                   # MCP 是什么 / 为什么需要
│   │   ├── 02-architecture.md               # Host / Client / Server 三角
│   │   ├── 03-primitives.md                 # Tools / Resources / Prompts 三大原语
│   │   ├── 04-protocol-lifecycle.md         # JSON-RPC + 握手 + 能力协商
│   │   ├── 05-installation.md               # Python SDK + Inspector 安装
│   │   └── 06-first-server.md               # 5 分钟跑通 Hello World
│   ├── 02-server/                           # 构建 Server（8 篇）
│   │   ├── 01-tools.md                      # 工具：参数 schema / 错误返回
│   │   ├── 02-resources.md                  # 资源：URI / ResourceTemplate / 订阅
│   │   ├── 03-prompts.md                    # 提示：模板与参数
│   │   ├── 04-lifespan-context.md           # 生命周期 + 上下文注入
│   │   ├── 05-completion-pagination.md      # 补全 + 分页
│   │   ├── 06-logging-progress-ping.md      # 日志 / 进度 / Ping / 取消
│   │   ├── 07-tasks.md                      # Tasks 扩展（异步任务）
│   │   └── 08-errors-validation.md          # 错误处理 + 参数校验
│   ├── 03-client/                           # 构建 Client（5 篇）
│   │   ├── 01-client-basics.md              # 自写 Client：连接 / 列工具 / 调工具
│   │   ├── 02-transports.md                 # stdio / SSE / Streamable HTTP
│   │   ├── 03-sampling.md                   # Sampling：Server 反向请求 LLM
│   │   ├── 04-roots-elicitation.md          # Roots / Elicitation
│   │   └── 05-multi-server-best-practices.md# 多 Server 聚合 + 最佳实践
│   ├── 04-integration/                      # 生态集成（5 篇）
│   │   ├── 01-claude-code.md                # Claude Code 接 MCP
│   │   ├── 02-cursor-vscode.md              # Cursor / VS Code / 其他客户端
│   │   ├── 03-langchain-mcp.md              # langchain-mcp-adapters
│   │   ├── 04-pydantic-ai-mcp.md            # Pydantic AI 集成
│   │   └── 05-comparison.md                 # vs Function Calling / OpenAPI
│   ├── 05-production/                       # 生产化（5 篇）
│   │   ├── 01-remote-mcp.md                 # 远程部署：Streamable HTTP
│   │   ├── 02-auth-oauth.md                 # OAuth 2.1 + RFC 9728
│   │   ├── 03-enterprise-auth.md            # 企业管理授权 / Client Credentials
│   │   ├── 04-security.md                   # Prompt 注入 / Tool 投毒防御
│   │   └── 05-debugging-inspector.md        # MCP Inspector + 可观测性
│   ├── 06-advanced/                         # 进阶 & 新特性（3 篇）
│   │   ├── 01-mcp-apps.md                   # MCP Apps：客户端内嵌交互式 UI
│   │   ├── 02-agent-skills.md               # Agent Skills 集成
│   │   └── 03-registry.md                   # MCP Registry 发布
│   └── 07-practice/                         # 实战（3 篇）
│       ├── 01-project-internal-kb.md        # 内部知识库 MCP Server
│       ├── 02-project-db-mcp.md             # 只读数据库 MCP（含 SQL 安全过滤）
│       └── 03-project-claude-code-tool.md   # 给 Claude Code 写自定义 MCP
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

## 三、学习路径建议

### 路径 A：零基础入门（最快半天）

```
01-basics/01-overview
  → 01-basics/03-primitives
  → 01-basics/06-first-server
  → 02-server/01-tools
  → 04-integration/01-claude-code
```

跑通这条线，你就能写一个能被 Claude Code 调用的本地 MCP Server。

### 路径 B：系统掌握（推荐，约 1-2 周）

按目录顺序通读，每篇配套跑 demo。建议节奏：

1. **第 1 天**：01-basics 全部 6 篇
2. **第 2-3 天**：02-server 全部 8 篇
3. **第 4 天**：03-client 全部 5 篇
4. **第 5 天**：04-integration 全部 5 篇
5. **第 6-7 天**：05-production 全部 5 篇
6. **第 8 天**：06-advanced + 07-practice

### 路径 C：直奔 Claude Code MCP 二开

```
01-basics/03-primitives
  → 01-basics/06-first-server
  → 02-server/01-tools
  → 04-integration/01-claude-code
  → 07-practice/03-project-claude-code-tool
```

### 路径 D：架构师视角（搞清楚协议本身）

```
01-basics/02-architecture
  → 01-basics/04-protocol-lifecycle
  → 03-client/02-transports
  → 03-client/03-sampling
  → 05-production/02-auth-oauth
```

---

## 四、版本与依赖

本系列基于 **MCP Specification 2025-11-25** 与 **Python SDK 1.x**：

| 包 | 版本 | 用途 |
|----|------|------|
| `mcp` | `>=1.10.0` | MCP Python SDK（含 Server / Client） |
| `pydantic` | `>=2.6.0` | Schema 生成与校验 |
| `anyio` | `>=4.5.0` | 异步运行时（SDK 底层） |
| `httpx` | `>=0.27.0` | HTTP 传输 |
| `uvicorn` | `>=0.27.0` | 远程 MCP 部署 |
| `starlette` | `>=0.37.0` | 远程 MCP 部署 |
| `langchain-mcp-adapters` | `>=0.1.0` | LangChain 集成（04-integration 用） |
| `pydantic-ai` | `>=0.0.13` | Pydantic AI 集成（04-integration 用） |

完整依赖见 `requirements.txt`。

> **注意**：MCP Python SDK 在 1.0 后 API 已稳定，但 `2025-11-25` 规范引入的 Tasks、MCP Apps 等扩展可能在 SDK 后续版本逐步落地。本手册中标注「新规范扩展」的章节会同时给出 SDK 落地状态。

---

## 五、环境准备

### 1. 创建虚拟环境

```bash
cd 03-mcp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入你的 key
```

最少需要的 Key：

- `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`：跑 03-client/03-sampling、04-integration 等需要 LLM 的 demo
- `MCP_INSPECTOR_PORT`：本地 Inspector 端口，默认 6274

### 3. 跑第一个 demo

```bash
python demos/basics/06_first_server.py
```

然后用 Inspector 连接：

```bash
npx @modelcontextprotocol/inspector python demos/basics/06_first_server.py
```

浏览器打开 `http://localhost:6274` 即可可视化调试。

---

## 六、本系列写作约定

1. **每篇结构统一**：一句话总结 → 概念 → 最小可用代码 → 进阶用法 → 生产建议 → 常见坑 → demo 入口
2. **代码全部可独立运行**：每段 Python 代码 `import` 完整，不依赖前文
3. **代码块前必标语言**：`` ```python ``、`` ```json ``、`` ```bash ``
4. **关键 API 路径写完整**：例如 `mcp.server.fastmcp.FastMCP` 而不是只写 `FastMCP`
5. **错误示范有标注**：`# ❌ 错误写法` 和 `# ✅ 正确写法` 对比
6. **官方规范引用**：每篇结尾给出对应的 `modelcontextprotocol.io` 链接
7. **横向对比**：合适的地方插入和 LangChain / Pydantic AI / Function Calling 的对照代码

---

## 七、版权与反馈

教程内容对照 MCP 官方文档（https://modelcontextprotocol.io）独立编写，所有示例均为原创，可自由复用。

开始你的 MCP 之旅吧 👉 [01-basics/01-overview.md](docs/01-basics/01-overview.md)
