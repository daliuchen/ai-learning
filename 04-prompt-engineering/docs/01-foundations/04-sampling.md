# PE 04：不确定性的源头 —— Temperature / Top-p / Sampling

> **一句话**：同一个 prompt 同一个模型问 10 次给 10 种答案——这种"不稳定"是 LLM 内置的设计：每一步生成都在按概率**采样**下一个 token。理解 temperature / top_p / top_k 三个旋钮怎么控这种不确定性，你才能在生产里精确调试。

---

## 1. 一次生成发生了什么

模型生成"今天天气真"的下一个 token 时，内部发生的事：

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

**贪心** = 永远选概率最高的 → 输出确定但缺乏多样性。
**采样** = 引入随机性 → 输出有变化、更"自然"。

LLM API 默认用采样。

---

## 2. Temperature：把分布"拉平"或"陡峭"

Temperature `T` 在采样前对概率分布做变换：

```
new_prob(i) = exp(logit_i / T) / sum(exp(logit_j / T))
```

- **T → 0**：分布变陡峭，最高概率 token 几乎独占 → 接近贪心
- **T = 1**：原始分布
- **T → ∞**：分布拉平 → 接近均匀采样

### 直观效果

| temperature | 同一 prompt 多次结果 | 适用场景 |
|-------------|---------------------|---------|
| **0** | 几乎完全一样 | 抽取、分类、结构化输出、需要稳定的工具调用 |
| **0.3-0.5** | 微小变化 | 总结、问答、解释 |
| **0.7-1.0** | 明显变化 | 创意写作、brainstorming、对话 |
| **1.2-2.0** | 失控、可能不通顺 | 不建议生产用 |

### 代码

```python
# Anthropic
client.messages.create(
    model="claude-sonnet-4-6",
    temperature=0,        # 抽取任务用 0
    max_tokens=1024,
    messages=[...],
)

# OpenAI
client.chat.completions.create(
    model="gpt-4o",
    temperature=0,
    messages=[...],
)

# Gemini
client.models.generate_content(
    model="gemini-2.0-flash",
    contents="...",
    config={"temperature": 0},
)
```

> ⚠️ `temperature=0` 不保证完全确定（GPU 浮点累加非交换、batched inference 等带来"剩余随机"），但能让结果接近稳定。

---

## 3. Top-p（Nucleus Sampling）

只在"累积概率 ≥ p"的最小 token 集合里采样。

```
概率分布: [0.62, 0.18, 0.08, 0.05, 0.02, ...]
top_p = 0.9
   ↓
取前几个直到累积 >= 0.9: [0.62, 0.18, 0.08] (累积 0.88) + 0.05 (累积 0.93)
   ↓
只在这 4 个里按概率采样
```

效果：**砍掉长尾低概率 token**，避免输出"很奇怪"的词。

| top_p | 含义 |
|-------|------|
| 1.0 | 不裁剪 |
| 0.9 | 主流选择，砍掉极不可能的 token |
| 0.5 | 更激进的裁剪 |
| 0.1 | 接近贪心 |

---

## 4. Top-k

只在 top k 个概率最高的 token 里采样。比 top-p 更粗暴，但简单。

```python
client.messages.create(
    model="claude-sonnet-4-6",
    top_k=40,
    ...
)
```

OpenAI API 不直接暴露 top_k（用 top_p）。Anthropic / Gemini 都有。

---

## 5. 实用组合：常见任务的参数

| 任务 | temperature | top_p | top_k | 备注 |
|------|-------------|-------|-------|------|
| 结构化抽取 | 0 | 1.0 | — | 最高稳定性 |
| 分类（标签输出） | 0 | 1.0 | — | 同上 |
| 工具调用（参数生成） | 0 | 1.0 | — | 参数不能漂 |
| 翻译 | 0.2-0.3 | 1.0 | — | 一点点变化 |
| 总结 | 0.3-0.5 | 0.9 | — | 适度自由 |
| Q&A（知识型） | 0.3 | 1.0 | — | 准确为先 |
| 创意写作 | 0.7-1.0 | 0.95 | 40 | 自然多样 |
| Brainstorming | 0.8-1.2 | 0.95 | 40 | 越多样越好 |
| 代码生成 | 0-0.3 | 1.0 | — | 代码错一字就废 |

---

## 6. 不稳定 vs Bug：怎么区分

调 prompt 时同一份 input 跑两次得到不同结果——是 prompt 写得不够好，还是采样随机性？

### 判断方法

```python
# 1. temperature=0 跑 5 次
results_t0 = [run(prompt, input, temperature=0) for _ in range(5)]
print(set(results_t0))
```

- 5 次完全一样 → 是 prompt / 模型确定的行为
- 5 次仍有变化 → 模型内部还有少量随机（"剩余熵"）

```python
# 2. temperature=1 跑 10 次
results_t1 = [run(prompt, input, temperature=1) for _ in range(10)]
```

- 变化都在合理范围（语义同、表述不同）→ prompt 鲁棒
- 偶尔大跑偏（10 次里 2 次完全错） → prompt 不够鲁棒

**关键**：评测时**永远固定 temperature**（建议 0），让评测结果可重复。生产可以调高。

---

## 7. Self-Consistency：把不确定性变成优势

不确定性不全是坏事——有些场景可以**多采样投票**，提升准确率。

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

研究表明（Wang et al. 2022 "Self-Consistency"）这种"多采样 + 投票"在推理任务上可以提升 10-20 分准确率。代价是 5 倍 cost。

详细在 [03-techniques/09-self-consistency.md](../03-techniques/09-self-consistency.md)。

---

## 8. seed 参数：复现实验

OpenAI 和 Gemini 都支持 `seed`：

```python
client.chat.completions.create(
    model="gpt-4o",
    seed=42,
    temperature=0,
    messages=[...],
)
```

同一个 seed + 同一个 prompt + 同 temperature → 输出**接近**确定。

> 注意：`seed` 是"尽力而为"——OpenAI 文档明说不保证完全可复现（servers 升级会变）。但比无 seed 的可重复性高得多。Anthropic API 目前不暴露 seed。

---

## 9. 几个反直觉发现

### 9.1 temperature=0 不一定准确率最高
人们直觉以为"最确定 = 最准"，但很多推理任务上 `temperature=0.3-0.5 + multi-sample + voting` 比 `temperature=0` 还准（详见 self-consistency）。

### 9.2 高 temperature 不是"创造力"开关
高 temp 只是把分布拉平，让低概率词更容易被选。这经常导致**输出不通顺**而不是"更有创意"。真正的"创意"来自 prompt 设计（要求多角度、给反例等）。

### 9.3 temperature 不能修复"模型不会的事"
如果模型本身不知道某事实，temperature 调任何值都给不出正确答案——只是给出**不同的错误版本**。

### 9.4 evaluator 必须 temperature=0
评测脚本里调 LLM 当 judge 时，**一定要 temperature=0**。不然 "v1 vs v2 哪个好"两次评出来不一样，评测无意义。

---

## 10. demo：感受 temperature

```python
# demos/foundations/04_temperature_demo.py
"""同一个 prompt，三种 temperature，各跑 5 次看变化"""
import anthropic

client = anthropic.Anthropic()
prompt = "用一句话描述北京。"

for temp in [0, 0.5, 1.0]:
    print(f"\n=== temperature={temp} ===")
    for i in range(5):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            temperature=temp,
            messages=[{"role": "user", "content": prompt}],
        )
        print(f"  [{i+1}] {resp.content[0].text}")
```

期望观察：
- temp=0 五次几乎完全一样
- temp=0.5 表述微调，主语义稳
- temp=1.0 风格大变（一会儿"古都"一会儿"摩登"一会儿描述地理）

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **抽取任务用了 temp=1** | 改 temp=0 |
| **创意任务用 temp=0** | 输出会很 robot，调到 0.7+ |
| **评测时没固定 temp** | 评测脚本永远 temp=0 |
| **以为 temp=0 完全确定** | 仍有 GPU 浮点剩余熵，重要场景叠 seed |
| **想多样性，盲目调高 temp** | 先改 prompt 要"多角度"，再考虑 temp |
| **不同模型 temp 参考值通用** | 不同模型的 0.7 表现不一样，要各自测 |
| **超高 temp 输出乱码** | temp > 1.2 通常没用，保持 0-1 |

---

## 12. 下一步

- 📖 评测先于 prompt → [05-eval-first.md](./05-eval-first.md)
- 📖 self-consistency 实战 → [03-techniques/09-self-consistency.md](../03-techniques/09-self-consistency.md)

## 参考资料

- Self-Consistency paper: https://arxiv.org/abs/2203.11171
- Anthropic temperature 文档: https://docs.anthropic.com/en/api/messages
- OpenAI seed 文档: https://platform.openai.com/docs/api-reference/chat/create#chat-create-seed
- Nucleus sampling paper: https://arxiv.org/abs/1904.09751
