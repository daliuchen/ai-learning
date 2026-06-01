# CE 09-02：实战 · 长文档问答的上下文工程

> **一句话**：对一本几百页的文档做问答，本质是「怎么把对的内容放进窗口」。两条路线：(A) 长上下文整本塞进去 + prompt caching，(B) RAG 检索按需注入。不是谁更先进，而是看文档规模、问答频率、答案分布——选错路线，要么烧钱要么答不准。

---

## 1. 整体架构：两条路线

```
                          ┌────────────────────────────────────┐
   一本 300 页文档 ───────▶│  路线 A：长上下文 + Prompt Caching    │
                          │  整本一次性塞进 system，缓存前缀       │
   问题 ─────────────────▶│  每个问题复用缓存，只付新 token        │
                          └────────────────────────────────────┘
                          ┌────────────────────────────────────┐
                          │  路线 B：RAG 检索注入                 │
   一本 300 页文档 ─切块─▶ │  分块 → embed → 向量库                │
                          │  问题 → 检索 top-k 块 → 拼进窗口        │
   问题 ─────────────────▶│  只把相关片段放进上下文                │
                          └────────────────────────────────────┘
```

A 让模型「看见全文」，靠 caching 摊薄成本（串 [07-long-context](../07-long-context/03-prompt-caching.md)）；B 让模型「只看见相关片段」，靠检索保证精度（串 [03-retrieval](../03-retrieval/01-rag-as-context.md)）。

---

## 2. 什么场景选哪条

| 维度 | 路线 A 长上下文 | 路线 B RAG |
|------|----------------|------------|
| 文档规模 | ≤ 窗口（约 ≤150 页 / 100K tok） | 任意大，几千页也行 |
| 问答频率 | 同一文档反复问（缓存才划算） | 一次性 / 文档常变 |
| 答案分布 | 分散、跨章节、需全局推理 | 局部、能定位到几个片段 |
| 首次延迟 | 高（要灌全文） | 低（只灌片段） |
| 单次成本 | 高（缓存命中后才便宜） | 低 |
| 漏检风险 | 无（全文都在） | 有（检索没召回到就答不出） |
| 实现复杂度 | 低（塞进去就行） | 高（切块 / embed / 检索调优） |

经验法则：**文档塞得下窗口、且会被反复追问、且问题需要全局视角 → A；文档超大 / 一次性 / 答案能定位 → B。** 现实里常是混合：先 RAG 粗筛到几十页，再长上下文精读。

---

## 3. 路线 A：长上下文 + Prompt Caching

整本塞进 system，用 Anthropic 的 `cache_control` 把文档前缀缓存住。第一次问灌全文（贵），后续问命中缓存（便宜 10 倍、快很多）。

```python
# ✅ 路线 A：整本文档塞进缓存前缀，多问题复用
import anthropic

client = anthropic.Anthropic()  # 读 ANTHROPIC_API_KEY

with open("handbook.md", encoding="utf-8") as f:
    full_doc = f.read()   # 假设约 80K token，塞得下

SYSTEM_BLOCKS = [
    {"type": "text", "text": "你是文档问答助手，只依据下面文档回答；文档没写的就说「文档未提及」。"},
    {
        "type": "text",
        "text": f"<document>\n{full_doc}\n</document>",
        "cache_control": {"type": "ephemeral"},   # 关键：缓存整篇文档
    },
]

def ask_A(question: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SYSTEM_BLOCKS,
        messages=[{"role": "user", "content": question
                   + "\n\n请引用文档中的原文片段支撑你的回答。"}],
    )
    # 观察缓存命中：cache_read_input_tokens 大 = 命中，省钱
    print("cache_read:", resp.usage.cache_read_input_tokens,
          "| cache_write:", resp.usage.cache_creation_input_tokens)
    return resp.content[0].text

print(ask_A("第 3 章的退款政策是什么？"))   # 首问：cache_write 大
print(ask_A("保修和退款有什么区别？"))       # 再问：cache_read 大，便宜
```

缓存默认 5 分钟 TTL（可延长），所以这条路线适合「短时间内对同一文档密集追问」。零散问、文档又大，缓存反复失效就不划算了。

---

## 4. 路线 B：RAG 检索注入

文档切块、embed、入库；问题来了检索 top-k 块拼进窗口。这里用 OpenAI embedding + 内存向量库示意。

```python
# ✅ 路线 B：切块 → 检索 → 注入
import numpy as np
import openai

oai = openai.OpenAI()

def chunk(text: str, size: int = 800, overlap: int = 150) -> list[str]:
    """按字符切，带重叠避免切断答案。生产用按段落 / 语义切。"""
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out

def embed(texts: list[str]) -> np.ndarray:
    resp = oai.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([d.embedding for d in resp.data], dtype=np.float32)

# 建库
chunks = chunk(full_doc)
chunk_vecs = embed(chunks)

def retrieve(query: str, k: int = 5) -> list[str]:
    qv = embed([query])[0]
    sims = chunk_vecs @ qv / (np.linalg.norm(chunk_vecs, axis=1) * np.linalg.norm(qv))
    top = np.argsort(sims)[::-1][:k]
    return [chunks[i] for i in top]

def ask_B(question: str) -> str:
    hits = retrieve(question)
    context = "\n\n---\n\n".join(f"[片段{i+1}]\n{c}" for i, c in enumerate(hits))
    resp = oai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content":
             "只依据提供的文档片段回答；不要编造。回答末尾用 [片段N] 标注引用来源。"},
            {"role": "user", "content": f"文档片段：\n{context}\n\n问题：{question}"},
        ],
        temperature=0,
    )
    return resp.choices[0].message.content

print(ask_B("第 3 章的退款政策是什么？"))
```

只把 5 个相关块（约 4K token）放进窗口，无论原文档多大都成立——这是 RAG 突破窗口限制的根本。

---

## 5. 上下文怎么组织：文档 + 问题 + 引用要求

无论 A/B，窗口里这三块的**摆放和指令**直接决定答案质量：

```
┌─────────────────────────────────────┐
│ System：角色 + 边界（只依据文档 / 没有就说没有） │  ← 头部，最不易被忽略
├─────────────────────────────────────┤
│ 文档 / 片段：用 <document> 或 [片段N] 包裹    │  ← 显式分隔，便于引用
├─────────────────────────────────────┤
│ 问题 + 引用要求（要求标注来源）              │  ← 尾部，紧邻生成
└─────────────────────────────────────┘
```

三个工程要点：

- **显式分隔符**：`<document>...</document>` 或 `[片段N]`，让模型能精确引用、也方便你校验来源，降低幻觉。
- **边界指令前置**：「文档未提及就说没有」放 system 头部——这是抗幻觉最有效的一句。
- **要求带引用**：让模型输出 `[片段3]` 这类标注，既可溯源又能在 B 路线里发现「检索召回错了」。

---

## 6. 处理多跳问题（答案分散在多处）

「保修和退款有什么区别」这类问题，答案分散在文档不同章节——这是路线选择的真正分水岭。

```
# ❌ 朴素 RAG：一次检索 top-k，可能只召回到「退款」的块，漏掉「保修」
retrieve("保修和退款的区别")  → 全是退款相关片段 → 答案残缺

# ✅ 多跳策略
```

| 策略 | 做法 | 适用 |
|------|------|------|
| 查询分解 | 用小模型把多跳问题拆成子问题，分别检索再合并 | RAG，答案明确可拆 |
| 多轮检索 | 先检索→读→发现缺口→再生成新 query 检索（agentic RAG） | 复杂推理链 |
| 退回长上下文 | 文档塞得下就直接走路线 A，全文在窗口里天然支持跨章节推理 | 中等规模文档 |
| 提高 k + rerank | 多召回再重排，覆盖更多片段 | 答案分布稍散 |

查询分解的最小实现：

```python
# ✅ 多跳：分解 → 各自检索 → 合并去重 → 一起注入
def ask_multihop(question: str) -> str:
    sub = oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content":
                   f"把这个问题拆成 2-3 个可独立检索的子问题，每行一个，只输出子问题：\n{question}"}],
        temperature=0,
    ).choices[0].message.content.strip().splitlines()

    seen, merged = set(), []
    for q in sub:
        for c in retrieve(q.strip("-• ").strip(), k=3):
            if c not in seen:           # 跨子问题去重
                seen.add(c); merged.append(c)

    context = "\n\n---\n\n".join(f"[片段{i+1}]\n{c}" for i, c in enumerate(merged))
    return oai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "综合所有片段回答，标注 [片段N] 来源。"},
                  {"role": "user", "content": f"片段：\n{context}\n\n原问题：{question}"}],
        temperature=0,
    ).choices[0].message.content
```

核心思想：**多跳问题 = 多次检索 + 一次综合**，把分散的证据先聚齐再让模型推理。

---

## 7. 优化点

| 优化 | 做法 |
|------|------|
| 切块更聪明 | 按标题 / 段落语义切，别硬切字符；保留章节标题做元数据 |
| 检索更准 | embedding 检索 + BM25 关键词混合（hybrid），再 rerank |
| 引用可点击 | 块带 `doc_id / page / heading` 元数据，回答里给出可定位的引用 |
| A 省钱 | 缓存命中率监控；密集问答期内复用，过期前主动续命 |
| A/B 混合 | RAG 粗筛到几十页 → 长上下文精读，兼顾规模与全局推理 |
| 防答错不说不知道 | system 强制「片段没有就答未提及」，并用引用标注自检 |

---

## 8. 常见坑

| 坑 | 后果 | 解法 |
|----|------|------|
| 文档塞不下还硬走 A | 超窗报错 / 截断丢内容 | 估 token，超了走 B 或 A/B 混合 |
| 缓存没命中却以为省钱 | TTL 过期 / 前缀变了，每次全价 | 监控 `cache_read_input_tokens`，固定缓存前缀别动 |
| 切块切断答案 | 检索到半句，答不全 | 加重叠 + 按语义边界切 |
| 多跳只检索一次 | 漏掉另一半证据 | 查询分解 / 多轮检索 |
| 不要求引用 | 幻觉难发现 | 强制 [片段N] 标注，可溯源 |
| 把文档放窗口中间 | lost in the middle | 文档放前、问题放尾 |

---

## 9. 下一步

- 📖 RAG 作为上下文注入手段 → [03-retrieval/01-rag-as-context.md](../03-retrieval/01-rag-as-context.md)
- 📖 静态注入 vs 动态检索的权衡 → [03-retrieval/02-static-vs-dynamic.md](../03-retrieval/02-static-vs-dynamic.md)
- 📖 长上下文与 prompt caching → [07-long-context/02-prompt-caching.md](../07-long-context/03-prompt-caching.md)
- 📖 lost in the middle 与上下文腐烂 → [01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)
- 📖 下一个实战：多 Agent 研究系统 → [03-multi-agent-research.md](./03-multi-agent-research.md)

## 参考资料

- Anthropic, "Prompt caching" 文档：https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Anthropic, "Contextual Retrieval"：https://www.anthropic.com/news/contextual-retrieval
- OpenAI Embeddings 文档：https://platform.openai.com/docs/guides/embeddings
