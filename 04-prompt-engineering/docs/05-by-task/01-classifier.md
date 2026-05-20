# PE By-Task 01：分类器（Classifier）

> **一句话**：分类器是 LLM 最常见任务——把文本归到 N 个固定类别之一。本篇给一套**生产级分类器 prompt 模板** + 关键设计点。

---

## 1. 任务特征

- 输入：自由文本
- 输出：单一类别名（或多标签）+ confidence
- 类别数：常 2-20，超过 30 考虑用嵌入检索
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

**enum 必须含描述**——单纯列名 LLM 容易混：

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
| 互斥（单标签） | "分到**一个**类别" | `category: enum` |
| 可多标 | "标注**所有适用**的类别" | `categories: [enum, ...]` |
| 主类 + 副类 | "主类**一个**，副类**任意**" | `primary, secondary[]` |

业务上拿不准 → 用"主+副"，比单标签宽容。

---

## 4. confidence 设计

confidence 不是模型自报"我有多自信"——是**有用的下游信号**。建议：

```python
# 后处理: confidence 阈值路由
if result["confidence"] < 0.5:
    return "low_confidence_route"  # 转人工 / 二次确认
elif result["confidence"] < 0.8:
    return "human_review_queue"
else:
    return "auto_accept"
```

不要把 confidence 当 "概率"——它是 LLM 自评的 anchored 0-1 分。

更可靠的 confidence：用 self-consistency 投票分布（详 [03-techniques/09](../03-techniques/09-self-consistency.md)）。

---

## 5. 边界类别（"other"）的设计

每个分类器都该有 `other`：

```
other: 不属于上述任何类别 / 输入无关 / 信息不足
```

防止模型硬选某类。

**避免"other"用得过多**——如果 30% 都归 other，要么类别没设计好、要么 prompt 不够具体。

---

## 6. 类别 enum 设计原则

- **互斥**：类别之间不重叠（如果重叠考虑多标签）
- **完备**：常见输入都有归属（找不到归属的应该是 "other" 而不是没归属）
- **可观察**：能从输入直接判断（不要类别需要外部知识）
- **业务对齐**：不只是技术分类，要对业务有意义
- **粒度均衡**：避免一类 90%、其他 1% 各

设计完用真实数据**试评一遍**——人类标注者间一致率 > 80% 再上线。否则连人都不同意，模型怎么做？

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

长文本（> 2k 字）有两种处理：

### 8.1 截断 + 分类
取前 N 字 / 头尾各 N 字。简单但可能漏。

### 8.2 分段 + 聚合
切 N 段 → 每段分类 → 聚合（多数票 / 投票加权）。

```python
def classify_long(text: str, chunk_size: int = 1500) -> dict:
    chunks = split_chunks(text, chunk_size)
    per_chunk = [classify(c) for c in chunks]
    # 多数票
    counter = Counter(c["category"] for c in per_chunk)
    return {"category": counter.most_common(1)[0][0]}
```

---

## 9. structured output 强制 enum

最稳的写法：

```python
from typing import Literal
from pydantic import BaseModel, Field

class Classification(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise", "question", "billing", "account", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=50)

# OpenAI 强制 schema
resp = client.beta.chat.completions.parse(
    model="gpt-4o-mini",
    response_format=Classification,
    messages=[...],
)
```

枚举从此**保证**正确——比 prompt 描述强 10 倍。详 [03-techniques/05-structured-output.md](../03-techniques/05-structured-output.md)。

---

## 10. 完整 demo

```python
# demos/by_task/01_classifier.py
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
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
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
| **enum 单纯列名没描述** | 给每个类一句话 |
| **没 "other" 兜底** | 模型硬归错类 |
| **类别重叠** | 一条样本两类都合理，分类器哪个都对 |
| **真实分布失衡** | 90% bug，evalset 也要这个分布 |
| **不用 structured output** | enum 名拼写有时跑偏 |
| **confidence 当概率信** | 它是 LLM 自报 0-1，做阈值路由可以，做精确概率不行 |
| **没多标签场景却强制单标** | 同时含 bug + feature 时丢信息 |

---

## 12. 下一步

- 📖 信息抽取 → [02-extractor.md](./02-extractor.md)
- 📖 文本生成 → [03-generator.md](./03-generator.md)
- 📖 LLM-as-judge → [05-judge.md](./05-judge.md)
- 🛠️ 实战：从 0 到分类器 → [08-practice/01-build-classifier.md](../08-practice/01-build-classifier.md)
