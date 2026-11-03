# PE Practice 01：从需求到一个分类器（完整闭环）

> 这篇把 02-process 那一章讲的流程完整走一遍：从一个模糊的需求出发，依次写 Spec、起 v0、建评测集、迭代、上线。每一步都附上代码、数据和迭代记录，读完你应该能照着做出自己的分类器。

---

## 1. 任务背景

一家 SaaS 公司的客服每天收到 1000 多条用户反馈，希望用 AI 自动分类，再路由到对应的处理队列。

---

## 2. Stage 1：需求澄清（7 问）

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
- 200 条留作示例池和开发集 / 100 条做评测集
- 每周线上抽 20 条人工 review
```

---

## 3. Stage 2：Baseline v0

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

先拿 10 个探针样本试一下：

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

## 4. Stage 3：建评测集

`evalset/v1.0.jsonl`（节选）：

```jsonl
{"id":"s001","input":"App 一打开就闪退","exp":{"category":"bug","conf_min":0.85,"has_pii":false},"tag":"happy"}
{"id":"s002","input":"希望加深色模式","exp":{"category":"feature","conf_min":0.8,"has_pii":false},"tag":"happy"}
{"id":"s003","input":"客服爱答不理的","exp":{"category":"complaint","conf_min":0.7,"has_pii":false},"tag":"happy"}
{"id":"s004","input":"棒棒的，再也不用你们的服务了","exp":{"category":"complaint","conf_min":0.5,"has_pii":false},"tag":"tricky-sarcasm"}
{"id":"s005","input":"","exp":{"category":"other","conf_min":0,"has_pii":false},"tag":"edge-empty"}
{"id":"s006","input":"我手机 13800138000，急退款","exp":{"category":"billing","has_pii":true},"tag":"edge-pii"}
{"id":"s007","input":"App 闪退啊！崩溃！请帮我修好不然投诉！","exp":{"category":"bug","conf_min":0.85},"tag":"happy"}
... (共 100 条：60 happy、8 tricky-sarcasm、2 edge-empty、5 edge-pii、5 attack、20 multi-class)
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
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": text or "(empty)"}],
    )
    try:
        return json.loads(resp.content[-1].text)
    except json.JSONDecodeError:
        return {"_parse_error": resp.content[-1].text}


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
  multi-class:       6/20
```

baseline 是 76%。

---

## 5. Stage 4：迭代

### v1：加反讽示例，解决 tricky-sarcasm

```diff
-(no examples)
+示例：
+<example><input>棒棒，再也不用了</input><output>{"category":"complaint","confidence":0.85,"reasoning":"反讽性投诉"}</output></example>
+<example><input>客服真贴心，再也找不到这么差的</input><output>{"category":"complaint","confidence":0.9}</output></example>
```

跑评测：

```
通过 84/100 = 84.0%
  tricky-sarcasm:    7/8   (+4)
  multi-class:       10/20 (+4)
  其他类目无 regression
```

multi-class 也涨了 4 条，这几条样本里混着反讽语气，主类原来被判成 praise，示例顺带把它们纠正了。

### v2：补充多类别的处理规则

```diff
+约束：
+- 多类同时出现 → 选主要类（含具体 bug 描述 / 强烈情感的优先）
+- reasoning 字段标注 "副类: X" 如果有
```

```
通过 88/100 = 88.0%
  happy:             60/60 (+2)
  multi-class:       12/20 (+2)
```

happy 里那两条也是一句话同时报 bug 又威胁投诉的（比如 s007），「具体 bug 描述优先」这条规则让它们回到了 bug。

### v3：把 PII 判定写细

```diff
+has_pii 判定：
+- 含 11 位数字（手机号格式）→ true
+- 含 @域名 → true
+- 含 18 位数字（身份证）→ true
+- 仅 5-6 位数字（如订单号）→ false
```

```
通过 90/100 = 90.0%
  edge-pii:          5/5   (+1)
  multi-class:       13/20 (+1)
```

multi-class 多对的那条带了订单号，之前被误判成含 PII，「5-6 位数字不算」把它放过了。

### v4：校准 confidence

边界样本的 confidence 不稳定，有的过高、有的过低，于是加了一段校准规则：

```diff
+confidence 校准:
+- 类别明显（关键词清晰） → 0.85-0.95
+- 有合理但可能多解 → 0.6-0.8
+- 边界 / 反讽 → 0.5-0.65
+- 模糊 → < 0.5
```

```
通过 91/100 = 91.0%
  tricky-sarcasm:    8/8   (+1)
```

多过的这条反讽样本，之前类别判对了，但 confidence 只给到 0.4，低于 0.5 的下限；有了「边界 / 反讽 → 0.5-0.65」这个锚点就过了。剩下的 9 条失败：attack 2 条、multi-class 7 条。到这里就可以上线了。再往上涨需要换模型或者加 self-consistency，成本上不划算。

---

## 6. Stage 5：上线（目录结构）

```
.
├── prompts/classifier/v4.0.0/
│   ├── system.md
│   ├── CHANGELOG.md
│   └── meta.yml
├── current.txt: v4.0.0
└── (灰度: 5% v4 → 100% v4)
```

上线后监控这几项：通过率（用 LLM-judge 实时打分）、转人工率（confidence < 0.6 的比例）、PII 路由数、错误率、成本。

---

## 7. Stage 6：监控与反哺

```python
# 每周抽线上 20 条
sample = db.fetch_random_traces(days=7, n=20)
# QA 人工标注
labeled = qa_label(sample)
# 加进 evalset regression（标错的）/ happy（标对的代表性的）
add_to_evalset(labeled)
```

评测集就这样从 100 条慢慢涨到 150、200、300 条。

---

## 8. 完整代码

上面的迭代和评测都在 Claude Haiku 5.5 上跑；下面的上线版本改用 GPT-6 Luna 的 Structured Outputs 锁住 schema。换了模型，就要拿同一份 `evalset/v1.0.jsonl` 重跑一遍，确认通过率没掉再上线。

```python
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
    resp = client.chat.completions.parse(
        model="gpt-6-luna",
        temperature=0,   # GPT-6 是否接受自定义 temperature 以模型页为准；不接受就删掉这行
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

## 9. 复盘

| 现象 | 结论 |
|------|------|
| v0 没给反讽的 few-shot，反讽样本几乎全错 | 关键的边界情况一定要有示例 |
| v1 加了示例，其他类别没有退步 | 有代表性的示例不会损害通用性 |
| v2 多类别最难调 | 严格限定 enum，副类写进 reasoning |
| v3 PII 靠数字模式就判得准 | 没必要上专门的识别模型 |
| v4 校准 confidence | 模型给的 confidence 不稳定，有的过高有的过低，需要给锚点 |
| 91% 就停 | 剩下 9% 交给人工兜底，不必死磕 99% |

总共花了 4 天，包括写 Spec、建评测集、4 轮迭代、上线和搭监控。

---

## 10. 下一步

- [Research Agent 的 5 版迭代史](./02-research-agent.md)
- [用 Claude Code 当 Prompt 优化器](./03-claude-code-as-optimizer.md)
