# 端到端 RAG 评测（含 LLM 答案）

> 检索的 Recall 很高，不等于最后的答案就对。端到端评测要看三个维度：Faithfulness（答案有没有忠于文档）、Answer Relevance（是不是回答了用户问的问题）、Context Relevance（召回的内容是否相关）。这篇先讲怎么自己写 LLM 判官，再介绍 RAGAS、TruLens、DeepEval 这几个现成框架。

---

## 1. 为什么光看 Recall 不够

```
Recall@5 = 95%（很高）

但是：
- LLM 看到了对的文档，但答歪了（用了错的 chunk）
- LLM 答案有幻觉（编了文档没说的）
- LLM 把多个文档信息混淆（A 文档说 P, B 文档说 Q, 答 P+Q）
- 文档过期，LLM 没意识到
```

这些问题，只有端到端评测才能发现。

---

## 2. 三个维度

| 维度 | 关心什么 | 衡量方法 |
|------|--------|---------|
| Context Relevance | 召回的文档和 query 相关吗 | LLM-as-judge / 人审 |
| Faithfulness | 答案能在文档里找到依据吗 | LLM-as-judge |
| Answer Relevance | 答案有没有回答 query | LLM-as-judge |

除此之外还可以看：

- Recall@k / Precision@k：见 [检索评测指标：Recall@k / MRR / nDCG](./01-metrics.md)
- Answer Correctness：和标准答案对比

---

## 3. 自己写 LLM-as-judge

```python
from openai import OpenAI
import json


client = OpenAI()


def judge_faithfulness(answer: str, context: str) -> dict:
    """答案是否忠实文档"""
    resp = client.chat.completions.create(
        model="gpt-6.1-sol",
        messages=[
            {"role": "system", "content": """判断 answer 是否被 context 支持。

逐句检查 answer：
- 每句 claim 能否在 context 找到证据？
- 是否有编造（context 没说的）？

输出 JSON: {
  "faithful": true/false,
  "score": 0-1,
  "unsupported_claims": ["..."]
}
"""},
            {"role": "user", "content": f"Context:\n{context}\n\nAnswer:\n{answer}"},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def judge_relevance(answer: str, query: str) -> dict:
    """答案是否回答了用户问题"""
    resp = client.chat.completions.create(
        model="gpt-6.1-sol",
        messages=[
            {"role": "system", "content": """判断 answer 是否回答了 query。

输出 JSON: {
  "answered": true/false,
  "score": 0-1,
  "reason": "..."
}
"""},
            {"role": "user", "content": f"Query: {query}\n\nAnswer: {answer}"},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def judge_context_relevance(context: str, query: str) -> dict:
    resp = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[
            {"role": "system", "content": """判断 context 跟 query 的相关性。
输出 {"score": 0-1, "reason": "..."}"""},
            {"role": "user", "content": f"Query: {query}\n\nContext: {context}"},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)
```

---

## 4. RAGAS（开箱即用）

```bash
pip install ragas
```

ragas 0.2 起推荐用 `EvaluationDataset`，字段名改成了 `user_input` / `response` / `retrieved_contexts` / `reference`；下面这种老字段名的 HF Dataset 写法仍会被自动转换，但会给弃用警告，新代码以 ragas 当前文档为准。

```python
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from datasets import Dataset


# 你的数据
data = {
    "question": ["如何取消订阅", "退款政策是什么"],
    "answer": ["登录后进入设置 → 取消订阅", "年付 7 天内全额"],
    "contexts": [
        ["如何取消订阅：登录 → 设置 → 订阅 → 取消..."],
        ["退款政策：年付 7 天内全额，月付不退..."],
    ],
    "ground_truth": ["登录后进入设置，在订阅里点取消", "年付 7 天内全额退款，月付不退"],  # 标准答案；context_recall 要用到它
}


dataset = Dataset.from_dict(data)


result = evaluate(
    dataset,
    metrics=[
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
    ],
)

print(result)
# 形如（示意）：
# {
#   "faithfulness": 0.92,
#   "answer_relevancy": 0.88,
#   "context_precision": 0.95,
#   "context_recall": 0.90,
# }
```

---

## 5. TruLens

TruLens 从 1.0 起拆成了 `trulens-*` 一组包，旧的 `trulens-eval` / `trulens_eval` 只剩兼容层；2.x 里 `Feedback` 又改名为 `Metric`（旧名还能用，会报弃用警告）。下面按 2.16 的包名写，选择器（`.on(...)` 里填什么）随版本变化较大，以[官方文档](https://www.trulens.org/)为准。

```bash
pip install trulens trulens-apps-langchain trulens-providers-openai
```

```python
from trulens.core import TruSession, Metric
from trulens.apps.langchain import TruChain
from trulens.providers.openai import OpenAI
from trulens.dashboard import run_dashboard


session = TruSession()
openai_provider = OpenAI()


# 定义评测指标
f_groundedness = Metric(implementation=openai_provider.groundedness_measure_with_cot_reasons).on(...)
f_qa_relevance = Metric(implementation=openai_provider.relevance_with_cot_reasons).on_input_output()
f_context_relevance = Metric(implementation=openai_provider.context_relevance).on(...)


# 包裹你的 RAG chain
tru_chain = TruChain(
    my_rag_chain,
    app_name="rag",
    app_version="v1",
    feedbacks=[f_groundedness, f_qa_relevance, f_context_relevance],
)


# 跑
for case in evalset:
    with tru_chain as recording:
        my_rag_chain.invoke({"query": case["query"]})


# Dashboard
run_dashboard(session)
```

TruLens 自带 dashboard，可以看分数分布和表现最差的案例。

---

## 6. DeepEval

```bash
pip install deepeval
```

```python
from deepeval.test_case import LLMTestCase
from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric


metric_faith = FaithfulnessMetric(threshold=0.7)
metric_rel = AnswerRelevancyMetric(threshold=0.7)


for case in evalset:
    test_case = LLMTestCase(
        input=case["query"],
        actual_output=case["answer"],
        retrieval_context=case["contexts"],
    )
    
    metric_faith.measure(test_case)
    metric_rel.measure(test_case)
    
    print(f"Q: {case['query']}")
    print(f"  Faithfulness: {metric_faith.score:.2f}")
    print(f"  Relevance:    {metric_rel.score:.2f}")
```

---

## 7. 完整 demo

```python
import asyncio
import numpy as np
import json
from openai import OpenAI


client = OpenAI()


CORPUS = [
    "如何取消订阅：登录账户 → 设置 → 订阅 → 取消按钮，确认后订阅在当前周期末停止。",
    "退款政策：年付订阅 7 天内可全额退款，月付不支持退款，请联系客服。",
    "登录帮助：访问首页点击右上角'登录'，输入邮箱和密码。忘记密码请重置。",
    "API 速率限制：免费版 100 req/min，Pro 1000 req/min。",
]


corpus_vecs = np.array([
    d.embedding for d in client.embeddings.create(model="text-embedding-3-small", input=CORPUS).data
])


def retrieve(query, top_k=3):
    q_vec = client.embeddings.create(model="text-embedding-3-small", input=[query]).data[0].embedding
    sims = corpus_vecs @ np.array(q_vec)
    top = np.argsort(-sims)[:top_k]
    return [CORPUS[i] for i in top]


def rag_answer(query):
    contexts = retrieve(query)
    context_text = "\n\n".join(contexts)
    
    resp = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[
            {"role": "system", "content": "基于以下文档回答用户问题。如果文档没说，回答'不知道'。"},
            {"role": "user", "content": f"文档：\n{context_text}\n\n问题：{query}"},
        ],
    )
    return {
        "answer": resp.choices[0].message.content,
        "contexts": contexts,
    }


def judge(query, answer, contexts):
    context_text = "\n\n".join(contexts)
    resp = client.chat.completions.create(
        model="gpt-6.1-sol",
        messages=[
            {"role": "system", "content": """评测 RAG 答案。输出 JSON：
{
  "faithfulness": 0-1（答案是否被 context 支持）,
  "answer_relevance": 0-1（是否答了 query）,
  "context_relevance": 0-1（context 跟 query 多相关）,
  "comments": "..."
}"""},
            {"role": "user", "content": f"Query: {query}\n\nContext:\n{context_text}\n\nAnswer:\n{answer}"},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


EVALSET = [
    "如何取消订阅",
    "你们退款政策是什么",
    "API 限流多少",
]


for q in EVALSET:
    result = rag_answer(q)
    eval_res = judge(q, result["answer"], result["contexts"])
    
    print(f"\nQ: {q}")
    print(f"A: {result['answer'][:80]}...")
    print(f"  Faithfulness:    {eval_res['faithfulness']:.2f}")
    print(f"  Answer Relevance:{eval_res['answer_relevance']:.2f}")
    print(f"  Context Relevance:{eval_res['context_relevance']:.2f}")
```

---

## 8. 评测成本

LLM-as-judge 按调用次数计费，单次不贵，但跑得勤、换强判官之后成本会明显上去。下面按 gpt-6-luna（输入 $0.10、输出 $0.50 每百万 token）粗算，假设每次判官调用约 2,000 输入 token、200 输出 token，数字只是示意：

```
1 case = 3 次 judge call（faith + rel + ctx_rel）
每次 ≈ 2000 × $0.10/1M + 200 × $0.50/1M ≈ $0.0003

200 case evalset ≈ 600 次调用 ≈ $0.18 一轮

每周跑回归 ≈ $0.8/月
每次 commit 跑（按每月 100 次 commit）≈ $18/月
```

换成 gpt-6.1-sol 这类强判官，成本按它的单价同比放大；召回的上下文越多、文档越长，输入 token 也会成倍增加。

几个省钱办法：

- 便宜的判官（gpt-6-luna）跑全量，强判官（gpt-6.1-sol）抽样复核
- 加缓存，相同的 (query, answer, context) 不重复判
- 放到离线跑，不阻塞 CI

---

## 9. 评测什么模型

```
所有版本：v1.0, v1.1, v2.0 各跑一次

每个版本 N 个候选方案：
  - embedder: 3-small / 3-large / bge
  - chunk_size: 200 / 400 / 800
  - retrieval: vector only / hybrid / hybrid+rerank
  - LLM: gpt-6-luna / gpt-6.1-sol / claude
```

全部跑完，选分数最好的组合。

---

## 10. 跟 PE 手册的方法论一致

这里完全沿用 [Prompt Engineering 手册的流程篇](../../../04-prompt-engineering/docs/02-process/01-lifecycle.md)：

1. Spec：想清楚 RAG 要解决什么任务
2. v0：先跑通
3. evalset：100 条以上
4. 迭代：每轮只改一处，改完跑评测
5. 何时停：Faithfulness 和 Relevance 都超过 0.85
6. 上线：灰度加监控

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 判官模型太弱 | 小模型（如 gpt-6-luna）打分容易偏宽松，换 gpt-6.1-sol 这类强模型 |
| 不分 case 类型 | 打 tag，按 happy / edge / typo 分组看 |
| 只跑一次取平均 | LLM 判官有方差，重复跑 3 次 |
| evalset 和 corpus 重叠 | 等于拿自家文档考自家，分数虚高 |

---

## 12. 下一步

- [持续评测 + 回归测试](./04-continuous.md)
- [完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
- [监控：召回率 / Latency / Cost](../07-production/05-monitoring.md)
