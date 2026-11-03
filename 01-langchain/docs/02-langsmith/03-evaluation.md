# LangSmith 03：Datasets 与 Evaluation

> 一次评估由三样东西组成：Dataset（一组 input/output）、Evaluator（评分函数）和 Target（被评估的 chain），跑一遍的结果叫 Experiment。改 prompt、换模型、调 RAG 之后到底变好还是变差，有了它就能拿分数说话，不用靠感觉。

版本：以 `langsmith 0.14.x` 为准，demo 模型用千问。`LangChainStringEvaluator` 已从 `langsmith.evaluation` 移除，改用 `openevals` 或自己写 evaluator；`@unit` 换成了 `@pytest.mark.langsmith`。

---

## 1. 为什么要做 Eval

做 LLM 应用，最头疼的往往是判断改动的效果：改了 prompt，不知道整体是变好还是变坏；换了模型，不知道哪类问题退步了；RAG 加了 reranker，说不清有没有提升；新版本上线，也不确定质量有没有回归。

靠人工逐条看又贵又慢，不同的人判断标准还不一致。所以得把评估自动化，像跑单测一样跑。

---

## 2. 三个核心对象

### 2.1 Dataset

一组 `Example`，每个 example 由 inputs / outputs(ground truth) / metadata 组成。

可以在界面里上传文件、从 trace 复制，也可以用 SDK 创建：

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

被评估的对象，任何返回 dict 的 callable 都行：

```python
def target(inputs: dict) -> dict:
    return {"answer": chain.invoke(inputs["question"])}
```

也可以直接传 `chain.invoke`、`agent.invoke`。

### 2.3 Evaluator

接收 `run`（运行结果）和 `example`（数据集里的 ground truth），返回评分：

```python
def correctness(run, example) -> dict:
    pred = run.outputs["answer"]
    gold = example.outputs["answer"]
    return {"key": "correct", "score": int(pred.strip().startswith(gold[:10]))}
```

更推荐的新写法：

```python
def correctness(outputs: dict, reference_outputs: dict) -> bool:
    return outputs["answer"].lower() == reference_outputs["answer"].lower()
```

LangSmith 会按参数名自动识别新签名。

---

## 3. 跑一次 Evaluation

```python
from langsmith import evaluate          # 也可 from langsmith.evaluation import evaluate

result = evaluate(
    target,
    data="lc-qa-v1",        # 数据集名或 id；也可以直接传本地 Example 列表
    evaluators=[correctness],
    experiment_prefix="baseline",
    metadata={"model": "gpt-6-luna"},
    max_concurrency=4,
)
print(result.experiment_name)
```

**没有 LangSmith 账号也能跑。** `data` 传一组本地的 `langsmith.schemas.Example`，再加上 `upload_results=False`（beta），评估就在本地执行、结果不上传，适合在 CI 或学习环境里试评估器，具体见后面的 demo。

跑完后在 LangSmith 界面进入 Datasets → lc-qa-v1 → Experiments，结果页里有这些信息：

- 每条 example 的预测、得分、耗时
- 各 evaluator 平均分
- 和上一次 Experiment 的差异（diff）

---

## 4. LLM-as-Judge

用大模型打分是最常用的评估方式。`LangChainStringEvaluator`（`qa` / `criteria` / `score_string` 等内置类型）已经从 `langsmith.evaluation` 里移除，官方把现成的 LLM-as-judge、字符串距离等评估器放进了独立的 `openevals` 包（`create_llm_as_judge` 等，用法以它的文档为准）。如果想少一个依赖、完全自己掌控，直接写 evaluator 也不难，下面是最小版本。

### 4.1 自定义 Judge prompt

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

class Score(BaseModel):
    score: int = Field(description="0-10")
    reasoning: str

judge_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 QA 评估官。给以下回答打分 0-10，并给理由。"),
    ("human", "问题：{q}\n标准答：{gold}\n模型答：{pred}"),
])
judge = judge_prompt | ChatOpenAI(model="gpt-6-luna", temperature=0).with_structured_output(Score)

def llm_judge(outputs: dict, reference_outputs: dict, inputs: dict) -> dict:
    s = judge.invoke({"q": inputs["question"], "gold": reference_outputs["answer"], "pred": outputs["answer"]})
    return {"key": "quality", "score": s.score / 10, "comment": s.reasoning}
```

---

## 5. Pairwise Eval（对比两个版本）

先让 A、B 两个版本在同一个数据集上各跑一个 experiment，再用对比评估器让 LLM 直接挑更好的那个：

```python
from langsmith import evaluate
from langsmith.evaluation import evaluate_comparative

def judge_pair(runs: list, example) -> dict:
    """runs 是同一个 example 在 A、B 两个 experiment 下的 run；返回每个 run 的得分"""
    a, b = runs[0].outputs["answer"], runs[1].outputs["answer"]
    a_wins = ask_llm_which_is_better(example.inputs["q"], a, b)     # 你的 LLM 评审，返回 True/False
    return {"key": "preference", "scores": {runs[0].id: int(a_wins), runs[1].id: int(not a_wins)}}

res_a = evaluate(target_v1, data="lc-qa-v1", experiment_prefix="A", blocking=False)
res_b = evaluate(target_v2, data="lc-qa-v1", experiment_prefix="B", blocking=False)
res_a.wait(); res_b.wait()

evaluate_comparative((res_a.experiment_name, res_b.experiment_name), evaluators=[judge_pair])
```

比较两个模型或两版 prompt 时，让评审直接二选一，比分别打分再比较更可靠，因为单独打分时两次的尺度未必一致。这个功能要连 LangSmith 服务才能运行。

---

## 6. Summary Evaluators（聚合指标）

不针对单条 example，而是看整体表现：

```python
def avg_length(runs, examples) -> dict:
    lengths = [len(r.outputs["answer"]) for r in runs]
    return {"key": "avg_length", "score": sum(lengths) / len(lengths)}

evaluate(target, data="...", evaluators=[correctness], summary_evaluators=[avg_length])
```

---

## 7. RAG Eval 模板

RAG 系统一般看这几个指标：

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

> RAG 的 faithfulness / relevance 评估可以用 `openevals` 里的 RAG 评估器或 RAGAS 等第三方库，只要最后返回 `{"key", "score"}` 就能挂进 `evaluators=`。

---

## 8. 在 CI 跑 Eval

和 `pytest` 集成时，给用例加上 `@pytest.mark.langsmith`，SDK 会把输入、输出、反馈记录成 LangSmith 上的一次 experiment：

```python
# tests/test_quality.py
import pytest
from langsmith import testing as t

@pytest.mark.langsmith
@pytest.mark.parametrize("q,ref", [("LCEL 是什么？", "LangChain Expression Language")])
def test_qa(q, ref):
    t.log_inputs({"q": q})
    out = chain.invoke({"q": q})
    t.log_outputs({"answer": out})
    t.log_reference_outputs({"a": ref})
    t.log_feedback(key="contains_ref", score=int(ref.lower() in out.lower()))
    assert ref.lower() in out.lower()
```

运行 `pytest --langsmith-output tests/`，结果会在终端以表格形式输出，配了 key 还会同时上传。CI 失败时可以去界面里看具体的 trace。（`@unit` 装饰器仍作为别名保留，新代码用 `@pytest.mark.langsmith`。）

---

## 9. Online Evaluation（生产）

评估不只能在数据集上跑，生产环境的 trace 也可以自动打分。在 LangSmith 控制台进入 Project → Rules（Automations / Online evaluators）配置：

- 触发条件：按 metadata、tag、是否出错、延迟等过滤，并设置采样比例
- 动作：运行某个评估器并写入 feedback、加入数据集（自动收集 bad case）、触发 webhook 告警

这部分主要在控制台配置，SDK 里没有稳定的方法（也不存在 `client.create_automation_rule`），要程序化管理得看官方 REST API 文档。第 05 篇会细讲。

---

## 10. 本章 demo

完整代码：[`demos/langsmith/03_evaluation.py`](../../demos/langsmith/03_evaluation.py)

完整代码见 `demos/langsmith/03_evaluation.py`。有 `LANGSMITH_API_KEY` 时会建数据集并上传 experiment，没有时就用本地 Example 离线评估。核心部分如下：

```python
import os, uuid
from datetime import datetime, timezone
from langsmith import evaluate
from langsmith.schemas import Example
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

MODEL = os.environ.get("QWEN_MODEL", "gpt-6-luna")

# 1) 数据（离线模式：本地 Example；在线模式传数据集名即可）
now, ds_id = datetime.now(timezone.utc), uuid.uuid4()
data = [Example(id=uuid.uuid4(), dataset_id=ds_id, created_at=now, inputs=i, outputs=o)
        for i, o in [({"q": "LCEL 是什么？"}, {"a": "LCEL 是 LangChain Expression Language"}),
                     ({"q": "如何流式输出？"}, {"a": "使用 stream / astream"})]]

# 2) target
chain = ChatPromptTemplate.from_template("用一句话简要回答：{q}") | ChatOpenAI(model=MODEL, temperature=0) | StrOutputParser()
def target(inputs): return {"answer": chain.invoke({"q": inputs["q"]})}

# 3) evaluator：LLM 评审，结构化输出打分
class Score(BaseModel):
    score: int = Field(description="0-10")
    reasoning: str
judge = (ChatPromptTemplate.from_messages([("system", "你是 QA 评估官，给模型答案打 0-10 分，并给理由。"),
                                           ("human", "问题：{q}\n标准答：{gold}\n模型答：{pred}")])
         | ChatOpenAI(model=MODEL, temperature=0).with_structured_output(Score))

def quality(outputs, reference_outputs, inputs):
    s = judge.invoke({"q": inputs["q"], "gold": reference_outputs["a"], "pred": outputs["answer"]})
    return {"key": "quality", "score": s.score / 10, "comment": s.reasoning}

# 4) 跑（upload_results=False：不上传，纯本地）
res = evaluate(target, data=data, evaluators=[quality], experiment_prefix="qa-baseline",
               max_concurrency=2, upload_results=False)
for r in res:
    print(r["example"].inputs["q"], [x.score for x in r["evaluation_results"]["results"]])
```

> 偶尔会看到 `Failed to multipart ingest runs ... 401` 这样的日志，那是 SDK 想上传 trace 但没有 key，不影响本地的评估结果。

---

## 11. 工程实践

1. 每个版本都维护一个 dataset，用户反馈差的 trace 持续加进去。
2. 在 CI 里卡 eval 分数，低于阈值的 PR 不能合并。
3. 组合多个 evaluator。只有一个 evaluator 的话，prompt 很容易被调成专门迎合它。
4. 每次实验都拿当前线上版本做 baseline 对比。
5. 在线评估要采样，按 metadata 挑一部分 trace 评，控制成本。

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `evaluate` 报参数签名不对 | 旧 API | evaluator 用 `outputs / reference_outputs / inputs` 参数名（按名字注入）；也兼容 `(run, example)` |
| `data=[{...dict...}]` 报 `'dict' has no attribute 'modified_at'` | 本地数据必须是 `Example` 对象 | 用 `langsmith.schemas.Example` 构造 |
| `ImportError: LangChainStringEvaluator` | 已移除 | 用 `openevals` 或自写 evaluator |
| LLM judge 分数不稳定 | temperature 不为 0 | 能设就设 temperature=0；Claude 新模型已不支持 temperature，用多次评分取中位数 |
| 大数据集跑得很慢 | 串行执行 | `max_concurrency=10` |
| 数据集里出现重复 example | example_id 不会自动去重 | 调 `client.create_examples` 前先查一遍 |
| eval 写进了生产代码 | 离线评估和在线逻辑混在一起 | 用专门的 dataset + CI |

---

下一篇：[LangSmith 04：Prompt Hub 与 Playground](04-prompt-hub.md)
