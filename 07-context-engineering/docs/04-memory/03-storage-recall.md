# CE 04-03：记忆的存储与召回

> **一句话**：长期记忆不是一坨日志，是一套「写入 → 存储 → 召回」的管线。写入要解决「抽什么事实、何时抽」；存储要选对介质（向量库做语义召回、KV 做结构化精确取、文档做大段原文）；召回要解决「什么时候查、查什么、怎么拼回窗口」。三个环节里召回质量直接决定个性化体验。

---

## 1. 三个环节，缺一不可

长期记忆系统就三步，但每步都有坑：

```
  会话进行中 / 结束
        │
        ▼  ① 写入：从对话里抽取「值得长期记的事实」
  ┌───────────┐
  │  抽取器     │  "用户说对花生过敏" → fact: {type: allergy, value: peanut}
  └───────────┘
        │
        ▼  ② 存储：选介质，落盘
  ┌───────────────────────────────┐
  │ 向量库(语义) │ KV(结构化) │ 文档库 │
  └───────────────────────────────┘
        │
        ▼  ③ 召回：新会话相关时检索回来，拼进窗口
  ┌───────────┐
  │  检索器     │  query="点餐推荐" → 召回 allergy=peanut
  └───────────┘
```

下面逐环节拆。

---

## 2. 存储介质：按「怎么查」来选

不同记忆有不同的查询方式，别一股脑全塞向量库：

| 介质 | 适合存 | 查询方式 | 例子 |
|------|--------|----------|------|
| 向量库（Qdrant/Pinecone/pgvector） | 模糊、语义、"和当前话题相关的过往" | 向量相似度 | "用户以前讨论过类似问题吗" |
| KV / 关系库（Redis/Postgres） | 结构化、精确、字段明确的事实 | 按 key / SQL 精确查 | user.diet=vegan、user.tz=UTC+8 |
| 文档 / 对象存储 | 大段原文、会话归档 | 按 ID 取整段 | 上次会话的完整摘要 |

经验法则：**「这个用户是不是 X」→ KV；「和现在聊的内容相关的过往」→ 向量库**。生产里常常两者并用——结构化画像走 KV，开放式经历走向量库。

---

## 3. 写入时机：每轮抽，还是会话末总结

两种范式，各有取舍：

| 写入时机 | 做法 | 优点 | 缺点 |
|----------|------|------|------|
| 每轮抽取（hot path） | 每轮对话后判断是否有新事实，有就抽出来存 | 实时、不漏 | 每轮多一次 LLM 调用，慢、贵 |
| 会话结束总结（background） | 会话结束后异步跑一遍，提炼要点入库 | 不拖慢对话、可批处理 | 有延迟，会话中途新会话拿不到 |
| 混合 | 关键事实（过敏、偏好）热路径抽；其余结束后批量 | 兼顾 | 实现复杂 |

主流 Agent 框架（如 Letta、各家 memory 功能）倾向**异步 / 后台写入**——别让记忆抽取拖慢用户感知的响应。但「过敏」「禁忌」这类强约束事实，值得热路径立即落盘。

---

## 4. 记忆的 schema 设计

别把记忆存成自由文本一锅烩。给它结构，召回和更新才好做：

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class MemoryItem(BaseModel):
    user_id: str                              # 多用户隔离的关键，见 05-personalization
    kind: Literal["fact", "preference", "event", "task"]
    key: str                                  # 如 "diet", "timezone"，便于精确查/去重
    value: str                                # 如 "vegan", "对花生过敏"
    text: str                                 # 用于向量化的自然语言描述
    confidence: float = 1.0                   # 抽取置信度
    created_at: datetime
    updated_at: datetime
    last_accessed: datetime | None = None     # 给遗忘策略用，见 06-forgetting
    source: str                               # 来自哪次会话/哪轮，可追溯
```

几个字段是后续章节的伏笔：`key` 用来去重和冲突更新（[06-forgetting.md](./06-forgetting.md)），`last_accessed` 用来做访问频率衰减，`user_id` 用来做多用户隔离（[05-personalization.md](./05-personalization.md)）。

---

## 5. 抽取并存储用户事实（代码）

用模型从一轮对话里抽结构化事实，结合向量库存储。这里用 OpenAI 结构化输出 + 一个简化的向量索引：

```python
import json
from openai import OpenAI
from datetime import datetime, timezone

client = OpenAI()

EXTRACT_PROMPT = """从下面这轮对话里抽取「值得长期记住」的关于用户的事实。
只抽稳定的偏好/约束/身份/长期目标，不要抽一次性的临时问题。
没有可抽的就返回空数组。输出 JSON：{"facts": [{"kind","key","value","text"}]}
"""

def extract_facts(user_msg: str, assistant_msg: str) -> list[dict]:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": f"用户：{user_msg}\n助手：{assistant_msg}"},
        ],
    )
    return json.loads(resp.choices[0].message.content).get("facts", [])


def embed(text: str) -> list[float]:
    return client.embeddings.create(
        model="text-embedding-3-small", input=text
    ).data[0].embedding


# 向量库这里用 qdrant 示意
from qdrant_client import QdrantClient
from qdrant_client.models import PointId, PointStruct
import uuid

qdrant = QdrantClient(":memory:")  # 生产换成真实地址

def store_facts(user_id: str, facts: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    points = []
    for f in facts:
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=embed(f["text"]),
            payload={**f, "user_id": user_id, "created_at": now, "updated_at": now},
        ))
    if points:
        qdrant.upsert(collection_name="memories", points=points)


# 一轮对话后
facts = extract_facts("我不吃辣，对花生也过敏", "好的，我记下了")
# facts == [{"kind":"preference","key":"spice","value":"no_spicy","text":"用户不吃辣"},
#           {"kind":"fact","key":"allergy","value":"peanut","text":"用户对花生过敏"}]
store_facts("u_42", facts)
```

---

## 6. 召回时机与方式

**召回时机**：新一轮用户输入到来时，先拿它当 query 去长期记忆里检索；不必每轮都检索全库——可以只在「话题切换」或「输入里出现可能涉及画像的信号」时触发，省 token 省延迟。

**召回方式**：向量库走语义相似度 top-k，KV 走精确查。召回回来后筛掉低相关的，拼进窗口靠前位置（重要信息别埋中间）。

```python
def recall(user_id: str, query: str, top_k: int = 3) -> list[str]:
    hits = qdrant.search(
        collection_name="memories",
        query_vector=embed(query),
        query_filter={"must": [{"key": "user_id", "match": {"value": user_id}}]},  # 用户隔离！
        limit=top_k,
    )
    return [h.payload["text"] for h in hits]


def build_context(user_id: str, user_input: str, history: list[dict]) -> list[dict]:
    memories = recall(user_id, user_input)
    mem_block = "已知用户事实：\n" + "\n".join(f"- {m}" for m in memories) if memories else ""
    return [
        {"role": "system", "content": "你是一个贴心助手。" + (f"\n{mem_block}" if mem_block else "")},
        *history[-8:],
        {"role": "user", "content": user_input},
    ]


# 下一次会话，用户问点餐
ctx = build_context("u_42", "推荐个午餐", history=[])
# 召回到 "对花生过敏"、"不吃辣" → 模型据此避开花生和辣菜
```

`query_filter` 里的 `user_id` 过滤是**安全红线**——漏了它就会把别人的记忆召回给当前用户（详见 [05-personalization.md](./05-personalization.md)）。

---

## 7. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 把整轮对话原样存进向量库 | 召回一堆噪声，token 爆 | 先抽成结构化事实再存 |
| 每轮都热路径抽取 | 响应慢、成本高 | 异步/后台写入，关键事实才热路径 |
| 召回不做相关性过滤，全塞 | 窗口被无关记忆稀释 | top-k + 相似度阈值 |
| 没存 key，事实重复堆积 | 同一偏好存了十遍 | 用 key 去重/覆盖 |
| 召回时漏 user_id 过滤 | 记忆跨用户泄漏 | 检索强制带 user_id filter |

---

## 8. 下一步

- 📖 把存储召回组织成分层架构（MemGPT/Letta） → [04-agent-memory-arch.md](./04-agent-memory-arch.md)
- 📖 用召回的事实做个性化与多用户隔离 → [05-personalization.md](./05-personalization.md)
- 📖 记忆怎么过期、更新、衰减 → [06-forgetting.md](./06-forgetting.md)
- 📖 向量检索的底层机制，回看检索章 → [03-retrieval/02-vector-search.md](../03-retrieval/01-rag-as-context.md)

## 参考资料

- OpenAI Embeddings 文档：https://platform.openai.com/docs/guides/embeddings
- Qdrant 文档：https://qdrant.tech/documentation/
- Mem0（开源记忆层）：https://github.com/mem0ai/mem0
