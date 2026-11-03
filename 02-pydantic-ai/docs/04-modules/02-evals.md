# Pydantic AI 04-02：Pydantic Evals 评测框架

> Pydantic Evals 让你用写单元测试的方式评测 Agent 的回答质量。一边是 `Case`（输入加期望输出），一边是 `Evaluator`（打分函数），用 `Dataset` 把它们组织起来一次跑完，最后得到一份能读、能对比的报告。同一批 evaluator 还能挂到线上流量上做抽样评估。

以 `pydantic-evals 2.64.0` 为准（和 pydantic-ai 同一个版本号）。`report.averages()` 现在返回对象，不再是 dict。

---

## 1. 为什么需要"评测"而不是"单元测试"

写传统软件时，你写的是单元测试：

```python
assert add(1, 2) == 3
```

LLM 的输出却是非确定的、模糊的，质量也是连续变化的：

- "Paris"、"巴黎"、"It's Paris." 这三个答案没法用 `==` 比
- 同一个 prompt 跑两次，结果不完全一样
- "回答得体"这种维度根本不是布尔值

所以 GenAI 应用的测试其实分两层：

| 层 | 目标 | 工具 |
|---|------|------|
| 单元测试 | 工具函数本身的正确性、deps 注入路径、消息构造 | `pytest` + `TestModel` |
| 评测（Eval） | Agent 在一组真实或拟真输入上的质量分布 | Pydantic Evals |

Eval 的用处在于回归监控：改 prompt 或换模型之后，你能知道效果是变好了还是变差了。单条 case 的 pass/fail 反而没那么重要。

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

| 抽象 | 长什么样 | 做什么 |
|------|-------|-------|
| `Case` | `Case(name, inputs, expected_output, metadata)` | 一条测试用例 |
| `Dataset` | `Dataset(cases=[...], evaluators=[...])` | Case 加全局 Evaluator 集合 |
| `Evaluator` | 一个返回 `bool / float / dict` 的函数（或 dataclass） | 打分逻辑 |
| `EvaluatorContext` | 带 `inputs / output / expected_output / metadata / duration` | 传给 evaluator 的上下文 |
| `EvaluationReport` | `dataset.evaluate_sync(...)` 的返回值 | 可以 `.print()`、序列化、上传 |

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
| `Equals(value=X)` | bool | 输出严格等于某个固定值 |
| `EqualsExpected()` | bool | 输出 == `case.expected_output` |
| `Contains(value="x", case_sensitive=False)` | bool | 输出包含某个子串（`case_sensitive` 默认 `True`） |
| `IsInstance(type_name="MyModel")` | bool | 输出是某个类的实例（按名字匹配） |
| `MaxDuration(seconds=2.0)` | bool | 运行时长 ≤ N 秒 |
| `HasMatchingSpan(query=...)` | bool | OTel span 里出现某个调用（配合 Logfire） |
| `LLMJudge(rubric=..., model=...)` | float/dict | 让另一个 LLM 当裁判打分 |
| `MaxModelRequests(n)` / `MaxToolCalls(n)` | bool | 一次运行里模型请求 / 工具调用次数的上限（防循环、控成本） |
| `ToolCorrectness` / `ArgumentCorrectness` / `TrajectoryMatch` | bool / float | 评测 agent 的工具调用轨迹：工具选对没有、参数对不对、顺序对不对 |
| `GEval(...)` | float | G-Eval 风格的 LLM 评分（按评分步骤拆开打分的裁判） |

### 4.1 EqualsExpected 与 Equals 的区别

```python
EqualsExpected()              # 拿 case.expected_output 对比
Equals(value="Paris")         # 拿固定值对比（所有 case 同一目标）
```

前者更常用；后者适合"输出必须是某个固定枚举值"的场景。

### 4.2 LLMJudge：用 LLM 给 LLM 打分

```python
from pydantic_evals.evaluators import LLMJudge

judge = LLMJudge(
    rubric=(
        "回答必须：1) 中文；2) 包含具体数字；3) 不超过 100 字。"
        "全部满足 → 1.0；部分满足 → 0.5；都不满足 → 0.0。"
    ),
    model="openai-chat:qwen-flash",   # 当裁判的模型（千问可用）
    include_input=True,           # 把 inputs 也喂给裁判
)
```

LLMJudge 不看 expected_output，只看 rubric。它能评风格、礼貌、完整度这类模糊维度，代价是裁判本身也有噪声，同一条输出多跑几次分数可能不同，重要的决策别只靠它。

---

## 5. 自定义 Evaluator

最常见的写法有下面几种。

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

`evaluate()` 也可以返回 dict，每个 key 是一项分数，报告里会自动展开：

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

报告里会出现三列：`has_period / is_chinese / length_ok`。

### 5.3 异步 evaluator

```python
class AsyncCheck(Evaluator[str, str]):
    async def evaluate(self, ctx: EvaluatorContext[str, str]) -> float:
        async with httpx.AsyncClient() as c:
            r = await c.post("https://my-judge/score", json={"text": ctx.output})
            return r.json()["score"]
```

需要调外部服务打分时，写成异步的在并发执行时快得多。

---

## 6. 评测一个真实 Agent

```python
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected, LLMJudge, MaxDuration

classifier = Agent(
    "openai-chat:qwen-flash",
    instructions="把用户输入分类成 question / complaint / praise 三类之一，只回答类名。",
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

几点说明：

1. `evaluators` 是 dataset 级的，对每个 case 都跑一遍
2. 也可以在单个 `Case(evaluators=[...])` 上加 case 级 evaluator
3. `report.averages()` 返回的是 `ReportCaseAggregate` 对象，不是 dict，要用属性读：`avg.assertions`（所有布尔断言的通过率）、`avg.scores`、`avg.labels`、`avg.metrics`、`avg.task_duration`。每个 case 的明细在 `report.cases`，失败的在 `report.failures`

---

## 7. Dataset 持久化（YAML / JSON）

数据集通常和代码分开存放，方便产品或 QA 同学维护：

```python
dataset.to_file("evals/intent.yaml")

# 之后
loaded = Dataset[str, str].from_file("evals/intent.yaml")
loaded.evaluate_sync(task)
```

YAML 长这样，可以直接手改：

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

在 Logfire 里能看到：

- 每个 case 的 prompt 和 output
- 工具调用链路（如果 Agent 用了工具）
- 每个 evaluator 的打分
- 平均分趋势（需要自己用 Logfire SQL 拼 dashboard）

改了一版 prompt 之后想确认整体准确率是不是真的涨了 3%，这些数据就派上用场了。

---

## 9. 重复运行、并发、报告级评估

```python
report = dataset.evaluate_sync(
    task,
    repeat=3,                 # 每个 case 跑 3 次，看波动（LLM 非确定）
    max_concurrency=4,        # 限制并发，避免打爆限流
    task_timeout=30,          # 单个 case 超时
)
```

- `repeat=N`：同一个 case 跑多次，报告按组统计，适合看同一个 prompt 的结果稳不稳；
- Report evaluator（`Dataset(report_evaluators=[...])`）：分析整份实验结果，而不是逐 case 打分。内置有 `ConfusionMatrixEvaluator`（分类混淆矩阵）、`PrecisionRecallEvaluator` / `ROCAUCEvaluator`（曲线加 AUC）、`KolmogorovSmirnovEvaluator`，也可以自己写，输出 `ScalarResult` / `TableResult`；
- Case 生命周期（`lifecycle=`）：每个 case 运行前后做 setup / teardown，比如重置数据库；
- span 评估（`HasMatchingSpan`）：基于 OpenTelemetry span 断言"工具 X 被调用过"。

## 10. 线上评估

离线评测用的是固定数据集；线上评估则把 evaluator 挂到真实流量上，在后台异步执行、可以抽样，结果以 OpenTelemetry 事件（`gen_ai.evaluation.result`）发出：

```python
from dataclasses import dataclass
from pydantic_ai import Agent
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.online_capability import OnlineEvaluation

@dataclass
class OutputNotEmpty(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return bool(ctx.output)

agent = Agent(
    "openai-chat:qwen-flash",
    name="assistant",
    capabilities=[OnlineEvaluation(evaluators=[OutputNotEmpty()])],
)
```

每次 run 结束后，capability 按各个 evaluator 的 `sample_rate` 抽样，构造 `EvaluatorContext`（输出、提示、用量、时长、span 树），然后在后台分发，不会阻塞调用方。普通函数可以用 `@pydantic_evals.online.evaluate(...)` 装饰器达到同样效果。如果想在 Python 里对结果再做处理（告警、聚合），注册一个 sink。

---

## 11. 评测的四个维度

一个上线的 Agent，通常要同时看这几个维度：

| 维度 | 怎么测 | 推荐 Evaluator |
|------|-------|---------------|
| Accuracy（准不准） | 与 expected_output 对比 | `EqualsExpected` / `Contains` / 自定义 |
| Format（格式对不对） | 字段齐全、JSON 合法、长度合理 | `IsInstance` / 自定义 |
| Latency（够不够快） | 单次运行耗时 | `MaxDuration` |
| Quality（够不够好） | 主观维度，比如礼貌、完整 | `LLMJudge` |

只看 accuracy 容易被那种"答案对、但回答方式很糟"的模型骗过去，几个维度要一起看。

---

## 12. CI 集成

把 eval 当成 PR 的门槛，分数高于阈值才能合并：

```python
# scripts/run_eval.py
report = dataset.evaluate_sync(task)
avg = report.averages()
report.print(include_input=True, include_output=True)
if avg.assertions is not None and avg.assertions < 0.85:      # 所有布尔断言的平均通过率
    raise SystemExit("accuracy regression!")
```

CI 里跑：

```yaml
- run: python scripts/run_eval.py
```

为了不让每个 PR 都花一笔 API 费用：

- 被测模型固定用便宜的档位（mini / flash / haiku）
- LLMJudge 也用便宜模型
- 数据集控制在 50 条以内，够做 smoke test
- 大数据集放到 nightly 跑

---

## 13. 实战：评测一个分类 Agent

完整代码见 [`demos/modules/02_evals.py`](../../demos/modules/02_evals.py)。它会：

1. 定义一个 8 条 case 的客服意图数据集
2. 注册 `EqualsExpected` + `MaxDuration` + 自定义 `IsLowercase` + `LLMJudge`
3. 用 Pydantic AI Agent 跑（没有 API Key 时用 `TestModel`）
4. 打印 report，统计平均分

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `evaluate_sync` 报 `expected_output` 是 None | 用了 `EqualsExpected` 但 case 没写 expected | 改用 `Equals(value=...)` 或补上 expected |
| LLMJudge 每次跑结果都不一样 | 裁判模型本身有随机性 | 跑多次取平均，或者给裁判设 `temperature=0`（Claude 5.x 不接受这个参数，用它当裁判只能多跑几次取中位数） |
| LLMJudge 打分过严或过松 | rubric 太模糊 | 在 rubric 里给出满分、半分、零分的具体例子 |
| 自定义 evaluator 没生效 | 忘了实例化 | 写 `evaluators=[MyEval()]`，不是 `[MyEval]` |
| 数据集太小，结论不可信 | 少于 10 条 case | smoke 至少 30 条，nightly 跑 100 条以上 |
| `evaluate_sync` 特别慢 | task 是同步函数，串行调 LLM | task 写成 `async def`，用 `await dataset.evaluate(task, max_concurrency=...)` |
| `AttributeError: 'ReportCaseAggregate' object has no attribute 'items'` | `report.averages()` 不再是 dict | 用属性：`avg.assertions` / `avg.scores` / `avg.task_duration` |
| 每次 CI 花费很高 | 数据集太大，又用了旗舰模型 | 小数据集加 mini 模型，全量只在 nightly 跑 |
| 想看每个 case 的详情 | `.print()` 默认截断长字符串 | `report.print(include_input=True, include_output=True)` |
| 该写单元测试还是 eval？ | 看你测的是代码逻辑还是模型质量 | 前者用 `pytest+TestModel`，后者用 `pydantic-evals` |

---

## 15. 何时不要用 Pydantic Evals

| 场景 | 替代方案 |
|------|---------|
| 测一个不调 LLM 的工具函数 | 直接 `pytest` |
| 想跟踪线上真实流量分布 | 线上评估（§10）+ Logfire |
| 想做 RLHF 标注 / 人工评估 | LangSmith / Argilla 之类的标注平台 |
| 想自动找 prompt 最优解 | DSPy / promptfoo（pydantic-evals 不做自动优化） |

---

## 16. 本章 demo

完整可运行代码：[`demos/modules/02_evals.py`](../../demos/modules/02_evals.py)

下一篇：[Pydantic Graph 状态机](03-graph.md)，讲怎样用 `pydantic-graph` 把多步骤工作流写成类型安全的状态机。
