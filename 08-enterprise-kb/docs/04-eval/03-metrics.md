# EKB 19：评估指标（recall@k 与引用准确率）

> RAG 的评估分两层，各有一组指标。检索层看 recall@k，也就是该命中的文档有没有进 top-k；生成层看引用准确率和兜底正确率，即答案是否忠于检索结果、没依据时肯不肯承认。这篇把每个指标讲清楚，并给出计算代码。

---

## 1. 检索层：recall@k

它回答的问题是：期望命中的文档，有没有出现在检索返回的前 k 条里。

```
recall@k = 命中的期望文档数 / 期望文档总数
```

举个例子：某个问题期望命中 doc [7, 9]，检索 top-5 返回的 chunk 分别属于 doc {7, 3, 15, 20, 9}。7 和 9 都在，recall@5 = 2/2 = 1.0。

为什么看 recall 而不是 precision？在 RAG 里，漏掉正确文档比多召回几篇无关文档更要命，后者还可以交给 rerank 再筛一遍。所以 recall 优先。

```python
def recall_at_k(retrieved_doc_ids: list[int], expected: list[int], k: int) -> float | None:
    if not expected:           # 答不出用例，检索层不算 recall
        return None
    topk = set(retrieved_doc_ids[:k])
    hit = len(topk & set(expected))
    return hit / len(expected)
```

报告里我一般同时列 recall@3 和 recall@5（或 @10）。k 越大 recall 只会持平或升高，塞进 prompt 的噪声也越多，两者要权衡。

---

## 2. 生成层：引用准确率

它看的是模型声称引用的文档（`cited_doc_ids`）是否真的支撑了答案，并且落在期望范围内。

```
引用准确率 = 引用正确的用例数 / 总用例数
```

起步可以用简化判定：模型引用的文档和期望文档有交集，且没有引用明显无关的文档。

```python
def citation_correct(cited: list[int], expected: list[int]) -> bool:
    if not expected:           # 答不出用例不该有引用
        return len(cited) == 0
    return len(set(cited) & set(expected)) > 0
```

更严格的做法是用 LLM-as-judge 逐条判断「这条引用是否支撑了对应的论断」（见 04 手册的 [LLM-as-Judge](/docs/04-prompt-engineering/05-by-task/05-judge)）。刚开始用上面的集合判定就够了。

---

## 3. 生成层：兜底正确率

这个指标看的是：该说「没找到」的时候，模型有没有老实说，而不是编一个答案。

```
兜底正确率 = 在「答不出」用例上正确返回 found=false 的比例
```

```python
def fallback_correct(answer_found: bool, expected: list[int]) -> bool:
    # 期望为空 = 本就该答不出
    should_be_unfound = (len(expected) == 0)
    return answer_found == (not should_be_unfound)
```

对企业知识库来说，它的权重最高。recall 再高，只要模型在没有依据时硬编，整个系统就不可信。上线门禁里要把这个指标卡死，见 [上线 Checklist](../10-production/06-launch-checklist.md)。

---

## 4. 指标总览

| 层 | 指标 | 衡量什么 | 目标方向 | 权重 |
|----|------|----------|----------|------|
| 检索 | recall@5 | 该召回的有没有召回 | 越高越好 | 高 |
| 检索 | recall@3 | 精排后头部质量 | 越高越好 | 中 |
| 生成 | 引用准确率 | 答案是否忠于来源 | 越高越好 | 高 |
| 生成 | 兜底正确率 | 没依据时是否承认 | 接近 1.0 | 最高 |
| 生成 | 答案要点覆盖 | 关键信息是否答全 | 越高越好 | 中 |
| 系统 | 越权召回数 | 是否检索到无权文档 | 必须 0 | 红线 |

最后一行越权召回数是红线指标，要求必须为 0，而不是越低越好。第 08 章会专门讲。

---

## 5. 聚合成一张记分牌

每次评估跑完，输出固定格式的记分牌，方便和上一次对比。下面是一份示例（数字为示意数据）：

```
=== EKB Eval Report ===
用例数: 20
检索层:
  recall@3: 0.74
  recall@5: 0.85
生成层:
  引用准确率: 0.80
  兜底正确率: 1.00  ✅
  答案要点覆盖: 0.72
红线:
  越权召回: 0      ✅
```

每次改动后跑一遍，涨了就留，跌了就查原因。完整的评估脚本在下一篇。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只看一个综合分 | 看不出问题在哪一层 | 分层报告 |
| 用 precision 当主指标 | 漏召回被忽视 | RAG 优先看 recall |
| 兜底正确率不单列 | 编答案的问题被平均掉 | 单独列出，权重最高 |
| 越权召回当普通指标 | 安全问题被容忍 | 设为必须为 0 的红线 |
| k 固定只看一个值 | 看不到召回和噪声的权衡 | recall@3 和 @5 都看 |

---

## 下一步

把这些指标实现成一个一键运行的脚本：[评估脚本](./04-eval-script.md)
