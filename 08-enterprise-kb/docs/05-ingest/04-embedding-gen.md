# EKB 24：生成 embedding——批量、归一化、对齐

> **一句话**：给每个 chunk 算向量。这一步要注意三件事——**批量**调用（省时省钱）、**归一化**（配合余弦距离）、以及确保「文档侧」和「查询侧」用同一个模型同样处理。本篇接上一篇的 chunk，把它们变成向量。

---

## 1. 用上选型时定的可切换 embedder

第 03 章把 embedding 抽象成了 `Embedder` 接口，这里直接用：

```python
# ingest/embed.py
from generate.embedder import embedder   # 选型阶段定的实现（BGE / OpenAI）

def embed_chunks(chunks: list[dict]) -> list[dict]:
    texts = [c["content"] for c in chunks]
    vectors = embedder.embed(texts)       # 批量
    for c, v in zip(chunks, vectors):
        c["embedding"] = v
    return chunks
```

注意 embed 的是 `c["content"]`——也就是**已经拼了 section_path 的内容**（见上一篇），保证检索时语义信息完整。

---

## 2. 批量调用：省时省钱的关键

一条一条 embed 是新手最常见的低效写法：

```python
# ❌ 慢且贵：逐条调用
for c in chunks:
    c["embedding"] = embedder.embed([c["content"]])[0]   # 几百次网络往返

# ✅ 批量：一次喂一批
vectors = embedder.embed([c["content"] for c in chunks])  # 大幅减少往返
```

API 模型按请求计费/限流，本地模型批量能用满 GPU。**批量是数量级的差异**。注意单批别太大（API 有 token 上限，本地有显存上限），几百条一批，超了就分批：

```python
def embed_in_batches(texts, batch_size=128):
    out = []
    for i in range(0, len(texts), batch_size):
        out.extend(embedder.embed(texts[i:i + batch_size]))
    return out
```

---

## 3. 归一化：配合余弦距离

我们的 pgvector 索引用余弦距离（`vector_cosine_ops`）。如果向量**归一化**（长度变成 1），余弦相似度就等价于点积，计算更稳、有些后端更快：

```python
# BGE 等模型通常提供 normalize 选项
self.model.encode(texts, normalize_embeddings=True)
```

要点：**归一化要么都做，要么都不做，且查询和文档保持一致**。不一致会让距离计算失真。用余弦距离时，建议统一归一化。

---

## 4. 查询侧必须同样处理

这是最容易踩的坑：文档侧 embed 时做了某种处理（加前缀、归一化），**查询侧也必须一模一样**，否则两个向量不在同一空间，检索全乱。

```python
# 有些模型（如 bge）建议查询加指令前缀
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

def embed_query(q: str):
    return embedder.embed([QUERY_PREFIX + q])[0]   # 查询侧专用

def embed_docs(texts: list[str]):
    return embedder.embed(texts)                   # 文档侧不加前缀
```

把「查询侧」和「文档侧」的处理都收进 `embedder`，避免散落各处导致不一致（呼应第 03 章「收敛到一处」）。

---

## 5. 维度与表结构对齐

`chunks.embedding` 列的维度（`vector(1024)`）必须和模型输出维度一致：

| 模型 | 维度 |
|------|------|
| bge-large-zh | 1024 |
| OpenAI text-embedding-3-small | 1536 |
| bge-m3 | 1024 |

换模型 → 维度可能变 → 要 `ALTER` 列并重新 ingest 全部 chunk。所以前面强调**选型要在全量 ingest 之前定**。可以把维度从 `embedder.dim` 读出来动态建表，减少硬编。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 逐条 embed | 慢、贵、限流 | 批量 + 分批 |
| 文档归一化、查询不归一化 | 距离失真 | 两侧一致 |
| 查询不加模型要求的前缀 | 召回率下降 | 按模型卡处理查询侧 |
| embed 裸文本（没拼 path） | 丢语义上下文 | embed 拼了 path 的 content |
| 维度和表列不一致 | 写入报错 | 维度对齐，换模型重建 |

---

## 下一步

向量算好了，写进 Postgres：

→ [05-write-pgvector](./05-write-pgvector.md)
