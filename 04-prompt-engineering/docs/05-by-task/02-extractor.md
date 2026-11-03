# PE By-Task 02：信息抽取（Extractor）

> 发票、名片、合同、邮件、用户反馈，这些非结构化文本最后往往都要变成一份结构化 JSON。抽取任务做得好不好，主要看三件事：schema 怎么设计、缺失字段怎么填 null、每个值能不能追溯到原文。

---

## 1. 任务特征

- 输入：自由文本（短信 / 邮件 / 表单 / PDF / 图片 OCR 结果）
- 输出：含 N 个字段的 JSON，可嵌套
- 评测：每个字段的 accuracy，加上整体一致率
- 难点：字段缺失、格式五花八门、夹杂噪声

---

## 2. 标准模板

```python
SCHEMA = {
    "vendor": "供应商名称",
    "amount": "金额（数字）",
    "currency": "货币（CNY / USD / EUR）",
    "date": "日期（YYYY-MM-DD）",
    "tax_id": "税号（可选）",
}

SYSTEM = """你是结构化信息抽取系统。

任务：从用户输入提取以下字段，返回 JSON：
- vendor (string): 供应商名称
- amount (number): 金额，去掉货币符号
- currency (string): "CNY" / "USD" / "EUR"
- date (string): ISO 格式 YYYY-MM-DD
- tax_id (string | null): 税号

约束：
- 找不到的字段填 null，**不要猜**
- amount 必须是数字（不带符号 / 不带"元"等单位）
- date 若原文是 "2026/5/20" 转成 "2026-05-20"
- vendor 保持原大小写

只返回 JSON，不要任何解释。
"""
```

---

## 3. Null 处理

这是抽取任务里最重要的设计点。模型天生不愿意填 null，宁可猜一个值。

```
输入: "今天买了点东西 50 块"
       ↓
错误输出: {"vendor": "便利店", ...}  ← vendor 不在输入里！
正确输出: {"vendor": null, "amount": 50, ...}
```

所以 prompt 里要把这条规则写得很重，最好配一个反例：

```
重要：
- 输入里**没有**的字段，**必须**填 null
- 不要根据上下文 / 常识猜测缺失字段
- 不要"为了完整"硬编内容

错误示例：
输入 "买东西 50 元"
错误输出: {"vendor": "未指明商家"}  ← 不要这样
正确输出: {"vendor": null}
```

---

## 4. 引用源：让幻觉可以定位

要求模型给每个字段附上它在原文里的出处：

```python
class SourcedValue(BaseModel):   # 别叫 Field，会和 pydantic.Field 撞名
    value: str | float | None
    source: str | None  # 原文出处片段


class Invoice(BaseModel):
    vendor: SourcedValue
    amount: SourcedValue
    ...
```

输出：

```json
{
  "vendor": {"value": "全家便利店", "source": "商家: 全家便利店"},
  "amount": {"value": 50, "source": "金额: ¥50.00"},
  "tax_id": {"value": null, "source": null}
}
```

后处理时检查 `source` 是否真的出现在原文里。找不到，就说明这个值是编的。

---

## 5. 嵌套 schema

数据复杂时用嵌套结构：

```python
class LineItem(BaseModel):
    name: str
    qty: int
    price: float


class Invoice(BaseModel):
    vendor: str
    items: list[LineItem]
    total: float
```

structured output API 支持深层嵌套，但超过 3 层准确率会下降，这时可以考虑拆成几次抽取。

---

## 6. 多语言 / 混合输入

```
任务：从中英文混合的发票图片 OCR 文本中抽取。

注意：
- 字段名固定英文（不要翻译）
- 字段值保留原语言
- 日期 / 数字格式统一 ISO
```

---

## 7. 抽取 + 分类

很多任务既要抽字段，又要顺手做个判断：

```python
class TicketSummary(BaseModel):
    issue: str           # 抽取
    severity: Literal["critical", "high", "medium", "low"]  # 分类
    customer_emotion: Literal["angry", "frustrated", "neutral", "positive"]  # 分类
    user_id: str | None  # 抽取
```

用 structured output 一次调用就能同时拿到。

---

## 8. 列表抽取

```python
class Person(BaseModel):
    name: str
    email: str | None
    role: str | None


class MeetingNotes(BaseModel):
    attendees: list[Person]
    action_items: list[str]
    decisions: list[str]
```

list 类型本身就在告诉模型"可以有多个，也可以一个都没有"。prompt 里再补几句：

```
attendees:
- 列出会议中提到的所有参与者
- 没提到任何人 → 空列表 []
- 不要重复
```

---

## 9. 图片 / PDF 抽取

详见 [多模态 Prompting（图片 / 文档 / 视频）](../04-advanced/04-multimodal.md)。几个要点：

- 多模态输入和 structured output 一起用
- 看不清的字段返回 null，并附上 `confidence`
- 关键字段加 `source_image_region`（坐标），方便人工核对

---

## 10. 完整 demo

```python
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()


class LineItem(BaseModel):
    name: str
    quantity: int
    unit_price: float


class Invoice(BaseModel):
    # 允许缺失的字段都要声明成可空，否则 strict schema 会逼模型编一个值
    vendor: str | None
    invoice_date: str | None = Field(description="ISO YYYY-MM-DD")
    currency: Literal["CNY", "USD", "EUR", "JPY", "OTHER"] | None
    total_amount: float | None
    tax_id: str | None = None
    line_items: list[LineItem] = []


SYSTEM = """你是发票信息抽取系统。

从输入文本中提取 Invoice 数据。

规则：
1. 找不到的字段填 null（line_items 找不到填空列表）
2. amount 必须数字（去掉货币符号）
3. date 转 ISO YYYY-MM-DD
4. 不要猜测缺失字段
"""

INVOICES = [
    """发票
商家: 全家便利店
日期: 2026/05/20
明细:
- 矿泉水 x2  ¥4.00
- 三明治 x1  ¥10.00
合计: ¥14.00""",

    """Invoice from AWS, billed on 2026-04-01
Total: $250.00
Service: EC2, S3""",
    
    "今天买东西花了 50 块",  # 大量字段缺失
]

for text in INVOICES:
    print(f"\n=== {text[:30]}... ===")
    resp = client.chat.completions.parse(
        model="gpt-6-luna",
        response_format=Invoice,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
    )
    print(resp.choices[0].message.parsed)
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 不允许 null | 模型会编一个值填上 |
| 没有引用源字段 | 出了幻觉也查不到是哪个字段 |
| 嵌套太深 | 拆分 schema |
| list 字段没说可以为空 | 模型会硬塞内容凑数 |
| 日期 / 金额格式不统一 | 强制 ISO，后处理再校验一遍 |
| 关键字段没有 retry | parse 一失败就直接降级了 |
| 不按字段单独算 accuracy | 总通过率会掩盖某个字段特别差 |

---

## 12. 下一步

- [文本生成（Generator）](./03-generator.md)
- [总结（Summarizer）](./04-summarizer.md)
- 多模态抽取：[多模态 Prompting（图片 / 文档 / 视频）](../04-advanced/04-multimodal.md)
- structured output 深入：[结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)
