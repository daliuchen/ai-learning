# EKB 03：技术栈与选型理由

> 选技术栈看的是合不合适这个场景，不是够不够新。这篇把整个项目的选型一次摊开，每个决定配一两句理由；完整的选型方法和权衡放在第 03 章。

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

这套架构的取舍是：产品外壳用 TS，AI 部分用 Python，两边共享一个 Postgres。枯燥的基础设施借脚手架，值得讲的核心逻辑自己写。

---

## 2. 逐层选择与理由

### 向量库：pgvector（不选 Qdrant/Milvus）

| 候选 | 适合场景 |
|------|----------|
| pgvector（选用） | 几千到几万 chunk，已有或愿意用 Postgres |
| Qdrant / Milvus | 百万级以上、需要独立扩缩容 |
| 纯长上下文（无向量库） | 知识库小到能整个塞进窗口 |

企业知识库一般几百到几千篇文档，切完也就几千到几万个 chunk。这个规模 pgvector 绰绰有余，还能少运维一个组件，并且和业务数据同库，做 ACL join 很方便。不要因为某个向量库名气大就用它。详见[向量库选型](../03-selection/02-vector-db-pgvector.md)。

### 检索：混合检索 + rerank + query 改写

企业知识库最头疼的是员工的问法和文档用词对不上。员工问「钱能不能要回来」，文档里写的是「退款流程」。三项增强各管一块：
- 纯向量检索会漏掉专有名词和缩写，所以加 BM25 关键词检索
- 两路召回都带噪声，所以加 rerank 精排
- 口语化的问题离文档表述太远，所以加 query 改写

检索质量的高低基本由这一层决定，详见检索增强一章（从[为什么 RAG 的上限由检索决定](../07-retrieval/01-retrieval-is-key.md)开始）。

### 生成框架：Pydantic AI（不选 LangChain）

企业知识库的 pipeline 相对固定，用不上 LangChain 那种重型编排。选 Pydantic AI 主要是因为它的结构化输出能强制模型按固定 schema 返回：

```python
from pydantic import BaseModel

class Answer(BaseModel):
    text: str                 # 回答正文
    cited_doc_ids: list[int]  # 引用了哪几篇文档
    found: bool               # 是否真的找到了依据（没找到就 False）
```

答案、引用、是否找到依据都写在类型里，引用溯源和兜底逻辑顺着类型就能实现。详见[生成框架选型](../03-selection/04-framework-pydantic-ai.md)。

### Embedding：可切换，中文优先

中文为主的知识库，不要不假思索地默认 OpenAI。我们把 embedding 做成可替换的一层，中文 BGE、商用 API、OpenAI 都能接进来。这样既照顾中文场景，也正好拿来演示 06 手册里讲的 embedding 选型。详见[Embedding 选型](../03-selection/03-embedding-selection.md)。

### 前端：Next.js + SSE

问答页要流式输出，答案逐字出现。这种服务器单向推送的场景用 SSE（Server-Sent Events）比 WebSocket 更轻。引用卡片用 React 组件渲染。详见前端一章（从[流式回答的问答页](../09-frontend/01-qa-page-streaming.md)开始）。

### 后端：FastAPI（Python）

AI 层用 Python，可以直接复用前几本手册的代码和生态（Pydantic AI、sentence-transformers、各种 embedding 库）。FastAPI 是异步的，支持 SSE，和 Pydantic 也是一套体系。

---

## 3. 为什么拆成 TS 前端加 Python AI 服务

常有人问：为什么不全用一种语言？

| 方案 | 问题 |
|------|------|
| 全 TS | AI 生态（embedding、rerank、Pydantic AI）在 Python 更成熟 |
| 全 Python | 前端体验（流式 UI、组件）用 TS/React 更顺手 |
| TS 壳 + Python 脑（选用） | 两边各用所长，生产里也很常见 |

这个拆分本身也值得讲，它演示了产品层和 AI 层职责分离的做法。

---

## 4. 项目用到了前几本手册的哪些内容

这本手册把前 7 本串起来用，对应关系如下：

| 本项目环节 | 来自哪本手册 |
|-----------|-------------|
| Embedding 选型、分块、向量索引 | 06 Embedding |
| 混合检索、rerank、query 改写 | 06 Embedding |
| 上下文裁剪、prompt caching | 07 Context Engineering |
| 评估先行、测试集驱动迭代 | 04 Prompt Engineering / 07 |
| Pydantic AI Agent、结构化输出 | 02 Pydantic AI |
| 答案 prompt、引用约束的措辞 | 04 Prompt Engineering |

做完这个项目再回头看，前 7 本讲的东西在这里都各有位置。

---

## 5. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 几千篇文档上 Milvus | 过度工程，多一个组件要运维 | 从 pgvector 起步 |
| 用 LangChain 编排固定 pipeline | 黑盒，难调试 | 自写薄检索层 + Pydantic AI 生成 |
| 中文知识库不评估就默认 OpenAI embedding | 中文召回未必是候选里最好的 | embedding 做成可切换，用评估集比 |
| 前后端硬凑同一种语言 | 总有一层的生态吃亏 | TS 壳 + Python 脑 |

---

## 下一步

- 选型怎么从头推导？→ [选型方法论](../03-selection/01-selection-methodology.md)
- 怎么跟着学、需要什么环境 → [如何使用本手册](./05-how-to-use.md)
