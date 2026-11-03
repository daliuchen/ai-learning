# 评测：把 Prompt Engineering 手册的评测集用到 Agent 上

> Prompt Engineering 手册里「先有评测再改 prompt」的做法，放到 Agent 上一样适用：给 Agent 建 evalset，每次改动跑回归，按 tag 分组看通过率，按维度打分。区别在于 Agent 除了最终输出，还要检查它调了哪些工具、转给了哪个专家。

---

## 1. Agent 评测跟 Prompt 评测的区别

怎么建 evalset，在 [PE Process 03：建评测集，从 5 条到 500 条](../../../04-prompt-engineering/docs/02-process/03-build-evalset.md) 里讲过。Agent 评测要多看几个维度：

| 维度 | Prompt 评测 | Agent 评测 |
|------|-------------|------------|
| 最终输出对错 | 主指标 | 主指标 |
| Tool 调用正确性 | N/A | 重点 |
| Handoff 路由对不对 | N/A | 重点 |
| 中间步骤合理 | N/A | 要看（trajectory eval） |
| Token / 时长 | 关心 | 关心 |
| 多轮一致性 | 可能 | 要看（session-level eval） |

---

## 2. 建 Agent evalset 的格式

```jsonl
{"id": "tri_001", "input": "我要退款", "expected_agent": "Billing", "expected_tool_calls": ["issue_refund"]}
{"id": "tri_002", "input": "登录报错 500", "expected_agent": "Support", "expected_tool_calls": ["search_kb"]}
{"id": "tri_003", "input": "你们有 enterprise 版本吗", "expected_agent": "Sales"}
{"id": "tri_004", "input": "今天天气真好", "expected_agent": "Triage", "expected_response_contains": "我可以帮"}
```

每条至少要有 `input`（测试输入）和若干 `expected_*`（期望的最终 agent、工具、输出关键词等）。

规模上，100-200 条就能覆盖大部分情况：正常路径、边缘情况、故意跑题的输入，以及混合主题、表述模糊这类边界请求。

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
        if item.type == "tool_call_item"       # 或 isinstance(item, ToolCallItem)
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
        "tokens": result.context_wrapper.usage.total_tokens,
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
    model="gpt-6-luna",
)


async def judge_one(case: dict, actual: str) -> Judgment:
    prompt = f"User question: {case['input']}\n\nActual response: {actual}"
    result = await Runner.run(judge, prompt)
    return result.final_output
```

---

## 5. Trajectory Eval：看中间步骤

除了最终输出，还可以检查 Agent 是沿着什么路径走到这一步的：

```python
def eval_trajectory(result, expected_path):
    """expected_path: ['triage', 'tool:search_kb', 'handoff:support', 'tool:create_ticket']"""
    actual = []
    for item in result.new_items:
        if item.type == "tool_call_item":
            actual.append(f"tool:{item.raw_item.name}")
        elif item.type == "handoff_output_item":              # 转交完成的那条：带 source_agent / target_agent
            actual.append(f"handoff:{item.target_agent.name}")
        # handoff_call_item 的 raw_item.name 是 "transfer_to_<agent>"，没有 target_agent 属性
    return actual == expected_path
```

多 Agent 流程比较复杂时，这种检查对定位问题很有帮助。

---

## 6. 不花钱的确定性回归：`agents.testing` 假模型

0.21 起 SDK 自带一套与 provider 无关的测试工具 `agents.testing`。其中 `ScriptedModel` 按脚本回放模型响应，不发任何请求，但工具管线、handoff、guardrail、重试、session 都是真实运行的。它适合在 CI 里测编排逻辑本身，比如路由规则、工具参数校验、守卫有没有生效；真实模型则留给数量更少、成本更高的 evalset。

```python
import pytest
from agents import Agent, RunConfig, Runner
from agents.testing import ScriptedModel, assistant_message, function_call


@pytest.mark.asyncio
async def test_triage_hands_off_to_billing():
    billing = Agent(name="Billing", model=ScriptedModel([[assistant_message("已为您退款")]]))
    triage = Agent(
        name="Triage",
        model=ScriptedModel([[function_call("transfer_to_billing", "{}", call_id="c1")]]),
        handoffs=[billing],
    )

    result = await Runner.run(triage, "退款", run_config=RunConfig(tracing_disabled=True))

    assert result.last_agent.name == "Billing"
    assert result.final_output == "已为您退款"
    # new_items 依次是 handoff_call_item → handoff_output_item(source=Triage,target=Billing) → message_output_item
```

用法上的几点：每一次预期的模型调用，对应脚本里的一组输出项，`function_call(...)` 要带 `call_id=`；`model.calls` 可以断言 runner 发给模型的输入是什么；`ModelStep.raise_error(...)` 用来注入模型故障，测重试策略；`ModelStep.respond()` 可以按请求动态应答；最后调 `model.assert_complete()` 检查脚本是否被消费完，防止流程悄悄变短了却没人发现。Realtime 有 `ScriptedRealtimeModel`，Voice 有 `ScriptedSTTModel` / `ScriptedTTSModel`。

不过这些替身测不了**模型会不会选对工具**，这个问题只能交给真实模型的评测。

---

## 7. 按 tag 分组

```json
{"id": "tri_001", "input": "退款", "tag": "billing.refund", "expected_agent": "Billing"}
{"id": "tri_010", "input": "登录错误", "tag": "support.login", "expected_agent": "Support"}
```

跑完后按 tag 汇总：

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

## 8. 跑回归

每次改 agent 都跑一遍 evalset：

```bash
python eval_runner.py && cp results.json results/v_$(date +%s).json
```

把结果文件提交进 git，看 diff 就知道哪些新通过了、哪些回归了。

---

## 9. CI 里跑

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
      - run: pip install -r requirements.txt     # openai-agents 0.24 需要 Python >= 3.10
      - run: pytest tests/                        # 先跑零成本的 ScriptedModel 确定性测试
      - run: python eval_runner.py                # 再跑真模型 evalset（可以用便宜模型 / 抽样）
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      - run: |
          PASS_RATE=$(jq '([.[] | select(.passed)] | length) / length' results.json)
          if (( $(echo "$PASS_RATE < 0.85" | bc -l) )); then
            echo "Pass rate $PASS_RATE below threshold"
            exit 1
          fi
```

---

## 10. 用观测平台做评测

LangSmith 和 Langfuse 都内置了评测功能：

```python
# 示意：记录方式以 LangSmith SDK 文档为准（数据集评测官方推荐 langsmith 的 evaluate / aevaluate）
import langsmith


for case in evalset:
    result = await Runner.run(agent, case["input"])
    langsmith.create_run(
        name="eval",
        inputs={"input": case["input"]},
        outputs={"output": result.final_output},
        evaluations=[
            {"key": "agent_correct", "score": 1.0 if result.last_agent.name == case["expected_agent"] else 0.0},
            {"key": "tokens", "value": result.context_wrapper.usage.total_tokens},
        ],
    )
```

结果上传后，可以在 LangSmith 里看分布、对比版本、按维度切分。

---

## 11. 完整 demo

```python
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

## 12. 何时升级 evalset

上线后用户报来新的 case，加进去；改 prompt 时发现某类情况没覆盖，补几条；每个季度回顾一次，删掉过时的。evalset 和代码一样需要维护。

---

## 13. 跟 PE 手册的方法论一致

这里沿用的是 [Prompt Engineering 手册流程篇](../../../04-prompt-engineering/docs/02-process/) 的思路：

1. Spec：先定 Agent 要做什么
2. v0：跑通第一版
3. evalset：建 100-200 条 case
4. 迭代：每轮只改一处，然后跑 evalset
5. 何时停：达到目标通过率
6. 上线：灰度加监控

---

## 14. 下一步

- PE 评测方法论 → [Prompt Engineering 手册流程篇](../../../04-prompt-engineering/docs/02-process/)
- 用 Claude Code 当评测优化器 → [PE Practice 03：用 Claude Code 当 Prompt 优化器](../../../04-prompt-engineering/docs/08-practice/03-claude-code-as-optimizer.md)
- 实战项目 → [实战 1：客服 Triage Agent](../08-practice/01-customer-triage.md)
