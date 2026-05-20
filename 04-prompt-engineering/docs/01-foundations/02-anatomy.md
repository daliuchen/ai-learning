# PE 02：一条 Prompt 的解剖

> **一句话**：一条生产级 prompt 不是"一段话"，而是**七个部件**：身份（role）、上下文（context）、任务（task）、约束（constraints）、示例（examples）、输出格式（output format）、收尾（reinforcement）。每个部件位置和长度都有讲究——这一篇把它们一次性拆开。

---

## 1. 看一个失败的 prompt

很多团队的"prompt"长这样：

```
"你是有用的助手，请总结这篇文章。{article}"
```

问题：
- 没说"总结"是几句话还是几段
- 没指定语言（英文文章要中文总结？）
- 没限制输出格式（要 bullet 还是段落？）
- 没说总结目的（给老板看 vs 给同事看）
- 没失败兜底（文章是空的怎么办？）

它会"能跑"，但跑出来的东西**不稳定**——同一篇文章问 10 次会给 10 种结构和长度。

---

## 2. 一条完整 prompt 的七个部件

下面给出一个**生产级模板**，按"应该出现的位置"排列：

```
┌─────────────────────────────────────────────────┐
│ [1] 身份 / Role        ← 决定语气、视角、专业领域 │
├─────────────────────────────────────────────────┤
│ [2] 上下文 / Context   ← 模型需要的背景信息       │
├─────────────────────────────────────────────────┤
│ [3] 任务 / Task        ← 一句话明确目标          │
├─────────────────────────────────────────────────┤
│ [4] 约束 / Constraints ← 必须 / 禁止的行为      │
├─────────────────────────────────────────────────┤
│ [5] 示例 / Examples    ← few-shot               │
├─────────────────────────────────────────────────┤
│ [6] 输出格式 / Format  ← JSON / XML / 自由文本   │
├─────────────────────────────────────────────────┤
│ [7] 收尾 / Reinforce   ← 强调最重要的约束        │
└─────────────────────────────────────────────────┘
```

放对位置 + 写对粒度 = 一份能用的 prompt。

---

## 3. 七部件 × 写法详解

### [1] 身份（Role）

**作用**：让模型选择"语气基座"——医生 vs 律师 vs 编程助手 vs 客服。

```
你是一位资深 Python 工程师，10 年经验，擅长 web 后端。
```

**好习惯**：
- 加经验年限（"10 年"）→ 让模型更倾向给"老司机"的答案
- 加领域（"web 后端"）→ 缩小输出范围
- 一句话以内，不要膨胀成段

**避免**：
- "你是世界上最聪明的 AI" — 没意义，反而触发模型"自夸式"输出
- 太多 persona（你又是医生又是程序员又是诗人）

### [2] 上下文（Context）

**作用**：把模型需要的背景信息放进来——业务规则、上游数据、用户身份、时间。

```
## 业务背景
我们是 SaaS 公司，目标客户是中小企业。
用户当前等级：standard。VIP 用户才能用高级功能 X / Y。
当前时间：2026-05-20。
```

**写法**：
- 用标题（Markdown header）切块，模型对结构化输入识别更稳
- 时间敏感的任务**必给当前时间**，否则模型用训练截止日期推理会错
- 业务规则用条目化（"- ..." / "1. ..."）

**避免**：
- 上下文塞过期文档（模型会把过期事实当真）
- 一坨长文本不分段（attention 退化）

### [3] 任务（Task）

**作用**：一句话告诉模型"要做什么"。

```
任务：根据以下产品描述，提取产品名、价格、SKU 三个字段。
```

**好习惯**：
- 动词开头（"提取" / "总结" / "翻译" / "判断"）
- 一句话能讲清；讲不清的拆分成多步（详见 03-techniques/04-decomposition）

**避免**：
- 多任务混在一句（"提取并总结并翻译"）
- 任务和约束混在一起

### [4] 约束（Constraints）

**作用**：限制模型的行为边界。

```
约束：
- 价格必须包含货币符号
- SKU 必须保留原大小写
- 如果字段缺失，输出 null，不要瞎猜
- 不要返回除字段外的任何说明文字
```

**写法**：
- 用"必须 / 不要 / 如果...就..."句式
- 每条独立，bullet 化
- 最重要的约束放在**第一条**和**最后一条**（首尾效应）

**避免**：
- 用否定式列一大堆（"不要这样不要那样"），模型会被"那些不要"反向激活
- 过度约束（5 条以上要质疑必要性）

### [5] 示例（Examples / Few-shot）

**作用**：让模型 anchor 到具体格式与风格。

```
示例：

输入：MacBook Air M3，售价 ¥9,999.00，型号 MBA-M3-13-512G
输出：
{
  "name": "MacBook Air M3",
  "price": "¥9,999.00",
  "sku": "MBA-M3-13-512G"
}

输入：罗技 MX Master 3S 鼠标 ¥899
输出：
{
  "name": "罗技 MX Master 3S 鼠标",
  "price": "¥899",
  "sku": null
}
```

**好习惯**：
- 至少给 2-3 个示例（1 个不够，少了模型可能误以为是"必这样"）
- 涵盖 happy path + 一个 edge case（缺字段、特殊符号）
- 输入输出对齐风格

**避免**：
- 示例和任务对应不上（用别的领域示例蒙混过关）
- 示例多到 10+ 条（详见 03-techniques/06-examples-design）

### [6] 输出格式（Output Format）

**作用**：精确指定模型应该返回什么形状。

```
输出格式：
- 只返回一个 JSON 对象，不要包 ```json fences
- 必须包含字段：name (string), price (string), sku (string|null)
- 不要返回任何解释 / 注释 / 道歉
```

**生产级最佳实践**：能用 **structured output / tool use** 强制 schema 的就别靠 prompt 描述（详见 03-techniques/05-structured-output）。

### [7] 收尾（Reinforcement）

**作用**：把最最重要的一条规则重复一次，用最后位置的"注意力红利"压住。

```
重要：如果输入文本不是产品描述（比如是问题、闲聊、空字符串），
返回 {"error": "not a product description"}，不要瞎编。
```

**为什么有效**：transformer 模型对**首尾**的 attention 系数普遍更高（"lost in the middle"），最后几行的指令比中间的更被"听进去"。

---

## 4. 完整模板示范

把上面七部件拼起来：

```
你是资深电商数据工程师，擅长从中英文混合的产品描述里抽信息。

## 业务背景
- 我们做跨境电商，描述可能中英文混合
- 价格可能用 ¥ / $ / € 等不同符号
- SKU 是商家自填，格式不统一

## 任务
从用户给的产品描述中，提取以下三个字段：
- name（产品名）
- price（含货币符号）
- sku（型号编码）

## 约束
- price 必须保留原货币符号
- sku 必须保留原大小写
- 如果某字段无法确定，填 null（不要猜）
- 只返回 JSON，不要额外说明

## 示例

输入：MacBook Air M3，售价 ¥9,999.00，型号 MBA-M3-13-512G
输出：{"name": "MacBook Air M3", "price": "¥9,999.00", "sku": "MBA-M3-13-512G"}

输入：罗技 MX Master 3S 鼠标 ¥899
输出：{"name": "罗技 MX Master 3S 鼠标", "price": "¥899", "sku": null}

## 输出格式
只返回一个 JSON 对象，字段：{name: string, price: string, sku: string|null}

---

重要：如果输入不是产品描述（问题、闲聊、空字符串），返回 {"error": "not a product description"}。
```

---

## 5. 三家 API 的"摆放位置"

不同 API 把这七部件放在不同位置：

### Anthropic

```python
client.messages.create(
    model="claude-sonnet-4-6",
    system="""[1] 你是资深电商数据工程师...
    [2] ## 业务背景 ...
    [3] [4] [6] 任务/约束/格式...
    [5] ## 示例 ...
    [7] 重要：...
    """,  # ← system 全部
    messages=[
        {"role": "user", "content": "MacBook Air M3 ¥9999"},  # 实际输入
    ],
)
```

Claude 推荐**所有指令都进 system**，user 只放真实数据。

### OpenAI

```python
client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "[1] 你是资深电商数据工程师...\n[2]...[3][4][6][5][7]"},
        {"role": "user", "content": "MacBook Air M3 ¥9999"},
    ],
)
```

GPT 推荐相似——system 放指令，user 放真实数据。

### Gemini

```python
client.models.generate_content(
    model="gemini-2.0-flash",
    contents="MacBook Air M3 ¥9999",
    config={
        "system_instruction": "[1]...[2]...[3][4][6][5][7]",
    },
)
```

---

## 6. user message 应该放什么

铁律：**user message 只放"每次都不一样的真实输入"**。

- ✅ 用户文档、用户问题、要分析的数据
- ❌ "请帮我..." / "我希望..." 等指令性话
- ❌ 角色、约束、示例（这些在 system 里）

为什么：
- system 部分可以走 **prompt caching** 节省成本（参考 07-production/02-caching）
- 把"指令"和"数据"混在 user 里，每次 cache miss
- 也让数据 vs 指令的边界清晰，防 prompt injection（参考 04-advanced/06）

---

## 7. 部件粒度参考

每个部件多长合适？经验值：

| 部件 | token 经验值 |
|------|--------------|
| 身份 | 30-100 |
| 上下文 | 100-1000（与业务复杂度有关） |
| 任务 | 30-100 |
| 约束 | 100-300（3-7 条 bullet） |
| 示例 | 300-2000（2-5 条） |
| 输出格式 | 50-200 |
| 收尾 | 30-100 |
| **总计** | **800-4000**（生产级） |

超过 4000 token 一定要质疑必要性。常见原因是约束/示例膨胀，多半能砍。

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| **多任务塞一条 prompt** | 拆成多步 / 多 prompt |
| **system 是空的，所有指令在 user** | prompt caching 用不上、每次 token 浪费 |
| **示例多但和真实输入分布不一致** | 示例要"代表性"不是"覆盖性" |
| **约束写在中间** | 重要约束放首尾 |
| **没收尾强化** | 长 prompt 末尾不加"重要：" 模型会忘 |
| **格式描述和真实期望不一致** | 用 structured output 强制 |
| **persona 太花** | 一个角色就够 |

---

## 9. 下一步

- 📖 模型怎么"读" prompt → [03-how-models-read.md](./03-how-models-read.md)
- 📖 sampling 与不确定性 → [04-sampling.md](./04-sampling.md)
- 📖 结构化输出 → [03-techniques/05-structured-output.md](../03-techniques/05-structured-output.md)
- 📖 prompt caching → [07-production/02-caching.md](../07-production/02-caching.md)

## 参考资料

- Anthropic Be Clear & Direct: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct
- OpenAI Best practices: https://platform.openai.com/docs/guides/prompt-engineering
