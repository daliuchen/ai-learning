# AI Learning — 大模型应用开发学习手册集合

> 一套自用 + 可分享的中文深度学习手册集合，每个子手册都对照官方文档系统编写，包含核心概念、原理剖析、可运行 demo 与生产建议。

---

## 一、当前手册清单

| 序号 | 手册 | 主题 | 篇数 | 入口 |
|------|------|------|------|------|
| 01 | **LangChain 全家桶** | LangChain / LangSmith / LangGraph + 横向对比 | 34 篇 | [01-langchain/README.md](01-langchain/README.md) |
| 02 | **Pydantic AI** | Pydantic 团队出的 Python 原生 Agent 框架 | 33 篇 | [02-pydantic-ai/README.md](02-pydantic-ai/README.md) |
| 03 | **MCP（Model Context Protocol）** | AI 与外部世界连接的开放协议（基于 2025-11-25 规范 + Python SDK） | 35 篇 | [03-mcp/README.md](03-mcp/README.md) |
| 04 | **Prompt Engineering 实战** | 把 PE 工程流程（评测→迭代→上线→监控）作为中轴线的系统教程 | 44 篇 | [04-prompt-engineering/README.md](04-prompt-engineering/README.md) |
| 05 | **OpenAI Agents SDK** | OpenAI 官方 Agent 框架——最小原语 + Hosted Tools + Handoffs 一等公民 | 38 篇 | [05-openai-agents-sdk/README.md](05-openai-agents-sdk/README.md) |
| 06 | **Embedding & 向量检索** | 把 RAG pipeline 拆成 6 个组件，每个讲 trade-off + 工业默认值 | 44 篇 | [06-embedding/README.md](06-embedding/README.md) |

每本手册都是独立的工程：自带 `README.md`、`requirements.txt`、`docs/`、`demos/`，互不依赖，可以单独 clone / 单独跑。

---

## 二、写作约定（所有手册通用）

为了让读者读得顺、抄得快，所有手册都遵循同一套约定：

1. **每篇结构统一**：开头一句话总结 → 概念 → 最小可用代码 → 进阶用法 → 生产建议 → 常见坑（表格）→ demo 入口
2. **代码全部可独立运行**：每段代码 import 完整，不依赖前文上下文
3. **代码块前必标语言**：` ```python `、` ```bash ` 等
4. **关键 API 路径写完整**：例如 `pydantic_ai.Agent` 而不是只写 `Agent`
5. **错误示范有标注**：用 `# ❌ 错误写法` 和 `# ✅ 正确写法` 对比
6. **横向对比**：合适的地方插入相邻框架（如 PydanticAI vs LangChain）的等价代码

---

## 三、目录结构

```
ai-learning/
├── README.md                                # 总索引（你正在看的）
│
├── 01-langchain/                            # 手册 1：LangChain 全家桶
│   ├── README.md                            # 手册入口
│   ├── requirements.txt
│   ├── .env.example
│   ├── docs/
│   │   ├── 01-langchain/                    # 14 篇
│   │   ├── 02-langsmith/                    # 5 篇
│   │   ├── 03-langgraph/                    # 12 篇
│   │   └── 04-comparison/                   # 3 篇
│   └── demos/
│       ├── langchain/
│       ├── langgraph/
│       └── langsmith/
│
├── 02-pydantic-ai/                          # 手册 2：Pydantic AI
│   ├── README.md                            # 手册入口
│   ├── requirements.txt
│   ├── .env.example
│   ├── docs/
│   │   ├── 01-basics/                       # 7 篇
│   │   ├── 02-tools/                        # 5 篇
│   │   ├── 03-advanced/                     # 8 篇
│   │   ├── 04-modules/                      # 5 篇
│   │   ├── 05-patterns/                     # 4 篇
│   │   └── 06-practice/                     # 4 篇
│   └── demos/
│       ├── basics/
│       ├── tools/
│       ├── advanced/
│       ├── modules/
│       ├── patterns/
│       └── practice/
│
├── 03-mcp/                                  # 手册 3：MCP（Model Context Protocol）
│   ├── README.md                            # 手册入口
│   ├── requirements.txt
│   ├── .env.example
│   ├── docs/
│   │   ├── 01-basics/                       # 6 篇：协议总览与基础
│   │   ├── 02-server/                       # 8 篇：构建 MCP Server
│   │   ├── 03-client/                       # 5 篇：构建 MCP Client
│   │   ├── 04-integration/                  # 5 篇：与 Claude Code / Cursor / LangChain / Pydantic AI 集成
│   │   ├── 05-production/                   # 5 篇：远程部署 / OAuth / 安全 / 可观测
│   │   ├── 06-advanced/                     # 3 篇：MCP Apps / Agent Skills / Registry
│   │   └── 07-practice/                     # 3 篇：实战项目
│   └── demos/
│       ├── basics/
│       ├── server/
│       ├── client/
│       ├── integration/
│       ├── production/
│       ├── advanced/
│       └── practice/
│
├── 04-prompt-engineering/                   # 手册 4：Prompt Engineering 实战
│   ├── README.md                            # 手册入口
│   ├── requirements.txt
│   ├── .env.example
│   └── docs/
│       ├── 01-foundations/                  # 5 篇：基础
│       ├── 02-process/                      # 6 篇：★ 中轴线（PE 怎么产生）
│       ├── 03-techniques/                   # 10 篇：核心技法
│       ├── 04-advanced/                     # 6 篇：进阶（ReAct/Tool/RAG/Multimodal/Meta/Injection）
│       ├── 05-by-task/                      # 5 篇：按任务组装
│       ├── 06-models/                       # 4 篇：模型差异
│       ├── 07-production/                   # 5 篇：生产化
│       └── 08-practice/                     # 3 篇：实战项目
│
├── 05-openai-agents-sdk/                    # 手册 5：OpenAI Agents SDK
│   ├── README.md                            # 手册入口
│   ├── requirements.txt
│   ├── .env.example
│   └── docs/
│       ├── 01-basics/                       # 6 篇：基础入门
│       ├── 02-tools/                        # 5 篇：工具系统（含 Hosted Tools）
│       ├── 03-handoffs/                     # 4 篇：★ Handoffs 独门
│       ├── 04-guardrails/                   # 3 篇：★ 守卫体系
│       ├── 05-advanced/                     # 6 篇：进阶（Tracing/Realtime/Voice）
│       ├── 06-integration/                  # 4 篇：集成与生态
│       ├── 07-production/                   # 5 篇：生产化
│       └── 08-practice/                     # 5 篇：实战项目
│
└── 06-embedding/                            # 手册 6：Embedding & 向量检索
    ├── README.md                            # 手册入口
    ├── requirements.txt
    ├── .env.example
    └── docs/
        ├── 01-foundations/                  # 6 篇：原理 / 相似度 / 训练 / 维度 / 多语言 / 多模态
        ├── 02-models/                       # 6 篇：OpenAI / Cohere / 开源 / sentence-transformers / rerank / MTEB
        ├── 03-vector-db/                    # 7 篇：选型 / Pinecone / Qdrant / pgvector / Chroma / 索引算法
        ├── 04-chunking/                     # 5 篇：策略 / 结构感知 / small-to-big / metadata
        ├── 05-retrieval/                    # 6 篇：BM25+Dense / HyDE / Multi-query / Rerank / Self-query
        ├── 06-evaluation/                   # 4 篇：指标 / 建集 / 端到端 / 持续评测
        ├── 07-production/                   # 5 篇：增量 / 批量 / 缓存 / 部署 / 监控
        └── 08-applications/                 # 5 篇：RAG / 语义搜索 / 多模态 / 推荐 / 去重
```

---

## 四、学习路径推荐

### 路径 A：从 LangChain 入门 LLM 应用开发

```
01-langchain/01-langchain/01-overview
  → 01-langchain/01-langchain/05-lcel
  → 01-langchain/01-langchain/07-tools
  → 01-langchain/01-langchain/13-rag
```

跑通这条线就能搭出一个 RAG 问答应用。

### 路径 B：直奔 Agent 框架（推荐已有 LLM 调用经验的人）

```
02-pydantic-ai/01-basics/01-overview
  → 02-pydantic-ai/01-basics/03-first-agent
  → 02-pydantic-ai/02-tools/01-function-tools
  → 02-pydantic-ai/06-practice/02-project-rag
```

### 路径 C：选型对比

```
02-pydantic-ai/06-practice/01-vs-langchain
  → 01-langchain/04-comparison/01-frameworks
```

### 路径 D：复杂多 Agent 工作流

```
01-langchain/03-langgraph 全部
  → 02-pydantic-ai/04-modules/03-graph
  → 02-pydantic-ai/05-patterns/01-multi-agent
  → 02-pydantic-ai/06-practice/03-project-research
```

---

## 五、未来计划（占位）

按需扩展，每个新主题独立一个 `NN-xxx/` 文件夹：

| 候选主题 | 状态 |
|----------|------|
| LlamaIndex 深度教程 | 待写 |
| Embedding / 向量检索专题 | 待写 |
| 大模型评测（Evals）专题 | 待写 |

新增手册时，参照 01-langchain 或 02-pydantic-ai 的目录结构即可。

---

## 六、如何加新手册

```bash
# 1. 在根目录建新文件夹（数字编号 + 名字）
mkdir -p ai-learning/03-llama-index/{docs,demos}

# 2. 把新手册的 README / requirements / .env.example 放进去
# 3. 在 ai-learning/README.md 的"当前手册清单"表格里加一行
```

---

## 七、版权与反馈

所有教程内容均对照对应项目官方文档独立编写，示例均为原创，可自由复用。
