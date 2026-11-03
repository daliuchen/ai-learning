# PE 03：模型如何读 Prompt：Tokenizer、Attention 与训练数据

> 为什么"think step by step"管用，为什么夹在 prompt 中间的指令容易被忘，为什么中文 prompt 的 token 数要多出一截？答案都在模型读 prompt 的方式里。这篇讲三个底层机制：tokenization、attention 和训练数据带来的先验，讲到够用来指导写 prompt 为止。

---

## 1. Tokenization：模型看到的不是字，是 token

### 1.1 什么是 token
模型不按字符读，按 token 读。token 是子词单位，tokenizer 把字符串切成 token 数组，每个 token 对应一个整数 ID。

```
"Hello, world!"
  ↓ tokenizer
['Hello', ',', ' world', '!']
  ↓ id（cl100k_base）
[9906, 11, 1917, 0]
```

模型实际接收的是这串 ID。

### 1.2 中英文 token 数差异

| 文本 | 字符数 | OpenAI cl100k_base token 数 |
|------|--------|----------------------------|
| `Hello world how are you doing today` | 35 | 7 |
| `你好世界今天过得怎么样` | 11 | 13 |
| `Lorem ipsum dolor sit amet` | 26 | 5 |
| `床前明月光，疑是地上霜` | 11 | 16 |

在 cl100k_base 上，中文的 token 数大约是字符数的 1.1 到 1.5 倍：常见字一字一个 token，生僻些的字会被拆成两三个字节级 token。英文则大约是字符数除以 4，一个 token 约等于 4 个字符、0.75 个单词。GPT-4o 起用的 o200k_base 对中文压缩好得多，上表两句中文分别只要 6 个和 12 个 token，所以中文的 token 成本很依赖具体模型的 tokenizer。

这对成本的影响很直接：

```
同一段语义 + 同一个对中文不友好的 tokenizer（示意，按输入 $3/百万 token 算）：
  中文 prompt: 3000 字 ≈ 4000 token ≈ $0.012
  英文 prompt: 4500 字符（同等信息量）≈ 1200 token ≈ $0.0036
```

两者差 3 倍多。换成对中文友好的 tokenizer，差距会小很多。对成本敏感的话，可以试着把 system 改成英文，user 输入保持原语言。

### 1.3 怎么实测 token 数

#### OpenAI（tiktoken）

```python
import tiktoken
# tiktoken 暂时认不出 GPT-6 的模型名，直接用 o200k_base 估算（和 gpt-4o 相同）；
# 要精确值以 API 返回的 usage 为准
enc = tiktoken.get_encoding("o200k_base")
text = "你好世界，今天过得怎么样？"
tokens = enc.encode(text)
print(f"{len(text)} 字 → {len(tokens)} token")
print(tokens)  # token id 数组
print([enc.decode([t]) for t in tokens])  # 反编码看怎么切的
```

#### Anthropic

```python
import anthropic
client = anthropic.Anthropic()
resp = client.messages.count_tokens(
    model="claude-sonnet-5-5",
    messages=[{"role": "user", "content": "你好世界"}],
)
print(resp.input_tokens)
```

#### Gemini

```python
from google import genai
client = genai.Client()
resp = client.models.count_tokens(
    model="gemini-3.8-flash",
    contents="你好世界",
)
print(resp.total_tokens)
```

> 三家的 tokenizer 各不相同，同一段文本的 token 数可能差出不少，中文上尤其明显。Claude 官方也建议用 `count_tokens` 统计 Claude 的 token，不要拿 tiktoken 估。

### 1.4 tokenization 引起的怪问题

#### 问题 1：数字被切碎

```python
"12345" → ['123', '45']
"01234" → ['012', '34']
"0.001" → ['0', '.', '001']
```

让模型做数字运算时，它看到的是几个离散的 token，而不是 12345 这个数。早期模型连两位数加法都会错，原因就在这里：它根本没有在做算术。

应对办法：数学题用 Program of Thoughts，让模型写代码去算；价格抽取类任务，示例里给完整数字，不要拆位。

#### 问题 2：JSON 引号和空格

```python
'{"name":"alice"}'     → ['{"', 'name', '":"', 'alice', '"}']
'{"name": "alice"}'    → ['{"', 'name', '":', ' "', 'alice', '"}']
```

多一个空格，整个 token 序列就变了。格式上再细微的差别，tokenizer 都分得清，所以光靠 prompt 描述来要求严格 JSON 不太稳。更可靠的做法是用 structured output API（见 [PE Technique 05：结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)）。

#### 问题 3：emoji 和特殊字符
emoji 和生僻字在 UTF-8 里占 4 个字节，词表里没收录整字时会按字节拆成多个 token。

```python
"😀" → cl100k_base 2 个 token，o200k_base 1 个
"𠮷" → 4 个 token（生僻汉字，按 UTF-8 字节拆开）
```

---

## 2. Attention：为什么会"lost in the middle"

### 2.1 简化版原理
Transformer 的 attention 机制让每个 token 在生成下一个 token 时回看前面所有 token。但回看的力度并不均匀，有的 token 被注意得多，有的少。

Liu et al. 2023 的论文 "Lost in the Middle" 给出了这样的规律：

```
attention 强度
    ▲
    │ ●                                ●
    │   ●                            ●
    │     ●                        ●
    │       ●                    ●
    │         ●   ●   ●   ●   ●
    │           low low low low
    └────────────────────────────────────▶
       开头                            结尾
       (高)        中间(低)            (高)
```

放在开头和结尾的指令，模型更容易遵守；放在中间的容易被忽略。

### 2.2 对写 prompt 的影响

| 位置 | 适合放 |
|------|--------|
| 开头（首条 system / 前 10%） | 身份、最主要的任务 |
| 结尾（最后 5-10%） | 最重要的约束、收尾强调 |
| 中间 | 上下文数据、示例 |

假设你的 prompt 是这样排的：

```
[1] 身份
[2] 上下文（5000 字业务文档）
[3] 任务
[4] 约束（"必须用中文回复"）
[5] 示例
[6] 输出格式
```

"必须用中文回复"落在中间，有可能被忘掉。改成这样：

```
[1] 身份 + 「必须用中文回复」
[2] 上下文
[3] 任务
[4] 示例
[5] 输出格式
[6] **重要：必须用中文回复，不要用英文。**
```

开头说一次，结尾再强调一次。

### 2.3 上下文窗口长度的影响

到 2026-11，主流模型普遍支持 1M token 的窗口，但有效注意力远远覆盖不到全长。下表是经验判断，不是官方数字，窗口大小以官方模型页为准：

| 模型 | 名义窗口 | 经验上的有效区域 |
|------|---------|-------------------|
| Claude 5 家族（Sonnet 5.5 等） | 1M | 开头 + 末尾最稳；关键信息别埋在中间 |
| GPT-6 家族 | 1.05M | 同上 |
| Gemini 4 Argon | 1M | 官方建议文档放前、问题放最后 |

所以超长 context 的任务，我建议拆开做（map-reduce），并把关键信息复制一份到开头或结尾。

---

## 3. 训练数据先验

### 3.1 模型不是一张白纸
LLM 的预训练数据是以万亿 token 计的互联网文本，这让它对很多提示形式有很强的默认反应：

| 提示形式 | 模型默认行为（先验） |
|---------|---------------------|
| "User: ... \n Assistant: ..." | 当成对话延续 |
| "Q: ... \n A: ..." | 当成 FAQ 形式 |
| "```python\n" | 后面接 Python 代码 |
| "<answer>" | 后面填答案，触发 XML 模式 |
| "Once upon a time" | 进入故事生成模式 |
| "TL;DR:" | 输出简短总结 |

### 3.2 利用先验

#### 让 Claude 用 XML
XML 标签对 Claude 特别有效，Anthropic 官方也推荐用 XML 标签来组织输入：

```python
content = """
<task>
分析下面文档，找出三个关键洞察。
</task>

<document>
{user_doc}
</document>

<output_format>
输出三个 <insight> 节点，每个含 <title> 和 <reasoning>。
</output_format>
"""
```

看到 `<output_format>`，模型就知道输出要符合里面声明的形状。

#### 让模型写代码
以"```python\n"开头，模型会直接进入写代码的状态。早期 Claude 靠 prefill（预先填好 assistant 回复的开头）做到这一点，但 Claude 4.6+ 已不再支持 prefill，现在改成在 prompt 里直接说明：

```python
resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=2000,
    system="只输出一个 python 代码块，不要任何前言或解释。",
    messages=[{"role": "user", "content": "写一个判断素数的函数"}],
)
```

如果需要强约束格式（JSON 之类），用结构化输出，见 [结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)。

### 3.3 跟先验对着干

有些写法和训练数据的习惯冲突，模型执行起来就会摇摆：

```
❌ "请用 emoji 列出 5 条，每条结尾加 [DONE]"
   → 模型一会儿带 emoji 一会儿不带、[DONE] 时有时无

✅ 给个示例，让模型 anchor 到具体格式
```

顺着先验写，比硬扳模型要省事得多。

### 3.4 时间先验
模型对世界的认知停在训练 cutoff。各模型的 cutoff 不同，以官方模型页为准，通常比发布日期早半年到一年。涉及时间的任务，要在 prompt 里明确给出当前日期：

```python
system = f"""
你是助手。当前时间：{datetime.now().isoformat()}
"""
```

不给的话，模型会用训练截止那年去推断"现在"，给出过时的事实。如果用了 prompt caching，别把时间戳放在 system 最前面，否则缓存每次都失效，应该放到末尾或 user 消息里。

---

## 4. 三个机制叠在一起：一个案例

任务：从中英文混合的财报里抽取 Q3 营收。

### 不考虑底层机制的写法

```
"请仔细分析下面的财报，告诉我 Q3 的营收数字是多少。
要准确无误，因为这关系到投资决策。
{长文档}"
```

三个机制各有一个问题。tokenization 上，长文档浪费 token，中文部分占比又高。attention 上，任务挤在文档前面，模型读完文档可能已经忘了要干什么。训练先验上，"Q3 营收"是很常见的说法，模型可能凭印象给出训练数据里某家公司的数字。

### 考虑底层机制的写法

```
你是财务分析师。当前时间：2026-05-20。

<task>
从下面的财报中提取 2025 年 Q3 营收数字（含货币单位）。
如果文档中没有 Q3 数据，返回 null。
</task>

<document>
{长文档（如果超过 30k token，建议先用 keyword search 截取相关段落）}
</document>

<output_format>
{"q3_revenue": "...", "currency": "...", "source_quote": "..."}
</output_format>

重要：必须从 <document> 里找答案，不要用任何外部知识或猜测。
source_quote 字段填你在文档里找到这个数字的**原文句子**。
```

每处改动都对应一个机制：

- 给出当前日期，对付时间先验
- 用 XML 标签，利用训练数据先验
- 任务放开头、收尾再强调，利用 attention 的首尾优势
- 要求 source_quote，逼模型回到文档里找依据，减少幻觉

---

## 5. 能拿到哪些收益

理解这三个机制之后，下面这些事就有了着手点（幅度因任务和模型而异，要用自己的评测集量）：

| 收益 | 怎么做到 |
|------|---------|
| 降低 token 成本 | 优化 tokenization（英文 system / 减少冗余指令） |
| 减少「模型没听话」 | 重要指令放首尾 |
| 减少幻觉 | 用 XML 标签划清输入边界 + 要求引用原文 |
| 降低延迟 | prompt 短了，输入处理就快了 |
| prompt cache 命中率高 | 指令和上下文模板放在 system 前缀，每次只有后半段的 user 输入在变 |

---

## 6. 实战 demo：token 测量工具

```python
"""对比同一段语义在中英文下的 token 成本"""
import tiktoken

def compare(texts: dict[str, str]):
    # tiktoken 认不出 GPT-6 的模型名，直接按 o200k_base 估算
    enc = tiktoken.get_encoding("o200k_base")
    for lang, t in texts.items():
        tokens = enc.encode(t)
        print(f"{lang:10s}: {len(t):4d} chars → {len(tokens):4d} tokens "
              f"(ratio = {len(tokens)/len(t):.2f} token/char)")


if __name__ == "__main__":
    compare({
        "EN": "Please summarize the following article in three bullet points.",
        "ZH": "请用三个要点总结下面的文章。",
        "EN-long": "Lorem ipsum dolor sit amet, consectetur adipiscing elit." * 5,
        "ZH-long": "床前明月光，疑是地上霜，举头望明月，低头思故乡。" * 5,
    })
```

输出大致如下：

```
EN        :   62 chars →   10 tokens (ratio = 0.16 token/char)
ZH        :   14 chars →   10 tokens (ratio = 0.71 token/char)
EN-long   :  280 chars →   50 tokens (ratio = 0.18 token/char)
ZH-long   :  120 chars →  125 tokens (ratio = 1.04 token/char)
```

按字符算，中文的 token 密度是英文的 4 到 6 倍；但一个汉字承载的信息比一个英文字母多得多，按同等语义比，前两行几乎打平（都是 10 个 token），古诗这种生僻字多的文本才明显更贵。

---

## 7. 常见坑

| 坑 | 排查 |
|----|------|
| 中文 prompt 成本超预算 | 试试把 system 改成英文，user 保持中文 |
| 放在中间的指令不生效 | 移到开头或结尾，或者两头都放 |
| 模型自己编数字 | 用 XML 标签划清输入 + 要求 source_quote |
| JSON 输出格式飘忽 | 用 structured output API，别只靠 prompt 描述 |
| emoji / 生僻字让 token 数暴涨 | 用 tokenizer 测一下，必要时改用纯文本 |
| 超长 context 中段的信息被忘 | 重要信息复制到开头或结尾，或者分块多次调用 |

---

## 8. 下一步

- [Temperature、Top-p 与采样](./04-sampling.md)
- [评测先于 Prompt](./05-eval-first.md)
- [Claude 专用写法](../06-models/01-claude.md)：XML 和结构化输出
- [Prompt Caching 实战](../07-production/02-caching.md)

## 参考资料

- "Lost in the Middle" paper: https://arxiv.org/abs/2307.03172
- OpenAI Tokenizer 可视化: https://platform.openai.com/tokenizer
- Anthropic Token Counting: https://docs.anthropic.com/en/api/messages-count-tokens
- Karpathy Tokenization 讲解: https://www.youtube.com/watch?v=zduSFxRajkE
