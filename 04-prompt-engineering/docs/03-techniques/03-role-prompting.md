# PE Technique 03：Role Prompting 与角色边界

> 给模型一个具体的身份、经验和边界，比一句「你是有用的助手」效果好得多。不过角色有它的限度：它改变的是说话方式，不会让模型多出它本来没有的能力，一次塞好几个角色还会适得其反。

---

## 1. 角色为什么管用

模型在训练数据里见过大量「身份、资历」和「行文风格」配在一起的文本：

```
"我是资深心脏病学专家" → 后面接的内容偏严谨、专业、含术语
"我是 5 岁小朋友" → 后面接的内容简单、直接、用比喻
"我是 senior Python engineer" → 后面接的代码更地道、考虑边界
```

指定一个明确的角色，相当于把模型引向训练数据里对应的那一部分分布，风格、用词、推理方式都会跟着变。

---

## 2. 一个 role 由什么组成

```
你是 <类型>，<经验/资历>，专长 <领域>。<说话风格 / 边界>。
```

例如：

```
你是资深 Python 后端工程师，10 年经验，擅长 web 框架（FastAPI / Django）和分布式系统。
你说话简洁直接，会指出代码的潜在 bug 和性能问题。不会写过度工程化的代码。
```

拆开来是四个要素：

| 要素 | 例子 |
|------|------|
| 类型 | Python 工程师 / 律师 / 医生 / 翻译 / 编辑 |
| 经验 | "10 年经验" / "刚毕业新人" / "高级专家" |
| 专长 | "web 后端" / "心脏科" / "民法" |
| 风格 / 边界 | "简洁直接" / "不出医疗诊断" / "拒绝色情内容" |

风格和边界最常被漏掉，可角色起作用主要靠的就是这两项。

---

## 3. 弱 role 和强 role

### 弱 role
```
你是有用的助手。
```

模型会用默认的中性风格作答：啰嗦、平淡，像教科书。

### 强 role
```
你是 Stripe 的资深 staff engineer，专注支付系统。
你说话简洁直接，给建议时给出 trade-off 而非单一答案。
熟悉 PCI 合规、幂等性、分布式事务等关键约束。
```

输出会带上 staff engineer 的口吻：简洁，讲技术细节，会摆出取舍。

---

## 4. 角色的边界约定

Role 除了定语气，也约定了能做什么、不能做什么：

```
你是法律咨询助手。

边界:
- 提供一般法律信息，不提供具体法律建议
- 涉及具体案件，建议用户咨询执业律师
- 不评论正在进行的司法案件
- 不就 user 的具体行为是否违法给"是 / 否"判断
```

边界写清楚，模型在不该做的时候会主动拒绝。

---

## 5. 反模式：Role Stacking

```
❌ "你是世界最聪明的 AI，同时是 senior Python engineer + 顶级数学家 + 普利策诗人 + ..."
```

这样写，模型不知道此刻该用哪个角色的语气，输出会在几种风格之间摇摆。「世界最聪明」这类自夸也没有意义，反倒容易让模型说大话。

一次只给一个明确的角色。确实需要多种能力时，拆成多个 sub-agent。

---

## 6. 反模式：用 role 弥补能力

```
❌ "你是医学博士，请诊断..."
   → 模型不是真医生，只是说话像医生，给出的诊断同样不可靠
```

**角色能改变风格和关注点，补不上能力。** 让模型扮演医生，它不会因此多出医生的知识和判断力，只会让错误的回答听起来更可信，也就更危险。

更稳妥的做法分两种情况：确实需要医学判断，就用 RAG 检索真实文献；不能给医学判断，就在边界里写明「不做诊断，建议就医」。

---

## 7. 用角色定风格

控制输出风格，角色比「请用简洁的方式回答」好用得多：

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

让这两个角色各写一篇「GraphQL vs REST」，出来的文章风格会完全不同。

---

## 8. 三家 API 差异

### Anthropic
推荐把 role 整个放进 system：

```python
client.messages.create(
    model="claude-sonnet-5-5",
    system="你是 Stripe 文档团队...",  # ← role 在这
    messages=[{"role": "user", "content": "解释一下 idempotency key"}],
)
```

### OpenAI
官方现在推荐用 developer message 承载应用规则，优先级高于 user。旧的 `system` 还能用，但新代码不再推荐：

```python
client.chat.completions.create(
    model="gpt-6.1-terra",
    messages=[
        {"role": "developer", "content": "你是 Stripe 文档团队..."},
        {"role": "user", "content": "..."},
    ],
)
# Responses API 里等价写法：client.responses.create(model=..., instructions="你是 ...", input="...")
```

### Gemini

```python
client.models.generate_content(
    model="gemini-3.8-flash",
    contents="...",
    config={"system_instruction": "你是 ..."},
)
```

---

## 9. 对话型应用里的 persona

虚拟伴侣、游戏 NPC、客服 bot 这类聊天产品，需要比 role 更具体的 persona：

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

persona 比 role 细，人格细节更多。但它依然要守边界，尤其是身份问题：被问到是不是 AI 时必须如实回答。

---

## 10. demo：对比不同 role 的效果

```python
"""同一个问题，四个不同 role"""
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
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=role,
        messages=[{"role": "user", "content": QUESTION}],
    )
    print(resp.content[-1].text)
```

4 个版本的输出在语气、用词、长度上会差得很远。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 只写「有用的助手」 | 太弱，补上经验、专长、风格 |
| Role stacking | 拆成多个 sub-agent |
| 指望角色带来能力 | 角色改变风格和关注点，补不上缺的知识 |
| 没写边界 | 加一份「不做什么」清单 |
| persona 写得太死 | 给性格留点变化的余地 |
| 角色和任务不匹配 | 比如角色是诗人，任务却是查 SQL |

---

## 12. 下一步

- [Decomposition 任务拆解](./04-decomposition.md)
- [边界条件与 Refusal 行为塑造](./07-boundaries-refusal.md)
- [Prompt Injection 防御](../04-advanced/06-injection-defense.md)：防止用户覆盖你设定的角色

## 参考资料

- Anthropic "Give Claude a role": https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/system-prompts
- OpenAI developer message: https://platform.openai.com/docs/guides/text-generation
