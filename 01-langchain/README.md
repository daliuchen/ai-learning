# LangChain 全家桶系列教程

> 这是我对照官方文档整理的 LangChain / LangSmith / LangGraph 中文笔记。每篇讲概念和原理，配一份能跑的 demo，最后是上线时要注意的事。

以 2026 年 11 月的版本为准：`langchain 1.4` / `langgraph 1.2` / `langsmith 0.14`。1.0 之后构建 agent 统一用 `langchain.agents.create_agent`，旧的 chains、`AgentExecutor`、旧 Memory、`langchain.hub`、`EnsembleRetriever` 等搬进了 `langchain-classic`。demo 默认用千问（`qwen-flash`）的 OpenAI 兼容接口。

---

## 一、教程定位

适合这几类读者：

- 想从头把 LangChain 生态学一遍的工程师
- 用过 LangChain，但在 LCEL、Agent、RAG 调优这些细节上卡住的人
- 要做上线的 LLM 应用，需要 LangSmith 做观测、LangGraph 做复杂编排的团队

官网的内容拆在很多短页面里，查 API 方便，系统学习不太顺。这里每个主题写成一篇长文加一份能跑的代码。除了 API 怎么调，我还会讲它背后的原理、同类方案之间怎么选，以及实际用下来遇到的坑、性能和调试问题。

---

## 二、目录结构

```
01-langchain/
├── README.md                              # 你正在看的总览
├── requirements.txt                       # 所有依赖
├── .env.example                           # 环境变量模板
├── docs/
│   ├── 01-langchain/                      # LangChain 框架（14 篇）
│   │   ├── 01-overview.md                 # 架构与生态（1.x 新包结构）
│   │   ├── 02-chat-models.md              # Chat Models & Messages
│   │   ├── 03-prompts.md                  # Prompt Templates
│   │   ├── 04-output-parsers.md           # Output Parsers / 结构化输出
│   │   ├── 05-lcel.md                     # LCEL & Runnable
│   │   ├── 06-streaming.md                # 流式
│   │   ├── 07-tools.md                    # Tools
│   │   ├── 08-agents.md                   # create_agent 与旧 AgentExecutor 迁移
│   │   ├── 09-memory.md                   # Memory（checkpointer / 旧 Memory 迁移）
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
│       ├── 01-frameworks.md               # vs CrewAI / MAF / LlamaIndex / OpenAI Agents / Pydantic AI
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

走完这条线，能搭出一个带 RAG 的小问答应用。

### 路径 B：系统学一遍（约 1-2 周，我比较推荐）

按目录顺序读，每篇跑一下配套 demo。节奏可以参考：

1. 第 1-3 天：LangChain 01-07（核心能力）
2. 第 4-5 天：LangChain 08-14（高级与 RAG）
3. 第 6 天：LangSmith 全部 5 篇
4. 第 7-9 天：LangGraph 01-08（核心 + 多 Agent）
5. 第 10 天：LangGraph 09-12（进阶 + 部署）
6. 第 11-12 天：04-comparison 三篇，再动手改一改实战项目

### 路径 C：直奔多 Agent（已有 LangChain 经验）

```
03-langgraph 全部 → 04-comparison/03-project-research-team
```

---

## 四、版本与依赖

全书基于 2026 年 11 月的稳定版 API（Python ≥ 3.10）：

| 包 | 版本 | 用途 |
|----|------|------|
| `langchain` | `>=1.4` | 主包：`create_agent`、middleware、messages、tools |
| `langchain-core` | `>=1.7` | 核心抽象（LCEL / Runnable 在这里） |
| `langchain-classic` | `>=1.0` | 旧 chains / AgentExecutor / retrievers / hub（遗留代码用） |
| `langchain-openai` | `>=1.7` | OpenAI 及一切 OpenAI 兼容接口（千问等） |
| `langchain-community` | `>=0.4` | 社区集成（Loader、BM25 等） |
| `langgraph` | `>=1.2` | LangGraph |
| `langgraph-checkpoint-sqlite` | `>=3.0` | SQLite 持久化 |
| `langsmith` | `>=0.14` | LangSmith SDK |
| `chromadb` / `langchain-chroma` | `>=1.0` | 向量数据库 |
| `faiss-cpu` | `>=1.8` | FAISS |

完整依赖见 `requirements.txt`。

> 1.0 的几处大变化：① 构建 agent 用 `from langchain.agents import create_agent`；② `from langchain.chains / retrievers / memory / hub import ...` 都要改成 `from langchain_classic.xxx import ...`；③ `langgraph.prebuilt.create_react_agent` 已弃用；④ LCEL（`prompt | model | parser`）完全没变。

---

## 五、环境准备

### 1. 创建虚拟环境

```bash
cd 01-langchain
python -m venv .venv        # 或 conda create -n lc python=3.12
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入你的 key
```

至少要配这几个：

- `OPENAI_API_KEY` + `OPENAI_BASE_URL`：模型调用。demo 默认接千问（OpenAI 兼容接口，`QWEN_MODEL=qwen-flash`，embedding 用 `text-embedding-v4`），要换成 OpenAI 官方接口，改这几个环境变量就行
- `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true`：开启可观测性（LangSmith 章节的云端功能需要；离线评估等不需要）
- `TAVILY_API_KEY`：搜索工具（可选）

> 踩坑：`langchain-openai` 优先读 `OPENAI_API_BASE`（旧变量名），它会覆盖 `OPENAI_BASE_URL`。如果千问返回 401 / 无效令牌，先 `unset OPENAI_API_BASE`。跑用假模型（fake model）的 demo 时不要开 `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2`，tracing 会提前消费假模型的预设回复。

### 3. 运行第一个 demo

```bash
python demos/langchain/01_hello_lcel.py
```

正常会打印出模型的回复。配了 LangSmith 的话，到 https://smith.langchain.com 还能看到这次调用的完整 trace。

demo 里的模型名统一写成 `os.environ.get("QWEN_MODEL", "qwen-flash")`，想换模型只改环境变量，不用改代码。

---

## 六、写作约定

几条约定，方便阅读和复制代码：

1. 每篇大致按这个顺序：开头导语、概念、最小可用代码、进阶用法、生产建议、常见坑
2. 「本章 demo」和 `demos/` 里的代码 import 写全，可以直接运行；正文里的片段为了简短，会省略前文出现过的 import 和变量
3. 代码块都标了语言，Python 代码块标 ` ```python `
4. 错误示范会用 `# ❌ 错误写法` 和 `# ✅ 正确写法` 对照

---

## 七、版权与反馈

内容对照 LangChain / LangSmith / LangGraph 官方文档整理，示例代码均为原创，可以随意复用。

问题和勘误可以在 GitHub 仓库提 issue。

从第一篇开始：[LangChain 01：整体架构与生态全景](docs/01-langchain/01-overview.md)
