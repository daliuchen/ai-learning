# CE 09-01：实战 · 带长期记忆的客服 Agent

> **一句话**：把前面学的「上下文分层」「记忆存取」「压缩」全串成一个能跑的东西——一个跨会话记得你是谁、记得你上次抱怨过啥的客服 Agent。核心不在模型多强，而在每一轮你往窗口里**装了什么**：system 规则 + 用户画像 + 召回的历史记忆 + 当前会话历史 + 当前 query，且每块都有 token 预算、超了就压。

---

## 1. 整体架构

一个生产级客服 Agent，「记忆」其实是两条管线：**写入管线**（每轮把新事实沉淀到长期记忆）和**读取管线**（每轮把相关记忆召回进窗口）。中间夹着一个会话历史的压缩阀门。

```
                      ┌─────────────────────────────────────────┐
   用户新消息 ───────▶│  build_context()  组装本轮上下文窗口        │
                      │                                           │
   ┌──────────────┐   │  ① System 规则（固定）                     │
   │  用户画像表    │──▶│  ② 用户画像（结构化，按 user_id 查）        │
   │ (KV / SQL)    │   │  ③ 召回的长期记忆（向量检索 top-k）          │
   └──────────────┘   │  ④ 当前会话历史（超阈值 → 摘要压缩）          │
   ┌──────────────┐   │  ⑤ 当前 query                              │
   │  长期记忆库    │──▶│                                           │
   │ (向量库)      │   └────────────────────┬──────────────────────┘
   └──────▲───────┘                        │
          │                                ▼
          │  写入管线                    LLM 调用 → 回复
          │  (每轮抽取事实)                  │
          └──────────────────────────────────┘
                    extract_facts(对话片段)
```

读取靠相关性召回（串 [04-memory](../04-memory/03-storage-recall.md)），写入靠每轮事实抽取，会话历史靠摘要压缩（串 [05-compaction](../05-compaction/02-summarization.md)），窗口组装靠分层 + 预算（串 [02-anatomy](../02-anatomy/06-structure.md)）。

---

## 2. 上下文构成与 token 预算

模型每次只看得见这一轮 `build_context` 拼出来的东西。先把预算分配定下来（以 Claude 200K 窗口、留 4K 给回复为例，实际给上下文约 12K 才划算——多了慢且贵）：

| 区块 | 内容 | 预算 | 超了怎么办 |
|------|------|------|------------|
| ① System 规则 | 客服身份、语气、能做不能做、安全边界 | ~800 tok | 固定，不动 |
| ② 用户画像 | 姓名、等级、套餐、已知偏好（结构化） | ~400 tok | 只放字段，不放原文 |
| ③ 召回记忆 | 向量检索 top-k 条历史事实 | ~1500 tok | 调小 k / 提高阈值 |
| ④ 会话历史 | 最近 N 轮原文 + 更早的摘要 | ~6000 tok | 超阈值触发摘要压缩 |
| ⑤ 当前 query | 用户这句话 | ~500 tok | 一般不动 |

原则一脉相承：**重要信息靠头部**（system / 画像 / 记忆放前面，避免 lost in the middle），**会话历史靠尾部**（最近的最相关）。

---

## 3. 核心代码：存储与召回层

先搭一个最小可跑的向量记忆库。这里用 OpenAI embedding + 内存字典模拟向量库（生产替换成 Qdrant / pgvector 即可，接口一致）。

```python
# ✅ 长期记忆：向量存储 + 召回（内存模拟版，可直接跑）
import numpy as np
import openai

client = openai.OpenAI()  # 读 OPENAI_API_KEY

def embed(text: str) -> np.ndarray:
    resp = client.embeddings.create(model="text-embedding-3-small", input=text)
    return np.array(resp.data[0].embedding, dtype=np.float32)

class MemoryStore:
    """每条记忆 = (user_id, 事实文本, 向量)。生产换成真向量库。"""
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def write(self, user_id: str, fact: str) -> None:
        # 简单去重：完全相同的事实不重复写
        if any(r["user_id"] == user_id and r["fact"] == fact for r in self._rows):
            return
        self._rows.append({"user_id": user_id, "fact": fact, "vec": embed(fact)})

    def recall(self, user_id: str, query: str, k: int = 4, thresh: float = 0.25) -> list[str]:
        qv = embed(query)
        scored = []
        for r in self._rows:
            if r["user_id"] != user_id:
                continue
            sim = float(qv @ r["vec"] / (np.linalg.norm(qv) * np.linalg.norm(r["vec"])))
            if sim >= thresh:          # 阈值过滤，避免召回噪声
                scored.append((sim, r["fact"]))
        scored.sort(reverse=True)
        return [f for _, f in scored[:k]]

memory = MemoryStore()
```

`thresh` 是召回质量的命门：太低召回一堆无关事实污染上下文，太高漏召回。客服场景建议 0.2~0.3 起步，按 badcase 调。

---

## 4. 核心代码：事实抽取（写入管线）

不要把整段对话原样塞进长期记忆——那等于没压缩。每轮结束后用一次小模型调用，把对话**抽成原子事实**再写入。

```python
# ✅ 每轮结束：从对话片段抽取可长期保留的事实
import json

EXTRACT_PROMPT = """从下面这段客服对话中，抽取关于「用户」的、值得长期记住的稳定事实。
只抽：偏好、个人情况、历史问题、明确诉求。不抽：寒暄、一次性的临时信息。
输出 JSON 数组，每条是一句中文短句；没有可抽的就输出 []。

对话：
{turn}"""

def extract_facts(user_msg: str, assistant_msg: str) -> list[str]:
    turn = f"用户：{user_msg}\n客服：{assistant_msg}"
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": EXTRACT_PROMPT.format(turn=turn)}],
        response_format={"type": "json_object"} if False else None,
        temperature=0,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        # 容错：模型可能裹 ```json
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        facts = json.loads(raw)
        return facts if isinstance(facts, list) else []
    except json.JSONDecodeError:
        return []
```

用 `gpt-4o-mini` 这类便宜模型抽取，`temperature=0` 求稳定。抽取出来的事实如「用户用的是企业版套餐」「上次反馈导出 Excel 乱码」会进 `memory.write`，下次相关问题来时被召回。

---

## 5. 核心代码：会话历史压缩

单次会话聊久了，④ 区会撑爆预算。策略：**保留最近 N 轮原文，更早的滚动摘要成一段**（buffer + summary 混合）。

```python
# ✅ 会话历史超阈值就摘要压缩
def maybe_compress(history: list[dict], keep_recent: int = 6,
                   token_budget: int = 6000) -> tuple[str, list[dict]]:
    """返回 (历史摘要, 保留的最近原文)。"""
    approx_tokens = sum(len(m["content"]) for m in history) // 2  # 中文粗估
    if approx_tokens <= token_budget or len(history) <= keep_recent:
        return "", history

    old, recent = history[:-keep_recent], history[-keep_recent:]
    transcript = "\n".join(f'{m["role"]}: {m["content"]}' for m in old)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user",
                   "content": f"用 5 句以内中文摘要这段客服对话，保留未解决的问题和关键结论：\n{transcript}"}],
        temperature=0,
    )
    return resp.choices[0].message.content.strip(), recent
```

注意：被摘要掉的轮次里，重要事实应该已经在第 4 步进了长期记忆——所以摘要丢一点细节不致命，长期记忆是兜底。

---

## 6. 核心代码：组装窗口 + 主循环

把五块按预算和顺序拼起来，跑完整轮。

```python
# ✅ 组装上下文 + 一轮完整对话
PROFILES = {"u_42": {"name": "刘工", "level": "VIP", "plan": "企业版"}}

SYSTEM = """你是「云盘 Pro」的客服助手。语气专业、简洁、友好。
- 只回答与产品相关的问题；涉及退款 / 账户安全的，引导走人工工单。
- 充分利用「已知用户信息」和「相关历史记忆」做个性化回应，但不要复述它们。"""

def build_context(user_id: str, query: str, history: list[dict]) -> list[dict]:
    profile = PROFILES.get(user_id, {})
    facts = memory.recall(user_id, query)                 # ③ 召回
    summary, recent = maybe_compress(history)             # ④ 压缩

    # 重要信息靠头部：画像 + 记忆放进 system
    sys = SYSTEM
    if profile:
        sys += f"\n\n## 已知用户信息\n{json.dumps(profile, ensure_ascii=False)}"
    if facts:
        sys += "\n\n## 相关历史记忆\n" + "\n".join(f"- {f}" for f in facts)
    if summary:
        sys += f"\n\n## 更早对话摘要\n{summary}"

    return [{"role": "system", "content": sys}, *recent,
            {"role": "user", "content": query}]           # ⑤ 当前 query

def chat(user_id: str, query: str, history: list[dict]) -> str:
    messages = build_context(user_id, query, history)
    resp = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.3)
    reply = resp.choices[0].message.content

    # 更新短期历史 + 异步沉淀长期记忆
    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": reply})
    for fact in extract_facts(query, reply):              # 写入管线
        memory.write(user_id, fact)
    return reply

# --- 跨会话演示 ---
h1: list[dict] = []
chat("u_42", "我的企业版导出 Excel 总是中文乱码", h1)   # 抽取并写入「导出乱码」事实
# ... 几天后，全新会话，h2 是空的 ...
h2: list[dict] = []
print(chat("u_42", "上次那个问题修好了吗？", h2))         # 召回到「导出乱码」，答得上来
```

第二个会话 `h2` 是空的短期历史，但因为长期记忆里有「导出 Excel 乱码」这条事实、且与「上次那个问题」语义相关被召回，Agent 才接得住——这就是分层记忆的价值。

---

## 7. 优化点

| 优化 | 做法 |
|------|------|
| 召回更准 | 用 query 改写（把「上次那个问题」改写成更可检索的句子）再 embed |
| 写入不重复 | 抽取后做语义去重 / 合并（「VIP 用户」别写两遍），或定期 dedup |
| 记忆会过期 | 给事实加时间戳，召回时降权旧事实，矛盾事实以新覆旧 |
| 抽取不阻塞 | `extract_facts` 放后台任务队列，不卡用户响应 |
| 省 token | 画像 / system 用 prompt caching，固定前缀复用缓存 |
| 防幻觉 | system 明确「记忆仅供参考，不确定就反问」，别让模型硬编历史 |

---

## 8. 常见坑

| 坑 | 后果 | 解法 |
|----|------|------|
| 把整段对话写进长期记忆 | 库膨胀、召回全是噪声 | 只写抽取后的原子事实 |
| 召回全量倒进窗口 | token 爆 + 稀释关键信息 | top-k + 阈值过滤 |
| 摘要时把未解决问题丢了 | Agent「失忆」用户的诉求 | 摘要 prompt 显式要求保留未决项 |
| 记忆放窗口中间 | lost in the middle，模型忽略 | 记忆 / 画像放 system 头部 |
| 不区分会话级 / 用户级 | 张三的事实召回给李四 | 召回严格按 `user_id` 隔离 |
| 抽取用大模型且同步 | 每轮慢一倍、贵一倍 | 小模型 + 异步 |

---

## 9. 下一步

- 📖 短期 vs 长期记忆的分层原理 → [04-memory/01-short-vs-long.md](../04-memory/01-short-vs-long.md)
- 📖 长期记忆的存取与召回细节 → [04-memory/03-storage-recall.md](../04-memory/03-storage-recall.md)
- 📖 会话历史的摘要压缩策略 → [05-compaction/01-summary-compaction.md](../05-compaction/02-summarization.md)
- 📖 上下文窗口的分块组装 → [02-anatomy/01-context-blocks.md](../02-anatomy/06-structure.md)
- 📖 下一个实战：长文档问答 → [02-long-doc-qa.md](./02-long-doc-qa.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OpenAI Embeddings 文档：https://platform.openai.com/docs/guides/embeddings
- Letta / MemGPT 分层记忆：https://github.com/letta-ai/letta
