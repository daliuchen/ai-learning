# EKB 31：端到端跑通第一个 MVP

> 这篇把检索、生成、引用、兜底串成一个函数 `answer_question()`，再跑一遍评估集，拿到 baseline。到这一步，项目有了第一个能运行的系统：能问能答，答案带引用，答不出会承认。后面所有增强都以这个 baseline 为起点。

---

## 1. 把各部件串成一条线

```python
# generate/pipeline.py
from retrieve.vector import retrieve_relevant
from generate.answer import generate, enforce_grounding, build_citations
from db import log_query

async def answer_question(question: str, role: str = "all") -> dict:
    # 1. 检索（带相关度过滤）
    chunks = retrieve_relevant(question, k=5)
    # 2. 生成结构化答案
    answer = await generate(question, chunks)
    # 3. 后处理：校验引用 + 兜底降级
    answer = enforce_grounding(answer, chunks)
    # 4. 组装引用卡片
    citations = build_citations(answer, chunks)
    # 5. 记日志（反馈回流用）
    log_query(role, question, [c["id"] for c in chunks], answer)
    return {
        "text": answer.text,
        "citations": citations,
        "found": answer.found,
    }
```

这就是第 02 章架构图里 Query 流的 MVP 版本。`role` 参数先占个位，MVP 阶段并不真正按角色过滤，第 08 章再接上权限。

---

## 2. 跑起来试试

```python
import asyncio

async def demo():
    for q in ["差旅报销单次上限多少？",
              "出差花的钱多久能报销？",
              "公司明年的涨薪计划是什么？"]:   # 最后一个应兜底
        r = await answer_question(q)
        print(f"\nQ: {q}")
        print(f"A: {r['text']}")
        print(f"found={r['found']}  引用={[c['title'] for c in r['citations']]}")

asyncio.run(demo())
```

预期结果：前两个问题带着正确的引用，`found=true`；第三个 `found=false`，没有引用，返回兜底话术。看到这样的输出，MVP 就算跑通了。

---

## 3. 马上测 baseline

MVP 一跑通，就跑评估集，把起点数字记下来：

```bash
python -m eval.run_eval
```

下面这份报告是示意数据，用来说明 baseline 大概长什么样：

```
=== EKB Eval Report ===  (MVP baseline)
用例数: 20
  recall@3: 0.58
  recall@5: 0.66
  引用准确率: 0.70
  兜底正确率: 0.90
```

这组数字是后面每项增强的对照基准。MVP 的分数一般不会好看，单路向量检索召回有限，兜底也偶尔失守，这很正常。要紧的是从这一刻起，每个改动是涨是跌都能量化。

---

## 4. 从 baseline 看下一步做什么

baseline 不只是一个分数，它还告诉你该往哪儿优化：

| 观察到 | 说明 | 下一步 |
|--------|------|--------|
| recall 低（0.66） | 该召回的没召回 | 加 BM25 + 混合检索（07 章） |
| 口语问题大量漏召回 | 向量对口语不敏感 | 加 query 改写（07 章） |
| recall 够但答案选错片段 | 排序不行 | 加 rerank（07 章） |
| 兜底偶尔失守（0.90） | 模型还是会编 | 强化三道防线（已讲过） |

该不该加 rerank，由数据决定，不靠感觉。

---

## 5. 盘点一下代码结构

到这里，项目骨架已经成形：

```
ekb/
├── ingest/      ✅ parse, chunk, embed, load（05章）
├── retrieve/    ✅ vector（07章会加 hybrid, rerank）
├── generate/    ✅ embedder, answer, pipeline
├── eval/        ✅ testset, run_eval（04章）
├── permission/  ⬜ 待建（08章）
├── api/         ⬜ 待建（09章）
└── db.py        ✅
```

打勾的模块都能跑、能测。接下来就在这个骨架上一项项加增强，每加一项都用评估验证。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| MVP 跑通就急着加功能 | 没记 baseline，后面没有对照 | 先跑评估记下基准 |
| baseline 分数低就慌 | 其实很正常 | MVP 本来就朴素，看后续提升 |
| 不看 baseline 直接猜优化方向 | 可能优化错方向 | 让数据指出下一步 |
| 漏了 log_query | 反馈无法回流 | pipeline 里就记日志 |
| 没给 role 参数占位 | 第 08 章接权限时要改函数签名 | MVP 就留好 role 参数 |

---

## 下一步

MVP 有了 baseline，接下来做第一项重头增强：检索质量。先说清楚为什么检索最要紧：[为什么 RAG 的上限由检索决定](../07-retrieval/01-retrieval-is-key.md)
