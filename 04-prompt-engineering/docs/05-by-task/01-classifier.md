# PE By-Task 01：分类器（Classifier）

> 把一段文本归到 N 个固定类别之一，是 LLM 在业务里最常接到的活。这篇给出一套能直接上生产的分类器 prompt 模板，并逐个讲清模板里每个设计点为什么这样定。

---

## 1. 任务特征

- 输入：自由文本
- 输出：单一类别名（或多标签）+ confidence
- 类别数：常见 2-20 个，超过 30 个就该考虑嵌入检索
- 评测：accuracy / F1 / per-class recall

---

## 2. 标准模板

```python
SYSTEM = """你是<领域>分类器。

任务：把用户输入分到以下类别**之一**：
{enum_list_with_descriptions}

输出格式（JSON）：
{
  "category": "<上述 enum 之一>",
  "confidence": 0.0-1.0,
  "reasoning": "<不超过 30 字>"
}

约束：
- category 必须是 enum 之一
- 不确定时 confidence < 0.5
- 输入为空 / 无关 / 乱码 → 用 "other" 兜底类
"""
```

**enum 一定要带描述。** 只列类别名的话，模型很容易把相近的类混在一起：

```
❌ "bug / feature / complaint"
✅ "
- bug: 用户报告软件错误、闪退、不工作
- feature: 用户提出新功能 / 改进建议
- complaint: 抱怨服务态度 / 产品体验（不含具体 bug）
"
```

---

## 3. 多标签 vs 单标签

| 类型 | prompt 写法 | 输出 |
|------|------------|------|
| 互斥（单标签） | "分到一个类别" | `category: enum` |
| 可多标 | "标注所有适用的类别" | `categories: [enum, ...]` |
| 主类 + 副类 | "主类一个，副类任意" | `primary, secondary[]` |

业务方自己也说不清该单标还是多标时，我会选"主类 + 副类"，它比单标签宽容。

---

## 4. confidence 设计

confidence 字段的用处是给下游一个分流信号，不必把它当成模型真实的把握程度：

```python
# 后处理: confidence 阈值路由
if result["confidence"] < 0.5:
    return "low_confidence_route"  # 转人工 / 二次确认
elif result["confidence"] < 0.8:
    return "human_review_queue"
else:
    return "auto_accept"
```

别把它当概率。它只是 LLM 自评出来的一个 0-1 分数，有锚点但没有校准。

想要更可靠的 confidence，可以用 self-consistency 的投票分布，见 [Self-Consistency 采样投票](../03-techniques/09-self-consistency.md)。

---

## 5. 兜底类别 other

每个分类器都应该有 `other`：

```
other: 不属于上述任何类别 / 输入无关 / 信息不足
```

没有它，模型遇到无关输入也会硬选一个类。

反过来也要盯着 other 的占比。如果 30% 的样本都落进 other，说明要么类别体系没设计好，要么 prompt 写得不够具体。

---

## 6. 类别 enum 设计原则

- 互斥：类别之间不重叠，真重叠就改用多标签
- 完备：常见输入都有归属，归不进去的应该落到 other，而不是悬空
- 可观察：只看输入就能判断，不依赖外部知识
- 业务对齐：分类要对业务有意义，不只是技术上好分
- 粒度均衡：避免一类占 90%、其余各占 1%

类别定完后先拿真实数据让人试标一遍，标注者之间一致率超过 80% 再上线。连人都意见不一的类别，没理由指望模型分得准。

---

## 7. 多语言分类

```
系统：用户反馈可能是中英文混合。

任务：分类。

注意：
- 不要按语言分类（中文 / 英文不应是类别）
- 反讽多见于中文（"棒棒的，再也不用了"）— 按真实意图归
- emoji 是情感信号但不决定类别
```

---

## 8. 长文本分类

超过 2k 字的文本有两种处理办法。

### 8.1 截断 + 分类
取前 N 字，或头尾各 N 字。做法简单，代价是中间的信息可能漏掉。

### 8.2 分段 + 聚合
切成 N 段，每段单独分类，再聚合（多数票或加权投票）。

```python
def classify_long(text: str, chunk_size: int = 1500) -> dict:
    chunks = split_chunks(text, chunk_size)
    per_chunk = [classify(c) for c in chunks]
    # 多数票
    counter = Counter(c["category"] for c in per_chunk)
    return {"category": counter.most_common(1)[0][0]}
```

---

## 9. 用 structured output 锁死 enum

最稳的写法是让 API 按 schema 强制输出：

```python
from typing import Literal
from pydantic import BaseModel, Field

class Classification(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise", "question", "billing", "account", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=50)

# OpenAI 强制 schema
resp = client.chat.completions.parse(
    model="gpt-6-luna",
    response_format=Classification,
    messages=[...],
)
```

这样类别名就不可能拼错或越界，比在 prompt 里反复强调可靠得多。细节见 [结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)。

---

## 10. 完整 demo

```python
import json
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()


class Result(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise", "question", "billing", "account", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=50)


SYSTEM = """你是客服反馈分类师。

类别（互斥）：
- bug: 报告软件错误（闪退、加载失败、功能不工作）
- feature: 请求新功能或改进
- complaint: 抱怨服务 / 体验，但非具体 bug
- praise: 好评 / 满意表达
- question: 使用问题、求助
- billing: 账单 / 支付 / 退款
- account: 登录 / 密码 / 账号
- other: 不属上述（含空 / 乱码 / 无关）

约束：
- 反讽（"挺好的，再也不用了"）按真实意图归
- emoji / 错别字不影响分类
- 不确定 → confidence < 0.5

输出 JSON 含 category, confidence, reasoning。
"""


def classify(text: str) -> Result:
    resp = client.chat.completions.parse(
        model="gpt-6-luna",
        response_format=Result,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text or "(empty)"},
        ],
    )
    return resp.choices[0].message.parsed


if __name__ == "__main__":
    TEST = [
        "App 一打开就闪退",
        "希望加深色模式",
        "客服爱答不理的",
        "好用，五星",
        "怎么改密码",
        "退款怎么操作",
        "登录不上",
        "草莓蛋糕的做法",
        "",
        "用得真好，再也不会推荐给朋友",  # 反讽
    ]
    for t in TEST:
        r = classify(t)
        print(f"{r.category:12s} (conf={r.confidence:.2f}) {t}")
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| enum 只列名、没描述 | 给每个类补一句说明 |
| 没有 other 兜底 | 模型会把无关输入硬归到某一类 |
| 类别重叠 | 一条样本归两类都说得通，怎么分都算对，评测失去意义 |
| 真实分布失衡 | 线上 90% 是 bug 时，评测集要有一部分按线上比例抽样来估整体准确率，同时给小类补够样本，看 per-class recall |
| 不用 structured output | 类别名偶尔拼错或自创 |
| 把 confidence 当概率 | 它是模型自报的 0-1 分，做阈值路由可以，当精确概率不行 |
| 需要多标签却强制单标 | 一条反馈同时含 bug 和 feature 时会丢信息 |

---

## 12. 下一步

- [信息抽取（Extractor）](./02-extractor.md)
- [文本生成（Generator）](./03-generator.md)
- [LLM-as-Judge](./05-judge.md)
- 实战：[从需求到一个分类器（完整闭环）](../08-practice/01-build-classifier.md)
