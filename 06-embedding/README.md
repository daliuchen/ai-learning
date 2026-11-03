# Embedding & 向量检索实战手册

> 这本手册把 RAG 和语义搜索的 pipeline 拆成 chunk、embed、索引、检索、重排、评测六个环节，逐个讲清楚每一环有哪些取舍，以及在没有特殊需求时该用什么默认值。

---

## 版本基线（以 2026 年 11 月为准）

| 项 | 版本 / 结论 |
|---|---|
| 默认演示 embedding | 千问 `text-embedding-v4`（OpenAI 兼容接口，默认 1024 维，可截维） |
| 商业 API 当前模型 | OpenAI 仍为 text-embedding-3；Cohere `embed-v5.0-pro/fast`、`rerank-v5.0`；Voyage `voyage-4` 系列；千问 `text-embedding-v4` / `qwen3.7-text-embedding`。均以各家官方文档为准，价格只保留有官方出处的 |
| 开源 | Qwen3-Embedding（0.6B/4B/8B）+ Qwen3-Reranker、BGE-M3、Jina v5 |
| 向量库客户端 | `qdrant-client 1.20`（`search` 已移除，用 `query_points`）、`pinecone 10.x`（旧包 `pinecone-client` 已改名）、`chromadb 1.5`、`lancedb 0.41`、pgvector 0.8.7 |
| 评测 | `mteb 2.x`（`mteb.evaluate`）、`ranx 0.3.21`、`beir 2.2.0` |
| LangChain | 1.x：原来的 retriever/chain 移到了 `langchain-classic` |
| 运行条件 | 千问 v4 + qdrant 内存模式 / chromadb / lancedb / ranx / rank-bm25 的示例只需要百炼 API key；Cohere、Voyage、Pinecone 需要各自的账号，pgvector 需要 Postgres，本地模型需要 torch 并下载权重，文中相应位置都有说明 |

---

## 为什么写这本

LangChain、LlamaIndex、OpenAI FileSearch 都能搭 RAG，但它们站在 SDK 的角度，讲的是"怎么调用我的 API"，很少讲底层组件之间怎么权衡。实际做项目时绕不开的是下面这些问题：

- embedding 用 OpenAI、千问 v4、Cohere、Voyage，还是自己部署 Qwen3-Embedding / BGE？维度选多少？
- 向量库用 Pinecone 还是 pgvector？什么时候该上 Qdrant？
- chunk size 取 256、512 还是 1024？
- 要不要加 rerank？要不要做混合检索？
- 召回率怎么评测？改了 chunk 策略，怎么知道是变好还是变坏？

这本手册不绑定任何 SDK，从原理和可运行的代码出发，把每个参数的影响讲明白，再按场景给出默认值。

---

## 和前面几本手册的分工

| 维度 | 01-langchain RAG | 05-openai-agents FileSearchTool | 06-embedding（本手册） |
|------|------------------|--------------------------------|--------------------------|
| 视角 | LangChain SDK | OpenAI 托管 | 底层组件 + 工程权衡 |
| 内容 | 怎么用 LangChain 拼 | 托管和自搭怎么选 | 每个组件怎么选、怎么调 |
| 评测 | 简单提及 | 简单提及 | 单独一章 |
| 适合 | 快速搭起来 | 不想运维 | 要做生产级 RAG |

---

## 章节结构（44 篇）

### [01-foundations（基础原理）](./docs/01-foundations)：6 篇
1. 什么是 embedding，为什么有效
2. 向量空间 + similarity metrics
3. embedding 怎么训出来的（contrastive learning）
4. dimension / 精度 / 性能权衡
5. 多语言 embedding 怎么工作的
6. 多模态 embedding（CLIP 原理）

### [02-models（模型选型）](./docs/02-models)：6 篇
1. OpenAI text-embedding-3 系列
2. Cohere / VoyageAI
3. 开源模型：Qwen3-Embedding / BGE / Jina / Nomic
4. sentence-transformers 工具链
5. Rerank 模型（重点）
6. MTEB 怎么看 + 选型决策树

### [03-vector-db（向量数据库）](./docs/03-vector-db)：7 篇
1. 选型全景 + 决策树
2. Pinecone（云端托管）
3. Qdrant（self-hosted）
4. pgvector（已有 Postgres）
5. Chroma / LanceDB（embedded）
6. HNSW / IVF / PQ 索引原理
7. 混合存储（vector + metadata filter）

### [04-chunking（切片策略）](./docs/04-chunking)：5 篇
1. 为什么 chunking 决定 RAG 上限
2. 固定 vs 语义 vs 递归
3. 文档结构感知（PDF / Markdown / HTML / 表格）
4. 多粒度（small-to-big）（重点）
5. metadata 设计

### [05-retrieval（检索策略）](./docs/05-retrieval)：6 篇
1. 纯向量 vs 关键词 vs 混合
2. BM25 + Dense 融合（RRF）
3. HyDE
4. Multi-query / Sub-query
5. 重排 pipeline（重点）
6. Self-query / metadata filter

### [06-evaluation（评测）](./docs/06-evaluation)：4 篇（重点）
1. 检索指标：Recall@k / MRR / nDCG
2. 建评测集
3. 端到端 RAG 评测
4. 持续评测 + 回归

### [07-production（生产化）](./docs/07-production)：5 篇
1. 增量索引
2. 批量 embed + cost 优化
3. 缓存策略
4. 部署形态
5. 监控

### [08-applications（应用场景）](./docs/08-applications)：5 篇
1. RAG 完整 pipeline
2. 语义搜索
3. 多模态：图搜图 / 文搜图
4. 用 embedding 做推荐系统
5. 去重 / 聚类 / 异常检测

标了"重点"的几个环节，是我觉得最容易被低估、对线上效果影响却很大的部分。

---

## 安装与运行

示例代码都在正文里，可直接复制运行。

先装依赖、配好 key：

```bash
cd 06-embedding
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 默认用千问 text-embedding-v4：
export DASHSCOPE_API_KEY=sk-...   # 阿里云百炼；兼容接口 base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
# 其它厂商的 key（COHERE_API_KEY / VOYAGE_API_KEY / PINECONE_API_KEY）按需设置
```

其余几本手册统一用 `OPENAI_API_KEY` + `OPENAI_BASE_URL` 指向百炼兼容接口，和这里的 `DASHSCOPE_API_KEY` 是同一把 key、同一个端点，两种写法等价。正文代码读的是 `DASHSCOPE_API_KEY` 并显式传 `base_url`；如果你已经按其他手册设好了那两个变量，执行 `export DASHSCOPE_API_KEY=$OPENAI_API_KEY` 即可。

`requirements.txt` 里 torch、sentence-transformers 这些本地模型依赖体积很大，只用 API 的话可以先注释掉。

---

## 跟其它手册的交叉

- [LangChain 全家桶系列教程](../01-langchain/README.md)：本手册讲底层，可以和 LangChain 的 RAG 章节对照着读
- [Prompt Engineering 实战手册](../04-prompt-engineering/README.md)：本手册的评测部分（从[检索评测指标：Recall@k / MRR / nDCG](docs/06-evaluation/01-metrics.md)开始）沿用了那边的 evalset 方法
- [OpenAI Agents SDK 实战手册](../05-openai-agents-sdk/README.md)：[向量库选型全景 + 决策树](docs/03-vector-db/01-selection.md)讨论了自己搭和用 FileSearch 托管怎么取舍
- [MCP 学习手册](../03-mcp/README.md)：检索能力可以包成 MCP Server，给各种 Agent 共用

---

## 学习路径

如果只想尽快搭一个能用的 RAG，按这个顺序读：
```
01-foundations/01-what-is-embedding
  → 02-models/01-openai
  → 03-vector-db/04-pgvector
  → 04-chunking/02-strategies
  → 05-retrieval/01-vector-vs-keyword
  → 08-applications/01-full-rag
```

已经有 RAG、想提升质量：
```
04-chunking/04-small-to-big
  → 05-retrieval/02-bm25-fusion
  → 05-retrieval/05-rerank-pipeline
  → 06-evaluation/01-metrics
  → 06-evaluation/03-end-to-end
```

准备上生产：
```
03-vector-db/01-selection
  → 07-production/01-incremental
  → 07-production/02-batch-cost
  → 07-production/05-monitoring
```
