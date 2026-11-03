# Pydantic AI 学习手册

> 这是我对照 [Pydantic AI 官方文档](https://pydantic.dev/docs/ai) 写的一套中文教程，每篇讲概念和原理，配一份能跑的 demo，再加一些上生产时的建议。Pydantic AI 是 Pydantic 团队做的 Python Agent 框架，重点在类型安全、不绑定模型、能直接上生产。

---

## 一、写给谁

适合这几类读者：

- 想从零系统掌握 Pydantic AI 的 Python 工程师
- 之前用 LangChain / LlamaIndex / CrewAI，想对比迁移到 Pydantic AI 的团队
- 在 LLM 应用里反复碰到工具调用、结构化输出、多 Agent 编排问题的开发者
- 想构建生产级 LLM 应用（带可观测性、评测、状态管理）的架构师

官网把内容拆在很多短页面里，这里每个主题写成一篇文章加一份可运行代码。除了 API 怎么调，我还会讲它背后的原理，和 LangChain / LangGraph / CrewAI 做对比，说明什么时候该用哪个，最后记下踩过的坑、性能、可观测性和调试方面的经验。

---

## 二、目录结构

```
02-pydantic-ai/
├── README.md                                # 你正在看的总览
├── requirements.txt                         # 所有依赖
├── .env.example                             # 环境变量模板
├── docs/
│   ├── 01-basics/                           # 入门基础（7 篇）
│   │   ├── 01-overview.md                   # Pydantic AI 是什么 / 为什么用
│   │   ├── 02-installation.md               # 安装与环境
│   │   ├── 03-first-agent.md                # 第一个 Agent（Hello World）
│   │   ├── 04-models-providers.md           # 模型与 Provider 全览
│   │   ├── 05-dependencies.md               # 依赖注入系统
│   │   ├── 06-output-types.md               # 结构化输出
│   │   └── 07-messages-history.md           # 消息与对话历史
│   ├── 02-tools/                            # 工具系统（5 篇）
│   │   ├── 01-function-tools.md             # @agent.tool 基础
│   │   ├── 02-advanced-tools.md             # 工具高级特性
│   │   ├── 03-toolsets.md                   # Toolset 抽象
│   │   ├── 04-common-tools.md               # 内置常用工具
│   │   └── 05-native-tools.md               # 模型原生工具
│   ├── 03-advanced/                         # 进阶能力（8 篇）
│   │   ├── 01-streaming.md                  # 流式响应
│   │   ├── 02-multimodal.md                 # 多模态输入
│   │   ├── 03-thinking.md                   # 思维链
│   │   ├── 04-hooks.md                      # Hooks 钩子
│   │   ├── 05-direct-requests.md            # 直接调模型
│   │   ├── 06-capabilities.md             # Capabilities（V2 核心原语）+ ModelProfile
│   │   ├── 07-retries-http.md               # HTTP 重试与错误
│   │   └── 08-deferred-tools.md             # 延迟工具
│   ├── 04-modules/                          # 配套模块（5 篇）
│   │   ├── 01-mcp.md                        # MCP 集成
│   │   ├── 02-evals.md                      # Pydantic Evals
│   │   ├── 03-graph.md                      # Pydantic Graph
│   │   ├── 04-logfire.md                    # Logfire 可观测性
│   │   └── 05-cli-harness.md             # clai 与 pydantic-ai-harness
│   ├── 05-patterns/                         # 多 Agent 与模式（4 篇）
│   │   ├── 01-multi-agent.md
│   │   ├── 02-web-chat-ui.md
│   │   ├── 03-testing.md
│   │   └── 04-embeddings.md
│   └── 06-practice/                         # 实战与对比（4 篇）
│       ├── 01-vs-langchain.md               # 横向对比
│       ├── 02-project-rag.md                # 实战 1：RAG Agent
│       ├── 03-project-research.md           # 实战 2：多 Agent 研究助手
│       └── 04-project-mcp-server.md         # 实战 3：自定义 MCP 服务
└── demos/                                   # 所有可运行 demo
    ├── basics/
    ├── tools/
    ├── advanced/
    ├── modules/
    ├── patterns/
    └── practice/
```

---

## 三、学习路径建议

### 路径 A：快速入门（约 1 天）

```
01-overview → 03-first-agent → 04-models → 06-output-types → 02-tools/01-function-tools
```

走完这条线，就能写出一个会调工具、能返回结构化结果的 Agent。

### 路径 B：按顺序通读（约 1-2 周）

按目录顺序读，每篇跑一下配套 demo。参考节奏：

1. 第 1-2 天：基础部分全部 7 篇（第一篇：[整体定位与生态全景](docs/01-basics/01-overview.md)）
2. 第 3 天：工具部分全部 5 篇（第一篇：[Function Tools 函数工具](docs/02-tools/01-function-tools.md)）
3. 第 4-5 天：进阶部分全部 8 篇（第一篇：[流式响应（Streaming）](docs/03-advanced/01-streaming.md)）
4. 第 6-7 天：模块部分全部 5 篇，重点是 [MCP（Model Context Protocol）集成](docs/04-modules/01-mcp.md)、[Pydantic Evals 评测框架](docs/04-modules/02-evals.md)、[Pydantic Graph 状态机](docs/04-modules/03-graph.md)
5. 第 8 天：模式部分全部 4 篇（第一篇：[多 Agent 协作模式](docs/05-patterns/01-multi-agent.md)）
6. 第 9-10 天：实战部分全部 4 篇（第一篇：[与 LangChain 等主流 Agent 框架横向对比](docs/06-practice/01-vs-langchain.md)），再自己改造实战项目

### 路径 C：直奔多 Agent（已有 LangChain 经验）

```
06-practice/01-vs-langchain → 03-first-agent → 04-modules/03-graph → 05-patterns/01-multi-agent → 06-practice/03-project-research
```

---

## 四、版本与依赖

以 2026 年 11 月的版本为准，API 对照官方文档（<https://pydantic.dev/docs/ai>，有 `llms.txt`）。框架升级后请对照官方 changelog。

| 包 | 版本 | 用途 |
|----|------|------|
| `pydantic-ai` / `pydantic-ai-slim` | `>=2.64.0` | 主包（需要 Python 3.11+） |
| `pydantic-evals` | `>=2.64.0` | 评测框架（与主包同版本号） |
| `pydantic-graph` | `>=2.64.0` | 状态机 / 工作流（`GraphBuilder` 新 API） |
| `pydantic-ai-harness` | `>=0.64.0` | 官方 capability / harness 库（Coder、Skills、StepPersistence…，0.x，小版本可能变） |
| `clai` | `>=2.64.0` | 命令行（现为独立包） |
| `fastmcp` | `>=4.0` | MCP 服务端 / 客户端传输（`pydantic-ai-slim[mcp]` 带客户端部分） |
| `logfire` | `>=4.0` | 可观测性（文中示例基于 5.4） |
| `pydantic` | `>=2.12` | 底层数据校验 |
| `openai` | `>=3.0` | OpenAI / 千问（OpenAI 兼容端点）Provider 依赖 |

完整依赖见 `requirements.txt`。

### 从 0.0.x / 1.x 升级时最容易碰到的变化

| 以前的写法 | 现在 |
|--------|------|
| `Agent(..., system_prompt=...)` | 推荐 `instructions=`（`system_prompt` 仍可用，语义是"保留在历史里"） |
| `result.usage()` | `result.usage`（属性） |
| `StreamedRunResult.output` | `await result.get_output()` |
| `Agent(..., output_retries=N)` / `result_type=` | `retries={'output': N}`（工具预算用 `'tools'` 键，或直接 `retries=N` 两者同设） / `output_type=` |
| `RetryPromptPart` | 废弃；重试反馈是 `ToolReturnPart(outcome='retried')` / `RetryFeedbackPart` |
| `builtin_tools=` / `history_processors=` / `instrument=` | `capabilities=[NativeTool(...)]` / `ProcessHistory` / `Instrumentation` |
| `MCPServerStdio` / `MCPServerStreamableHTTP` | `MCPToolset(...)` 或 `MCP` capability（基于 FastMCP） |
| `from mcp.server.fastmcp import FastMCP` | `from fastmcp import FastMCP`（第三方包；官方 SDK 2.x 里对应的是 `mcp.server.MCPServer`，见 MCP 手册的[写第一个 MCP Server 和 Client](../03-mcp/docs/01-basics/06-first-server.md)） |
| `Graph(nodes=[...])`、`graph.run(StartNode(), ...)`、`mermaid_code()`、`pydantic_graph.persistence` | `GraphBuilder` + `build()`；`graph.run(state=..., inputs=...)` 直接返回输出；`graph.render()`；持久化已移除 |
| `ModelProfile` 属性访问 | 它是 `TypedDict`：`profile.get('supports_tools')` |
| `AsyncTenacityTransport`（httpx） | `AsyncHTTPX2TenacityTransport` + `create_async_httpx2_client` |
| `openai:` 前缀 = Chat Completions | `openai:` 默认是 Responses API；千问等兼容端点用 `openai-chat:` |
| `google-gla:` / `google-vertex:` | `google:` / `google-cloud:` |
| `agent.to_a2a()` | 已不存在 |
| `report.averages()` 返回 dict | 返回 `ReportCaseAggregate` 对象（属性读取） |

---

## 五、环境准备

### 1. 创建虚拟环境（Python 3.11+）

```bash
cd 02-pydantic-ai
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

- `OPENAI_API_KEY` + `OPENAI_BASE_URL`：本手册 demo 默认用千问（阿里云百炼的 OpenAI 兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-flash`，embedding 用 `text-embedding-v4`）；换官方 OpenAI 就去掉 `OPENAI_BASE_URL`、改模型名
- 没有任何 key 也能跑：demo 会自动退回 `TestModel` / `FunctionModel`（离线、不花钱）
- 可选：`ANTHROPIC_API_KEY`（Anthropic 分支）、`LOGFIRE_TOKEN`（Logfire 上报）、`GITHUB_TOKEN`（MCP 实战）

### 3. 运行第一个 demo

```bash
python demos/basics/03_first_agent.py
```

正常会看到 Agent 的回复（没有 key 时是 `TestModel` 的占位输出）。第一次运行时终端会打印 Pydantic AI 的启动横幅，`export PYDANTIC_AI_NO_BANNER=1` 可关掉。

---

## 六、写作约定

1. 每篇大致按这个顺序：导语 → 概念 → 最小可用代码 → 进阶用法 → 生产建议 → 常见坑 → demo 入口
2. demo 尽量能独立运行，import 写全
3. 代码块都标注语言，如 `` ```python ``
4. 关键 API 写完整路径，例如 `pydantic_ai.Agent` 而不是只写 `Agent`
5. 错误示范用 `# ❌ 错误写法` 和 `# ✅ 正确写法` 对比
6. 相关章节附上 LangChain 的对照代码

---

## 七、版权

内容对照 Pydantic AI 官方文档（https://pydantic.dev/docs/ai）编写，示例都是自己写的，可以自由复用。问题和勘误可以在 GitHub 仓库提 issue。

从第一篇开始：[Pydantic AI 01：整体定位与生态全景](docs/01-basics/01-overview.md)
