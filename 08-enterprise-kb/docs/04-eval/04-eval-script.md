# EKB 20：评估脚本——一键跑出记分牌

> **一句话**：把测试集和指标缝成一个可一键运行的脚本 `run_eval.py`。它读 `testset.jsonl`、对每条用例跑检索 + 生成、算出所有指标、打印记分牌。这个脚本是整个项目的「回归测试」——以后每次改动后跑一遍就知道有没有变好。

---

## 1. 脚本的整体结构

```python
# eval/run_eval.py
import json
from statistics import mean

from retrieve.hybrid import retrieve     # 检索（各章逐步增强）
from generate.answer import generate     # 生成

def load_testset(path="eval/testset.jsonl"):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

async def run_eval():
    cases = load_testset()
    rows = []
    for c in cases:
        chunks = await retrieve(c["question"], role=c["asker_role"], k=5)
        retrieved_docs = [ch["doc_id"] for ch in chunks]
        ans = await generate(c["question"], chunks)
        rows.append(score_one(c, retrieved_docs, ans))
    report(rows)
```

核心就是：**对每条用例，跑一遍真实的检索 + 生成，记录结果，最后聚合。** 因为它调的是真实的 `retrieve` 和 `generate`，所以检索一升级、脚本立刻能测出来。

---

## 2. 单条打分

把第 19 篇的指标函数拼进来：

```python
def score_one(case, retrieved_docs, ans):
    expected = case["expected_doc_ids"]
    topk = retrieved_docs
    # 检索层
    recall5 = recall_at_k(topk, expected, 5)
    recall3 = recall_at_k(topk, expected, 3)
    # 生成层
    cite_ok = citation_correct(ans.cited_doc_ids, expected)
    fallback_ok = fallback_correct(ans.found, expected)
    return {
        "id": case["id"],
        "recall5": recall5, "recall3": recall3,
        "cite_ok": cite_ok, "fallback_ok": fallback_ok,
        "expected": expected, "retrieved": topk,
        "found": ans.found,
    }

def recall_at_k(retrieved, expected, k):
    if not expected:
        return None
    return len(set(retrieved[:k]) & set(expected)) / len(expected)

def citation_correct(cited, expected):
    if not expected:
        return len(cited) == 0
    return len(set(cited) & set(expected)) > 0

def fallback_correct(found, expected):
    return found == (len(expected) > 0)
```

---

## 3. 聚合成记分牌

```python
def report(rows):
    # recall 只对有期望文档的用例平均（答不出用例 recall 为 None）
    recalls5 = [r["recall5"] for r in rows if r["recall5"] is not None]
    recalls3 = [r["recall3"] for r in rows if r["recall3"] is not None]
    cite = mean(r["cite_ok"] for r in rows)
    fallback_rows = [r for r in rows if not r["expected"]]
    fallback = mean(r["fallback_ok"] for r in fallback_rows) if fallback_rows else 1.0

    print("=== EKB Eval Report ===")
    print(f"用例数: {len(rows)}")
    print(f"  recall@3: {mean(recalls3):.2f}")
    print(f"  recall@5: {mean(recalls5):.2f}")
    print(f"  引用准确率: {cite:.2f}")
    print(f"  兜底正确率: {fallback:.2f}")
    # 打印失败用例，方便排查
    print("\n--- 失败用例 ---")
    for r in rows:
        if (r["recall5"] is not None and r["recall5"] < 1) or not r["cite_ok"]:
            print(f"  #{r['id']} expected={r['expected']} got={r['retrieved'][:5]} found={r['found']}")
```

关键细节：**打印失败用例**。光看平均分不够，你需要知道**具体哪条挂了**，才能去查是检索没召回还是答歪了。

---

## 4. 怎么用它驱动迭代

固定的工作循环（贯穿 05-10 章）：

```bash
# 1. 跑 baseline，记下数字
python -m eval.run_eval     # recall@5=0.62 ...

# 2. 改一处（比如加 rerank）

# 3. 再跑，对比
python -m eval.run_eval     # recall@5=0.78 ✅ 涨了，留下
```

**纪律**：一次只改一处再跑（呼应 [04 手册迭代闭环](/docs/04-prompt-engineering/02-process/04-iteration-loop)）。一次改三处，涨跌都归因不了。

---

## 5. 把它升级成「门禁」

到生产阶段，这个脚本可以变成上线 gate——指标不达标就拒绝发布：

```python
def gate(rows):
    fallback = mean(r["fallback_ok"] for r in rows if not r["expected"])
    leak = sum(1 for r in rows if leaked(r))   # 越权召回数
    assert fallback >= 0.95, f"兜底正确率不达标: {fallback}"
    assert leak == 0, f"存在越权召回: {leak}"   # 红线
    print("✅ gate passed")
```

把它接进 CI，每次改动自动跑。详见 [10-production/06-launch-checklist](../10-production/06-launch-checklist.md)。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 评估脚本调的不是真实检索 | 测的是假的 | 直接调生产用的 retrieve/generate |
| 只打印平均分 | 不知道哪条挂了 | 打印失败用例明细 |
| recall 把答不出用例也算进去 | 拉低分数失真 | 期望为空的跳过 recall |
| 改多处再跑 | 归因不了 | 一次改一处 |
| 评估只跑一次 | 退化无法察觉 | 每次改动都跑（回归测试） |

---

## 下一步

标尺立好了。现在开始往库里灌数据——从 demo 文档准备开始：

→ [05-ingest/01-demo-data](../05-ingest/01-demo-data.md)
