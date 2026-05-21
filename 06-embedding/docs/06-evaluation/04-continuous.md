# 持续评测 + 回归测试

> **一句话**：把 evalset 跑成 CI 测试 + 灰度后看真实数据反馈，**每次 PR 都跑回归**——这是从"一次性优化"变成"持续提质"的关键。

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

PR 阈值不过 → 阻断 merge。

---

## 3. 看 diff（每个 PR 之前 vs 之后）

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

PR 评论里贴这个 diff——评审者一眼看到改了啥。

---

## 4. 多版本对比

记录每个 commit 的 metric：

```python
# scripts/save_eval_history.py
import json
import git


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

可视化趋势：

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

部署后每天抽样：

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

定时任务 cron 跑。

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

新版上线时：

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

```python
def route_to_version(user_id):
    """灰度路由"""
    hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
    return "v2" if hash_val % 100 < 50 else "v1"
```

详见 [04-prompt-engineering/07-production/04-ab-observability.md](../../../04-prompt-engineering/docs/07-production/04-ab-observability.md)。

---

## 8. evalset 版本化

```
evalset/
  v1.0.jsonl   # 100 条初版
  v1.1.jsonl   # +50 条线上日志
  v1.2.jsonl   # +30 条 edge cases
  ...
```

跑评测时报告：

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

- **Golden set**：稳定，每次跑回归（100-200 条）
- **Noise set**：随时间扩大，月度审查（500+ 条）

Golden 跑得快，每次 PR 都跑；Noise 跑得慢，nightly。

---

## 10. demo：完整流程

```python
# demos/evaluation/04_continuous.py
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
| evalset 没版本化 | 每次改 evalset 起新 vN |
| 阈值定太严，CI 一直挂 | 起步松点，逐步提 |
| LLM judge 每 PR 跑太贵 | 离线 recall 走 CI，LLM judge 走 nightly |
| 改了 corpus 没更新 evalset | corpus diff 时 evalset 跑一遍发现哪些 case 影响 |

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

跟 PE 手册的方法论一致。

---

## 13. 下一步

- 📖 生产化：怎么部署 → [07-production/01-incremental.md](../07-production/01-incremental.md)
- 📖 完整 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
- 📖 PE 评测方法论 → [04-prompt-engineering/02-process](../../../04-prompt-engineering/docs/02-process/)
