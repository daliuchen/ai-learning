# EKB 23：语义分块——按结构切，别用固定长度硬切

> **一句话**：分块决定 RAG 的上限。企业文档有清晰的小节结构，应该**按语义边界（小节）切**，而不是「每 500 字一刀」。每个 chunk 还要带上「属于哪篇文档、哪个小节」的元数据——这是引用溯源的基础。本篇给出语义分块的实现。

---

## 1. 为什么不用固定长度切

固定长度切（如每 500 字一刀）最省事，但会出大问题：

```
❌ 固定切：
「...报销单次上限 2000 元。需在出差结 |切| 束后 15 个工作日内提交。
住宿费一线城市...」

→ 「报销时限」这句话被从中间切断，分到两个 chunk
→ 用户问「报销多久内提交」，命中的 chunk 只有半句话，答不全
```

企业文档的知识是**按小节组织**的。一个小节（「报销时限」）就是一个完整的语义单元，应该尽量**整块**进同一个 chunk。

---

## 2. 语义分块的策略

基于上一篇产出的「带 path 的 blocks」，按小节聚合：

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

两个关键点：
1. **小节标题拼进 chunk 内容**（`b['path']\n...`）——让 embedding 也能感知「这段在讲什么主题」，提升检索准确率
2. **小节太长才二次切分**，且切分时保留 `path`

---

## 3. 把小节标题放进 chunk 内容，为什么有用

对比两种 chunk：

```
❌ 裸内容：「单次上限 2000 元。需在 15 个工作日内提交。」
   → embedding 不知道这是「报销」相关，可能召回不到

✅ 带路径：「差旅与报销制度 > 报销流程 > 单次额度
           单次上限 2000 元。需在 15 个工作日内提交。」
   → embedding 捕捉到「报销/额度」语义，「报销上限多少」更易命中
```

这是个**低成本高回报**的技巧——上下文标题给了片段「身份」，检索准确率往往明显提升。这也呼应 [06 手册分块](/docs/06-embedding/04-chunking/01-why-chunking) 和 [07 手册上下文结构](/docs/07-context-engineering/02-anatomy/06-structure)。

---

## 4. chunk 大小怎么定

没有万能值，按内容密度权衡：

| chunk 偏小（~200字） | chunk 偏大（~800字） |
|----------------------|----------------------|
| 检索精准，定位准 | 上下文完整，少切断 |
| 但可能缺上下文 | 但召回时夹带噪声 |
| 适合 FAQ 式短问答 | 适合需要前后文的长说明 |

**起步建议**：~300-500 字，重叠 ~50-80 字。然后**用评估集测**——这正是第 04 章评估的用途：调 chunk 大小，看 recall 怎么变，选最优。别凭感觉定。

---

## 5. 重叠（overlap）解决什么

相邻 chunk 留一点重叠，防止「答案正好横跨两个 chunk 边界」时两边都答不全：

```
chunk A: [...报销标准。单次上限 2000 元。]
chunk B: [单次上限 2000 元。需 15 个工作日内提交...]   ← 重叠了一句
```

代价是轻微的存储/检索冗余。小重叠（10-20%）通常划算。但**语义分块本身已经减少了边界切断**，所以重叠可以比固定切时更小。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 固定长度硬切 | 切断语义单元，答不全 | 按小节语义切 |
| chunk 不带 section_path | 无法引用溯源 | 每个 chunk 带 path |
| 小节标题不放进内容 | 检索召回率低 | path 拼进 chunk 文本 |
| chunk 大小凭感觉定 | 不是最优 | 用评估集测着调 |
| 完全不重叠 | 边界答案丢失 | 留小重叠 |

---

## 下一步

chunk 切好了，给每个 chunk 生成向量：

→ [04-embedding-gen](./04-embedding-gen.md)
