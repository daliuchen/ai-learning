# PE 02：一条 Prompt 的解剖

> 生产里用的 prompt 不是随手写的一段话。拆开来看，它由七个部件组成：身份（role）、上下文（context）、任务（task）、约束（constraints）、示例（examples）、输出格式（output format）和收尾（reinforcement）。每个部件放在哪、写多长都有讲究，这篇一次拆清楚。

---

## 1. 看一个失败的 prompt

很多团队的 prompt 长这样：

```
"你是有用的助手，请总结这篇文章。{article}"
```

它缺了很多东西：总结是几句话还是几段没说，语言没指定（英文文章要不要出中文总结？），输出用 bullet 还是段落没限制，总结给谁看（老板还是同事）也没交代，文章为空时该怎么办更没有兜底。

这条 prompt 能跑，但结果不稳定，同一篇文章问 10 次，会得到 10 种结构和长度。

---

## 2. 一条完整 prompt 的七个部件

下面是生产里常用的模板，按各部件应该出现的位置排列：

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

位置放对、粒度写对，prompt 就能用了。

---

## 3. 七个部件逐个看

### [1] 身份（Role）

身份决定模型用什么语气回答：医生、律师、编程助手、客服，口吻各不一样。

```
你是一位资深 Python 工程师，10 年经验，擅长 web 后端。
```

写经验年限（"10 年"）会让模型更倾向给出老手的答案；写领域（"web 后端"）能缩小输出范围。身份一句就够，别扩成一段。

要避免两种写法。"你是世界上最聪明的 AI"没有任何作用，还容易引出自夸式的回答。另一种是 persona 太多，又是医生又是程序员又是诗人。

### [2] 上下文（Context）

这一块放模型需要的背景：业务规则、上游数据、用户身份、当前时间。

```
## 业务背景
我们是 SaaS 公司，目标客户是中小企业。
用户当前等级：standard。VIP 用户才能用高级功能 X / Y。
当前时间：{today}。
```

用 Markdown 标题切块，模型识别结构化输入更稳。业务规则写成条目（"- ..." / "1. ..."）。**跟时间有关的任务一定要给当前时间**，否则模型会拿训练截止日期去推理，结果出错。

别把过期文档塞进上下文，模型会把过期事实当真。也别丢一整坨不分段的长文本，注意力会退化。

### [3] 任务（Task）

用一句简短的话告诉模型要做什么。

```
任务：根据以下产品描述，提取产品名、价格、SKU 三个字段。
```

以动词开头（"提取""总结""翻译""判断"）。一句话讲不清的任务，就拆成多步（见 [PE Technique 04：Decomposition 任务拆解](../03-techniques/04-decomposition.md)）。

常见的错误是把多个任务塞进一句（"提取并总结并翻译"），或者把任务和约束混在一起写。

### [4] 约束（Constraints）

约束划定模型行为的边界。

```
约束：
- 价格必须包含货币符号
- SKU 必须保留原大小写
- 如果字段缺失，输出 null，不要瞎猜
- 不要返回除字段外的任何说明文字
```

用"必须 / 不要 / 如果……就……"的句式，每条独立成一个 bullet。最重要的约束放在第一条和最后一条，利用首尾效应。

两种情况要警惕：一是用否定式列一大串"不要这样、不要那样"，反而会把那些行为提醒给模型；二是约束过多，超过 5 条就该想想每条是否都必要。

### [5] 示例（Examples / Few-shot）

示例让模型锚定到具体的格式和风格。

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

至少给 2 到 3 个。只给 1 个，模型可能以为所有输出都得跟它一模一样。示例里要有 happy path，也要有一个边界情况（缺字段、特殊符号），输入和输出的风格要对齐。

示例要和任务对得上，别拿其他领域的例子凑数。数量也别到 10 条以上，原因见 [PE Technique 06：Few-shot 示例怎么设计](../03-techniques/06-examples-design.md)。

### [6] 输出格式（Output Format）

精确说明模型该返回什么形状。

```
输出格式：
- 只返回一个 JSON 对象，不要包 ```json fences
- 必须包含字段：name (string), price (string), sku (string|null)
- 不要返回任何解释 / 注释 / 道歉
```

生产环境里，能用 structured output 或 tool use 强制 schema 的，就别靠 prompt 描述（见 [PE Technique 05：结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)）。

### [7] 收尾（Reinforcement）

把最重要的一条规则在末尾再说一遍，借最后位置更容易被注意到的特点压住它。

```
重要：如果输入文本不是产品描述（比如是问题、闲聊、空字符串），
返回 {"error": "not a product description"}，不要瞎编。
```

这么做有效，是因为 transformer 模型对开头和结尾的注意力普遍更高（即"lost in the middle"现象），最后几行指令比中间的更容易被执行。

---

## 4. 完整模板示范

把七个部件拼起来：

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

## 5. 三家 API 里放在哪

不同 API 放这七个部件的位置不一样。

### Anthropic

```python
client.messages.create(
    model="claude-sonnet-5-5",
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

Anthropic 官方的建议是 system 主要用来设定角色，任务相关的指令放在 user 轮也可以。我更习惯把固定不变的指令都放进 system、user 只放每次变化的数据，原因见第 6 节。

### OpenAI

```python
client.chat.completions.create(
    model="gpt-6.1-terra",
    messages=[
        {"role": "developer", "content": "[1] 你是资深电商数据工程师...\n[2]...[3][4][6][5][7]"},
        {"role": "user", "content": "MacBook Air M3 ¥9999"},
    ],
)
```

GPT 的建议类似：developer（旧称 system）放指令，user 放真实数据。

### Gemini

```python
client.models.generate_content(
    model="gemini-3.8-flash",
    contents="MacBook Air M3 ¥9999",
    config={
        "system_instruction": "[1]...[2]...[3][4][6][5][7]",
    },
)
```

---

## 6. user message 应该放什么

user message 只放每次都不一样的真实输入。用户文档、用户问题、要分析的数据放这里。"请帮我……""我希望……"这类指令性的话不要放，角色、约束、示例也不要放，它们属于 system。

这样分有三个好处。固定不变的 system 部分可以走 prompt caching 省钱（见 [PE Production 02：Prompt Caching 实战](../07-production/02-caching.md)）；缓存按前缀匹配，指令如果和每次变化的数据混写在一起，前缀对不上，就很难命中。另外，数据和指令的边界清楚了，也更容易防 prompt injection（见 [PE Advanced 06：Prompt Injection 防御](../04-advanced/06-injection-defense.md)）。

---

## 7. 各部件写多长

下面是我常用的经验值：

| 部件 | token 经验值 |
|------|--------------|
| 身份 | 30-100 |
| 上下文 | 100-1000（与业务复杂度有关） |
| 任务 | 30-100 |
| 约束 | 100-300（3-7 条 bullet） |
| 示例 | 300-2000（2-5 条） |
| 输出格式 | 50-200 |
| 收尾 | 30-100 |
| 总计 | 800-4000（生产级） |

超过 4000 token 就该回头看看哪些是多余的。常见原因是约束和示例越写越多，大多能砍掉一部分。

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| 多个任务塞进一条 prompt | 拆成多步或多个 prompt |
| system 是空的，所有指令都和数据混在 user 里 | 缓存前缀对不上，prompt caching 很难命中 |
| 示例不少，但和真实输入的分布对不上 | 示例要有代表性，不求覆盖全 |
| 约束写在中间 | 重要约束放首尾 |
| 没有收尾强调 | 长 prompt 末尾不再提一句"重要："，模型容易忘 |
| 格式描述和真实期望不一致 | 用 structured output 强制 |
| persona 太花 | 一个角色就够 |

---

## 9. 下一步

- [模型如何读 Prompt：Tokenizer、Attention 与训练数据](./03-how-models-read.md)
- [Temperature、Top-p 与采样](./04-sampling.md)
- [结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)
- [Prompt Caching 实战](../07-production/02-caching.md)

## 参考资料

- Anthropic Be Clear & Direct: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/be-clear-and-direct
- OpenAI Best practices: https://platform.openai.com/docs/guides/prompt-engineering
