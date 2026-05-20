# PE By-Task 03：文本生成（Generator）

> **一句话**：生成任务（营销文案、邮件、代码、产品描述）的关键不是"让模型写得好"，而是**约束多样性 + 锚定风格**。本篇给文案、邮件、代码三类典型模板。

---

## 1. 任务特征

- 输入：通常是"主题 / 元数据 / 输入数据"
- 输出：自由文本（200-5000 字）
- 评测：LLM-as-judge + 人工抽查（主观）
- 关键挑战：风格不稳定、长度漂移、和品牌 voice 不符

---

## 2. 通用模板

```
你是<具体身份>，专门写<内容类型>。

风格规范：
- <风格 1>
- <风格 2>
- <风格 3>

要避免：
- <反例 1>
- <反例 2>

任务：根据用户输入，生成<内容>。

输出约束：
- 长度: <字数范围>
- 格式: <markdown / 纯文本 / HTML>
- 语言: <中 / 英 / 跟随输入>
```

关键是 **role 锚定具体风格 + 反例清单**——参考 [03-techniques/03-role-prompting.md](../03-techniques/03-role-prompting.md)。

---

## 3. 案例 1：营销文案

```python
SYSTEM = """你是品牌"小红"的资深文案，5 年经验。

风格：
- 短句优先（< 15 字）
- 用第二人称"你"
- 一段一个意思
- 结尾必有 call-to-action

避免：
- "震惊" / "必看" / "点击" 等标题党
- "我们" / "本公司" 自称
- 长复合句
- 太多 emoji（每段 < 1 个）

输出：
- 标题: 8-15 字
- 正文: 50-150 字（分 2-3 段）
- CTA: 一句话

返回 JSON {title, body, cta}
"""
```

加 2-3 个 few-shot 示例固定风格（参考 [03-techniques/06-examples-design.md](../03-techniques/06-examples-design.md)）。

---

## 4. 案例 2：邮件生成

```python
SYSTEM = """你是商务邮件助手。

风格：
- 简洁直接，无废话
- 用 "Hi/Hello {name}" 开头
- 主语主动（avoid passive）
- 每段 < 50 字
- 结尾用 "Best, ..."

避免：
- 过度道歉
- 多余客套
- 复杂从句

输入：发送目的 + 关键信息

输出：完整邮件含 subject / body
"""
```

---

## 5. 案例 3：代码生成

```python
SYSTEM = """你是资深 Python 工程师。

风格：
- 简洁 idiomatic Python（no Java-style）
- 类型注解完整
- docstring 一行（简短）
- 单一职责函数
- 不写 try/except 兜底 except Exception

不要：
- 过度抽象
- 没必要的 class
- 防御性代码（用户输入信任）
- 注释明显的事

输入：功能描述

输出：
- 直接代码（```python fence 包）
- 必要时一句话解释
"""
```

代码生成特殊：
- 严格控制 imports（不要冒出 import 全文件）
- 让模型自己 explain 关键决策
- 对接 review 工具（lint / type check）做后处理

---

## 6. 控制长度

```
"长度 200-500 字"  ← 模糊
"3-5 段，每段 50-100 字"  ← 精确
"恰好 3 个 bullet，每个不超过 30 字"  ← 严格
```

越精确越稳。但太严格会牺牲质量——找平衡。

---

## 7. 控制语气 / Voice

让 voice 一致最有效的方法：**给品牌 voice guide + 3 个真实样本**：

```
[品牌 voice guide]
我们的品牌是 "Notion"：
- 友好但专业
- 用 "you" 直接对话
- 比喻 / 类比常用
- 避免技术 jargon
- 不卑微也不傲慢

[真实样本]
样本 1: "Move your databases to Notion. It's easier than it sounds."
样本 2: "..."
样本 3: "..."

现在按此 voice 写：{topic}
```

3 个真实样本 + voice guide 远比 "请用友好语气" 管用。

---

## 8. 多样性 vs 一致性

| 场景 | 要 |
|------|-----|
| Brand voice | 一致性 → temperature=0.3 |
| 多角度 brainstorming | 多样性 → temperature=0.8 + 要求"3 个不同角度" |
| A/B 测试 variants | 多样性 → 同 prompt 跑 N 次 temperature=0.7 |

---

## 9. 生成 + critique 闭环

参考 [03-techniques/08-self-critique.md](../03-techniques/08-self-critique.md)：

```python
draft = generate(...)
critique = ask_judge(draft, criteria)
if critique["issues"]:
    final = refine(draft, critique)
else:
    final = draft
```

对高质量需求（推送给客户 / 上线营销）必加。

---

## 10. 完整 demo

```python
# demos/by_task/03_generator_marketing.py
from pydantic import BaseModel
from openai import OpenAI

client = OpenAI()


class Ad(BaseModel):
    title: str
    body: str
    cta: str


SYSTEM = """你是品牌「小蓝」的资深文案，专写 SaaS 推广。

风格：
- 短句（每句 < 15 字）
- 第二人称 "你"
- 一段一个意思
- 结尾一定有具体 CTA

避免：
- 标题党词："震惊"、"必看"、"点击"
- 自夸："最好"、"行业第一"
- emoji 多于 1 个 / 段

输出 JSON {title, body, cta}
- title: 8-15 字
- body: 50-150 字（2-3 段）
- cta: 一句话
"""


def generate_ad(product: str, audience: str, hook: str) -> Ad:
    user = f"""产品: {product}
目标人群: {audience}
卖点: {hook}"""
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        temperature=0.5,
        response_format=Ad,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.parsed


if __name__ == "__main__":
    print(generate_ad(
        product="一款 AI 笔记应用",
        audience="设计师 / 创意工作者",
        hook="语音转文字 + 自动整理",
    ))
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **role 太抽象** | "友好" → "用 you / 短句 / 比喻" |
| **没反例** | 加"避免"清单 |
| **temperature=0** | 输出 robot；调到 0.3-0.7 |
| **长度只说"短"** | 给精确字数范围 |
| **没 few-shot** | 风格漂；加 2-3 个真实样本 |
| **生成完不 review** | 高价值任务加 critique |
| **同一 prompt 跑多次差异大** | 多样性 vs 一致性没设计 |

---

## 12. 下一步

- 📖 总结 → [04-summarizer.md](./04-summarizer.md)
- 📖 LLM-as-judge → [05-judge.md](./05-judge.md)
- 📖 self-critique → [03-techniques/08-self-critique.md](../03-techniques/08-self-critique.md)
