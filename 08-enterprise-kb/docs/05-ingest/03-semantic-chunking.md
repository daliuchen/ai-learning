# EKB 23：按文档结构做语义分块

> 分块的好坏，基本决定了 RAG 能做到多好。企业文档本身有清楚的小节结构，应该沿着小节这种语义边界切，而不是每 500 字砍一刀。每个 chunk 还要记住自己属于哪篇文档、哪个小节，引用溯源靠的就是这份元数据。这篇给出语义分块的实现。

---

## 1. 为什么不用固定长度切

固定长度切最省事，但问题很明显：

```
❌ 固定切：
「...报销单次上限 2000 元。需在出差结 |切| 束后 15 个工作日内提交。
住宿费一线城市...」

→ 「报销时限」这句话被从中间切断，分到两个 chunk
→ 用户问「报销多久内提交」，命中的 chunk 只有半句话，答不全
```

企业文档里的知识是按小节组织的。像「报销时限」这样一个小节，本身就是完整的语义单元，应该尽量整块放进同一个 chunk。

---

## 2. 分块策略

输入是上一篇产出的带 path 的 blocks，按小节聚合：

```python
# ingest/chunk.py
MAX_CHARS = 500       # 单 chunk 软上限
OVERLAP = 80          # 相邻 chunk 重叠，防止边界信息丢失

def chunk_blocks(blocks: list[dict], doc_meta: dict) -> list[dict]:
    chunks = []
    for b in blocks:
        text = f"{b['path']}\n{b['text']}"   # 把小节标题拼进内容（关键！）
        if len(text) <= MAX_CHARS:
            chunks.append(_mk(text, b, doc_meta))
        else:
            # 小节太长，再按句子切，但保留 path
            for piece in split_long(b["text"], MAX_CHARS, OVERLAP):
                chunks.append(_mk(f"{b['path']}\n{piece}", b, doc_meta))
    return chunks

def _mk(text, block, doc_meta):
    return {
        "content": text,
        "section_path": block["path"],
        "doc_title": doc_meta["title"],
        "source_url": doc_meta.get("source_url"),
    }
```

这段代码有两处要留意。第一，小节标题被拼进了 chunk 内容（`b['path']\n...`），embedding 因此能感知这段在讲什么主题，检索更准。第二，只有小节太长时才二次切分，而且切出来的每一片都保留 `path`。

---

## 3. 把小节标题放进内容为什么有用

对比一下两种 chunk：

```
❌ 裸内容：「单次上限 2000 元。需在 15 个工作日内提交。」
   → embedding 不知道这是「报销」相关，可能召回不到

✅ 带路径：「差旅与报销制度 > 报销流程 > 单次额度
           单次上限 2000 元。需在 15 个工作日内提交。」
   → embedding 捕捉到「报销/额度」语义，「报销上限多少」更易命中
```

这个技巧几乎不花成本，收益却很明显：标题告诉 embedding 这段话属于什么主题。[06 手册的分块](/docs/06-embedding/04-chunking/01-why-chunking) 和 [07 手册的上下文结构](/docs/07-context-engineering/02-anatomy/06-structure) 讲的也是同一个道理。

---

## 4. chunk 大小怎么定

没有通用的最优值，要看内容密度：

| chunk 偏小（~200字） | chunk 偏大（~800字） |
|----------------------|----------------------|
| 检索精准，定位准 | 上下文完整，少切断 |
| 但可能缺上下文 | 但召回时夹带噪声 |
| 适合 FAQ 式短问答 | 适合需要前后文的长说明 |

我的起步值是 300 到 500 字、重叠 50 到 80 字，然后用评估集去测。第 04 章的评估集正好派上用场：改 chunk 大小，看 recall 怎么变，挑表现最好的那组，别凭感觉拍。

---

## 5. 重叠解决什么问题

相邻 chunk 留一点重叠，是为了应付答案恰好横跨两个 chunk 边界的情况，否则两边都答不全：

```
chunk A: [...报销标准。单次上限 2000 元。]
chunk B: [单次上限 2000 元。需 15 个工作日内提交...]   ← 重叠了一句
```

代价是存储和检索上有一点冗余，10% 到 20% 的重叠通常划算。语义分块本身已经减少了从中间切断的情况，所以重叠可以比固定长度切时更小。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 固定长度硬切 | 切断语义单元，答不全 | 按小节语义切 |
| chunk 不带 section_path | 无法引用溯源 | 每个 chunk 带 path |
| 小节标题不放进内容 | 召回率低 | path 拼进 chunk 文本 |
| chunk 大小凭感觉定 | 达不到最优 | 用评估集测着调 |
| 完全不重叠 | 跨边界的答案丢失 | 留小重叠 |

---

## 下一步

chunk 切好了，接下来给每个 chunk 生成向量：[生成 embedding](./04-embedding-gen.md)
