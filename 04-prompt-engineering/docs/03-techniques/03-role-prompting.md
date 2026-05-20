# PE Technique 03：Role Prompting —— 角色与边界

> **一句话**：给模型一个明确的"身份 + 经验 + 边界"——比"你是有用的助手"具体百倍——能极大提升输出质量。但 role prompting 不是万能：**角色不能给模型不会的能力**，且过度 stacking 角色会反噬。

---

## 1. 角色为什么管用

模型训练数据见过无数"resume / 资历 + 输出风格"的搭配：

```
"我是资深心脏病学专家" → 后面接的内容偏严谨、专业、含术语
"我是 5 岁小朋友" → 后面接的内容简单、直接、用比喻
"我是 senior Python engineer" → 后面接的代码更地道、考虑边界
```

给模型一个明确 role，等于**激活某一片训练数据分布**——风格、词汇、推理方式都跟着变。

---

## 2. 一个 role 的解剖

```
你是 <类型>，<经验/资历>，专长 <领域>。<说话风格 / 边界>。
```

具体例子：

```
你是资深 Python 后端工程师，10 年经验，擅长 web 框架（FastAPI / Django）和分布式系统。
你说话简洁直接，会指出代码的潜在 bug 和性能问题。不会写过度工程化的代码。
```

四个要素：

| 要素 | 例子 |
|------|------|
| **类型** | Python 工程师 / 律师 / 医生 / 翻译 / 编辑 |
| **经验** | "10 年经验" / "刚毕业新人" / "高级专家" |
| **专长** | "web 后端" / "心脏科" / "民法" |
| **风格 / 边界** | "简洁直接" / "不出医疗诊断" / "拒绝色情内容" |

风格 + 边界**经常被忽略**——但它们才是 role 真正起作用的部分。

---

## 3. 弱 vs 强 role 对比

### 弱 role
```
你是有用的助手。
```

模型默认"中性"输出风格——啰嗦、平淡、教科书式。

### 强 role
```
你是 Stripe 的资深 staff engineer，专注支付系统。
你说话简洁直接，给建议时给出 trade-off 而非单一答案。
熟悉 PCI 合规、幂等性、分布式事务等关键约束。
```

输出会带 staff engineer 的语气——简洁、技术细节、有 trade-off。

---

## 4. 角色的边界约定

Role 不只是"语气"——也是**能做什么 / 不能做什么**的约定：

```
你是法律咨询助手。

边界:
- 提供一般法律信息，不提供具体法律建议
- 涉及具体案件，建议用户咨询执业律师
- 不评论正在进行的司法案件
- 不就 user 的具体行为是否违法给"是 / 否"判断
```

明确边界让模型在不该做的时候主动 refuse。

---

## 5. 反模式：Role Stacking

```
❌ "你是世界最聪明的 AI，同时是 senior Python engineer + 顶级数学家 + 普利策诗人 + ..."
```

问题：

- 模型不知道现在该用哪个 role 的语气
- 输出在多种风格间摇摆
- "世界最聪明" 这种自夸描述无意义，反而触发模型说大话

**正解**：一次一个明确 role。需要多个能力时用 sub-agent 拆分。

---

## 6. 反模式：用 role 修补能力缺失

```
❌ "你是医学博士，请诊断..."
   → 模型不是真医生，只是说话像医生，给出的诊断同样不可靠
```

**Role 给"风格"，不给"能力"**。给模型扮演医生不会让它有医生的判断力——只会让错误回答听起来更可信、更危险。

正解：

- 真要医学判断 → RAG + 真实文献检索
- 不能给医学判断 → 在边界里明确"不能诊断，建议看医生"

---

## 7. 风格 anchoring 的力量

Role 能精确控制输出风格——比"请用简洁的方式"管用得多：

```python
ROLE_TECH_WRITER = """你是 Stripe 文档团队的资深技术写作工程师。
你写的文档:
- 短句优先，平均一句 < 20 词
- 代码示例先于解释
- 用 'You / Your' 视角而非 'we / our'
- 一定避免"easy" / "just" / "simply" 这类居高临下的词
- 列表用 '- ' 而非 numbered list（除非有顺序）
"""

ROLE_BLOG_WRITER = """你是 Hacker News 风格的技术博客作者。
你写的内容:
- 第一段用一个 hook（数字 / 反直觉的事实）
- 中间夹个人观点 + 论据
- 结尾不要总结，留一个开放问题
- 避免 listicle 风格
"""
```

两个 role 写同一个"GraphQL vs REST"的文章，风格会完全不同。

---

## 8. 三家 API 差异

### Anthropic
推荐 role 全部放 system：

```python
client.messages.create(
    model="claude-sonnet-4-6",
    system="你是 Stripe 文档团队...",  # ← role 在这
    messages=[{"role": "user", "content": "解释一下 idempotency key"}],
)
```

### OpenAI
推荐 system message 第一条：

```python
client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "你是 Stripe 文档团队..."},
        {"role": "user", "content": "..."},
    ],
)
```

GPT-5 后推荐用 **developer message** 而非 system（更高优先级）：

```python
messages=[
    {"role": "developer", "content": "..."},
    {"role": "user", "content": "..."},
]
```

### Gemini

```python
client.models.generate_content(
    model="gemini-2.0-flash",
    contents="...",
    config={"system_instruction": "你是 ..."},
)
```

---

## 9. 用 role + persona 控对话型应用

聊天产品（虚拟伴侣 / NPC / 客服 bot）需要 **persona**（更具体的角色）：

```
你是「小美」，25 岁，杭州人，做美妆电商运营。
性格：活泼、直接、会用网络流行语（但不油腻）。
说话习惯：
- 句子短，喜欢用 emoji 但每条 < 2 个
- 会引用最近热点（前提是不过期）
- 不知道的事直接说不知道，不编

边界：
- 不评论政治 / 宗教
- 用户问私事会礼貌引开
- 不冒充真人；被问"你是 AI 吗"时坦白说是 AI
```

persona 比 role 更细 + 含更多人格 detail。但**仍然不是"演员"**：边界要清楚，特别是身份认知。

---

## 10. demo：role 的效果对比

```python
# demos/techniques/03_role_compare.py
"""同一个问题，三个不同 role"""
import anthropic
client = anthropic.Anthropic()
QUESTION = "解释什么是 idempotency key"

ROLES = {
    "default": "你是 AI 助手。",
    "academic": "你是计算机科学博士，专注分布式系统理论。用严格的学术语言，会引用论文。",
    "stripe": "你是 Stripe 的 senior staff engineer，写过支付 SDK。说话直白，给真实代码示例，强调边界 case。",
    "5yo": "你是给 5 岁小朋友讲解计算机概念的老师。用最简单的比喻，没有专业术语。",
}

for name, role in ROLES.items():
    print(f"\n=== {name} ===")
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=role,
        messages=[{"role": "user", "content": QUESTION}],
    )
    print(resp.content[0].text)
```

预期：4 个版本的语气、词汇、长度差异极大。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **"有用的助手"** | 太弱，加经验 / 专长 / 风格 |
| **Role stacking** | 拆成多个 sub-agent |
| **用 role 假装能力** | Role 给风格，不给真能力 |
| **边界没写** | 加 "不做什么"清单 |
| **persona 太具体反而僵化** | 留些"性格变化"的余地 |
| **role 和实际任务不匹配** | 比如 role 是 "诗人"，任务是 "查 SQL" |

---

## 12. 下一步

- 📖 任务拆解 → [04-decomposition.md](./04-decomposition.md)
- 📖 边界与拒绝行为 → [07-boundaries-refusal.md](./07-boundaries-refusal.md)
- 📖 注入防御（防止用户 override role） → [04-advanced/06-injection-defense.md](../04-advanced/06-injection-defense.md)

## 参考资料

- Anthropic "Give Claude a role": https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts
- OpenAI developer message: https://platform.openai.com/docs/guides/text-generation
