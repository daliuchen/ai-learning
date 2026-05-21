# 端到端 RAG 评测（含 LLM 答案）

> **一句话**：检索 Recall 高不代表答案对——端到端评测要看 **Faithfulness（答案忠实文档）+ Answer Relevance（答用户问的）+ Context Relevance（召回相关）** 三大维度，常用 RAGAS / TruLens / DeepEval 等框架。

---

## 1. 为啥光看 Recall 不够

```
Recall@5 = 95%（很高）

但是：
- LLM 看到了对的文档，但答歪了（用了错的 chunk）
- LLM 答案有幻觉（编了文档没说的）
- LLM 把多个文档信息混淆（A 文档说 P, B 文档说 Q, 答 P+Q）
- 文档过期，LLM 没意识到
```

端到端评测才能发现这些。

---

## 2. 三大维度

| 维度 | 关心啥 | 衡量方法 |
|------|--------|---------|
| **Context Relevance** | 召回的文档跟 query 相关吗 | LLM-as-judge / 人审 |
| **Faithfulness** | 答案在文档里能找到根据吗 | LLM-as-judge |
| **Answer Relevance** | 答案是不是答了 query | LLM-as-judge |

外加：

- **Recall@k** / **Precision@k**：见 [01-metrics.md](./01-metrics.md)
- **Answer Correctness**：跟标准答案对比

---

## 3. 自己写 LLM-as-judge

```python
from openai import OpenAI
import json


client = OpenAI()


def judge_faithfulness(answer: str, context: str) -> dict:
    """答案是否忠实文档"""
    resp = client.chat.completions.create(
        model="gpt-4o",
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
        model="gpt-4o",
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
        model="gpt-4o-mini",
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
    "ground_truth": ["..."],  # 可选，跟标准答案对比时用
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
# {
#   "faithfulness": 0.92,
#   "answer_relevancy": 0.88,
#   "context_precision": 0.95,
#   "context_recall": 0.90,
# }
```

---

## 5. TruLens

```bash
pip install trulens-eval
```

```python
from trulens_eval import Tru, Feedback, TruChain
from trulens_eval.feedback.provider.openai import OpenAI


tru = Tru()
openai_provider = OpenAI()


# 定义 feedback functions
f_groundedness = Feedback(openai_provider.groundedness_measure_with_cot_reasons).on(...)
f_qa_relevance = Feedback(openai_provider.relevance_with_cot_reasons).on_input_output()
f_context_relevance = Feedback(openai_provider.context_relevance).on(...)


# 包裹你的 RAG chain
tru_chain = TruChain(my_rag_chain, app_id="rag_v1", feedbacks=[f_groundedness, f_qa_relevance, f_context_relevance])


# 跑
for case in evalset:
    with tru_chain as recording:
        my_rag_chain.invoke({"query": case["query"]})


# Dashboard
tru.run_dashboard()
```

TruLens 有内置 dashboard 看分布、最差案例。

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
# demos/evaluation/03_end_to_end.py
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
        model="gpt-4o-mini",
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
        model="gpt-4o",
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

LLM-as-judge 很贵：

```
1 case = 3 次 judge call（faith + rel + ctx_rel）
每次 ~$0.005（gpt-4o）

200 case evalset = $3 一轮

每周跑回归 = $12/月
每次 commit 跑 = $30+/月
```

省钱：

- gpt-4o-mini（弱判官）+ gpt-4o（强判官）抽样
- Cache：相同 (query, answer, context) 不重判
- 离线跑（不阻塞 CI）

---

## 9. 评测什么模型

```
所有版本：v1.0, v1.1, v2.0 各跑一次

每个版本 N 个候选方案：
  - embedder: 3-small / 3-large / bge
  - chunk_size: 200 / 400 / 800
  - retrieval: vector only / hybrid / hybrid+rerank
  - LLM: gpt-4o-mini / gpt-4o / claude
```

最终选最好的组合。

---

## 10. 跟 PE 手册的方法论一致

完整沿用 [04-prompt-engineering/02-process](../../../04-prompt-engineering/docs/02-process/)：

1. **Spec**：RAG 要解决什么任务
2. **v0**：先跑通
3. **evalset**：100+ 条
4. **迭代**：每轮改一处 + 跑评测
5. **何时停**：Faithfulness / Relevance 都 > 0.85
6. **上线**：灰度 + 监控

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| Judge 模型用太弱 | gpt-4o-mini 偏宽松，用 gpt-4o |
| 不分 case 类型 | tag → 按 happy / edge / typo 分组看 |
| 只跑一次取平均 | LLM judge 有方差，重复 3 次 |
| evalset 跟 corpus 重叠 | 测出来"自家答自家"虚高 |

---

## 12. 下一步

- 📖 持续评测 + 回归 → [04-continuous.md](./04-continuous.md)
- 📖 完整 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
- 📖 监控 → [07-production/05-monitoring.md](../07-production/05-monitoring.md)
