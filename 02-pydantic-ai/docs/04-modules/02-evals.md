# Pydantic AI 04-02：Pydantic Evals 评测框架

> **一句话**：Pydantic Evals 让你像写单元测试一样系统地评测 Agent 的回答质量——一边是 `Case`（输入 + 期望输出），一边是 `Evaluator`（打分函数），中间用 `Dataset` 一锅端跑完，最后输出可读 + 可对比的报告。

---

## 1. 为什么需要"评测"而不是"单元测试"

写传统软件你写单元测试：

```python
assert add(1, 2) == 3
```

但 LLM 输出是**非确定的、模糊的、连续的**：

- "Paris" 和 "巴黎" 和 "It's Paris." 你能 `==` 比吗？
- 同一个 prompt 跑两次结果不完全一样
- "回答得体" 这种维度根本不是 boolean

所以 GenAI 应用的"测试"实际上有两层：

| 层 | 目标 | 工具 |
|---|------|------|
| **单元测试** | 工具函数本身的正确性、deps 注入路径、消息构造 | `pytest` + `TestModel` |
| **评测（Eval）** | Agent 在一组真实/拟真输入上的"质量分布" | **Pydantic Evals** |

Eval 不是为了 pass/fail，而是为了**回归监控 + 改 prompt / 换模型时知道是变好还是变差**。

---

## 2. 三个核心抽象

```
┌──────────┐ many   ┌────────────┐ many   ┌────────────┐
│   Case   │ ─────► │  Dataset   │ ─────► │ Evaluator  │
└──────────┘        └────────────┘        └────────────┘
   ↑                       │                     ↑
   每个 Case 是一行         运行 task            一个 Case 可被
   "input + expected"      返回 report          多个 Evaluator 打分
```

| 抽象 | 长啥样 | 干啥的 |
|------|-------|-------|
| `Case` | `Case(name, inputs, expected_output, metadata)` | 一条测试用例 |
| `Dataset` | `Dataset(cases=[...], evaluators=[...])` | Case + 全局 Evaluator 集合 |
| `Evaluator` | 一个返回 `bool / float / dict` 的函数（或 dataclass） | 打分逻辑 |
| `EvaluatorContext` | 带 `inputs / output / expected_output / metadata / duration` | 喂给 evaluator 的上下文 |
| `EvaluationReport` | `dataset.evaluate_sync(...)` 的返回值 | 可 `.print()` / 序列化 / 上传 |

导入路径：

```python
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import (
    Evaluator, EvaluatorContext,
    Equals, EqualsExpected, Contains, IsInstance,
    LLMJudge, MaxDuration,
)
```

---

## 3. 三十秒最小例子

```python
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected

dataset = Dataset(
    name="capital_quiz",
    cases=[
        Case(inputs="France", expected_output="Paris"),
        Case(inputs="Germany", expected_output="Berlin"),
        Case(inputs="China", expected_output="Beijing"),
    ],
    evaluators=[EqualsExpected()],
)

def task(country: str) -> str:
    # 假设这里是你的 Agent / chain / 任意可调用
    return {"France": "Paris", "Germany": "Berlin"}.get(country, "?")

report = dataset.evaluate_sync(task)
report.print(include_input=True, include_output=True)
```

输出（节选）：

```
                         Evaluation report: capital_quiz
┃ Case      ┃ Inputs   ┃ Output ┃ Scores              ┃ Duration ┃
│ Case 1    │ France   │ Paris  │ EqualsExpected: 1.0 │   0.0 ms │
│ Case 2    │ Germany  │ Berlin │ EqualsExpected: 1.0 │   0.0 ms │
│ Case 3    │ China    │ ?      │ EqualsExpected: 0.0 │   0.0 ms │
```

---

## 4. 内置 Evaluator 一览

| Evaluator | 类型 | 用途 |
|-----------|------|------|
| `Equals(value=X)` | bool | 输出严格等于某固定值 |
| `EqualsExpected()` | bool | 输出 == `case.expected_output` |
| `Contains(value="x", case_sensitive=False)` | bool | 输出包含某子串 |
| `IsInstance(type_name="MyModel")` | bool | 输出是某个类的实例（按名字匹配） |
| `MaxDuration(seconds=2.0)` | bool | 运行时长 ≤ N 秒 |
| `HasMatchingSpan(query=...)` | bool | OTel span 里出现某调用（配合 Logfire） |
| `LLMJudge(rubric=..., model=...)` | float/dict | 让另一个 LLM 当裁判打分 |
| `Python(expression="output > 0.5")` | bool | 内联表达式（脚本化场景） |

### 4.1 EqualsExpected 与 Equals 的区别

```python
EqualsExpected()              # 拿 case.expected_output 对比
Equals(value="Paris")         # 拿固定值对比（所有 case 同一目标）
```

前者用得多，后者适合"输出必须是固定枚举值"这类场景。

### 4.2 LLMJudge：用 LLM 给 LLM 打分

```python
from pydantic_evals.evaluators import LLMJudge

judge = LLMJudge(
    rubric=(
        "回答必须：1) 中文；2) 包含具体数字；3) 不超过 100 字。"
        "全部满足 → 1.0；部分满足 → 0.5；都不满足 → 0.0。"
    ),
    model="openai:gpt-4o-mini",   # 当裁判的模型
    include_input=True,           # 把 inputs 也喂给裁判
)
```

LLMJudge **不在乎 expected_output**，只看 rubric。它的优点是能评测"风格 / 礼貌 / 完整度"等模糊维度；缺点是**裁判本身有 5-15% 的噪声**，重要决策不要只信它。

---

## 5. 自定义 Evaluator

最常见的两种写法：

### 5.1 dataclass + `evaluate()`

```python
from dataclasses import dataclass
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

@dataclass
class StartsWithUppercase(Evaluator[str, str]):
    def evaluate(self, ctx: EvaluatorContext[str, str]) -> bool:
        return ctx.output[:1].isupper()
```

注册：

```python
dataset = Dataset(
    cases=[Case(inputs="hello", expected_output="HELLO")],
    evaluators=[StartsWithUppercase()],
)
```

### 5.2 返回多分项

`evaluate()` 也能返回一个 dict，每个 key 是一项打分，自动展开：

```python
@dataclass
class FormatChecks(Evaluator[str, str]):
    def evaluate(self, ctx: EvaluatorContext[str, str]) -> dict[str, bool]:
        return {
            "has_period": ctx.output.endswith("."),
            "is_chinese": any("一" <= c <= "鿿" for c in ctx.output),
            "length_ok": len(ctx.output) <= 100,
        }
```

报告里会看到三列：`has_period / is_chinese / length_ok`。

### 5.3 异步 evaluator

```python
class AsyncCheck(Evaluator[str, str]):
    async def evaluate(self, ctx: EvaluatorContext[str, str]) -> float:
        async with httpx.AsyncClient() as c:
            r = await c.post("https://my-judge/score", json={"text": ctx.output})
            return r.json()["score"]
```

并发执行时性能更好。

---

## 6. 评测一个真实 Agent

```python
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import LLMJudge, MaxDuration

classifier = Agent(
    "openai:gpt-4o-mini",
    system_prompt="把用户输入分类成 question / complaint / praise 三类之一，只回答类名。",
)

async def task(text: str) -> str:
    r = await classifier.run(text)
    return r.output.strip().lower()

dataset = Dataset(
    name="intent_classifier",
    cases=[
        Case(inputs="退款怎么这么慢！",      expected_output="complaint"),
        Case(inputs="你们家客服小姐姐真好。", expected_output="praise"),
        Case(inputs="请问周末发货吗？",      expected_output="question"),
    ],
    evaluators=[
        EqualsExpected(),
        MaxDuration(seconds=3),
        LLMJudge(rubric="回答必须是 question/complaint/praise 之一"),
    ],
)

report = dataset.evaluate_sync(task)
report.print()
```

观察点：

1. `evaluators` 是 **dataset 级**的，对每个 case 都跑一遍
2. 你也可以在单个 `Case(evaluators=[...])` 上加 case 级 evaluator
3. `report.averages()` 给你每个 evaluator 的平均分

---

## 7. Dataset 持久化（YAML / JSON）

数据集通常和代码分离，方便产品 / QA 同学维护：

```python
dataset.to_file("evals/intent.yaml")

# 之后
loaded = Dataset[str, str].from_file("evals/intent.yaml")
loaded.evaluate_sync(task)
```

YAML 长这样（可以直接编辑）：

```yaml
name: intent_classifier
cases:
  - name: refund_complaint
    inputs: 退款怎么这么慢！
    expected_output: complaint
  - name: praise_cs
    inputs: 你们家客服小姐姐真好。
    expected_output: praise
evaluators:
  - EqualsExpected
```

---

## 8. 与 Logfire 集成

```python
import logfire
logfire.configure()
logfire.instrument_pydantic_ai()

# 跑 evaluate 时 span 自动汇总到 Logfire
report = dataset.evaluate_sync(task)
```

在 Logfire dashboard 你能看到：

- 每个 case 的 prompt / output 详情
- 工具调用链路（如果 Agent 用了工具）
- 每个 evaluator 的打分
- 平均分趋势（用 Logfire SQL 自己拼 dashboard）

这一点对"prompt 改了一版，整体准确率上升 3%"的判断特别有用。

---

## 9. 评测的四个维度

一个真实场景的 Agent，你通常会同时关心：

| 维度 | 怎么测 | 推荐 Evaluator |
|------|-------|---------------|
| **Accuracy（准不准）** | 与 expected_output 对比 | `EqualsExpected` / `Contains` / 自定义 |
| **Format（格式对不对）** | 字段齐全、JSON 合法、长度合理 | `IsInstance` / 自定义 |
| **Latency（够不够快）** | 单次运行耗时 | `MaxDuration` |
| **Quality（够不够好）** | 主观维度，比如礼貌、完整 | `LLMJudge` |

四个维度结合起来才有意义。只看 accuracy 容易被"会答但回答方式很烂"的模型骗过去。

---

## 10. CI 集成

把 eval 当 PR gate（高于阈值才能合）：

```python
# scripts/run_eval.py
report = dataset.evaluate_sync(task)
avg = report.averages()
print(report.print())
if avg["EqualsExpected"].value < 0.85:
    raise SystemExit("accuracy regression!")
```

CI 里跑：

```yaml
- run: python scripts/run_eval.py
```

为了避免每次 PR 都烧 API 钱：

- 把模型固定到便宜版本（`gpt-4o-mini` / `haiku`）
- LLMJudge 也用便宜模型
- 数据集 < 50 条就够 smoke test
- 大数据集放 nightly 跑

---

## 11. 实战：评测一个分类 Agent

完整代码见 [`demos/modules/02_evals.py`](../../demos/modules/02_evals.py)。它会：

1. 定义一个 8 条 case 的客服意图数据集
2. 注册 `EqualsExpected` + `MaxDuration` + 自定义 `IsLowercase` + `LLMJudge`
3. 用 Pydantic AI Agent（或没 API Key 时用 `TestModel`）跑
4. 打印 report，统计平均分

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `evaluate_sync` 报 `expected_output` 是 None | 用了 `EqualsExpected` 但 case 没写 expected | 改用 `Equals(value=...)` 或补 expected |
| LLMJudge 每次跑结果都不一样 | 裁判模型本身随机 | 跑多次取平均，或换 `temperature=0` 的裁判 |
| LLMJudge 打分严苛 / 宽松离谱 | rubric 太模糊 | 在 rubric 里给"满分 / 半分 / 零分"的具体例子 |
| 自定义 evaluator 没生效 | 忘了实例化 | `evaluators=[MyEval()]`，不是 `[MyEval]` |
| 数据集太小，结论不可信 | < 10 条 case | 至少 30 条做 smoke，nightly 跑 100+ |
| `evaluate_sync` 慢得离谱 | 串行调 LLM | 改用 `await dataset.evaluate(task)` 异步，自动并发 |
| 每次 CI 烧钱 | 数据集太大 + 用了 GPT-4 | 小数据集 + mini 模型，nightly 才跑全量 |
| 想看每个 case 详情 | `.print()` 默认裁掉长字符串 | `report.print(include_input=True, include_output=True)` |
| 单元测试还是 eval？ | 在测"代码逻辑"还是"模型质量" | 前者 `pytest+TestModel`，后者 `pydantic-evals` |

---

## 13. 何时不要用 Pydantic Evals

| 场景 | 替代方案 |
|------|---------|
| 测一个不调 LLM 的工具函数 | 直接 `pytest` |
| 想跟踪线上真实流量分布 | Logfire + 自己跑 SQL 聚合 |
| 想做 RLHF 标注 / 人工评估 | LangSmith / Argilla 之类的标注平台 |
| 想自动找 prompt 最优解 | DSPy / promptfoo（pydantic-evals 不做自动优化） |

---

## 14. 本章 demo

完整可运行代码：[`demos/modules/02_evals.py`](../../demos/modules/02_evals.py)

下一篇：[03-graph.md](03-graph.md) —— 用 `pydantic-graph` 把多步骤工作流写成类型安全的状态机。
