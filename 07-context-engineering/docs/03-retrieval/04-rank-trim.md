# 检索结果的排序、裁剪、去重

> 检索回来的一堆 chunk 不能原样塞进窗口。要先重排，把最相关的提到前面；再去重，删掉冗余；然后按 token 预算贪心填充；最后把最相关的放在窗口两端，避开 lost in the middle。这几步不用换模型，做好了效果提升常常很明显。

---

## 1. 从召回的一堆到进窗口的几条

召回阶段为了不漏，一般会放宽到 top-50。但这 50 条里，有的其实不相关（向量相似不等于真的相关），有的内容重复，加起来远超 token 预算，顺序也只是按粗排分数排的。进窗口之前要过四道工序：

```
召回 top-50
   ↓ ① rerank   —— 精排，把真正相关的提到前面
   ↓ ② dedup    —— 删掉内容高度重复的 chunk
   ↓ ③ pack     —— 按 token 预算贪心填充，填满即停
   ↓ ④ reorder  —— 最相关的放两端，次要的塞中间
进窗口的 5~8 条
```

重排（rerank）的算法细节，比如 cross-encoder 和 LLM-as-judge，见 Embedding 手册的 [重排 Pipeline：召回 → 精排](../../../06-embedding/docs/05-retrieval/05-rerank-pipeline.md)。这篇假设已经有一个 `rerank()` 能给出分数，只讲后面三步：去重、裁剪和排布。

---

## 2. 去重：删掉冗余的 chunk

知识库里同一段内容经常以好几种形式出现（改写过的 FAQ、不同版本的文档、镜像页面），召回时会被一起捞回来，白占预算，还稀释注意力。可以用 embedding 相似度检测近似重复：

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

阈值一般取 0.9 到 0.95，太低会把其实不同的内容也删掉。遍历时按 rerank 分数从高到低，保留分高的那条，丢掉后面的近似重复。手头没有现成的 embedding，也可以退一步用 MinHash 或字符串相似度，只是粗糙一些。

---

## 3. 按 token 预算贪心填充

别写「固定取 5 条」。chunk 长短不一，5 条可能是 800 token，也可能是 6000。合理的做法是给定一个 token 预算，从高分往下贪心地填，放不下就跳过。

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

几点说明：

- 预算从窗口反推：总窗口减去 system、历史、query 和留给输出的 token，剩下的才是检索能用的预算。别把窗口填满，要给生成留出空间。
- `continue` 和 `break` 的区别：用 `continue`，后面更短的高价值 chunk 还有机会挤进来；要严格按顺序取就用 `break`。
- 单条 chunk 太长时，可以先做句子级截断，只保留和 query 最相关的片段，而不是整条丢掉。

---

## 4. 排布：最相关的放两端

模型对上下文首尾的注意力强于中部（lost in the middle，[Context Rot 一篇](../01-foundations/03-context-rot.md)讲了机理和示意数据）。所以填好的 chunk 按分数从高到低线性排并不理想，更好的做法是把最相关的放在头尾，次要的放在中间：

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

另外，可以在每个 chunk 前面加上编号或来源（`[文档 N]`）。这样既方便做引用归因（第 5 篇），也让模型清楚这是第几条参考资料。

---

## 5. 串起来：完整的裁剪流水线

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
| rerank | 粗排不准，相关的排得太靠后 | 真答案在 top-50 里，但没进 top-5 |
| dedup | 冗余 chunk 占预算 | 重复内容挤掉了其他有用的信息 |
| pack | 长度不一，超出预算 | 截断报错，或者浪费 token |
| reorder | lost in the middle | 关键 chunk 被埋在中部，模型没注意到 |

这条流水线不需要换模型，投入很小。很多「RAG 答不准」的问题，原因不在召回，而在这四步没做。

---

## 下一步

- [引用与归因（Citation & Grounding）](./05-attribution.md)：排好的 chunk 怎么让模型注明出处，不脱离资料乱编
- 跨章，lost in the middle 的机理和示意数据：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
- 回顾 k 选多大和 token 预算的关系：[RAG 作为上下文来源](./01-rag-as-context.md)
