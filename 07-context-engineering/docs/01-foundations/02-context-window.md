# CE 02：上下文窗口到底是什么

> 上下文窗口是模型一次能看到的 token 总数上限。system、工具定义、检索结果、历史、用户输入都在里面，还要给输出留出预算。窗口里的位置也有讲究：self-attention 的计算量是 O(n²)，模型靠位置编码分辨先后，所以装什么、装多少、装在哪，都会影响结果。这篇从 token 讲起，把这些机制捋一遍。

---

## 1. Token 是什么

模型不直接读字符，也不直接读单词，它读的是 token，一种介于字符和单词之间的子词单元。

- 英文里 1 个 token ≈ 4 个字符 ≈ 0.75 个单词。
- 中文每个汉字折合多少 token，不同 tokenizer 差别很大：新一些的 `o200k_base` 常用词会合并成一个 token，平均每字不到 1 个；老的 `cl100k_base` 每字接近或超过 1 个。
- 标点、空格、换行都算 token。

把文本切成 token 的组件叫 tokenizer（分词器）。主流模型用的都是 BPE（Byte Pair Encoding）的某种变体：

| 模型家族 | tokenizer | 备注 |
|----------|-----------|------|
| GPT-4o 及之后的 OpenAI 模型 | `o200k_base` 系 | OpenAI tiktoken；新模型是否沿用以官方为准，只能当估算 |
| GPT-3.5 / 老 GPT-4 | `cl100k_base` | tiktoken |
| Claude | 专有 tokenizer | 用 `count_tokens` 接口；Opus 4.7 起换了新 tokenizer，同一段文字的 token 数和旧模型不同 |
| Gemini | SentencePiece 系 | 用 `count_tokens` API |

> 不同模型数出来的 token 不一样，同一段中文在不同 tokenizer 下的计数可能差出一截。别拿一个 tokenizer 的数字去估另一个模型的成本。

---

## 2. 用 tiktoken 数 token

OpenAI 的 `tiktoken` 是最常用的本地计数工具，离线就能跑，速度也快：

```python
# pip install tiktoken
import tiktoken

# 以 o200k_base 为例（只是估算，不是任何模型的精确计数）
enc = tiktoken.get_encoding("o200k_base")

text_en = "Context engineering is about what you put in the window."
text_zh = "上下文工程关心的是窗口里到底装什么。"

tokens_en = enc.encode(text_en)
tokens_zh = enc.encode(text_zh)

print(f"英文: {len(tokens_en)} tokens")   # 11
print(f"中文: {len(tokens_zh)} tokens")   # 12（18 个字符；换成 cl100k_base 是 21）

# 看看具体切成了什么
print([enc.decode([t]) for t in tokens_zh[:6]])
# ['上下', '文', '工程', '关', '心', '的是']
```

Claude 的 token 数用官方 SDK 的计数接口拿，不要用 tiktoken 凑：

```python
# pip install anthropic
import anthropic

client = anthropic.Anthropic()
resp = client.messages.count_tokens(
    model="claude-opus-5-5",
    messages=[{"role": "user", "content": "上下文工程关心装什么。"}],
)
print(resp.input_tokens)
```

---

## 3. 窗口里有什么

一次调用的输入由下面几块拼起来，先后顺序由 API 或框架决定：

```
[ system ]      角色 / 规则 / 全局约束
[ tools ]       工具定义 schema（Agent / function calling 场景）
[ retrieved ]   RAG 检索来的文档片段
[ history ]     前面 N 轮 user / assistant 消息
[ user ]        本轮用户输入
─────────────────────────────────
↑ 以上全部加起来 = 输入 token 数，必须 ≤ 窗口上限 - 输出预算
```

每一块都占 token。在 Agent 场景里，工具定义和工具返回结果常常是大头，一个返回 JSON 的工具调上几次，几千 token 就没了。

---

## 4. 各模型窗口大小

| 模型 | 上下文窗口 | 最大输出 | 出处 |
|------|-----------|----------|------|
| Claude Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5 | 1M（默认，无需 beta header） | 128K | Anthropic Models overview |
| OpenAI GPT-6 系（Astra / Sol / Terra / Luna） | 1.05M | 128K | OpenAI Models 页 |
| Gemini 4 Argon | 1M（输入、思考、输出共享一个窗口） | 最高可 1M | Gemini API Models / Long context |
| Gemini 3.x（Flash 等） | 1M 输入 / 64K 输出（Long context 页对 3 与 2.5 系的描述） | 64K | Gemini API Long context |
| 千问（qwen3.8-max / qwen3.7-plus / qwen3.8-flash 等） | 以百炼各模型详情页为准 | — | 阿里云百炼 |

> 窗口大不代表能输出得长，**输入窗口和输出 token 是两笔账**。另外，窗口数字几个月就变一轮，上表只是 2026-11 的快照。代码里别写死，可以用 Anthropic 的 Models API（`max_input_tokens`）这类接口查询。

---

## 5. 输入窗口和输出预算

新手最容易在这里出问题：

```
窗口上限 = 输入 tokens + 输出 tokens
```

比如窗口是 200K（老一些的模型或者自己限定的小窗口都在这个量级），你设 `max_tokens=8000` 想让它写长文，留给输入的就只剩约 192K。要是输入已经塞到 199K，模型只剩 1K 的空间写输出。具体后果看平台：Claude 4.5 及之后的模型，输入加 `max_tokens` 超过窗口时请求仍会被接受，生成撞到窗口上限后以 `stop_reason: "model_context_window_exceeded"` 结束，输出被截断；如果输入本身就超了窗口，所有模型都会返回 400「prompt is too long」。

```python
# ❌ 输入塞满，没给输出留空间
messages = build_messages(huge_context)  # 输入 199K
client.messages.create(model=..., messages=messages, max_tokens=8000)
# → 199K + 8K > 200K：有的平台直接报错，Claude 会接受请求但输出撞到窗口就被截断

# ✅ 先核算输入预算，给输出留够
INPUT_BUDGET = 200_000 - 8_000  # 给输出留 8K
context = truncate_to(context, INPUT_BUDGET)
```

怎么系统地分配预算，见 [按预算分配上下文（Context Budget）](./05-context-budget.md)。

---

## 6. 为什么位置很重要：attention 与位置编码

模型读上下文靠 self-attention：每个 token 都要和其他所有 token 计算注意力。

- 序列长度为 n 时，attention 的计算量是 O(n²)。长上下文又贵又慢，根子在这里（见 [上下文的成本与延迟模型](./04-cost-latency.md)）。
- Transformer 本身不知道 token 的先后，要靠位置编码（positional encoding，比如 RoPE）告诉它每个 token 排第几。

这带来两个后果。第一，模型会把位置当成信号：开头和结尾的 token 往往得到更稳定的注意力，埋在中间的内容容易被忽略，这就是 lost in the middle，它是 context rot 的一种表现（见 [Context Rot 与 Lost in the Middle](./03-context-rot.md)）。第二，越长越稀释：n 翻倍，每个 token 分到的注意力整体被摊薄，关键信息更容易被淹没。

```
注意力强度（经验性示意）
高 │■                              ■
   │■■                          ■■
   │ ■■                       ■■
低 │   ■■■■■■■■■■■■■■■■■■■■■■
   └──────────────────────────────
    开头        中间          结尾
```

落到工程上：重要的内容放头尾，别埋在中间。

---

## 7. 常见坑

| 坑 | 说明 |
|----|------|
| 拿 tiktoken 估 Claude/Gemini 成本 | tokenizer 不同，数字对不上，用各家官方计数 |
| 以为窗口可以用满 | 要扣掉输出预算，还要防 rot |
| 忽略工具定义的占用 | Agent 里工具 schema 和返回值经常是 token 大头 |
| 把中文按英文估 | 中文每字折合的 token 数随 tokenizer 变化很大（`o200k_base` 下不到 1，老 tokenizer 下 1 以上），用目标模型的计数接口量 |
| 历史无限往上堆 | 多轮下来历史会撑爆窗口，要截断或摘要 |

---

## 8. 下一步

- 长上下文为什么会变差：[Context Rot 与 Lost in the Middle](./03-context-rot.md)
- 这些 token 花多少钱、拖多少延迟：[上下文的成本与延迟模型](./04-cost-latency.md)
- 怎么在各块之间分配窗口：[按预算分配上下文（Context Budget）](./05-context-budget.md)
- tokenizer 和 attention 更细的讲解，可以回看：[模型如何读 Prompt：Tokenizer、Attention 与训练数据](../../../04-prompt-engineering/docs/01-foundations/03-how-models-read.md)

## 参考资料

- OpenAI tiktoken: https://github.com/openai/tiktoken
- Anthropic Token counting: https://platform.claude.com/docs/en/build-with-claude/token-counting
- Vaswani et al., "Attention Is All You Need": https://arxiv.org/abs/1706.03762
