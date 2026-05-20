# LangChain 全家桶系列教程

> 一套对照官方文档系统编写的 LangChain / LangSmith / LangGraph 中文深度教程，每一篇都包含核心概念、原理剖析、可运行 demo 与生产建议。

---

## 一、教程定位

本系列教程的目标读者是：

- 想从零系统掌握 LangChain 生态的工程师
- 已用过 LangChain 但卡在某些细节（如 LCEL、Agent、RAG 调优）的开发者
- 想构建生产级 LLM 应用，需要可观测性（LangSmith）和复杂编排（LangGraph）能力的团队

不同于官网把内容分散在大量短页面里，本系列把每个主题写成"一篇长文 + 一份可运行代码"，重点放在：

1. **概念-代码-原理**三层结合，不只是 API 调用
2. **横向对比**，告诉你什么时候该用什么
3. **生产经验**，包含坑、性能、可观测性、调试技巧

---

## 二、目录结构

```
lang-chain-demo/
├── README.md                              # 你正在看的总览
├── requirements.txt                       # 所有依赖
├── .env.example                           # 环境变量模板
├── docs/
│   ├── 01-langchain/                      # LangChain 框架（14 篇）
│   │   ├── 01-overview.md                 # 架构与生态
│   │   ├── 02-chat-models.md              # Chat Models & Messages
│   │   ├── 03-prompts.md                  # Prompt Templates
│   │   ├── 04-output-parsers.md           # Output Parsers / 结构化输出
│   │   ├── 05-lcel.md                     # LCEL & Runnable
│   │   ├── 06-streaming.md                # 流式
│   │   ├── 07-tools.md                    # Tools
│   │   ├── 08-agents.md                   # 传统 Agents
│   │   ├── 09-memory.md                   # Memory
│   │   ├── 10-document-loaders.md         # Loader & Splitter
│   │   ├── 11-vectorstores.md             # Embedding & VectorStore
│   │   ├── 12-retrievers.md               # Retrievers
│   │   ├── 13-rag.md                      # RAG 完整实战
│   │   └── 14-callbacks.md                # Callbacks
│   ├── 02-langsmith/                      # LangSmith（5 篇）
│   │   ├── 01-overview.md
│   │   ├── 02-tracing.md
│   │   ├── 03-evaluation.md
│   │   ├── 04-prompt-hub.md
│   │   └── 05-monitoring.md
│   ├── 03-langgraph/                      # LangGraph（12 篇）
│   │   ├── 01-introduction.md
│   │   ├── 02-stategraph.md
│   │   ├── 03-state-and-reducers.md
│   │   ├── 04-react-agent.md
│   │   ├── 05-persistence.md
│   │   ├── 06-human-in-the-loop.md
│   │   ├── 07-streaming.md
│   │   ├── 08-multi-agent.md
│   │   ├── 09-subgraph.md
│   │   ├── 10-map-reduce.md
│   │   ├── 11-functional-api.md
│   │   └── 12-deployment.md
│   └── 04-comparison/                     # 横向对比与实战（3 篇）
│       ├── 01-frameworks.md               # vs CrewAI / AutoGen / LlamaIndex
│       ├── 02-project-rag-agent.md        # 实战 1：RAG 问答 Agent
│       └── 03-project-research-team.md    # 实战 2：多 Agent 研究助手
└── demos/                                 # 所有可运行 demo
    ├── langchain/
    ├── langsmith/
    └── langgraph/
```

---

## 三、学习路径建议

### 路径 A：零基础入门（最快 2 天上手）

```
01-overview → 02-chat-models → 03-prompts → 05-lcel → 07-tools → 13-rag
```

跑通这条线，你就能搭出一个具备 RAG 能力的小问答应用。

### 路径 B：系统掌握（推荐，约 1-2 周）

按目录顺序通读，每篇配套跑 demo。建议节奏：

1. 第 1-3 天：LangChain 01-07（核心能力）
2. 第 4-5 天：LangChain 08-14（高级与 RAG）
3. 第 6 天：LangSmith 全部 5 篇
4. 第 7-9 天：LangGraph 01-08（核心 + 多 Agent）
5. 第 10 天：LangGraph 09-12（进阶 + 部署）
6. 第 11-12 天：04-comparison 三篇 + 自己改造实战项目

### 路径 C：直奔多 Agent（已有 LangChain 经验）

```
03-langgraph 全部 → 04-comparison/03-project-research-team
```

---

## 四、版本与依赖

本系列基于 2025 年 LangChain 生态的最新稳定 API：

| 包 | 版本 | 用途 |
|----|------|------|
| `langchain` | `>=0.3.0` | 主包 |
| `langchain-core` | `>=0.3.0` | 核心抽象 |
| `langchain-openai` | `>=0.2.0` | OpenAI 集成 |
| `langchain-anthropic` | `>=0.2.0` | Anthropic 集成 |
| `langchain-community` | `>=0.3.0` | 社区集成 |
| `langgraph` | `>=0.2.0` | LangGraph |
| `langgraph-checkpoint-sqlite` | `>=2.0.0` | SQLite 持久化 |
| `langsmith` | `>=0.1.0` | LangSmith SDK |
| `chromadb` | `>=0.5.0` | 向量数据库 |
| `faiss-cpu` | `>=1.8.0` | FAISS |

完整依赖见 `requirements.txt`。

---

## 五、环境准备

### 1. 创建虚拟环境

```bash
cd lang-chain-demo
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入你的 key
```

最少需要的 Key：

- `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`：模型调用
- `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true`：开启可观测性（LangSmith 章节必需）
- `TAVILY_API_KEY`：搜索工具（Agent / 多 Agent 章节需要）

### 3. 运行第一个 demo

```bash
python demos/langchain/01_hello_lcel.py
```

应该看到模型的回复输出。如果配置了 LangSmith，到 https://smith.langchain.com 还能看到一条完整的 trace。

---

## 六、本系列写作约定

为了让读者读得顺、抄得快：

1. **每篇结构统一**：开头一句话总结 → 概念 → 最小可用代码 → 进阶用法 → 生产建议 → 常见坑
2. **代码全部可独立运行**：每段代码 import 完整，不依赖前文上下文
3. **代码块前必标语言**：Python 代码块都标 ` ```python `，便于阅读
4. **关键 API 路径写完整**：例如 `langchain_core.runnables.RunnableLambda` 而不是只写 `RunnableLambda`
5. **错误示范有标注**：会用 `# ❌ 错误写法` 和 `# ✅ 正确写法` 对比

---

## 七、版权与反馈

教程内容对照 LangChain / LangSmith / LangGraph 官方文档编写，所有示例均为原创，可自由复用。

开始你的 LangChain 之旅吧 👉 [01-langchain/01-overview.md](docs/01-langchain/01-overview.md)
