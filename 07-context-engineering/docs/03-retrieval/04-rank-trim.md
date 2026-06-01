# 检索结果的排序、裁剪、去重

> **一句话**：检索回来一堆 chunk 不能原样塞进窗口——先**重排**把最相关的提前，**去重**删掉冗余，**按 token 预算贪心填充**，再把最相关的放窗口**两端**对抗 lost-in-the-middle。这一步往往比换 embedding 模型更能提准。

---

## 1. 从「召回的一堆」到「进窗口的几条」

召回阶段为了不漏，通常宽召回 top-50。但这 50 条里：有的不相关（向量相似 ≠ 真相关）、有的内容重复、加起来远超 token 预算、而且顺序是按粗排分数来的。进窗口前要过四道工序：

```
召回 top-50
   ↓ ① rerank   —— 精排，把真正相关的提到前面
   ↓ ② dedup    —— 删掉内容高度重复的 chunk
   ↓ ③ pack     —— 按 token 预算贪心填充，填满即停
   ↓ ④ reorder  —— 最相关的放两端，次要的塞中间
进窗口的 5~8 条
```

重排（rerank）的**算法细节**（cross-encoder、LLM-as-judge）见 Embedding 手册的重排 pipeline 篇。本篇聚焦后三步——**裁剪、去重、排布**这些"进窗口"的上下文工程动作，假设已有一个 `rerank()` 给出分数。

---

## 2. 去重：删掉冗余 chunk

知识库里同一段内容常以多种形式重复（FAQ 改写、文档版本、镜像页），召回会把它们一起捞回来，白占预算还稀释注意力。用 embedding 相似度做近重复检测：

```python
import numpy as np

def dedup(chunks, embeddings, threshold: float = 0.92):
    """贪心去重：与已保留 chunk 余弦相似度超阈值则丢弃。"""
    kept, kept_emb = [], []
    for c, emb in zip(chunks, embeddings):
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        if kept_emb and max(emb @ k for k in kept_emb) > threshold:
            continue  # 和已有的太像，跳过
        kept.append(c)
        kept_emb.append(emb)
    return kept
```

- 阈值经验值 0.9~0.95；太低会误删真正不同的内容。
- 按 rerank 分数从高到低遍历，**保留分高的那个**，丢后来的近重复。
- 没有现成 embedding 时，退化用 MinHash / 字符串相似度也行，只是糙一些。

---

## 3. 按 token 预算贪心填充

不要写"固定取 5 条"——chunk 长度参差，5 条可能是 800 token 也可能是 6000。正确做法是**给一个 token 预算，从高分往下贪心填，填不下就停**：

```python
import tiktoken  # 或用对应模型的 tokenizer

enc = tiktoken.get_encoding("cl100k_base")

def n_tokens(text: str) -> int:
    return len(enc.encode(text))

def pack_to_budget(ranked_chunks, max_tokens: int = 4000):
    """ranked_chunks 已按相关性降序。贪心填到预算上限。"""
    packed, used = [], 0
    for c in ranked_chunks:
        t = n_tokens(c.text)
        if used + t > max_tokens:
            continue  # 这条放不下，看下一条（也可直接 break）
        packed.append(c)
        used += t
    return packed
```

要点：

- **预算从窗口里反推**：总窗口 − system − 历史 − query − 留给输出的 token = 检索可用预算。别把窗口填满，给生成留地方。
- `continue` vs `break`：用 `continue` 可以让后面更短的高价值 chunk 仍有机会挤进来；要严格按序则用 `break`。
- 单条 chunk 超长时，考虑先做**句子级截断**只保留与 query 最相关的片段，而不是整条丢弃。

---

## 4. 排布：最相关的放两端，对抗 lost-in-the-middle

模型对上下文**首尾的注意力强于中部**（lost-in-the-middle，本手册长上下文章有实测）。所以填好的 chunk 不要按分数线性排，而要把最相关的放头尾、次要的埋中间：

```python
def reorder_for_attention(packed_chunks):
    """packed_chunks 按相关性降序。重排成：最相关在两端，次要在中间。
    结果形如 [1, 3, 5, ..., 6, 4, 2]。"""
    head, tail = [], []
    for i, c in enumerate(packed_chunks):
        (head if i % 2 == 0 else tail).append(c)
    return head + tail[::-1]
```

```
# ❌ 线性排（最相关在最前，关键信息也可能滑入中部被忽略）
[最相关, 次, 次, ..., 最不相关]

# ✅ 两端排（最相关锚在首尾注意力高地）
[最相关, 第3, 第5, ..., 第6, 第4, 第2相关]
```

补充手段：在每个 chunk 前加编号/来源（`[文档 N]`），既方便引用归因（第 5 篇），也给模型"这是第几条参考"的结构感。

---

## 5. 串起来：完整 trim pipeline

```python
def prepare_context(query, retriever, reranker, max_tokens: int = 4000):
    # 1. 宽召回
    candidates = retriever.search(query, k=50)
    # 2. 精排（算法见 Embedding 手册）
    ranked = reranker.rerank(query, candidates)        # 降序
    # 3. 去重
    embs = [c.embedding for c in ranked]
    ranked = dedup(ranked, embs, threshold=0.92)
    # 4. 按预算填充
    packed = pack_to_budget(ranked, max_tokens=max_tokens)
    # 5. 两端排布
    ordered = reorder_for_attention(packed)
    # 6. 格式化（带编号，供归因）
    return "\n\n".join(
        f"[文档 {i+1}] {c.text}" for i, c in enumerate(ordered)
    )
```

| 工序 | 解决什么 | 跳过的后果 |
|------|----------|------------|
| rerank | 粗排不准，相关的排太后 | 真答案在 top-50 但没进 top-5 |
| dedup | 冗余 chunk 占预算 | 重复内容挤掉了其他有用信息 |
| pack | 长度不一、超预算 | 截断报错或 token 浪费 |
| reorder | lost-in-the-middle | 关键 chunk 被埋中部，模型忽略 |

这套 trim pipeline 是纯上下文工程动作，不依赖换模型，**性价比极高**——很多"RAG 答不准"的问题，根因不是召回差，而是这四步没做。

---

## 下一步

- [05-attribution.md](./05-attribution.md)：排好的 chunk 怎么让模型引用出处、不脱离资料胡编
- 跨章：[../01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md) lost-in-the-middle 的实测与机理
- 回顾：[./01-rag-as-context.md](./01-rag-as-context.md) k 选多大与 token 预算的关系
