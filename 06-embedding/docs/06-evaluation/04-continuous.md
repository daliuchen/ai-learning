# 持续评测 + 回归测试

> 一次性的调优做完，过几周效果可能就悄悄退化了。办法是把 evalset 接进 CI，每个 PR 都跑一遍回归；上线后再用灰度和线上抽样补上真实数据的反馈。这篇讲这套闭环怎么搭。

---

## 1. 三层评测体系

```
1. 离线 evalset（CI 跑）：每 PR 必跑
2. 线上 trace 抽样：每天 / 每周抽几百条人工或 LLM judge
3. 用户反馈：thumbs up/down → 加进 evalset
```

闭环：

```
线上 trace → 发现差 case → 加 evalset → 改 → 跑回归 → 上线 → 看反馈 → ...
```

---

## 2. CI 跑离线 evalset

```yaml
# .github/workflows/rag-eval.yml
name: RAG Eval Regression
on: [pull_request]


jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r requirements.txt
      
      - name: Run retrieval eval
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python scripts/eval_retrieval.py
      
      - name: Check thresholds
        run: |
          python scripts/check_thresholds.py \
            --recall-min 0.85 \
            --mrr-min 0.70
```

`check_thresholds.py`：

```python
import json
import sys


with open("eval_results.json") as f:
    results = json.load(f)


thresholds = {
    "recall@5": 0.85,
    "mrr": 0.70,
}


failed = False
for metric, threshold in thresholds.items():
    actual = results[metric]
    status = "✅" if actual >= threshold else "❌"
    print(f"{status} {metric}: {actual:.3f} (threshold {threshold})")
    if actual < threshold:
        failed = True


sys.exit(1 if failed else 0)
```

指标没过阈值，PR 就不能合并。

---

## 3. 看 diff（每个 PR 改动前后对比）

```python
# scripts/eval_with_diff.py
import json


# 跑当前版本
current_results = run_eval()

# 拿上次基线（从 main 分支拉）
baseline = json.load(open("baseline_results.json"))


# 找：哪些 case 从 pass 变 fail（regression）/ 从 fail 变 pass（improvement）
regressions = []
improvements = []
for case in evalset:
    before = baseline["per_case"][case["id"]]["passed"]
    after = current_results["per_case"][case["id"]]["passed"]
    if before and not after:
        regressions.append(case)
    elif not before and after:
        improvements.append(case)


print(f"\n📈 Improved: {len(improvements)} cases")
for c in improvements[:5]:
    print(f"  - {c['query']}")


print(f"\n📉 Regressed: {len(regressions)} cases")
for c in regressions[:5]:
    print(f"  - {c['query']}")
```

把这个 diff 贴到 PR 评论里，评审的人一眼就能看出改动的影响。

---

## 4. 多版本对比

记录每个 commit 的指标：

```python
# scripts/save_eval_history.py
import json
from datetime import datetime
import git   # pip install GitPython


repo = git.Repo(".")
commit_hash = repo.head.commit.hexsha[:8]


results = run_eval()


history = json.load(open("eval_history.json", "r"))
history.append({
    "commit": commit_hash,
    "date": str(datetime.now()),
    "metrics": results,
})


with open("eval_history.json", "w") as f:
    json.dump(history, f, indent=2)
```

画出趋势：

```python
import matplotlib.pyplot as plt


history = json.load(open("eval_history.json"))
plt.plot([h["date"] for h in history], [h["metrics"]["recall@5"] for h in history])
plt.xlabel("Date")
plt.ylabel("Recall@5")
plt.savefig("eval_trend.png")
```

---

## 5. 线上 trace 持续评测

部署之后每天抽样评测：

```python
# crons/daily_eval.py
import asyncio
import random
from datetime import datetime, timedelta


async def daily_sample_eval():
    # 拿过去 24h 的 trace
    traces = await db.query(
        "SELECT * FROM rag_logs WHERE created_at > %s",
        datetime.now() - timedelta(days=1),
    )
    
    # 抽 100 条
    sample = random.sample(traces, min(100, len(traces)))
    
    # LLM-as-judge
    results = []
    for trace in sample:
        judgment = await judge(
            query=trace["query"],
            answer=trace["answer"],
            contexts=trace["retrieved_contexts"],
        )
        results.append({
            "trace_id": trace["id"],
            "faithfulness": judgment["faithfulness"],
            "answer_relevance": judgment["answer_relevance"],
            "context_relevance": judgment["context_relevance"],
        })
    
    # 平均
    avg_faith = sum(r["faithfulness"] for r in results) / len(results)
    print(f"Daily faithfulness avg: {avg_faith:.3f}")
    
    # 异常告警
    if avg_faith < 0.85:
        await alert("RAG faithfulness 跌破阈值!")


asyncio.run(daily_sample_eval())
```

用 cron 定时跑。

---

## 6. 用户反馈循环

```
用户在 chat 里点 👍 / 👎
  ↓
👎 case 自动入 review queue
  ↓
每天 / 每周人工 review
  ↓
确认是 RAG 问题 → 加进 evalset
  ↓
下次迭代修复
```

```python
@app.post("/feedback")
async def feedback(req):
    """用户点 thumbs down"""
    await db.insert("feedback", {
        "trace_id": req.trace_id,
        "rating": req.rating,   # -1 / 1
        "comment": req.comment,
        "created_at": now(),
    })
    
    if req.rating == -1:
        await review_queue.add(req.trace_id)


# 人工 review 后导入 evalset
def add_to_evalset(trace_id):
    trace = db.get_trace(trace_id)
    
    new_case = {
        "query": trace["query"],
        "relevant_docs": manually_annotated_docs(trace),
        "tags": ["from_user_feedback", "regression_test"],
    }
    
    evalset.append(new_case)
```

---

## 7. 灰度对照

新版本上线时：

```
50% 走 v1 (control)
50% 走 v2 (treatment)


每天对比：
  v1 thumbs up rate: 78%
  v2 thumbs up rate: 84%   ← 显著提升，全量上 v2

或：
  v1: 78%
  v2: 76%   ← 反而降了，回滚
```

几个百分点的差距要看样本量，先做一下显著性检验（比如两比例 z 检验）再决定全量还是回滚。

```python
import hashlib


def route_to_version(user_id):
    """灰度路由"""
    hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
    return "v2" if hash_val % 100 < 50 else "v1"
```

细节见 [PE Production 04：A/B 与可观测](../../../04-prompt-engineering/docs/07-production/04-ab-observability.md)。

---

## 8. evalset 版本化

```
evalset/
  v1.0.jsonl   # 100 条初版
  v1.1.jsonl   # +50 条线上日志
  v1.2.jsonl   # +30 条 edge cases
  ...
```

跑评测时在报告里写明版本：

```
Eval @ v1.2 (180 cases)
  Recall@5: 0.92
  by tag:
    happy:     0.96
    edge:      0.81
    typo:      0.85
    multi_qa:  0.78
```

---

## 9. golden set 跟 noise set 分开

golden set 保持稳定，100-200 条，每次回归都跑；noise set 随时间不断扩大，500 条以上，每月审查一次。golden set 跑得快，可以挂在每个 PR 上；noise set 跑得慢，放到 nightly。

---

## 10. demo：完整流程

```python
import json
from pathlib import Path
from datetime import datetime


class EvalRunner:
    def __init__(self, evalset_path):
        self.evalset = [json.loads(l) for l in open(evalset_path)]
    
    def run(self, retriever_fn, judge_fn):
        """retriever_fn: query -> top_k docs list
           judge_fn: (query, doc) -> faithfulness/relevance score"""
        results = []
        for case in self.evalset:
            retrieved = retriever_fn(case["query"], top_k=5)
            recall = self._recall(retrieved, case["relevant_docs"])
            
            # judge 仅对 happy path（成本控制）
            faith = None
            if "happy" in case.get("tags", []):
                faith = judge_fn(case["query"], retrieved)
            
            results.append({
                "query_id": case["query_id"],
                "recall": recall,
                "faithfulness": faith,
            })
        return results
    
    def _recall(self, retrieved, relevant):
        return len(set(retrieved) & set(relevant)) / len(relevant) if relevant else 1.0


def save_run(version, results):
    """保存到历史"""
    history_path = Path("eval_history.jsonl")
    with history_path.open("a") as f:
        f.write(json.dumps({
            "version": version,
            "date": datetime.now().isoformat(),
            "avg_recall": sum(r["recall"] for r in results) / len(results),
            "results": results,
        }) + "\n")


def compare_with_baseline(current, baseline):
    """对比当前 vs 基线"""
    regressions = []
    improvements = []
    for cur, base in zip(current, baseline):
        if base["recall"] > 0 and cur["recall"] == 0:
            regressions.append(cur)
        elif base["recall"] == 0 and cur["recall"] > 0:
            improvements.append(cur)
    return regressions, improvements
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| evalset 没版本化 | 每次改 evalset 都升一个 vN |
| 阈值定得太严，CI 一直挂 | 起步时放宽，再逐步收紧 |
| 每个 PR 都跑 LLM 判官，太贵 | 离线 recall 进 CI，LLM 判官放 nightly |
| corpus 改了，evalset 没跟着更新 | corpus 有 diff 时跑一遍 evalset，找出受影响的 case |

---

## 12. 06-evaluation 章节小结

完整闭环：

```
1. 建 evalset (100-300 条)
2. 选指标 (Recall@5 + Faithfulness)
3. PR CI 跑回归
4. 上线 + 抽样线上 trace
5. 用户反馈进队列
6. review 后加进 evalset → 跑回归
```

思路和 PE 手册的评测方法一致。

---

## 13. 下一步

- 进入生产化，从索引更新开始：[增量索引：文档新增 / 修改 / 删除](../07-production/01-incremental.md)
- [完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
- PE 手册的评测方法：[Prompt Engineering 手册的流程篇](../../../04-prompt-engineering/docs/02-process/01-lifecycle.md)
