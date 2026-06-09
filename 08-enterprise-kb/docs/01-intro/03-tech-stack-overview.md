# EKB 03：技术栈全景——每个选择背后的理由

> **一句话**：技术栈不是「挑最酷的」，而是「挑最适合这个场景的」。本篇把整个项目的选型一次性摊开，给出每个决策的**一句话理由**；完整的选型方法论和权衡在第 03 章展开。

---

## 1. 全景图

```
前端层    Next.js (TS) ── 问答页 + 文档后台 ── SSE 流式
              │
              │ HTTP / SSE
              ▼
AI 服务层  FastAPI (Python)
          ├─ Pydantic AI      ← 生成 + 结构化输出
          ├─ 自写检索层         ← 混合检索 + rerank + query 改写
          └─ 自写 Ingest 层     ← 解析 + 分块 + embedding
              │
              ▼
数据层    Postgres + pgvector  ← 文档 / chunk / 向量 / ACL / 日志
```

一句话概括这套架构的取舍：**产品壳用 TS，AI 大脑用 Python，数据共享一个 Postgres**；无聊的 plumbing 借脚手架，可教的核心手写。

---

## 2. 逐层选择与理由

### 2.1 向量库：pgvector（不是 Qdrant/Milvus）

| 候选 | 适合场景 |
|------|----------|
| **pgvector** ✅ | 几千~几万 chunk，已有/愿意用 Postgres |
| Qdrant / Milvus | 百万级以上、需要独立扩缩容 |
| 纯长上下文（无向量库） | 知识库小到能整个塞进窗口 |

企业知识库通常几百~几千篇文档，切完也就**几千~几万 chunk**。这个规模 pgvector 绰绰有余，还能少维护一个组件、和业务数据同库（方便做 ACL join）。**别为了用网红向量库而用。** 详见 [03-selection/02-vector-db-pgvector](../03-selection/02-vector-db-pgvector.md)。

### 2.2 检索：混合检索 + rerank + query 改写

企业知识库最大的痛：**员工的问法和文档的用词对不上**。员工问「钱能不能要回来」，文档写「退款流程」。
- 纯向量检索会漏专有名词/缩写 → 加 **BM25 关键词**
- 两路召回有噪声 → 加 **rerank** 精排
- 口语化问题离文档表述远 → 加 **query 改写**

这套是检索质量的分水岭，详见第 07 章。

### 2.3 生成框架：Pydantic AI（不是 LangChain）

企业知识库是个**相对定型的 pipeline**，不需要 LangChain 那种重编排。选 Pydantic AI 的关键理由：它的**结构化输出**能强制模型返回固定 schema：

```python
from pydantic import BaseModel

class Answer(BaseModel):
    text: str                 # 回答正文
    cited_doc_ids: list[int]  # 引用了哪几篇文档
    found: bool               # 是否真的找到了依据（没找到就 False）
```

「答案 + 引用 + 是否找到」直接落在类型里，引用溯源和兜底逻辑天然成立。详见 [03-selection/04-framework-pydantic-ai](../03-selection/04-framework-pydantic-ai.md)。

### 2.4 Embedding：可切换，中文优先

中文为主的知识库，不要无脑默认 OpenAI。我们把 embedding 做成**可替换**的一层（中文 BGE / 商用 / OpenAI 都能插），既贴合中文场景，又是 06 手册「embedding 选型」的活教材。详见 [03-selection/03-embedding-selection](../03-selection/03-embedding-selection.md)。

### 2.5 前端：Next.js + SSE

问答页要**流式**（答案逐字蹦），用 SSE（Server-Sent Events）比 WebSocket 更轻、更适合「服务器单向推」。引用卡片用 React 组件渲染。详见第 09 章。

### 2.6 后端：FastAPI（Python）

AI 那一层用 Python，能直接复用前 6 本手册的全部代码和生态（Pydantic AI、sentence-transformers、各种 embedding 库）。FastAPI 异步、自带 SSE 支持、和 Pydantic 天生一对。

---

## 3. 为什么是「TS 壳 + Python 脑」的拆分

很多人会问：为什么不全用一种语言？

| 方案 | 问题 |
|------|------|
| 全 TS | AI 生态（embedding、rerank、Pydantic AI）在 Python 更成熟 |
| 全 Python | 前端体验（流式 UI、组件）TS/React 更顺手 |
| **TS 壳 + Python 脑** ✅ | 各用所长，且这就是**真实生产里的常见架构** |

这个拆分本身就是可教的——它演示了「产品层和 AI 层职责分离」这个真实工程模式。

---

## 4. 整个项目用到了前几本手册的什么

这本手册是前 7 本的「综合实战」，对应关系：

| 本项目环节 | 来自哪本手册 |
|-----------|-------------|
| Embedding 选型、分块、向量索引 | 06 Embedding |
| 混合检索、rerank、query 改写 | 06 Embedding |
| 上下文裁剪、prompt caching | 07 Context Engineering |
| 评估先行、测试集驱动迭代 | 04 Prompt Engineering / 07 |
| Pydantic AI Agent、结构化输出 | 02 Pydantic AI |
| 答案 prompt、引用约束的措辞 | 04 Prompt Engineering |

学完这本，你会发现前 7 本不是孤立的——它们本来就是为了拼成这样一个系统而存在的。

---

## 5. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 几千文档上 Milvus | 过度工程、多一个组件要运维 | pgvector 起步 |
| 用 LangChain 编排定型 pipeline | 黑盒、调试难 | 自写薄检索层 + Pydantic AI 生成 |
| 中文知识库默认 OpenAI embedding | 中文召回可能不如 BGE | embedding 做成可切换 |
| 前后端用同一语言硬凑 | 某一层生态吃亏 | TS 壳 + Python 脑 |

---

## 下一步

- 选型怎么从头推导？→ [03-selection/01-selection-methodology](../03-selection/01-selection-methodology.md)
- 怎么跟着学、需要什么环境 → [05-how-to-use](./05-how-to-use.md)
