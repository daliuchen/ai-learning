# PE 04：Temperature、Top-p 与采样

> 同一个 prompt、同一个模型，问 10 次能得到 10 种答案。这不是故障，是 LLM 的设计：每一步都按概率采样下一个 token。这篇讲 temperature、top_p、top_k 三个参数怎样控制这种随机性，以及现在各家 API 收回这些参数之后，生产里该怎么处理。

各家 API 正在收回这几个采样参数（以 2026 年 11 月为准）：

| 厂商 | 现状 |
|------|------|
| Anthropic | Claude 4.7+ 的模型不再接受 `temperature` / `top_p` / `top_k`；API 版本 `2026-10-21` 起，对所有模型发送这三个参数都返回 400（`anthropic` 2.x SDK 的 `messages.create` 已经没有这些参数）。Fable 5/5.1、Opus 5.5、Sonnet 5.5、Haiku 5.5 都不能调 |
| Gemini 3+ | 官方建议保持默认（temperature 低于 1.0 可能出现循环或推理变差），不建议调 |
| OpenAI GPT-6 | 全系列是带 reasoning 的模型，是否接受自定义 temperature 以具体模型页为准；SDK 里参数仍在 |
| Qwen（百炼，OpenAI 兼容）/ 开源模型 | 仍可用 temperature / top_p / top_k（本篇代码用 `qwen-flash`） |

下文讲的原理依然成立，它解释了同一个 prompt 为什么每次结果不同。但"评测 judge 必须 temperature=0"这类操作建议要改成：能设就设；不能设的（Claude 新模型）就靠结构化输出、简短的评分细则、多次评分取多数或中位数、固定 judge 版本。同一输入重复评 N 次的一致率，可以当作 judge 的质量指标，详见 [LLM-as-Judge](../05-by-task/05-judge.md)。

---

## 1. 一次生成发生了什么

模型给"今天天气真"生成下一个 token 时，内部是这样的：

```
输入: "今天天气真"
   ↓
模型算出整个词表上的概率分布:
   "好"   → 0.62
   "棒"   → 0.18
   "晴"   → 0.08
   "热"   → 0.05
   "差"   → 0.02
   ...（剩下的 ≈ 0.05）
   ↓
按某种策略选一个:
   - 贪心：永远选 "好"
   - 采样：按概率随机抽
```

贪心解码每次都选概率最高的，输出确定，但缺少变化。采样引入随机性，输出有变化，读起来也更自然。LLM API 默认用采样。

---

## 2. Temperature：让分布更平或更陡

Temperature `T` 在采样前对概率分布做一次变换：

```
new_prob(i) = exp(logit_i / T) / sum(exp(logit_j / T))
```

- T → 0：分布变陡，最高概率的 token 几乎独占，接近贪心
- T = 1：原始分布
- T → ∞：分布被拉平，接近均匀采样

### 实际效果

| temperature | 同一 prompt 多次结果 | 适用场景 |
|-------------|---------------------|---------|
| 0 | 几乎完全一样 | 抽取、分类、结构化输出、需要稳定的工具调用 |
| 0.3-0.5 | 微小变化 | 总结、问答、解释 |
| 0.7-1.0 | 明显变化 | 创意写作、brainstorming、对话 |
| 1.2-2.0 | 失控、可能不通顺 | 不建议生产用 |

### 代码

```python
# Anthropic：Claude 4.7+ / API 2026-10-21 起已无采样参数，不要传 temperature
client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=1024,
    messages=[...],
)

# OpenAI
client.chat.completions.create(
    model="gpt-6.1-terra",
    temperature=0,   # GPT-6 是否接受自定义 temperature 以模型页为准；不接受就删掉这行
    messages=[...],
)

# Gemini
client.models.generate_content(
    model="gemini-3.8-flash",
    contents="...",
    config={"temperature": 0},   # Gemini 3+ 官方建议保持默认，仅旧模型/确需时才设
)
```

> `temperature=0` 并不保证完全确定。GPU 上浮点加法不满足结合律，累加顺序随 batch 组成变化，结果就会有细微差别。它只能让结果接近稳定。

---

## 3. Top-p（Nucleus Sampling）

只在累积概率达到 p 的最小 token 集合里采样。

```
概率分布: [0.62, 0.18, 0.08, 0.05, 0.02, ...]
top_p = 0.9
   ↓
取前几个直到累积 >= 0.9: [0.62, 0.18, 0.08] (累积 0.88) + 0.05 (累积 0.93)
   ↓
只在这 4 个里按概率采样
```

它的作用是砍掉长尾里的低概率 token，避免冒出很奇怪的词。

| top_p | 含义 |
|-------|------|
| 1.0 | 不裁剪 |
| 0.9 | 常用值，砍掉极不可能的 token |
| 0.5 | 更激进的裁剪 |
| 0.1 | 接近贪心 |

---

## 4. Top-k

只在概率最高的 k 个 token 里采样。比 top-p 粗糙，但简单。

```python
# Gemini（google-genai）；Qwen/开源模型经 OpenAI 兼容接口时用 extra_body={"top_k": 40}
config = {"top_k": 40}
```

OpenAI API 不直接提供 top_k，只有 top_p。Gemini 和开源模型有；Anthropic 的新模型已经移除。

---

## 5. 常见任务的参数组合

下表只适用于还能调采样参数的模型（千问、开源模型；GPT-6 以模型页为准）。Claude 5.x 不接受 `temperature`、`top_p`、`top_k`，传了返回 400，用 Claude 时跳过这张表，靠 prompt、结构化输出和多次采样取中位数来控制稳定性。

| 任务 | temperature | top_p | top_k | 备注 |
|------|-------------|-------|-------|------|
| 结构化抽取 | 0 | 1.0 | — | 最高稳定性 |
| 分类（标签输出） | 0 | 1.0 | — | 同上 |
| 工具调用（参数生成） | 0 | 1.0 | — | 参数不能漂 |
| 翻译 | 0.2-0.3 | 1.0 | — | 允许一点变化 |
| 总结 | 0.3-0.5 | 0.9 | — | 适度自由 |
| Q&A（知识型） | 0.3 | 1.0 | — | 准确优先 |
| 创意写作 | 0.7-1.0 | 0.95 | 40 | 自然多样 |
| Brainstorming | 0.8-1.2 | 0.95 | 40 | 越多样越好 |
| 代码生成 | 0-0.3 | 1.0 | — | 代码错一个字就跑不了 |

---

## 6. 不稳定还是 bug：怎么区分

调 prompt 时，同一份 input 跑两次结果不同。这是 prompt 写得不够好，还是采样的随机性？

### 判断方法

```python
# 1. temperature=0 跑 5 次
results_t0 = [run(prompt, input, temperature=0) for _ in range(5)]
print(set(results_t0))
```

5 次完全一样，说明这是 prompt 和模型决定的确定行为。5 次仍有变化，说明模型内部还有少量残余随机性。

```python
# 2. temperature=1 跑 10 次
results_t1 = [run(prompt, input, temperature=1) for _ in range(10)]
```

如果变化都在合理范围内（语义一致、措辞不同），prompt 是稳健的；如果偶尔大幅跑偏（10 次里有 2 次完全错），prompt 还不够稳健。

能设采样参数的模型，评测时要固定 temperature（建议设 0），评测结果才可重复，生产环境可以调高。Claude 5.x 不接受这个参数，上面的判断方法要换成：同一输入多跑几次，看结果的分布。

---

## 7. Self-Consistency：利用随机性

随机性也有用处。有些场景可以多采样几次再投票，准确率反而更高。

```python
# 同一个数学题问 5 次（temp=0.7），看哪个答案出现最多
answers = []
for _ in range(5):
    a = solve_math(question, temperature=0.7)
    answers.append(a)

# 多数投票
from collections import Counter
final = Counter(answers).most_common(1)[0][0]
```

Wang et al. 2022 的 "Self-Consistency" 论文显示，这种多采样加投票的做法在多步推理题上通常有几个百分点的提升，采样数越多边际收益越小；代价是调用成本翻 N 倍（上面的例子是 5 倍）。

详见 [Self-Consistency 采样投票](../03-techniques/09-self-consistency.md)。

---

## 8. seed 参数：复现实验

OpenAI、Gemini 和多数开源或兼容接口支持 `seed`：

```python
client.chat.completions.create(
    model="gpt-6.1-terra",
    seed=42,
    temperature=0,   # GPT-6 是否接受自定义 temperature 以模型页为准；不接受就删掉这行
    messages=[...],
)
```

seed、prompt、temperature 都相同时，输出接近确定。

> `seed` 只是尽力而为，OpenAI 文档明确说不保证完全可复现（服务端升级后结果会变），但比不设 seed 的可重复性高得多。Anthropic API 不提供 seed。

---

## 9. 几个反直觉的地方

### 9.1 temperature=0 不一定准确率最高
很多人以为最确定就是最准。但在不少推理任务上，`temperature=0.3-0.5` 加多次采样和投票，比 `temperature=0` 更准（见 self-consistency）。

### 9.2 高 temperature 不等于创造力
调高 temperature 只是把分布拉平，让低概率的词更容易被选中，结果往往是输出不通顺，并不会更有创意。想要创意，得靠 prompt 设计，比如要求从多个角度写、给出反例。

### 9.3 temperature 补不了模型不会的东西
模型不知道的事实，temperature 调到多少都答不对，只会换着花样给出不同的错误答案。

### 9.4 评测用的 judge 要尽量压低随机性
评测脚本里让 LLM 当 judge 时，能设 temperature 的模型设成 0。不能设的（Claude 新模型）和官方不建议调的（Gemini 3+），靠结构化输出、清晰的评分细则、固定 judge 模型版本、多次评分取多数来压低波动。否则"v1 和 v2 哪个好"两次评出来结论不同，评测就没有意义了。

---

## 10. demo：感受 temperature

```python
"""同一个 prompt，三种 temperature，各跑 5 次看变化（用千问 qwen-flash，OpenAI 兼容接口）"""
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
prompt = "用一句话描述北京。"

for temp in [0, 0.5, 1.0]:
    print(f"\n=== temperature={temp} ===")
    for i in range(5):
        resp = client.chat.completions.create(
            model="qwen-flash",
            max_tokens=100,
            temperature=temp,
            messages=[{"role": "user", "content": prompt}],
        )
        print(f"  [{i+1}] {resp.choices[0].message.content}")
```

> Claude 新模型不接受 `temperature`，所以这个 demo 用的是仍然支持采样参数的模型。

预期能看到：temp=0 时五次几乎一样；temp=0.5 时措辞略有调整，意思基本不变；temp=1.0 时风格变化很大，一会儿说"古都"，一会儿说"摩登"，一会儿又去讲地理。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 抽取任务用了 temp=1 | 改成 temp=0 |
| 创意任务用了 temp=0 | 输出会很呆板，调到 0.7 以上 |
| 评测时没固定 temp | 能设就设 0；不能设的模型靠结构化输出 + 多次评分取多数 |
| 给 Claude 新模型传 temperature | 返回 400，删掉 |
| 以为 temp=0 就完全确定 | GPU 浮点仍有残余随机性，重要场景再加 seed |
| 想要多样性就一味调高 temp | 先在 prompt 里要求多角度，再考虑 temp |
| 以为各家模型的 temp 参考值通用 | 不同模型的 0.7 表现不一样，要各自测 |
| 超高 temp 输出乱码 | temp 超过 1.2 通常没用，保持在 0 到 1 之间 |

---

## 12. 下一步

- [评测先于 Prompt](./05-eval-first.md)
- [Self-Consistency 采样投票](../03-techniques/09-self-consistency.md)

## 参考资料

- Self-Consistency paper: https://arxiv.org/abs/2203.11171
- Anthropic API 版本 2026-10-21 迁移指南: https://platform.claude.com/docs/en/api/migration-2026-10-21
- Gemini prompting strategies（temperature 建议）: https://ai.google.dev/gemini-api/docs/prompting-strategies
- OpenAI seed 文档: https://developers.openai.com/api/docs/api-reference
- Nucleus sampling paper: https://arxiv.org/abs/1904.09751
