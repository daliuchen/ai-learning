# EKB 24：生成 embedding（批量、归一化、对齐）

> 这一步给每个 chunk 算向量，有三件事要做对：批量调用，省时间也省钱；做归一化，配合余弦距离；文档侧和查询侧用同一个模型、同样的方式处理。下面接着上一篇的 chunk，把它们变成向量。

---

## 1. 直接用选型时定好的 embedder

第 03 章已经把 embedding 抽象成了 `Embedder` 接口，这里拿来就用：

```python
# ingest/embed.py
from generate.embedder import embedder   # 选型阶段定的实现（BGE / OpenAI 兼容端点）

def embed_chunks(chunks: list[dict]) -> list[dict]:
    texts = [c["content"] for c in chunks]
    vectors = embedder.embed(texts)       # 批量
    for c, v in zip(chunks, vectors):
        c["embedding"] = v
    return chunks
```

送去 embed 的是 `c["content"]`，也就是上一篇里已经拼好 section_path 的内容，这样检索时语义信息是完整的。

---

## 2. 批量调用

一条一条地 embed，是新手最常见的低效写法：

```python
# ❌ 慢且贵：逐条调用
for c in chunks:
    c["embedding"] = embedder.embed([c["content"]])[0]   # 几百次网络往返

# ✅ 批量：一次喂一批
vectors = embedder.embed([c["content"] for c in chunks])  # 大幅减少往返
```

API 模型按请求计费和限流，本地模型批量跑才能把 GPU 用满，两种写法的耗时能差一个数量级。单批也别太大：API 有条数和 token 上限，本地有显存上限。按服务上限定批大小（本地模型几十条一批常见），超了就分批：

```python
def embed_in_batches(texts, batch_size=64):
    out = []
    for i in range(0, len(texts), batch_size):
        out.extend(embedder.embed(texts[i:i + batch_size]))
    return out
```

> 各家服务的单批上限不一样。百炼 `text-embedding-v4` / `v3` 单次请求最多 10 条（见官方文档），传 128 条会被拒绝；OpenAI 和本地模型宽松得多。所以分批最好放在 `embedder.embed` 内部，按各自的上限切（第 13 篇的 `OpenAICompatEmbedder` 就是这么做的），上层不用操心。

---

## 3. 归一化

我们的 pgvector 索引用的是余弦距离（`vector_cosine_ops`）。把向量归一化成长度 1 之后，余弦相似度就等于点积，计算更稳，有些后端也更快：

```python
# BGE 等模型通常提供 normalize 选项
self.model.encode(texts, normalize_embeddings=True)
```

归一化要么两侧都做，要么都不做，查询和文档必须一致，否则距离会失真。用余弦距离时，我建议统一归一化。

---

## 4. 查询侧必须同样处理

这是最容易踩的坑。文档侧 embed 时做过什么处理（加前缀、归一化），查询侧就得原样照做，否则两个向量不在同一个空间里，检索结果会一团乱。

```python
# 有些模型（如 bge）建议查询加指令前缀
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

def embed_query(q: str):
    return embedder.embed([QUERY_PREFIX + q])[0]   # 查询侧专用

def embed_docs(texts: list[str]):
    return embedder.embed(texts)                   # 文档侧不加前缀
```

查询侧和文档侧的处理逻辑都收进 `embedder`，别散落在各处。第 03 章讲「收敛到一处」，说的就是这个。

---

## 5. 维度要和表结构对齐

`chunks.embedding` 列的维度（`vector(1024)`）必须和模型输出一致：

| 模型 | 维度 |
|------|------|
| bge-large-zh | 1024 |
| 百炼 text-embedding-v4（默认） | 1024（可选 2048/1536/…/64） |
| OpenAI text-embedding-3-small（默认） | 1536（可用 `dimensions` 缩短） |
| bge-m3 | 1024 |

换模型可能就换了维度，这时得 `ALTER` 列，再把所有 chunk 重新 ingest 一遍。所以选型要在全量 ingest 之前定下来。也可以从 `embedder.dim` 读出维度、动态建表，少写死一个数字。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 逐条 embed | 慢、贵、容易被限流 | 批量 + 分批 |
| 文档归一化、查询不归一化 | 距离失真 | 两侧一致 |
| 查询不加模型要求的前缀 | 召回率下降 | 按模型卡处理查询侧 |
| embed 裸文本（没拼 path） | 丢了语义上下文 | embed 拼了 path 的 content |
| 维度和表列不一致 | 写入报错 | 维度对齐，换模型就重建 |

---

## 下一步

向量算好了，下一篇把它们写进 Postgres：[写入 pgvector](./05-write-pgvector.md)
