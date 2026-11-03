# CE 09-02：实战 · 长文档问答的上下文工程

> 对一本几百页的文档做问答，要解决的是怎么把对的内容放进窗口。有两条路线：A 是把整本文档塞进长上下文，配合 prompt caching；B 是用 RAG 检索，按需注入。两条路线没有高下之分，选哪条要看文档规模、提问频率和答案分布。选错了，要么花冤枉钱，要么答不准。

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

路线 A 让模型看到全文，靠缓存摊薄成本（对应 [Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)）。路线 B 只让模型看到相关片段，靠检索保证精度（对应 [RAG 作为上下文来源](../03-retrieval/01-rag-as-context.md)）。

---

## 2. 什么场景选哪条

| 维度 | 路线 A 长上下文 | 路线 B RAG |
|------|----------------|------------|
| 文档规模 | 窗口放得下（主力模型 1M 量级，约 1,500 页），但受成本和延迟约束，实际多在 200K tok（约 300 页）以内 | 任意大，几千页也行 |
| 问答频率 | 同一文档反复问（缓存才划算） | 一次性，或文档经常变 |
| 答案分布 | 分散、跨章节、需要全局推理 | 局部，能定位到几个片段 |
| 首次延迟 | 高（要灌入全文） | 低（只灌入片段） |
| 单次成本 | 高（缓存命中后才便宜） | 低 |
| 漏检风险 | 无（全文都在） | 有（检索没召回就答不出） |
| 实现复杂度 | 低（塞进去就行） | 高（切块、embed、检索调优） |

现在窗口很少是瓶颈，更常见的约束是成本和延迟。按 Claude Sonnet 5.5 的标价（input \$2、cache read \$0.10，每百万 token）粗算：一份 200K token（约 300 页）的文档，命中缓存时每次提问的文档部分约 \$0.02，没命中就要重新写入缓存（5 分钟档写入价是 1.25×），约 \$0.5，首 token 延迟也明显变长；路线 B 每次只放几 K token 的片段，输入成本不到 \$0.01。所以我的阈值是：文档在 200K token 以内、同一份文档在缓存有效期内会被连续追问，A 才划算；文档更大，或者提问稀疏、缓存经常过期，就走 B。

答案分布也要考虑：问题需要跨章节的全局视角，A 更稳；答案能定位到几个具体片段，B 就够了。实际项目里常常两者混用：先用 RAG 粗筛到几十页，再交给长上下文精读。

---

## 3. 路线 A：长上下文 + Prompt Caching

把整本文档放进 system，用 Anthropic 的 `cache_control` 缓存文档前缀。第一次提问要灌入全文，比较贵；之后的提问命中缓存，读取价只有原价的一成甚至更低，速度也快很多。

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
        model="claude-sonnet-5-5",
        max_tokens=1024,
        system=SYSTEM_BLOCKS,
        messages=[{"role": "user", "content": question
                   + "\n\n请引用文档中的原文片段支撑你的回答。"}],
    )
    # 观察缓存命中：cache_read_input_tokens 大 = 命中，省钱
    print("cache_read:", resp.usage.cache_read_input_tokens,
          "| cache_write:", resp.usage.cache_creation_input_tokens)
    return next(b.text for b in resp.content if b.type == "text")

print(ask_A("第 3 章的退款政策是什么？"))   # 首问：cache_write 大
print(ask_A("保修和退款有什么区别？"))       # 再问：cache_read 大，便宜
```

缓存默认的 TTL 是 5 分钟（可以延长），所以这条路线适合短时间内对同一份文档密集追问。如果提问很零散、文档又大，缓存一次次过期，就不划算了。

---

## 4. 路线 B：RAG 检索注入

文档切块、embed、入库；问题进来后检索 top-k 块拼进窗口。这里用 OpenAI embedding 加内存向量库做示意。

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
        model="gpt-6.1-sol",
        messages=[
            {"role": "system", "content":
             "只依据提供的文档片段回答；不要编造。回答末尾用 [片段N] 标注引用来源。"},
            {"role": "user", "content": f"文档片段：\n{context}\n\n问题：{question}"},
        ],
        temperature=0,   # GPT-6 系是否接受 temperature 以模型页为准，不接受就去掉
    )
    return resp.choices[0].message.content

print(ask_B("第 3 章的退款政策是什么？"))
```

窗口里只放 5 个相关块，每块 800 字符，合计约 4,000 字符（中文 1 字约 0.7–1 个 token，按 1:1 粗估约 4K token），不管原文档有多大都是这样。RAG 能突破窗口限制，靠的就是这一点。

---

## 5. 上下文怎么组织：文档 + 问题 + 引用要求

不管走 A 还是 B，窗口里这三块怎么摆、指令怎么写，直接决定答案的质量：

```
┌──────────────────────────────────────────────────┐
│ System：角色 + 边界（只依据文档 / 没有就说没有） │  ← 头部，最不易被忽略
├──────────────────────────────────────────────────┤
│ 文档 / 片段：用 <document> 或 [片段N] 包裹       │  ← 显式分隔，便于引用
├──────────────────────────────────────────────────┤
│ 问题 + 引用要求（要求标注来源）                  │  ← 尾部，紧邻生成
└──────────────────────────────────────────────────┘
```

工程上有三点要做到：

- 用显式的分隔符，比如 `<document>...</document>` 或 `[片段N]`。模型能精确引用，你也方便校验来源，幻觉会少一些。
- 边界指令放在前面。「文档没提到就说没有」这句写在 system 头部，防幻觉最管用的就是这条指令。
- 要求带引用。让模型输出 `[片段3]` 这样的标注，既能溯源，在路线 B 里还能借此发现检索召回错了。

---

## 6. 处理多跳问题（答案分散在多处）

像「保修和退款有什么区别」这样的问题，答案分散在文档的不同章节。这类问题最能拉开两条路线的差距。

```
# ❌ 朴素 RAG：一次检索 top-k，可能只召回到「退款」的块，漏掉「保修」
retrieve("保修和退款的区别")  → 全是退款相关片段 → 答案残缺

# ✅ 多跳策略
```

| 策略 | 做法 | 适用 |
|------|------|------|
| 查询分解 | 用小模型把多跳问题拆成子问题，分别检索再合并 | RAG，答案明确可拆 |
| 多轮检索 | 先检索、阅读，发现缺口后生成新 query 再检索（agentic RAG） | 复杂推理链 |
| 退回长上下文 | 文档放得下就直接走路线 A，全文都在窗口里，跨章节推理不成问题 | 中等规模文档 |
| 提高 k + rerank | 多召回一些再重排，覆盖更多片段 | 答案分布稍分散 |

查询分解的最小实现如下：

```python
# ✅ 多跳：分解 → 各自检索 → 合并去重 → 一起注入
def ask_multihop(question: str) -> str:
    sub = oai.chat.completions.create(
        model="gpt-6-luna",
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
        model="gpt-6.1-sol",
        messages=[{"role": "system", "content": "综合所有片段回答，标注 [片段N] 来源。"},
                  {"role": "user", "content": f"片段：\n{context}\n\n原问题：{question}"}],
        temperature=0,
    ).choices[0].message.content
```

思路是多次检索、一次综合：先把分散的证据凑齐，再让模型推理。

---

## 7. 优化点

| 优化 | 做法 |
|------|------|
| 切块更合理 | 按标题、段落的语义切，别按字符硬切；保留章节标题作为元数据 |
| 检索更准 | embedding 检索和 BM25 关键词混合（hybrid），再 rerank |
| 引用可点击 | 块带上 `doc_id / page / heading` 元数据，回答里给出能定位的引用 |
| 路线 A 省钱 | 监控缓存命中率；在密集问答期内复用，过期前主动续期 |
| A/B 混合 | 先用 RAG 粗筛到几十页，再用长上下文精读，兼顾规模和全局推理 |
| 防止不知道还硬答 | system 里强制「片段里没有就答未提及」，再用引用标注自检 |

---

## 8. 常见坑

| 坑 | 后果 | 解法 |
|----|------|------|
| 文档放不下还硬走 A | 超出窗口报错，或被截断丢内容 | 先估算 token，超了就走 B 或 A/B 混合 |
| 缓存没命中却以为在省钱 | TTL 过期或前缀变了，每次都付全价 | 监控 `cache_read_input_tokens`，缓存前缀固定下来别动 |
| 切块切断了答案 | 只检索到半句，答不完整 | 加重叠，按语义边界切 |
| 多跳问题只检索一次 | 漏掉另一半证据 | 查询分解或多轮检索 |
| 不要求引用 | 幻觉很难发现 | 强制 [片段N] 标注，可以溯源 |
| 把文档放在窗口中间 | lost in the middle | 文档放前面，问题放最后 |

---

## 9. 下一步

- RAG 作为上下文注入手段：[RAG 作为上下文来源](../03-retrieval/01-rag-as-context.md)
- 静态注入和动态检索怎么权衡：[静态注入 vs 动态检索](../03-retrieval/02-static-vs-dynamic.md)
- 长上下文与 prompt caching：[Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)
- lost in the middle 与上下文腐烂：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
- 下一个实战：[多 Agent 研究系统的上下文编排](./03-multi-agent-research.md)

## 参考资料

- Anthropic, "Prompt caching" 文档：https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic, "Contextual Retrieval"：https://www.anthropic.com/research/contextual-retrieval
- OpenAI Embeddings 文档：https://developers.openai.com/api/docs/guides/embeddings
