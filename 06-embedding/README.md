# Embedding & 向量检索实战手册

> 一句话：把 RAG / 语义搜索 pipeline 拆成 **chunk → embed → 索引 → 检索 → 重排 → 评测** 6 个独立环节，每个环节都讲清楚 trade-off + 工业级默认值。

---

## 这本手册为啥要写

LangChain / LangIndex / OpenAI FileSearch 都能搭 RAG，但它们是 SDK 视角——告诉你"怎么用我的 API"，不告诉你**底层组件的工程权衡**：

- embedding 选 OpenAI text-embedding-3-small 还是 BGE？dimension 选多少？
- 向量库选 Pinecone 还是 pgvector？什么时候用 Qdrant？
- chunk size 256 / 512 / 1024 哪个对？
- 该不该上 rerank？该不该混合检索？
- 怎么评测召回率？怎么知道改 chunk 策略是好是坏？

本手册不绑任何 SDK，从原理 + 实测出发，帮你**看懂每个旋钮、按场景选对默认值**。

---

## 跟前面手册的差异化

| 维度 | 01-langchain RAG | 05-openai-agents FileSearchTool | **06-embedding（本手册）** |
|------|------------------|--------------------------------|--------------------------|
| 视角 | LangChain SDK | OpenAI 托管 | 底层组件 + 工程权衡 |
| 内容 | 怎么用 LangChain 拼 | 怎么用托管 vs 自搭 | 每个组件怎么选 / 怎么调 |
| 评测 | 简单提及 | 简单提及 | 一整章 |
| 适合 | 快速搭起来 | 不想运维 | 要做生产级 RAG |

---

## 章节结构（44 篇）

### [01-foundations（基础原理）](./docs/01-foundations) — 6 篇
1. 什么是 embedding / 为啥能 work
2. 向量空间 + similarity metrics
3. embedding 怎么训出来的（contrastive learning）
4. dimension / 精度 / 性能权衡
5. 多语言 embedding 怎么工作的
6. 多模态 embedding（CLIP 原理）

### [02-models（模型选型）](./docs/02-models) — 6 篇
1. OpenAI text-embedding-3 系列
2. Cohere / VoyageAI
3. 开源 SOTA：BGE / Nomic / Jina
4. sentence-transformers 工具链
5. ★ Rerank 模型
6. MTEB 怎么看 + 选型决策树

### [03-vector-db（向量数据库）](./docs/03-vector-db) — 7 篇
1. 选型全景 + 决策树
2. Pinecone（云端托管）
3. Qdrant（self-hosted）
4. pgvector（已有 Postgres）
5. Chroma / LanceDB（embedded）
6. HNSW / IVF / PQ 索引原理
7. 混合存储（vector + metadata filter）

### [04-chunking（切片策略）](./docs/04-chunking) — 5 篇
1. chunking 为啥决定 RAG 上限
2. 固定 vs 语义 vs 递归
3. 文档结构感知（PDF / Markdown / HTML / 表格）
4. ★ 多粒度（small-to-big）
5. metadata 设计

### [05-retrieval（检索策略）](./docs/05-retrieval) — 6 篇
1. 纯向量 vs 关键词 vs 混合
2. BM25 + Dense 融合（RRF）
3. HyDE
4. Multi-query / Sub-query
5. ★ 重排 pipeline
6. Self-query / metadata filter

### [06-evaluation（评测）](./docs/06-evaluation) — 4 篇 ★
1. 检索指标：Recall@k / MRR / nDCG
2. 建评测集
3. 端到端 RAG 评测
4. 持续评测 + 回归

### [07-production（生产化）](./docs/07-production) — 5 篇
1. 增量索引
2. 批量 embed + cost 优化
3. 缓存策略
4. 部署形态
5. 监控

### [08-applications（应用场景）](./docs/08-applications) — 5 篇
1. RAG 完整 pipeline
2. 语义搜索
3. 多模态：图搜图 / 文搜图
4. 推荐系统 with embedding
5. 去重 / 聚类 / 异常检测

★ = 容易被低估但工业上影响巨大的环节

---

## 安装与跑起来

```bash
cd 06-embedding
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入 OPENAI_API_KEY 等
python demos/foundations/01_basics.py
```

---

## 跟其它手册的交叉

- **01-langchain**：本手册是底层视角，可以跟 LangChain 的 RAG 章节互补
- **04-prompt-engineering**：06-evaluation 沿用 evalset 方法论
- **05-openai-agents-sdk**：03-vector-db 给出"自搭 vs FileSearch 托管"取舍
- **03-mcp**：检索能力可包成 MCP Server，给所有 Agent 共用

---

## 学习路径

**最短路径**（搭一个能用的 RAG）：
```
01-foundations/01-what-is-embedding
  → 02-models/01-openai
  → 03-vector-db/04-pgvector
  → 04-chunking/02-strategies
  → 05-retrieval/01-vector-vs-keyword
  → 08-applications/01-full-rag
```

**质量优化路径**（已有 RAG，想提质）：
```
04-chunking/04-small-to-big
  → 05-retrieval/02-bm25-fusion
  → 05-retrieval/05-rerank
  → 06-evaluation/01-metrics
  → 06-evaluation/03-end-to-end
```

**生产路径**：
```
03-vector-db/01-selection
  → 07-production/01-incremental
  → 07-production/02-batch-cost
  → 07-production/05-monitoring
```
