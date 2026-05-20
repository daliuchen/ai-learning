# PE 03：模型怎么"读" Prompt —— Tokenizer / Attention / 训练数据

> **一句话**：知道模型怎么"读" prompt，就知道为什么"think step by step"管用、为什么 prompt 中间的指令会被忘、为什么中文 token 数翻倍贵。这一篇把三个底层机制——**tokenization、attention、训练数据先验**——给你建立够用的物理直觉。

---

## 1. Tokenization：模型看到的不是字，是 token

### 1.1 什么是 token
模型不看字符，看 **token**——子词单位。tokenizer 把字符串切成 token 数组，每个 token 对应一个整数 ID。

```
"Hello, world!"
  ↓ tokenizer
['Hello', ',', ' world', '!']
  ↓ id
[15496, 11, 1917, 0]
```

模型实际接收的是这串 ID。

### 1.2 中英文 token 数差异（关键！）

| 文本 | 字符数 | OpenAI cl100k_base token 数 |
|------|--------|----------------------------|
| `Hello world how are you doing today` | 36 | 7 |
| `你好世界今天过得怎么样` | 11 | 12 |
| `Lorem ipsum dolor sit amet` | 26 | 7 |
| `床前明月光，疑是地上霜` | 11 | 14 |

**中文 token 数 ≈ 字符数 × 1.1-1.5**（汉字大多一字一 token，部分常见词 1 token 多字）。

英文 token 数 ≈ 字符数 ÷ 4（一个 token ≈ 4 字符 ≈ 0.75 个英文单词）。

**实战意义**：

```
同一段语义 + 同一个模型：
  中文 prompt: 3000 字 ≈ 4000 token ≈ $0.012
  英文 prompt: 4500 字（同等信息量）≈ 1200 token ≈ $0.0036

差 3 倍以上的 token cost！
```

如果对成本敏感，可以试着把 system 改成英文（user 输入维持原语言）。

### 1.3 怎么实测 token 数

#### OpenAI（tiktoken）

```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-4o")
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
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "你好世界"}],
)
print(resp.input_tokens)
```

#### Gemini

```python
from google import genai
client = genai.Client()
resp = client.models.count_tokens(
    model="gemini-2.0-flash",
    contents="你好世界",
)
print(resp.total_tokens)
```

> ⚠️ 三家 tokenizer 都不同——同一段文本三家 token 数可能差 30%。

### 1.4 tokenization 引发的奇怪 bug

#### Bug 1：数字被切碎

```python
"12345" → ['123', '45']
"01234" → ['01', '234']
"0.001" → ['0', '.', '001']
```

让模型做数字运算时，它"看"到的是离散 token，不是 12345 这个数。这就是为什么早期模型连两位数加法都出错——它根本没在做"算术"。

**对策**：
- 数学题：用 **Program of Thoughts**（让模型写代码而非算）
- 价格抽取：示例里给完整数字，不要拆位

#### Bug 2：JSON 引号和空格

```python
'{"name":"alice"}'     → ['{"', 'name', '":"', 'alice', '"}']
'{"name": "alice"}'    → ['{"', 'name', '":', ' "', 'alice', '"}']
```

加一个空格 token 序列完全变。这就是为什么"严格 JSON 输出"靠 prompt 描述不稳——细微格式差异 tokenizer 全看在眼里。

**对策**：用 structured output API（详见 03-techniques/05）。

#### Bug 3：emoji 和特殊字符
emoji 通常占 2-4 token（UTF-16 surrogate pair）。生僻字也可能多 token。

```python
"😀" → 2-3 tokens
"𠮷" → 2-3 tokens（生僻汉字）
```

---

## 2. Attention：为什么会"lost in the middle"

### 2.1 简化版原理
Transformer 模型的 attention 机制让每个 token 在生成下一个 token 时"看"所有前面的 token——但**不是均匀地看**，有的 token 被注意到更多，有的少。

研究显示（Liu et al. 2023 "Lost in the Middle"）：

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

**结论**：放在开头和结尾的指令，模型更"听得见"；放在中间的容易被忽略。

### 2.2 实战意义

| 位置 | 适合放 |
|------|--------|
| **开头**（首条 system / 前 10%） | 身份、最核心任务 |
| **结尾**（最后 5-10%） | 最重要的约束、收尾强化 |
| **中间** | 上下文数据、示例 |

如果你的 prompt 是这样：

```
[1] 身份
[2] 上下文（5000 字业务文档）
[3] 任务
[4] 约束（"必须用中文回复"）
[5] 示例
[6] 输出格式
```

那个"必须用中文回复"在中间，可能被忘。改成：

```
[1] 身份 + 「必须用中文回复」
[2] 上下文
[3] 任务
[4] 示例
[5] 输出格式
[6] **重要：必须用中文回复，不要用英文。**
```

收尾再强调一次。

### 2.3 上下文窗口长度的影响

虽然主流模型支持 100k-1M token 窗口，但**有效注意力**远不到全长。经验上：

| 模型 | 名义窗口 | 经验"有效专注"区域 |
|------|---------|-------------------|
| Claude Sonnet 4 | 200k | 开头 30k + 末尾 30k 最稳 |
| GPT-4o | 128k | 开头 + 末尾各 20k |
| Gemini 2.0 | 1M | 开头 + 末尾各 50k；中间可用但弱 |

**结论**：超长 context 任务建议拆分（map-reduce）+ 把关键信息复制到开头/结尾。

---

## 3. 训练数据先验

### 3.1 模型不是空白的
LLM 训练数据是几 T 的互联网文本——这给它带来强烈的**先验**：

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
Claude 训练数据里 XML 标签**特别有效**——Anthropic 自己也推荐 XML 标签结构化输入：

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

模型一看到 `<output_format>`，立刻知道输出要符合声明的形状。

#### 让模型写代码
"```python\n" 开头 prefill 会让模型直接进入代码模式：

```python
resp = client.messages.create(
    messages=[
        {"role": "user", "content": "写一个判断素数的函数"},
        {"role": "assistant", "content": "```python\n"},  # ← prefill
    ],
)
# 模型直接接代码，省去"好的，下面是代码："的废话
```

### 3.3 反先验：与训练数据打架

有些写法和训练数据先验冲突，模型会"挣扎"：

```
❌ "请用 emoji 列出 5 条，每条结尾加 [DONE]"
   → 模型一会儿带 emoji 一会儿不带、[DONE] 时有时无

✅ 给个示例，让模型 anchor 到具体格式
```

**总原则**：**顺着训练数据先验写**比"硬掰"模型容易得多。

### 3.4 时间先验
模型的"世界观"停在训练 cutoff（Claude 4.x 在 2025 年初，GPT-5 在 2024-Q4 等）。涉及时间敏感任务，**显式给当前日期**：

```python
system = f"""
你是助手。当前时间：{datetime.now().isoformat()}
"""
```

否则模型会用 "2024" 去推理"现在"，给出过时事实。

---

## 4. 三个机制如何叠加：一个案例

任务：让模型从中英文混合财报里抽 Q3 营收。

### 不懂底层的写法

```
"请仔细分析下面的财报，告诉我 Q3 的营收数字是多少。
要准确无误，因为这关系到投资决策。
{长文档}"
```

问题：
1. **tokenization**：长文档浪费 token；中文部分 token 比例高
2. **attention**：把任务挤在文档之前，模型读完文档可能忘了
3. **训练先验**："Q3 营收"是常见表述，模型可能给 "Q3" 训练数据先验里的某个常见公司数字

### 懂底层的写法

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

每条改动都对应一个底层机制：
- 给当前日期 → 治理**时间先验**
- 用 XML 标签 → **训练数据先验**
- 任务在开头 + 收尾再强调 → **attention 首尾红利**
- 要 source_quote → 防"幻觉"，强制模型回到文档
- 输出 JSON 不用 ```fences → 减少 **tokenization** 边界 bug

---

## 5. 关键收益指标

理解这三个机制后你能多做的事：

| 收益 | 怎么做到 |
|------|---------|
| **省 30%-70% token 成本** | 优化 tokenization（英文 system / 减少冗余指令） |
| **少 50% "模型没听话"投诉** | 重要指令放首尾 |
| **减少 70% 幻觉** | 用 XML 标签 anchor 输入边界 + 要求引用源 |
| **降低延迟** | prompt 短了 → 输入处理快了 |
| **prompt cache 命中率高** | 把"指令" / "上下文模板"放 system 前缀，每次 user 输入只变后半段 |

---

## 6. 实战 demo：token 测量工具

```python
# demos/foundations/03_token_compare.py
"""对比同一段语义在中英文下的 token 成本"""
import tiktoken

def compare(texts: dict[str, str], model="gpt-4o"):
    enc = tiktoken.encoding_for_model(model)
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

输出大致这样：

```
EN        :   62 chars →   13 tokens (ratio = 0.21 token/char)
ZH        :   17 chars →   21 tokens (ratio = 1.24 token/char)
EN-long   :  280 chars →   60 tokens (ratio = 0.21 token/char)
ZH-long   :  120 chars →  150 tokens (ratio = 1.25 token/char)
```

直观看到中文 token 密度 ≈ 6 倍英文。

---

## 7. 常见坑

| 坑 | 排查 |
|----|------|
| **中文 prompt 成本超预算** | 试试把 system 改英文，user 维持中文 |
| **指令在中间不生效** | 移到开头或结尾，或两边都放 |
| **模型自己编数字** | 加 XML 标签 anchor 输入 + 要求 source_quote |
| **JSON 输出格式飘忽** | 用 structured output API 而非 prompt 描述 |
| **emoji / 生僻字 token 暴增** | 用 tokenizer 测一下，必要时改用纯文本 |
| **超长 context 中部信息被忘** | 重要信息复制到开头 / 结尾，或分块多次调用 |

---

## 8. 下一步

- 📖 sampling / 不确定性 → [04-sampling.md](./04-sampling.md)
- 📖 评测先于 prompt → [05-eval-first.md](./05-eval-first.md)
- 📖 Claude 专用写法（XML + prefill） → [06-models/01-claude.md](../06-models/01-claude.md)
- 📖 prompt caching 实战 → [07-production/02-caching.md](../07-production/02-caching.md)

## 参考资料

- "Lost in the Middle" paper: https://arxiv.org/abs/2307.03172
- OpenAI Tokenizer 可视化: https://platform.openai.com/tokenizer
- Anthropic Token Counting: https://docs.anthropic.com/en/api/messages-count-tokens
- Karpathy Tokenization 讲解: https://www.youtube.com/watch?v=zduSFxRajkE
