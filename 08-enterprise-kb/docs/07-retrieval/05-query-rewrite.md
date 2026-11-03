# EKB 36：Query 改写，消除口语表达的错配

> 前面几项增强都在召回之后做文章，query 改写在召回之前动手：把员工的口语问题改写、扩展成更接近文档用词的查询，比如把「钱能要回来吗」改成「退款流程 退款政策 申请退款」。它从源头减少错配，对口语化、表述模糊的问题效果很明显。

---

## 1. 改写解决什么问题

```
原问题：「新人第一天要干啥」
文档表述：「入职流程」「报到指引」
→ 字面、语义都隔得远，向量和 BM25 都可能漏

改写后：「入职流程 新员工报到 第一天 入职指引」
→ 向量和 BM25 都更容易命中入职文档
```

改写相当于把用户的说法翻译成文档的写法，在检索开始前就把错配缩小了。

---

## 2. 三种改写策略

| 策略 | 做什么 | 适合 |
|------|--------|------|
| 同义扩展 | 补同义词和术语 | 口语和术语对不上 |
| 查询分解 | 把复杂问题拆成几个子查询 | 「报销和年假分别怎么弄」 |
| 假设性文档（HyDE） | 让模型先写一个理想答案，拿它去检索 | 问题太短、信息太少 |

企业知识库里最实用的是同义扩展和查询分解。HyDE 花样更多，但要多调一次模型，还可能带进模型自己编的词，按需使用。

---

## 3. 用 LLM 做同义扩展

```python
# retrieve/rewrite.py
from pydantic import BaseModel
from pydantic_ai import Agent

class Rewrite(BaseModel):
    queries: list[str]    # 改写出的多个查询变体

rewriter = Agent(
    "anthropic:claude-haiku-5-5",   # 改写用便宜快的小档模型即可（千问可用 qwen-flash）
    output_type=Rewrite,
    instructions=(
        "你是企业知识库的查询改写器。把用户的口语问题，改写成 2-3 个"
        "更接近正式文档用词的检索查询，补充可能的同义词/术语。"
        "只输出查询，不要回答问题。"
    ),
)

async def rewrite_query(question: str) -> list[str]:
    r = await rewriter.run(question)
    return [question] + r.output.queries    # 原问题也保留
```

原问题一定要保留。改写有可能跑偏，留着原问题能兜住。改写用 Haiku、qwen-flash 这一档的小模型就够了，不需要强模型。

---

## 4. 多个查询怎么配合检索

改写出几个查询后，每个都检索一遍，再把结果合并，这里又用得上 RRF：

```python
from retrieve.hybrid import hybrid_search
from retrieve.rerank import rerank
from retrieve.rewrite import rewrite_query

async def retrieve_v2(question: str, k: int = 4) -> list[dict]:
    queries = await rewrite_query(question)      # 原问题 + 改写
    all_hits = []
    for q in queries:
        all_hits.append(hybrid_search(q, recall_k=20, final_k=20))
    merged = rrf_fuse_many(all_hits)             # rrf_fuse 的多路版：每路贡献 1/(k+rank)，按 id 累加去重
    return rerank(question, merged, top_k=k)     # 注意：rerank 用原问题
```

注意 rerank 时用的是原问题，不是改写后的查询。最终要判断的是片段和用户本意是否相关，最能代表本意的就是原问题。

---

## 5. 改写的代价和开关

改写有三笔开销：

- 多一次模型调用，延迟增加，可以用小模型缓解。
- 每个查询变体都要检索一遍，数据库负载变大。
- 改写可能带进无关词，反而拉低召回，所以要保留原问题。

因此改写最好做成可开关、可降级的。延迟敏感的场景，或者问题本身已经是术语的情况，可以直接跳过。也可以先判断问题是否口语化，再决定要不要改写，省掉不必要的调用。

---

## 6. 评估改写效果，重点看口语类

加上改写后跑评估，重点看口语类用例，直截了当的问题本来就不靠改写。下表为示意数据：

```
                      recall@5    口语类 recall    延迟
混合+rerank            0.81        0.68            ~300ms
混合+rerank+改写        0.86        0.84            ~600ms   ← 口语类大涨，但慢一倍
```

一般会看到口语类 recall 明显提升，整体 recall 也跟着涨，但延迟翻倍。值不值，取决于你的场景能容忍多少延迟。这是个很明确的取舍，用数据来定。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 改写后丢掉原问题 | 改偏了就全错 | 保留原问题兜底 |
| 用强模型做改写 | 慢、贵，没必要 | 小模型够用 |
| rerank 用改写后的 query | 偏离用户本意 | rerank 用原问题 |
| 改写不能关 | 简单问题也要多等 | 可开关，按需改写 |
| 只看整体 recall | 看不出改写对口语类的作用 | 按口语类单独评估 |

---

## 下一步

四项增强都加完了，接下来用数据把整个提升过程复盘一遍：

→ [量化提升](./06-quantify-gains.md)
