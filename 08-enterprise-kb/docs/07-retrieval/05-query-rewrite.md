# EKB 36：Query 改写——在源头消除口语错配

> **一句话**：前面的增强都在「召回后」做文章，query 改写在**召回前**动手——把员工的口语问题，改写/扩展成更接近文档表述的查询。「钱能要回来吗」→「退款流程 退款政策 申请退款」。这是从源头解决错配，对口语化、模糊问题效果显著。

---

## 1. 改写解决什么

```
原问题：「新人第一天要干啥」
文档表述：「入职流程」「报到指引」
→ 字面、语义都隔得远，向量和 BM25 都可能漏

改写后：「入职流程 新员工报到 第一天 入职指引」
→ 向量和 BM25 都更容易命中入职文档
```

改写把「用户怎么说」翻译成「文档怎么写」，在检索发生**之前**就缩小了错配。

---

## 2. 三种改写策略

| 策略 | 做什么 | 适合 |
|------|--------|------|
| 同义扩展 | 加同义词/术语 | 口语 vs 术语 |
| 查询分解 | 复杂问题拆成多个子查询 | 「报销和年假分别怎么弄」 |
| 假设性文档（HyDE） | 让模型先编一个「理想答案」，用它去检索 | 问题太短、信息少 |

企业知识库最实用的是**同义扩展**和**分解**。HyDE 更花哨，但多一次模型调用、且可能引入幻觉词，按需用。

---

## 3. 用 LLM 做同义扩展

```python
# retrieve/rewrite.py
from pydantic import BaseModel
from pydantic_ai import Agent

class Rewrite(BaseModel):
    queries: list[str]    # 改写出的多个查询变体

rewriter = Agent(
    "openai:gpt-4o-mini",        # 改写用便宜快的小模型即可
    output_type=Rewrite,
    system_prompt=(
        "你是企业知识库的查询改写器。把用户的口语问题，改写成 2-3 个"
        "更接近正式文档用词的检索查询，补充可能的同义词/术语。"
        "只输出查询，不要回答问题。"
    ),
)

async def rewrite_query(question: str) -> list[str]:
    r = await rewriter.run(question)
    return [question] + r.output.queries    # 原问题也保留
```

要点：**原问题也保留**——改写可能跑偏，留着原问题做保底。用便宜小模型（gpt-4o-mini）就够，改写不需要强模型。

---

## 4. 多查询怎么和检索结合

改写出多个查询后，**每个都检索一遍，结果合并**（又是 RRF 的用武之地）：

```python
from retrieve.hybrid import hybrid_search
from retrieve.rerank import rerank
from retrieve.rewrite import rewrite_query

async def retrieve_v2(question: str, k: int = 4) -> list[dict]:
    queries = await rewrite_query(question)      # 原问题 + 改写
    all_hits = []
    for q in queries:
        all_hits.append(hybrid_search(q, recall_k=20, final_k=20))
    merged = rrf_fuse_many(all_hits)             # 多路 RRF 融合去重
    return rerank(question, merged, top_k=k)     # 注意：rerank 用原问题
```

关键细节：**rerank 时用原问题**，不用改写后的——因为最终要判断的是「片段和用户真实意图的相关性」，原问题最能代表意图。

---

## 5. 改写的代价与开关

改写不是白来的：

- **多一次模型调用**：增加延迟（用小模型缓解）
- **多次检索**：每个查询变体都检索，增加 DB 负载
- **可能跑偏**：改写引入无关词，反而降召回（保留原问题做保底）

所以改写最好**可开关、可降级**：延迟敏感或简单问题（已经是术语）可以跳过改写。可以加个判断「问题是否口语化」来决定要不要改写，省掉不必要的调用。

---

## 6. 评估改写效果（尤其口语类）

加改写后跑评估，**重点看口语类用例**（直球类本来就不靠改写）：

```
                      recall@5    口语类 recall    延迟
混合+rerank            0.81        0.68            ~300ms
混合+rerank+改写        0.86        0.84            ~600ms   ← 口语类大涨，但慢一倍
```

典型现象：**口语类 recall 显著提升**，整体 recall 也涨，但延迟翻倍。是否值得，看你的场景对延迟的容忍度——这是个明确的权衡，由数据说话。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 改写后丢掉原问题 | 改偏了就全错 | 原问题保留做保底 |
| 用强模型做改写 | 慢、贵、没必要 | 小模型够用 |
| rerank 用改写后的 query | 偏离真实意图 | rerank 用原问题 |
| 改写不可关 | 简单问题也付延迟 | 可开关/按需改写 |
| 只看整体 recall | 看不出改写对口语的价值 | 分口语类评估 |

---

## 下一步

四项增强都加完了，把整个提升过程用数据复盘一遍：

→ [06-quantify-gains](./06-quantify-gains.md)
