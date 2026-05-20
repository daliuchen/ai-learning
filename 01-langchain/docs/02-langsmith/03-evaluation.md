# LangSmith 03：Datasets 与 Evaluation

> **一句话**：Eval = "Dataset（一组 input/output）" × "Evaluator（评分函数）" × "Target（待评 chain）"，跑出来叫 Experiment。这是把 prompt 改造 / 模型升级 / RAG 调优变成"工程化决策"的关键工具。

---

## 1. 为什么要做 Eval

LLM 应用最难的部分：

- 改了 prompt 不知道是更好了还是更坏了
- 换了模型不知道哪类问题变差
- RAG 加了 reranker 不知道有没有提升
- 上线版本不知道质量是否回归

写人工测试不可行（贵、慢、不一致）。**自动化 Eval** 是工程实践的解法。

---

## 2. 三个核心对象

### 2.1 Dataset

一组 `Example`，每个 example 由 inputs / outputs(ground truth) / metadata 组成。

LangSmith 提供两种创建：

- UI：拖文件、复制 trace
- SDK：

```python
from langsmith import Client
client = Client()

ds = client.create_dataset(
    dataset_name="lc-qa-v1",
    description="LangChain 教程问答 baseline",
)
client.create_examples(
    dataset_id=ds.id,
    inputs=[
        {"question": "LCEL 是什么？"},
        {"question": "怎么流式输出？"},
    ],
    outputs=[
        {"answer": "LCEL = LangChain Expression Language..."},
        {"answer": "用 stream / astream..."},
    ],
)
```

### 2.2 Target Function

被评估的对象，任意 callable 都行，返回 dict：

```python
def target(inputs: dict) -> dict:
    return {"answer": chain.invoke(inputs["question"])}
```

也可以直接传 `chain.invoke`、`agent.invoke`。

### 2.3 Evaluator

接收 `run`（运行结果）和 `example`（数据集 ground truth），返回评分：

```python
def correctness(run, example) -> dict:
    pred = run.outputs["answer"]
    gold = example.outputs["answer"]
    return {"key": "correct", "score": int(pred.strip().startswith(gold[:10]))}
```

更现代写法（推荐）：

```python
def correctness(outputs: dict, reference_outputs: dict) -> bool:
    return outputs["answer"].lower() == reference_outputs["answer"].lower()
```

新签名 LangSmith 自动识别。

---

## 3. 跑一次 Evaluation

```python
from langsmith.evaluation import evaluate

result = evaluate(
    target,
    data="lc-qa-v1",        # 数据集名或 id
    evaluators=[correctness],
    experiment_prefix="baseline",
    metadata={"model": "gpt-4o-mini"},
    max_concurrency=4,
)
print(result.experiment_name)
```

跑完到 LangSmith UI → Datasets → lc-qa-v1 → Experiments 看结果，能看到：

- 每条 example 的预测、得分、耗时
- 各 evaluator 平均分
- 与上次 Experiment 的差异（diff）

---

## 4. LLM-as-Judge

用大模型评分（最常用）。简单写法：

```python
from langsmith.evaluation import LangChainStringEvaluator

evaluator = LangChainStringEvaluator(
    "labeled_score_string",   # 内置类型
    config={
        "criteria": "答案是否覆盖 reference 中提到的关键事实",
        "llm": ChatOpenAI(model="gpt-4o-mini"),
    },
    prepare_data=lambda run, example: {
        "prediction": run.outputs["answer"],
        "reference": example.outputs["answer"],
        "input": example.inputs["question"],
    },
)
```

内置 evaluator 类型：

- `qa`：QA 正误
- `cot_qa`：CoT 风格 QA
- `criteria`：自定义标准（如"无幻觉"）
- `labeled_criteria`：带参考答案的标准
- `score_string`：0-10 评分
- `labeled_score_string`：带参考答案 0-10 评分
- `embedding_distance`：embedding 相似度
- `string_distance`：字符串编辑距离

### 4.1 自定义 Judge prompt

```python
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

class Score(BaseModel):
    score: int = Field(description="0-10")
    reasoning: str

judge_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 QA 评估官。给以下回答打分 0-10，并给理由。"),
    ("human", "问题：{q}\n标准答：{gold}\n模型答：{pred}"),
])
judge = judge_prompt | ChatOpenAI(model="gpt-4o-mini").with_structured_output(Score)

def llm_judge(outputs: dict, reference_outputs: dict, inputs: dict) -> dict:
    s = judge.invoke({"q": inputs["question"], "gold": reference_outputs["answer"], "pred": outputs["answer"]})
    return {"key": "quality", "score": s.score / 10, "comment": s.reasoning}
```

---

## 5. Pairwise Eval（对比两个版本）

```python
from langsmith.evaluation import evaluate_comparative

def judge_pair(runs, example) -> dict:
    """0 = 平局, 1 = run A 更好, 2 = run B 更好"""
    a = runs[0].outputs["answer"]
    b = runs[1].outputs["answer"]
    # 喂 judge LLM 比较 a/b
    return {"key": "preference", "scores": [1 if a_wins else 0, 1 if b_wins else 0]}

evaluate_comparative(
    experiments=["exp-A-xxx", "exp-B-yyy"],
    evaluators=[judge_pair],
)
```

适合 A/B 模型 prompt 直接比较哪个更好，避免单独打分校准不齐。

---

## 6. Summary Evaluators（聚合指标）

不在 example 维度评，而是看整体：

```python
def avg_length(runs, examples) -> dict:
    lengths = [len(r.outputs["answer"]) for r in runs]
    return {"key": "avg_length", "score": sum(lengths) / len(lengths)}

evaluate(target, data="...", evaluators=[correctness], summary_evaluators=[avg_length])
```

---

## 7. RAG Eval 模板

RAG 系统典型指标：

```python
from langsmith.evaluation import evaluate

def faithfulness(outputs, reference_outputs):
    """答案是否只用了 context"""
    ...
def answer_relevance(outputs, inputs):
    """答案是否回答了问题"""
    ...
def context_relevance(run, example):
    """context 是否相关"""
    docs = run.outputs.get("docs", [])
    ...

evaluate(
    rag_target,
    data="rag-eval-set",
    evaluators=[faithfulness, answer_relevance, context_relevance],
)
```

RAGAS 框架可直接接 LangSmith：

```python
from ragas.metrics import faithfulness, answer_relevancy
from ragas.integrations.langsmith import langsmith_dataset_evaluator
```

---

## 8. 在 CI 跑 Eval

`pytest` 集成：

```python
# tests/test_quality.py
from langsmith import unit
import pytest

@unit
@pytest.mark.parametrize("inp,ref", [
    ({"q": "..."}, "..."),
])
def test_qa(inp, ref):
    out = chain.invoke(inp)
    assert ref.lower() in out.lower()
```

`@unit` 把 pytest 用例自动记录为 LangSmith run，CI 失败时可在 UI 查看具体 trace。

---

## 9. Online Evaluation（生产）

不仅在数据集跑，**生产 trace 也可以自动评分**。LangSmith UI → Project → Rules：

- 触发条件：`metadata.user_id == "vip"` / `trace.error` / `cost > 0.01`
- 操作：跑某 evaluator / 创建 feedback / Pager 告警

```python
# 也可用 SDK 创建 rule
client.create_automation_rule(
    project_id="...",
    name="auto-quality-check",
    sampling_rate=0.1,
    actions=[{...}],
)
```

---

## 10. demo

```python
# demos/langsmith/03_evaluation.py
import os
from dotenv import load_dotenv
from langsmith import Client
from langsmith.evaluation import evaluate
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field

load_dotenv()
client = Client()

DS = "lc-qa-demo"

# 1) 数据集
try:
    ds = client.read_dataset(dataset_name=DS)
except Exception:
    ds = client.create_dataset(dataset_name=DS, description="demo")
    client.create_examples(
        dataset_id=ds.id,
        inputs=[{"q": "LCEL 是什么？"}, {"q": "如何流式输出？"}],
        outputs=[
            {"a": "LCEL 是 LangChain Expression Language，是 Runnable 组合 DSL。"},
            {"a": "使用 stream/astream/astream_events 等 API。"},
        ],
    )

# 2) target
chain = (
    ChatPromptTemplate.from_template("简要回答：{q}")
    | ChatOpenAI(model="gpt-4o-mini", temperature=0)
    | StrOutputParser()
)
def target(inputs):
    return {"answer": chain.invoke({"q": inputs["q"]})}

# 3) evaluator
class Score(BaseModel):
    score: int = Field(description="0-10")
    reasoning: str

judge_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 QA 评估官，给以下回答打 0-10 分。"),
    ("human", "问题：{q}\n标准答：{gold}\n模型答：{pred}"),
])
judge = judge_prompt | ChatOpenAI(model="gpt-4o-mini").with_structured_output(Score)

def quality(outputs, reference_outputs, inputs):
    s = judge.invoke({"q": inputs["q"], "gold": reference_outputs["a"], "pred": outputs["answer"]})
    return {"key": "quality", "score": s.score / 10, "comment": s.reasoning}

# 4) 跑
result = evaluate(
    target,
    data=DS,
    evaluators=[quality],
    experiment_prefix="qa-baseline",
    max_concurrency=2,
)
print("Experiment:", result.experiment_name)
```

跑完到 LangSmith → Datasets → lc-qa-demo → 看 experiment。

---

## 11. 工程实践

1. **每个版本都建一个 dataset**：用户反馈差的 trace → dataset
2. **CI 卡 eval 分**：低于阈值 PR 不能合并
3. **多 evaluator 组合**：单一 evaluator 容易被 prompt hack
4. **对比实验贯穿**：永远拿当前线上 baseline 对比
5. **采样在线评估**：不评所有 trace，按 metadata 采样降本

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `evaluate` 报参数签名不对 | 旧 API | 用 `outputs / reference_outputs / inputs` 签名 |
| LLM judge 分数飘 | temperature 不为 0 | judge 模型固定 temp=0 |
| 大数据集很慢 | 串行 | `max_concurrency=10` |
| 数据集 example 多了重复 | example_id 不去重 | 用 `client.create_examples` 前先查 |
| eval 写在生产代码里 | 离线/在线混了 | 用专用 dataset+CI |

---

## 13. 本章 demo

[`demos/langsmith/03_evaluation.py`](../../demos/langsmith/03_evaluation.py)

下一篇：[04-prompt-hub.md](04-prompt-hub.md)
