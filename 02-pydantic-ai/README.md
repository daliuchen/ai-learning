# Pydantic AI 学习手册

> 一套对照 [Pydantic AI 官方文档](https://ai.pydantic.dev) 系统编写的中文深度教程，每一篇都包含核心概念、原理剖析、可运行 demo 与生产建议。Pydantic AI 是 Pydantic 团队推出的"Python 原生 Agent 框架"，主打类型安全 / 模型无关 / 生产可用。

---

## 一、教程定位

本系列教程的目标读者是：

- 想从零系统掌握 Pydantic AI 的 Python 工程师
- 之前用 LangChain / LlamaIndex / CrewAI，想对比迁移到 Pydantic AI 的团队
- 在 LLM 应用里反复遇到"工具调用 / 结构化输出 / 多 Agent 编排"问题的开发者
- 想构建生产级 LLM 应用（带可观测性、评测、状态管理）的架构师

不同于官网把内容分散在大量短页面里，本系列把每个主题写成"一篇长文 + 一份可运行代码"，重点放在：

1. **概念-代码-原理**三层结合，不只是 API 调用
2. **横向对比**（vs LangChain / LangGraph / CrewAI），告诉你什么时候该用什么
3. **生产经验**，包含坑、性能、可观测性、调试技巧

---

## 二、目录结构

```
pydantic-demo/
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
│   │   ├── 06-capabilities.md               # Capabilities
│   │   ├── 07-retries-http.md               # HTTP 重试与错误
│   │   └── 08-deferred-tools.md             # 延迟工具
│   ├── 04-modules/                          # 配套模块（5 篇）
│   │   ├── 01-mcp.md                        # MCP 集成
│   │   ├── 02-evals.md                      # Pydantic Evals
│   │   ├── 03-graph.md                      # Pydantic Graph
│   │   ├── 04-logfire.md                    # Logfire 可观测性
│   │   └── 05-cli-harness.md                # CLI 与 Harness
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

### 路径 A：零基础入门（最快 1 天上手）

```
01-overview → 03-first-agent → 04-models → 06-output-types → 02-tools/01-function-tools
```

跑通这条线，你就能写出一个能调工具、能结构化返回的 Agent。

### 路径 B：系统掌握（推荐，约 1-2 周）

按目录顺序通读，每篇配套跑 demo。建议节奏：

1. **第 1-2 天**：01-basics 全部 7 篇
2. **第 3 天**：02-tools 全部 5 篇
3. **第 4-5 天**：03-advanced 全部 8 篇
4. **第 6-7 天**：04-modules 全部 5 篇（重点 MCP、Evals、Graph）
5. **第 8 天**：05-patterns 全部 4 篇
6. **第 9-10 天**：06-practice 全部 4 篇 + 自己改造实战项目

### 路径 C：直奔多 Agent（已有 LangChain 经验）

```
06-practice/01-vs-langchain → 03-first-agent → 04-modules/03-graph → 05-patterns/01-multi-agent → 06-practice/03-project-research
```

---

## 四、版本与依赖

本系列基于 **Pydantic AI 0.0.x（稳定 alpha）** 的最新 API：

| 包 | 版本 | 用途 |
|----|------|------|
| `pydantic-ai` | `>=0.0.13` | 主包 |
| `pydantic-ai-slim` | `>=0.0.13` | 精简包（按需安装模型 Provider） |
| `pydantic` | `>=2.6.0` | 底层数据校验 |
| `logfire` | `>=0.50.0` | 可观测性 |
| `pydantic-evals` | `>=0.0.13` | 评测框架 |
| `pydantic-graph` | `>=0.0.13` | 状态机 / 工作流 |
| `openai` | `>=1.30.0` | OpenAI Provider 依赖 |
| `anthropic` | `>=0.30.0` | Anthropic Provider 依赖 |

完整依赖见 `requirements.txt`。

---

## 五、环境准备

### 1. 创建虚拟环境

```bash
cd pydantic-demo
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

- `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`：模型调用
- `LOGFIRE_TOKEN`：可观测性（Logfire 章节必需）
- `TAVILY_API_KEY`：搜索工具（部分章节用到）

### 3. 运行第一个 demo

```bash
python demos/basics/03_first_agent.py
```

应该看到 Agent 的回复输出。

---

## 六、本系列写作约定

1. **每篇结构统一**：一句话总结 → 概念 → 最小可用代码 → 进阶用法 → 生产建议 → 常见坑 → demo 入口
2. **代码全部可独立运行**：每段代码 import 完整，不依赖前文上下文
3. **代码块前必标语言**：`` ```python ``
4. **关键 API 路径写完整**：例如 `pydantic_ai.Agent` 而不是只写 `Agent`
5. **错误示范有标注**：会用 `# ❌ 错误写法` 和 `# ✅ 正确写法` 对比
6. **vs LangChain 标注**：在对应章节插入对比代码

---

## 七、版权与反馈

教程内容对照 Pydantic AI 官方文档（https://ai.pydantic.dev）编写，所有示例均为原创，可自由复用。

开始你的 Pydantic AI 之旅吧 👉 [01-basics/01-overview.md](docs/01-basics/01-overview.md)
