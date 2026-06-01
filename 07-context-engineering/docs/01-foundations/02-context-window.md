# CE 02：上下文窗口到底是什么

> **一句话**：上下文窗口是模型一次能「看见」的全部 token 的总长度上限，里面塞着 system + 工具定义 + 检索 + 历史 + 用户输入，外加要留给输出的预算。窗口里位置很重要，因为 self-attention 是 O(n²)、模型靠位置编码区分先后——这套机制决定了你装什么、装多少、装在哪都不是随便的。

---

## 1. Token 是什么

模型不直接读字符，也不直接读单词，它读 **token**——介于字符和单词之间的「子词单元」。

- 英文里 1 个 token ≈ 4 个字符 ≈ 0.75 个单词。
- 中文一个汉字通常是 1～2 个 token（不同 tokenizer 差异大）。
- 标点、空格、换行都算 token。

负责把文本切成 token 的组件叫 **tokenizer**（分词器）。主流模型用的是 BPE（Byte Pair Encoding）系的变体：

| 模型家族 | tokenizer | 备注 |
|----------|-----------|------|
| GPT-4o / GPT-5 系 | `o200k_base` | OpenAI tiktoken |
| GPT-3.5 / 老 GPT-4 | `cl100k_base` | tiktoken |
| Claude | 专有 tokenizer | 可用 SDK 的 count_tokens 接口估 |
| Gemini | SentencePiece 系 | 用 `count_tokens` API |

> ⚠️ 不同模型 token 数不一样。同一段中文，GPT-4o 数出来和 Claude 数出来可能差 20%+。**别用一个 tokenizer 的数字去估另一个模型的成本**。

---

## 2. 用 tiktoken 数 token

OpenAI 的 `tiktoken` 是最常用的本地数 token 工具，离线、快：

```python
# pip install tiktoken
import tiktoken

# GPT-4o / GPT-5 系用 o200k_base
enc = tiktoken.encoding_for_model("gpt-4o")

text_en = "Context engineering is about what you put in the window."
text_zh = "上下文工程关心的是窗口里到底装什么。"

tokens_en = enc.encode(text_en)
tokens_zh = enc.encode(text_zh)

print(f"英文: {len(tokens_en)} tokens")   # ≈ 11
print(f"中文: {len(tokens_zh)} tokens")   # ≈ 17（中文更费 token）

# 看看具体切成了什么
print([enc.decode([t]) for t in tokens_zh[:6]])
# ['上', '下', '文', '工', '程', '关'] 之类
```

Claude 则建议用官方 SDK 的计数接口，别拿 tiktoken 凑：

```python
# pip install anthropic
import anthropic

client = anthropic.Anthropic()
resp = client.messages.count_tokens(
    model="claude-opus-4-20250514",
    messages=[{"role": "user", "content": "上下文工程关心装什么。"}],
)
print(resp.input_tokens)
```

---

## 3. 窗口里有什么

一次调用的输入窗口，由这几块拼接而成（顺序由 API/框架决定）：

```
[ system ]      角色 / 规则 / 全局约束
[ tools ]       工具定义 schema（Agent / function calling 场景）
[ retrieved ]   RAG 检索来的文档片段
[ history ]     前面 N 轮 user / assistant 消息
[ user ]        本轮用户输入
─────────────────────────────────
↑ 以上全部加起来 = 输入 token 数，必须 ≤ 窗口上限 - 输出预算
```

每一块都吃 token。Agent 场景下，工具定义和工具返回结果常常是大头——一个返回 JSON 的工具调几次，几千 token 就没了。

---

## 4. 各模型窗口大小（2025-2026）

| 模型 | 上下文窗口 | 最大输出 |
|------|-----------|----------|
| GPT-4o | 128K | 16K |
| GPT-5 系 | 128K～400K（按档位） | 较大 |
| Claude Opus / Sonnet | 200K（部分 1M beta） | 一般 8K～64K |
| Claude Haiku | 200K | 较小 |
| Gemini 2.x Pro / Flash | 1M（部分 2M） | 8K 级 |

> 窗口大 ≠ 输出长。**输入窗口和输出 token 是两笔账**。

---

## 5. 输入窗口 vs 输出预算

这是新手最容易踩的坑：

```
窗口上限 = 输入 tokens + 输出 tokens
```

比如 Claude 200K 窗口，你 `max_tokens=8000` 想让它写长文，那留给输入的就只有约 192K。如果你的输入已经塞到 199K，模型只剩 1K 空间写输出——**要么被截断，要么直接报错**。

```python
# ❌ 输入塞满，没给输出留空间
messages = build_messages(huge_context)  # 输入 199K
client.messages.create(model=..., messages=messages, max_tokens=8000)
# → 报错：199K + 8K > 200K

# ✅ 先核算输入预算，给输出留够
INPUT_BUDGET = 200_000 - 8_000  # 给输出留 8K
context = truncate_to(context, INPUT_BUDGET)
```

预算分配的系统方法见 [05-context-budget.md](./05-context-budget.md)。

---

## 6. 为什么位置很重要：attention 与位置编码

模型读上下文靠的是 **self-attention**：每个 token 都要和其他所有 token 算「注意力」。

- 序列长度 n，attention 的计算量是 **O(n²)**——这是长上下文又贵又慢的根本原因（见 [04-cost-latency.md](./04-cost-latency.md)）。
- Transformer 本身不知道 token 的先后，靠 **位置编码（positional encoding，如 RoPE）** 注入「第几个」的信息。

后果有二：

1. **位置会被模型当成信号**。开头和结尾的 token 往往获得更稳定的注意力，埋在中间的内容容易被忽略——这就是 lost-in-the-middle / context rot（见 [03-context-rot.md](./03-context-rot.md)）。
2. **越长越稀释**。n 翻倍，每个 token 分到的「注意力份额」整体被摊薄，关键信息更容易被淹没。

```
注意力强度（经验性示意）
高 │■                              ■
   │■■                          ■■
   │ ■■                       ■■
低 │   ■■■■■■■■■■■■■■■■■■■■■■
   └──────────────────────────────
    开头        中间          结尾
```

工程含义一句话：**重要的东西放头尾，别埋中间**。

---

## 7. 常见坑

| 坑 | 说明 |
|----|------|
| 拿 tiktoken 估 Claude/Gemini 成本 | tokenizer 不同，数字对不上，用各家官方计数 |
| 以为「窗口 = 能用满」 | 要扣掉输出预算，还要防 rot |
| 忽略工具定义占用 | Agent 里工具 schema + 返回值常是 token 大头 |
| 中文当英文估 | 中文更费 token，按字数 ×1.5～2 粗估 |
| 历史无限堆 | 多轮下来历史撑爆窗口，要截断 / 摘要 |

---

## 8. 下一步

- 📖 长上下文为什么会「烂掉」：Context Rot → [03-context-rot.md](./03-context-rot.md)
- 📖 这些 token 要花多少钱、拖多少延迟 → [04-cost-latency.md](./04-cost-latency.md)
- 📖 怎么在各块之间分配窗口预算 → [05-context-budget.md](./05-context-budget.md)
- 📖 tokenizer / attention 的更细致讲解，可回看 → [04-prompt-engineering/01-foundations/03-how-models-read.md](../../../04-prompt-engineering/docs/01-foundations/03-how-models-read.md)

## 参考资料

- OpenAI tiktoken: https://github.com/openai/tiktoken
- Anthropic Token counting: https://docs.anthropic.com/en/docs/build-with-claude/token-counting
- Vaswani et al., "Attention Is All You Need": https://arxiv.org/abs/1706.03762
