# Multi-query / Sub-query 拆解

> **一句话**：用户 query 复杂时，**LLM 先拆成多个独立子问题分别检索**，再合并结果——比直接一次检索召回率高 5-15%，特别适合"AND" 类多条件、跨领域问题。

---

## 1. 何时需要拆分

### 例 1：多条件

```
用户：列出 2024 年 deep learning 论文和 reinforcement learning 论文，比较它们的引用量

直接检索：找不到完美匹配的文档（一篇文档很少同时有 DL + RL + 比较）

拆分：
  - 子问题 1：2024 年 deep learning 论文
  - 子问题 2：2024 年 reinforcement learning 论文
  - 子问题 3：引用量比较
  各自检索，合并结果给 LLM
```

### 例 2：歧义 / 改写

```
用户：怎么解决那个错？

模糊 → LLM 生成多个改写：
  - 怎么解决报错
  - 错误码 / 异常处理
  - 故障排查步骤
  
多路检索，提高召回
```

### 例 3：跨领域

```
用户：用 Python 和 SQL 都怎么做？

子问题 1：Python 怎么做
子问题 2：SQL 怎么做
```

---

## 2. Multi-Query 实现：query 改写

最简形式：LLM 给同一 query 改写出 N 个版本，并发检索：

```python
def generate_query_variants(query: str, n: int = 3) -> list[str]:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"为下面问题生成 {n} 个不同的改写版本，每行一个，不要序号。"},
            {"role": "user", "content": query},
        ],
    )
    return [line.strip() for line in resp.choices[0].message.content.split("\n") if line.strip()]


def multi_query_search(query, top_k=5):
    variants = generate_query_variants(query, n=3)
    variants.append(query)  # 加上原 query
    
    rankings = []
    for v in variants:
        hits = vector_db.search(embed(v), top_k=20)
        rankings.append([h.id for h in hits])
    
    return rrf(rankings, top_k=top_k)
```

---

## 3. Sub-Query 实现：query 拆分

对复合问题分解：

```python
import json


def decompose_query(query: str) -> list[str]:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": """把用户问题拆成独立子问题。

规则：
- 复合问题（多个独立维度）→ 拆
- 简单问题 → 输出原 query
- 输出 JSON array，例：["子问题 1", "子问题 2"]
"""},
            {"role": "user", "content": query},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("sub_questions", [query])


def sub_query_search(query, top_k=5):
    sub_qs = decompose_query(query)
    
    if len(sub_qs) == 1:
        # 简单 query，正常搜
        return vector_db.search(embed(query), top_k=top_k)
    
    # 复合 query：每个子问题独立检索
    all_hits = []
    for sub_q in sub_qs:
        hits = vector_db.search(embed(sub_q), top_k=top_k)
        all_hits.append({"sub_question": sub_q, "hits": hits})
    
    return all_hits   # 让 LLM 处理多个子结果
```

给 LLM 时按子问题组织：

```python
def answer_with_sub_queries(query):
    results = sub_query_search(query)
    
    context = ""
    for item in results:
        context += f"\n### 子问题：{item['sub_question']}\n"
        for h in item["hits"][:3]:
            context += f"- {h.payload['text']}\n"
    
    return llm_generate(f"基于以下信息回答：\n{context}\n\n问题：{query}")
```

---

## 4. Multi-Query vs Sub-Query

| | Multi-Query | Sub-Query |
|---|---|---|
| 输入 | 一个 query | 一个 query |
| LLM 干啥 | 改写成同义 | 拆成子问题 |
| 输出 | 同语义的多个 query | 不同维度的子问题 |
| 适合 | 单维度模糊 query | 多维度复合 query |

实战：**两者可以串联**：

```python
sub_qs = decompose_query(query)
all_variants = []
for sq in sub_qs:
    all_variants.extend(generate_query_variants(sq, n=2))
```

---

## 5. 实战 demo

```python
# demos/retrieval/04_multi_query.py
import json
import numpy as np
from openai import OpenAI


client = OpenAI()


corpus = [
    "如何取消订阅：登录后进入设置页面...",
    "退款政策：年付订阅 7 天内全额...",
    "如何登录：访问首页点击登录按钮...",
    "重置密码：忘记密码请点击重置链接...",
    "支付方式：支持信用卡和 PayPal...",
    "如何升级套餐：在订阅页选择新套餐...",
    "AI 功能使用指南：Pro 套餐含全部 AI...",
    "API 速率限制：免费版 100 req/min...",
]


corpus_vecs = np.array([
    d.embedding for d in client.embeddings.create(
        model="text-embedding-3-small", input=corpus
    ).data
])


def embed(text):
    return np.array(client.embeddings.create(model="text-embedding-3-small", input=[text]).data[0].embedding)


def basic_search(query, top_k=3):
    sims = corpus_vecs @ embed(query)
    return [corpus[i] for i in np.argsort(-sims)[:top_k]]


def decompose(query):
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": """把问题拆成 1-3 个独立子问题。
输出 JSON：{"sub_questions": ["..."]}"""},
            {"role": "user", "content": query},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)["sub_questions"]


def sub_query_search(query, top_k=3):
    subs = decompose(query)
    print(f"拆解为 {len(subs)} 个子问题: {subs}")
    
    all_docs = set()
    for sub in subs:
        for doc in basic_search(sub, top_k):
            all_docs.add(doc)
    
    return list(all_docs)


# 复合 query
query = "Pro 套餐的 AI 功能多少钱，怎么退款"

print("\n=== Basic ===")
for d in basic_search(query, top_k=3):
    print(f"  {d}")

print("\n=== Sub-Query ===")
for d in sub_query_search(query, top_k=3):
    print(f"  {d}")
```

---

## 6. 跟 LangChain 集成

```python
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain_openai import ChatOpenAI


retriever = MultiQueryRetriever.from_llm(
    retriever=vector_db.as_retriever(),
    llm=ChatOpenAI(model="gpt-4o-mini"),
)


results = retriever.invoke("怎么解决登录失败的问题")
# 内部：先让 LLM 改写 N 个 query，分别检索，合并
```

---

## 7. 跟 LlamaIndex 集成

```python
from llama_index.core.query_engine import SubQuestionQueryEngine
from llama_index.core.tools import QueryEngineTool


# 把多个子库当工具
tools = [
    QueryEngineTool.from_defaults(query_engine=billing_qe, name="billing"),
    QueryEngineTool.from_defaults(query_engine=support_qe, name="support"),
]


query_engine = SubQuestionQueryEngine.from_defaults(
    query_engine_tools=tools,
)


response = query_engine.query("我的订单退款流程和登录问题")
```

LlamaIndex 自动拆问题并调对应工具。

---

## 8. 性能 / 成本

```
Basic search:
  1 次 embed
  1 次向量库查询
  延迟：~50ms
  成本：~$0.00001

Multi-Query (3 variants):
  1 次 LLM 改写 (~$0.0001)
  3 次 embed + 查询
  延迟：~500ms
  成本：~$0.0001

Sub-Query (复杂):
  1 次 LLM 分解 + 多次 embed/查询
  延迟：~800ms
  成本：~$0.0002
```

贵 10-20x。**只对复杂 query 开**。

---

## 9. 何时跳过

```python
def smart_search(query):
    # 简单启发：query 短而单一 → 直接搜
    if len(query) < 30 and "和" not in query and "," not in query:
        return basic_search(query)
    
    # 复杂 → multi/sub
    return multi_query_search(query)
```

或者让 LLM 自己决定：

```python
def decompose(query):
    resp = llm("这个 query 需要拆吗？需要就拆，不需要就原 query。")
    return ...
```

---

## 10. 实测提升

200 条复合 evalset（每条带 2+ 子主题）：

| 方法 | Recall@10 |
|------|-----------|
| Basic | 72% |
| Multi-Query | 81% |
| Sub-Query | 86% |
| Sub-Query + RRF | 88% |

对**复合 query** 提升明显。简单 query 提升小，不值得。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| LLM 拆出无意义子问题 | prompt 加 "如果原 query 简单，直接返回原 query" |
| 每个 query 都 LLM 拆 | 加 query 长度判断 |
| 子问题答案合并混乱 | LLM 综合时分子问题组织 context |
| 子问题间冲突答案 | 让 LLM 标注矛盾、给用户决策 |

---

## 12. 下一步

- 📖 Rerank pipeline → [05-rerank-pipeline.md](./05-rerank-pipeline.md)
- 📖 Self-query / metadata filter → [06-self-query.md](./06-self-query.md)
- 📖 端到端 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
