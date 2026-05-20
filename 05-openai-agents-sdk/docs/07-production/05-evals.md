# 评测：把 04-prompt-engineering 的 evalset 套到 Agent 上

> **一句话**：Prompt Engineering 手册的"评测先于 prompt"思想也适用于 Agent——给 Agent 建 evalset、跑回归、按 tag 分组看通过率、按维度评分。

---

## 1. Agent 评测跟 Prompt 评测的区别

跨手册引用：[04-prompt-engineering/02-process/03-build-evalset.md](../../../04-prompt-engineering/docs/02-process/03-build-evalset.md) 讲怎么建 evalset。

Agent 评测有几个额外维度：

| 维度 | Prompt 评测 | Agent 评测 |
|------|-------------|------------|
| 最终输出对错 | ✅ 主指标 | ✅ 主指标 |
| Tool 调用正确性 | N/A | ✅ 关键 |
| Handoff 路由对不对 | N/A | ✅ 关键 |
| 中间步骤合理 | N/A | ✅（trajectory eval） |
| Token / 时长 | 关心 | 关心 |
| 多轮一致性 | 可能 | ✅（session-level eval） |

---

## 2. 建 Agent evalset 的格式

```jsonl
{"id": "tri_001", "input": "我要退款", "expected_agent": "Billing", "expected_tool_calls": ["issue_refund"]}
{"id": "tri_002", "input": "登录报错 500", "expected_agent": "Support", "expected_tool_calls": ["search_kb"]}
{"id": "tri_003", "input": "你们有 enterprise 版本吗", "expected_agent": "Sales"}
{"id": "tri_004", "input": "今天天气真好", "expected_agent": "Triage", "expected_response_contains": "我可以帮"}
```

每条至少：

- `input`：测试输入
- `expected_*`：期望（最终 agent / tool / 输出关键词等）

100-200 条覆盖：

- happy path
- edge case
- 故意打错主题
- 边界（混合主题、模糊请求）

---

## 3. 评测器

```python
# eval_runner.py
import asyncio
import json
from pathlib import Path
from agents import Agent, Runner


def load_evalset(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


async def run_one(triage: Agent, case: dict) -> dict:
    result = await Runner.run(triage, case["input"], max_turns=5)

    actual_agent = result.last_agent.name
    actual_tools = [
        item.raw_item.name
        for item in result.new_items
        if item.type == "tool_call_item"
    ]
    actual_output = str(result.final_output)

    # 检查
    checks = {}
    if "expected_agent" in case:
        checks["agent"] = actual_agent == case["expected_agent"]
    if "expected_tool_calls" in case:
        checks["tools"] = set(case["expected_tool_calls"]).issubset(set(actual_tools))
    if "expected_response_contains" in case:
        checks["contains"] = case["expected_response_contains"] in actual_output

    passed = all(checks.values())
    return {
        "id": case["id"],
        "passed": passed,
        "checks": checks,
        "actual_agent": actual_agent,
        "actual_tools": actual_tools,
        "actual_output": actual_output[:200],
        "tokens": result.usage.total_tokens,
    }


async def main():
    evalset = load_evalset("evalset/v1.jsonl")
    # 假设 triage 是你的主 agent
    from my_agents import triage

    results = await asyncio.gather(*[run_one(triage, c) for c in evalset])

    passed = sum(1 for r in results if r["passed"])
    print(f"\nPass: {passed}/{len(results)}")

    # 按维度分
    failures = [r for r in results if not r["passed"]]
    for f in failures:
        print(f"\n❌ {f['id']}: {f['checks']}")
        print(f"   actual_agent: {f['actual_agent']}")
        print(f"   actual_tools: {f['actual_tools']}")

    # 写结果
    Path("results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
```

---

## 4. LLM-as-judge 评测

```python
from agents import Agent, Runner
from pydantic import BaseModel


class Judgment(BaseModel):
    correct: bool
    reason: str


judge = Agent(
    name="Judge",
    instructions="""你是测试评判员。
判断 actual_response 是否合理回答了 user_question。
不严格逐字匹配，只看意图正确。""",
    output_type=Judgment,
    model="gpt-4o-mini",
)


async def judge_one(case: dict, actual: str) -> Judgment:
    prompt = f"User question: {case['input']}\n\nActual response: {actual}"
    result = await Runner.run(judge, prompt)
    return result.final_output
```

---

## 5. Trajectory Eval：看中间步骤

不只看最终输出，看 Agent 怎么走到的：

```python
def eval_trajectory(result, expected_path):
    """expected_path: ['triage', 'tool:search_kb', 'handoff:support', 'tool:create_ticket']"""
    actual = []
    for item in result.new_items:
        if item.type == "tool_call_item":
            actual.append(f"tool:{item.raw_item.name}")
        elif item.type == "handoff_call_item":
            actual.append(f"handoff:{item.target_agent.name}")
    return actual == expected_path
```

适合调试复杂多 Agent 流程。

---

## 6. 按 tag 分组

```json
{"id": "tri_001", "input": "退款", "tag": "billing.refund", "expected_agent": "Billing"}
{"id": "tri_010", "input": "登录错误", "tag": "support.login", "expected_agent": "Support"}
```

跑完后：

```python
from collections import defaultdict


tag_stats = defaultdict(lambda: {"total": 0, "passed": 0})
for r, case in zip(results, evalset):
    tag = case.get("tag", "other")
    tag_stats[tag]["total"] += 1
    if r["passed"]:
        tag_stats[tag]["passed"] += 1

for tag, s in tag_stats.items():
    print(f"{tag}: {s['passed']}/{s['total']} ({s['passed']/s['total']:.0%})")
```

---

## 7. 跑回归

每次改 agent 都跑一遍 evalset：

```bash
python eval_runner.py > results/v_$(date +%s).json
```

git commit results 文件 → 看 diff（哪些新 pass / 哪些回归）。

---

## 8. CI 里跑

```yaml
# .github/workflows/eval.yml
name: Agent Evals
on: [pull_request]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r requirements.txt
      - run: python eval_runner.py
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      - run: |
          PASS_RATE=$(jq '.pass_rate' results.json)
          if (( $(echo "$PASS_RATE < 0.85" | bc -l) )); then
            echo "Pass rate $PASS_RATE below threshold"
            exit 1
          fi
```

---

## 9. 用观测平台做评测

LangSmith / Langfuse 内置评测：

```python
import langsmith


for case in evalset:
    result = await Runner.run(agent, case["input"])
    langsmith.create_run(
        name="eval",
        inputs={"input": case["input"]},
        outputs={"output": result.final_output},
        evaluations=[
            {"key": "agent_correct", "score": 1.0 if result.last_agent.name == case["expected_agent"] else 0.0},
            {"key": "tokens", "value": result.usage.total_tokens},
        ],
    )
```

在 LangSmith 上看分布、对比版本、按维度切。

---

## 10. 完整 demo

```python
# demos/production/05_evals.py
import asyncio
import json
from agents import Agent, Runner, function_tool


@function_tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: $99"


billing = Agent(name="Billing", instructions="账单", tools=[lookup_invoice])
support = Agent(name="Support", instructions="技术")
triage = Agent(name="Triage", instructions="分流", handoffs=[billing, support])


evalset = [
    {"id": "1", "input": "退款 SO-1", "expected_agent": "Billing", "expected_tools": ["lookup_invoice"]},
    {"id": "2", "input": "登录 500 错误", "expected_agent": "Support"},
    {"id": "3", "input": "你好", "expected_agent": "Triage"},
]


async def eval_one(case):
    result = await Runner.run(triage, case["input"], max_turns=5)
    actual_agent = result.last_agent.name
    actual_tools = [
        i.raw_item.name for i in result.new_items if i.type == "tool_call_item"
    ]

    checks = {"agent": actual_agent == case["expected_agent"]}
    if "expected_tools" in case:
        checks["tools"] = set(case["expected_tools"]).issubset(set(actual_tools))

    return {
        "id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "actual_agent": actual_agent,
        "actual_tools": actual_tools,
    }


async def main():
    results = await asyncio.gather(*[eval_one(c) for c in evalset])
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        print(f"{icon} {r['id']}: {r['checks']} → {r['actual_agent']}")

    passed = sum(1 for r in results if r["passed"])
    print(f"\nPass: {passed}/{len(results)}")


asyncio.run(main())
```

---

## 11. 何时升级 evalset

- 上线后用户报新 case → 加进 evalset
- 改 prompt 时发现某类没覆盖 → 加几条
- 季度 review → 删过时的

evalset 也是要演进的 codebase。

---

## 12. 跟 PE 手册的方法论一致

完全沿用 [04-prompt-engineering/02-process](../../../04-prompt-engineering/docs/02-process/) 的思路：

1. **Spec**：先定 Agent 要做啥
2. **v0**：先跑通一版
3. **evalset**：建 100-200 case
4. **迭代**：每轮改一处 + 跑 evalset
5. **何时停**：达到 target 通过率
6. **上线**：灰度 + 监控

---

## 13. 下一步

- 📖 PE 评测方法论 → [04-prompt-engineering/02-process](../../../04-prompt-engineering/docs/02-process/)
- 📖 用 Claude Code 当评测优化器 → [04-prompt-engineering/08-practice/03-claude-code-as-optimizer.md](../../../04-prompt-engineering/docs/08-practice/03-claude-code-as-optimizer.md)
- 📖 实战项目 → [08-practice/01-customer-triage.md](../08-practice/01-customer-triage.md)
