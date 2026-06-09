# EKB 19：评估指标——recall@k 与引用准确率

> **一句话**：RAG 分两层评估，对应两组指标。检索层看 **recall@k**（该命中的文档进了 top-k 没有），生成层看 **引用准确率**和**兜底正确率**（答案有没有忠于检索、该承认时承认没有）。本篇把每个指标讲清并给出计算代码。

---

## 1. 检索层：recall@k

**问题**：期望命中的文档，有没有出现在检索返回的前 k 条里。

```
recall@k = 命中的期望文档数 / 期望文档总数
```

举例：某问题期望命中 doc [7, 9]，检索 top-5 返回的 chunk 属于 doc {7, 3, 15, 20, 9}：
- 期望 {7, 9} 都在里面 → recall@5 = 2/2 = 1.0

为什么用 recall 而非 precision？因为 RAG 里**漏掉正确文档**（该召回没召回）比**多召回无关文档**（rerank 还能再筛）更致命。recall 优先。

```python
def recall_at_k(retrieved_doc_ids: list[int], expected: list[int], k: int) -> float:
    if not expected:           # 答不出用例，检索层不算 recall
        return None
    topk = set(retrieved_doc_ids[:k])
    hit = len(topk & set(expected))
    return hit / len(expected)
```

报告时通常同时看 recall@3 和 recall@5/@10——k 越大 recall 越高，但塞进 prompt 的噪声也越多，要权衡。

---

## 2. 生成层：引用准确率

**问题**：模型答案声称引用的文档（`cited_doc_ids`），是不是真的支撑了答案、且在期望范围内。

```
引用准确率 = 引用正确的用例数 / 总用例数
```

简化判定：模型引用的文档 ∩ 期望文档 非空，且没有引用明显无关的：

```python
def citation_correct(cited: list[int], expected: list[int]) -> bool:
    if not expected:           # 答不出用例不该有引用
        return len(cited) == 0
    return len(set(cited) & set(expected)) > 0
```

更严格的版本可以用 LLM-as-judge 判断「每条引用是否真的支撑了对应论断」（详见 [04 手册 LLM 评估](/docs/04-prompt-engineering/01-foundations/05-eval-first)），但起步用上面的集合判定够了。

---

## 3. 生成层：兜底正确率（企业知识库的命门）

**问题**：该说「没找到」的时候，有没有老实说，而不是编。

```
兜底正确率 = 在「答不出」用例上正确返回 found=false 的比例
```

```python
def fallback_correct(answer_found: bool, expected: list[int]) -> bool:
    # 期望为空 = 本就该答不出
    should_be_unfound = (len(expected) == 0)
    return answer_found == (not should_be_unfound)
```

这个指标对企业知识库**权重最高**——编答案是事故。哪怕 recall 再高，如果模型在没依据时硬编，系统就不可信。上线 gate 里这个指标要卡死（详见 [10-production/06-launch-checklist](../10-production/06-launch-checklist.md)）。

---

## 4. 一张指标全景表

| 层 | 指标 | 衡量什么 | 目标方向 | 权重 |
|----|------|----------|----------|------|
| 检索 | recall@5 | 该召回的有没有召回 | 越高越好 | 高 |
| 检索 | recall@3 | 精排后头部质量 | 越高越好 | 中 |
| 生成 | 引用准确率 | 答案是否忠于来源 | 越高越好 | 高 |
| 生成 | 兜底正确率 | 没依据时是否承认 | 接近 1.0 | **最高** |
| 生成 | 答案要点覆盖 | 关键信息是否答全 | 越高越好 | 中 |
| 系统 | 越权召回数 | 是否检索到无权文档 | **必须 0** | 红线 |

最后一行「越权召回数」是**红线指标**——不是「越低越好」，而是「必须为 0」。详见第 08 章。

---

## 5. 把指标聚合成一个「记分牌」

每次评估跑完，输出一个固定格式的记分牌，方便对比：

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

每次改动后跑一遍，和上次对比。涨了留、跌了查。下一篇给出完整的评估脚本。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只看一个综合分 | 掩盖了是哪层的问题 | 分层报告 |
| 用 precision 当主指标 | 漏召回被忽视 | RAG 优先看 recall |
| 兜底正确率不单列 | 编答案问题被平均掉 | 单独且高权重 |
| 越权召回当普通指标 | 安全问题被容忍 | 设为必须为 0 的红线 |
| k 固定只看一个值 | 看不到召回/噪声权衡 | recall@3 和 @5 都看 |

---

## 下一步

把这些指标实现成一个能一键跑的评估脚本：

→ [04-eval-script](./04-eval-script.md)
