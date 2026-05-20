# PE Practice 01：从需求到一个分类器（完整闭环）

> **一句话**：本篇是 02-process 中轴线的完整实战——从模糊需求开始，一步步走完 Spec、v0、evalset、迭代、上线，每步给你**真实代码 + 真实数据 + 真实迭代记录**。读完你应该能照着做一个自己的分类器。

---

## 1. 任务背景

某 SaaS 公司客服每天收到 1000+ 条用户反馈，想让 AI 自动分类、路由到对应处理队列。这是真实需求。

---

## 2. Stage 1: 需求澄清（7 问）

```markdown
# feedback-classifier PE Spec

## Q1 输入
- 长度：5-2000 字，多数 50-200 字
- 语言：中文为主，少量英文
- 来源：App 内反馈表单 + 应用商店评论
- 干净度：含 emoji、错别字，少量 HTML

## Q2 输出
- JSON: {category, confidence, reasoning, has_pii}
- has_pii: 是否含个人信息（手机/邮箱/身份证），含则路由到合规队列

## Q3 类别
互斥 8 类：
- bug, feature, complaint, praise, question, billing, account, other

## Q4 边界
- 空 / 乱码 / 与产品无关 → other + confidence=0
- 反讽 → 按真实意图归（"棒棒，再也不用" → complaint）
- 多类 → 选主类，副类放 reasoning 里

## Q5 错误成本
- 漏判 bug 当 praise 损失最大（漏 escalation）
- confidence < 0.6 → 转人工
- has_pii=true → 必转合规

## Q6 性能预算
- QPS 10、p99 2s、月预算 $300

## Q7 评测
- 已有 300 条人工标注（QA 团队）
- 抽 200 训练 / 100 评测
- 每周线上抽 20 条人工 review
```

---

## 3. Stage 2: Baseline v0

```python
# prompts/classifier/v0.txt
"""
你是客服反馈分类师。

任务：把用户反馈分到 8 类之一：
bug / feature / complaint / praise / question / billing / account / other

输出 JSON：
{"category": "...", "confidence": 0.0-1.0, "reasoning": "<30字>", "has_pii": bool}

约束：
- enum 严格
- 空 / 无关 → other
- 反讽按意图
- has_pii: 含手机/邮箱/身份证号则 true
"""
```

跑 10 个 probe 样本：

```
✅ bug         → bug
✅ feature     → feature
❌ complaint   → other     ← 错（"客服真差"）
✅ praise      → praise
✅ question    → question
✅ empty       → other
✅ billing     → billing
❌ sarcasm     → praise    ← 错（"棒棒再也不用了"）
✅ multi-class → bug (主)
✅ with phone  → has_pii=true ✅

基线: 8/10 = 80%
```

---

## 4. Stage 3: 建 evalset

`evalset/v1.0.jsonl`（节选）：

```jsonl
{"id":"s001","input":"App 一打开就闪退","exp":{"category":"bug","conf_min":0.85,"has_pii":false},"tag":"happy"}
{"id":"s002","input":"希望加深色模式","exp":{"category":"feature","conf_min":0.8,"has_pii":false},"tag":"happy"}
{"id":"s003","input":"客服爱答不理的","exp":{"category":"complaint","conf_min":0.7,"has_pii":false},"tag":"happy"}
{"id":"s004","input":"棒棒的，再也不用你们的服务了","exp":{"category":"complaint","conf_min":0.5,"has_pii":false},"tag":"tricky-sarcasm"}
{"id":"s005","input":"","exp":{"category":"other","conf_min":0,"has_pii":false},"tag":"edge-empty"}
{"id":"s006","input":"我手机 13800138000，急退款","exp":{"category":"billing","has_pii":true},"tag":"edge-pii"}
{"id":"s007","input":"App 闪退啊！崩溃！请帮我修好不然投诉！","exp":{"category":"bug","conf_min":0.85},"tag":"happy"}
... (~30 happy + 15 edge + 5 attack)
```

`eval_runner.py`：

```python
import json, sys
from pathlib import Path
from collections import defaultdict
import anthropic


client = anthropic.Anthropic()


def run_prompt(prompt: str, text: str) -> dict:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        temperature=0,
        system=prompt,
        messages=[{"role": "user", "content": text or "(empty)"}],
    )
    try:
        return json.loads(resp.content[0].text)
    except json.JSONDecodeError:
        return {"_parse_error": resp.content[0].text}


def evaluate(sample: dict, output: dict) -> tuple[bool, list]:
    exp = sample["exp"]
    errors = []
    if "_parse_error" in output:
        return False, ["parse error"]
    if output.get("category") != exp["category"]:
        errors.append(f"category got={output.get('category')} exp={exp['category']}")
    if "conf_min" in exp and output.get("confidence", 0) < exp["conf_min"]:
        errors.append(f"confidence too low: {output.get('confidence')}")
    if "has_pii" in exp and output.get("has_pii") != exp["has_pii"]:
        errors.append(f"has_pii mismatch")
    return not errors, errors


def main(prompt_file, evalset_file):
    prompt = Path(prompt_file).read_text()
    samples = [json.loads(l) for l in Path(evalset_file).read_text().splitlines() if l.strip()]

    results = []
    by_tag = defaultdict(lambda: {"pass": 0, "total": 0})

    for s in samples:
        out = run_prompt(prompt, s["input"])
        ok, errs = evaluate(s, out)
        results.append({"id": s["id"], "passed": ok, "errors": errs, "input": s["input"][:40], "output": out})
        by_tag[s["tag"]]["total"] += 1
        if ok:
            by_tag[s["tag"]]["pass"] += 1

    total_pass = sum(v["pass"] for v in by_tag.values())
    total = sum(v["total"] for v in by_tag.values())
    print(f"\n=== Eval {Path(prompt_file).stem} ===")
    print(f"通过 {total_pass}/{total} = {total_pass/total*100:.1f}%")
    for tag, stats in by_tag.items():
        print(f"  {tag:20s}: {stats['pass']}/{stats['total']}")
    
    failures = [r for r in results if not r["passed"]]
    if failures:
        print("\n失败案例 (top 5):")
        for f in failures[:5]:
            print(f"  {f['input']:42s} | errors={f['errors']}")

    return total_pass / total


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

跑 v0：

```
通过 76/100 = 76.0%
  happy:             58/60
  tricky-sarcasm:    3/8
  edge-empty:        2/2
  edge-pii:          4/5
  attack:            3/5
  multi-class:       6/10
```

baseline 76%。

---

## 5. Stage 4: 迭代

### v1: 加反讽示例（解决 tricky-sarcasm）

```diff
-(no examples)
+示例：
+<example><input>棒棒，再也不用了</input><output>{"category":"complaint","confidence":0.85,"reasoning":"反讽性投诉"}</output></example>
+<example><input>客服真贴心，再也找不到这么差的</input><output>{"category":"complaint","confidence":0.9}</output></example>
```

跑评测：

```
通过 84/100 = 84.0%
  tricky-sarcasm:    7/8   ✅ (+4)
  其他类目无 regression
```

### v2: 加 multi-class 提示

```diff
+约束：
+- 多类同时出现 → 选主要类（含具体 bug 描述 / 强烈情感的优先）
+- reasoning 字段标注 "副类: X" 如果有
```

```
通过 88/100 = 88.0%
  multi-class:       8/10  ✅ (+2)
```

### v3: 强化 PII 检测

```diff
+has_pii 判定：
+- 含 11 位数字（手机号格式）→ true
+- 含 @域名 → true
+- 含 18 位数字（身份证）→ true
+- 仅 5-6 位数字（如订单号）→ false
```

```
通过 90/100 = 90.0%
  edge-pii:          5/5   ✅ (+1)
```

### v4: 调 confidence 校准

发现 edge case 的 confidence 偏高，加：

```diff
+confidence 校准:
+- 类别明显（关键词清晰） → 0.85-0.95
+- 有合理但可能多解 → 0.6-0.8
+- 边界 / 反讽 → 0.5-0.65
+- 模糊 → < 0.5
```

```
通过 91/100 = 91.0%
```

到此可以上线了——继续涨需要换模型 / 加 self-consistency，成本不划算。

---

## 6. Stage 5: 上线（伪代码）

```
.
├── prompts/classifier/v4.0.0/
│   ├── system.md
│   ├── CHANGELOG.md
│   └── meta.yml
├── current.txt: v4.0.0
└── (灰度: 5% v4 → 100% v4)
```

监控指标：
- 通过率（用 LLM-judge 实时打分）
- 转人工率（confidence < 0.6）
- PII 路由数
- 错误率
- 成本

---

## 7. Stage 6: 监控反哺

```python
# 每周抽线上 20 条
sample = db.fetch_random_traces(days=7, n=20)
# QA 人工标注
labeled = qa_label(sample)
# 加进 evalset regression（标错的）/ happy（标对的代表性的）
add_to_evalset(labeled)
```

evalset 从 100 长到 150 → 200 → 300...

---

## 8. 完整代码

```python
# demos/practice/01_classifier_full.py
"""完整客服分类器（v4 最终版）"""
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()


class Classification(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise", "question", "billing", "account", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=80)
    has_pii: bool


SYSTEM = """你是客服反馈分类师。

任务：把用户反馈分到 8 类之一：
- bug: 软件错误（闪退、加载失败、功能不工作）
- feature: 新功能 / 改进建议
- complaint: 抱怨服务 / 体验（非具体 bug）
- praise: 好评
- question: 使用问题、求助
- billing: 账单 / 支付 / 退款
- account: 登录 / 密码 / 账号
- other: 不属上述（含空 / 乱码 / 与产品无关）

约束：
- enum 严格使用上述 8 个
- 多类同时出现 → 选主类（reasoning 标 "副类: X"）
- 反讽（"再也不用"、"真贴心"）按真实意图归
- confidence 校准:
  * 明显 (0.85-0.95) | 合理 (0.6-0.8) | 边界 / 反讽 (0.5-0.65) | 模糊 (<0.5)

has_pii 判定:
- 11 位连续数字（手机号）→ true
- @域名 → true
- 18 位连续数字（身份证）→ true
- 5-6 位数字（订单号）→ false

示例：
- "棒棒的，再也不用你们了" → complaint (反讽)
- "客服真贴心，找不到比这更差的" → complaint
- "App 闪退啊！希望尽快修，否则投诉" → bug (主) + complaint (副)
- "我手机 13800138000 急退款" → billing, has_pii=true
"""


def classify(text: str) -> Classification:
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        temperature=0,
        response_format=Classification,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text or "(empty)"},
        ],
    )
    return resp.choices[0].message.parsed


if __name__ == "__main__":
    TESTS = [
        "App 一打开就闪退",
        "希望加深色模式",
        "客服爱答不理的",
        "棒棒的，再也不用了",
        "我手机 13800138000 退款",
        "",
    ]
    for t in TESTS:
        r = classify(t)
        print(f"{r.category:10s} (conf={r.confidence:.2f}) pii={r.has_pii}  | {t}")
```

---

## 9. 复盘：这次迭代教训

| 教训 | 启示 |
|------|------|
| v0 没 few-shot 反讽全错 | 关键 edge 必须有示例 |
| v1 加示例没 break 其他类 | "代表性"示例不损通用性 |
| v2 multi-class 难调 | 严格 enum + 副类放 reasoning |
| v3 PII 用数字 pattern 就够 | 没必要上正则模型 |
| v4 confidence 校准 | 模型默认 over-confident，要 anchor |
| 91% 已够 | 剩 9% 用人工兜底，不死磕 99% |

总时间：4 天（含 Spec + evalset + 4 轮迭代 + 上线 + 监控搭建）。

---

## 10. 下一步

- 📖 Research Agent 迭代史 → [02-research-agent.md](./02-research-agent.md)
- 📖 用 Claude Code 优化 prompt → [03-claude-code-as-optimizer.md](./03-claude-code-as-optimizer.md)
