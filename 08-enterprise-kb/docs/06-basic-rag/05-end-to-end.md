# EKB 31：端到端跑通——第一个能用的 MVP

> **一句话**：把检索 + 生成 + 引用 + 兜底串成一个函数 `answer_question()`，再跑一遍评估集拿到 baseline 数字。这是项目的第一个里程碑——一个**能问能答、带引用、答不出会承认**的可运行系统。后面所有增强都从这个 baseline 出发。

---

## 1. 把零件串成一条线

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

这条线就是第 02 章架构图里 Query 流的 MVP 版——`role` 参数先占位（MVP 不真正过滤，第 08 章接上权限）。

---

## 2. 跑起来试一下

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

预期输出：前两个带正确引用、`found=true`；第三个 `found=false`、无引用、走兜底话术。**看到这个，MVP 就活了。**

---

## 3. 立刻测 baseline

MVP 跑通的下一秒，就跑评估集，记下起点数字：

```bash
python -m eval.run_eval
```

```
=== EKB Eval Report ===  (MVP baseline)
用例数: 20
  recall@3: 0.58
  recall@5: 0.66
  引用准确率: 0.70
  兜底正确率: 0.90
```

**把这组数字记下来**——它是后面每个增强的对照基准。MVP 的分数通常不漂亮（单路向量检索召回有限、兜底偶尔失守），这很正常。重点是：**从现在起，每个改动都能量化是涨是跌。**

---

## 4. 从 baseline 读出「下一步该做什么」

baseline 不只是个分数，它**指明优化方向**：

| 观察到 | 说明 | 下一步 |
|--------|------|--------|
| recall 低（0.66） | 该召回的没召回 | 加 BM25 + 混合检索（07 章） |
| 口语问题大量漏召回 | 向量对口语不敏感 | 加 query 改写（07 章） |
| recall 够但答案选错片段 | 排序不行 | 加 rerank（07 章） |
| 兜底偶尔失守（0.90） | 模型还是会编 | 强化三道防线（已学） |

数据驱动，而不是「感觉该加 rerank 了」。这就是评估先行的回报——它把「优化」从猜测变成有依据的决策。

---

## 5. MVP 的代码结构盘点

到这里，项目骨架已经成型：

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

打✅的都能跑、能测。接下来就是在这个能跑的骨架上，一项项加增强、每项都用评估验证。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| MVP 跑通就急着加功能 | 没记 baseline，后续无对照 | 先跑评估记基准 |
| baseline 分数低就慌 | 其实很正常 | MVP 本就朴素，看后续提升 |
| 不看 baseline 直接猜优化 | 可能优化错方向 | 让数据指明下一步 |
| log_query 漏掉 | 反馈无法回流 | pipeline 里就记日志 |
| role 参数忘了占位 | 第 08 章接权限要改签名 | MVP 就留好 role 参数 |

---

## 下一步

MVP 有了 baseline，开始第一项重头增强——检索质量。先理解为什么检索是命门：

→ [07-retrieval/01-retrieval-is-key](../07-retrieval/01-retrieval-is-key.md)
