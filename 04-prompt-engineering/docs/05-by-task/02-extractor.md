# PE By-Task 02：信息抽取（Extractor）

> **一句话**：把非结构化文本变成结构化 JSON——发票、名片、合同、邮件、用户反馈。关键是 **schema 设计 + null 处理 + 引用源**。

---

## 1. 任务特征

- 输入：自由文本（短信 / 邮件 / 表单 / PDF / 图片 OCR 结果）
- 输出：JSON 含 N 个字段（可嵌套）
- 评测：每字段 accuracy + 整体一致率
- 关键挑战：字段缺失、格式不一、含噪声

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

## 3. Null 处理（最重要的设计点）

抽取最大坑：**模型不愿意填 null，倾向猜**。

```
输入: "今天买了点东西 50 块"
       ↓
错误输出: {"vendor": "便利店", ...}  ← vendor 不在输入里！
正确输出: {"vendor": null, "amount": 50, ...}
```

prompt 必须**反复**强调：

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

## 4. 引用源（让幻觉可定位）

要求模型每个字段标"源句"：

```python
class Field(BaseModel):
    value: str | None
    source: str | None  # 原文出处片段


class Invoice(BaseModel):
    vendor: Field
    amount: Field
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

后处理可校验 `source` 是否真的在原文中——不在 = 幻觉。

---

## 5. 嵌套 schema

复杂数据用嵌套：

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

structured output API 支持深层嵌套，但 **>3 层** 准确率下降。深嵌套考虑拆分。

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

## 7. 抽取 + 分类（组合任务）

很多任务同时要"抽取字段 + 判断分类"：

```python
class TicketSummary(BaseModel):
    issue: str           # 抽取
    severity: Literal["critical", "high", "medium", "low"]  # 分类
    customer_emotion: Literal["angry", "frustrated", "neutral", "positive"]  # 分类
    user_id: str | None  # 抽取
```

structured output 一次搞定。

---

## 8. 列表抽取（n + 1 个字段）

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

list 字段告诉模型可以"找多个" / "没找到就空列表"。

prompt 加：

```
attendees:
- 列出会议中提到的所有参与者
- 没提到任何人 → 空列表 []
- 不要重复
```

---

## 9. 图片 / PDF 抽取

参考 [04-advanced/04-multimodal.md](../04-advanced/04-multimodal.md)。

要点：
- 多模态 + structured output 一起用
- 模糊字段返回 null + 标注 `confidence`
- 关键字段加 `source_image_region`（坐标）便于人工核对

---

## 10. 完整 demo

```python
# demos/by_task/02_extractor_invoice.py
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()


class LineItem(BaseModel):
    name: str
    quantity: int
    unit_price: float


class Invoice(BaseModel):
    vendor: str
    invoice_date: str = Field(description="ISO YYYY-MM-DD")
    currency: Literal["CNY", "USD", "EUR", "JPY", "OTHER"]
    total_amount: float
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
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
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
| **不允许 null** | 模型硬编 |
| **没引用源字段** | 幻觉无法定位 |
| **嵌套太深** | 拆分 schema |
| **list 字段没说"可空"** | 模型放硬塞 |
| **日期 / 金额格式不一** | 强制 ISO + 后处理验证 |
| **关键字段没 retry** | parse 失败立即降级 |
| **不评每字段独立 accuracy** | 总通过率掩盖某字段差 |

---

## 12. 下一步

- 📖 文本生成 → [03-generator.md](./03-generator.md)
- 📖 总结 → [04-summarizer.md](./04-summarizer.md)
- 📖 多模态抽取 → [04-advanced/04-multimodal.md](../04-advanced/04-multimodal.md)
- 📖 structured output 深入 → [03-techniques/05-structured-output.md](../03-techniques/05-structured-output.md)
