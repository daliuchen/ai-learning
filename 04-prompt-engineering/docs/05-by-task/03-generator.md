# PE By-Task 03：文本生成（Generator）

> 营销文案、邮件、代码、产品描述都属于生成任务。这类任务难的不是让模型写得好，模型本来就写得不差；难的是把它的发挥收住：限制多样性，把风格钉在一个点上。这篇给出文案、邮件、代码三类常用模板。

---

## 1. 任务特征

- 输入：一般是主题、元数据或一份输入数据
- 输出：自由文本（200-5000 字）
- 评测：LLM-as-judge 加人工抽查，偏主观
- 难点：风格不稳、长度漂移、和品牌调性对不上

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

写好这个模板靠两样东西：把角色落到具体的风格描述上，再配一份"不要这样写"的反例清单。角色怎么写可以参考 [Role Prompting 与角色边界](../03-techniques/03-role-prompting.md)。

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

再加 2-3 个 few-shot 示例把风格固定下来，示例怎么挑见 [Few-shot 示例怎么设计](../03-techniques/06-examples-design.md)。

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

代码生成有几处和文案不一样：

- 严格控制 import，别让模型把用不到的库全导一遍
- 让模型对关键决策简短说明理由
- 输出后接 lint、类型检查这类工具做后处理

---

## 6. 控制长度

```
"长度 200-500 字"  ← 模糊
"3-5 段，每段 50-100 字"  ← 精确
"恰好 3 个 bullet，每个不超过 30 字"  ← 严格
```

描述越精确，长度越稳。但卡得太死会伤质量，需要在两者之间找个平衡。

---

## 7. 控制语气

想让语气保持一致，比较有效的办法是给一份品牌 voice guide，再附 3 个真实样本：

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

这比一句"请用友好的语气"管用得多。

---

## 8. 多样性 vs 一致性

| 场景 | 要 |
|------|-----|
| 品牌调性 | 一致性：3 个真实样本 + voice guide（能设 temperature 的模型可用 0.3） |
| 多角度头脑风暴 | 多样性：要求"给 3 个不同角度"（能设 temperature 的模型再调到 0.8） |
| A/B 测试的变体 | 多样性：同一个 prompt 跑 N 次 |

> Claude 新模型（4.7+）已不接受 temperature，多样性和一致性主要靠 prompt 设计来控制；Gemini 3+ 也建议保持默认值。

---

## 9. 生成 + critique 闭环

做法见 [Self-Critique（自我反思）](../03-techniques/08-self-critique.md)：

```python
draft = generate(...)
critique = ask_judge(draft, criteria)
if critique["issues"]:
    final = refine(draft, critique)
else:
    final = draft
```

要直接推给客户、或者要上线投放的内容，这一步不能省。

---

## 10. 完整 demo

```python
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
    resp = client.chat.completions.parse(
        model="gpt-6-luna",
        temperature=0.5,   # GPT-6 是否接受自定义 temperature 以模型页为准；不接受就删掉这行
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
| 角色描述太抽象 | 把"友好"拆成"用 you、短句、多用比喻" |
| 没有反例 | 加一份"避免"清单 |
| 一味追求确定性 | 输出像机器写的；在 prompt 里要求"换个说法"，能调 temperature 的模型调到 0.3-0.7 |
| 长度只说"短" | 给出精确的字数范围 |
| 没有 few-shot | 风格会漂；加 2-3 个真实样本 |
| 生成完不检查 | 高价值任务加一轮 critique |
| 同一 prompt 多次输出差异大 | 多样性和一致性没有事先想清楚要哪个 |

---

## 12. 下一步

- [总结（Summarizer）](./04-summarizer.md)
- [LLM-as-Judge](./05-judge.md)
- [Self-Critique（自我反思）](../03-techniques/08-self-critique.md)
