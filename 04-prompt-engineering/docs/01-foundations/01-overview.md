# PE 01：Prompt Engineering 是什么 / 不是什么

> 很多人把 Prompt Engineering 理解成换个更巧的写法，让模型多吐出点好东西。我在这本手册里讲的是另一回事：一套以评测驱动、能持续迭代、团队可以接手的工程流程。做完一项 PE 工作，交付的是 prompt、评测集、迭代记录和监控指标这一整套，prompt 文本只是其中一项。

---

## 1. 一个很常见的误会

「PE 不就是 prompt 写得讲究点吗」「给 ChatGPT 念几句咒语就行了吧」。抱着这种理解，人会一直停在凭感觉调 prompt 的阶段。

实际的工作流是这样的：

```
[需求]
   ↓ 把需求转成 "通过/失败" 的判定标准
[评测集 v0]（5-10 条样本，含 input + 期望行为）
   ↓ 写第一版 prompt
[Prompt v0]
   ↓ 跑评测
[基线指标]（80% 通过率？60%？）
   ↓ 看失败案例 → 假设原因 → 改一处
[Prompt v1]
   ↓ 跑同一份评测集对比 v0
[确认变好了 / 变差了 / 互有胜负]
   ↓ ...继续
[v_n 达标]
   ↓ 上线
[生产监控 → 发现新失败 → 加进评测集 → 再迭代]
```

这是一个闭环。想出一句更好的 prompt，只是其中一步。

---

## 2. PE 解决什么 / 不解决什么

### 2.1 PE 能解决的

| 问题 | PE 怎么帮 |
|------|----------|
| LLM 输出格式不稳定 | 结构化输出（JSON / XML / Schema）+ few-shot 示例锚定格式 |
| 复杂推理出错 | CoT + 任务拆解 |
| 角色 / 边界跑偏 | 在 system message 里写清 persona 和拒答边界 |
| 不知道结果好不好 | 评测集 + LLM-as-judge |
| 多人协作改坏 | Prompt Hub + 版本化 + 评测把关 |
| 跨家不兼容 | 抽象 prompt 模板 + 适配层 |

### 2.2 PE 不能解决的

| 问题 | 该用什么 |
|------|----------|
| 模型本身能力不够 | 换更强的模型 / 微调 |
| 需要私有数据知识 | RAG 或微调 |
| 实时数据 | Tool call（连搜索 / DB / API） |
| 任务太长一次跑不完 | 拆 sub-agent + 工作流 |
| 性能 / 成本 | 用更小的模型、prompt caching、批处理 |

> 很多团队把模型能力问题当成 prompt 问题，调了三周 prompt，最后从 Sonnet 换成 Opus 就解决了。先怀疑能力上限，再去调 prompt。

---

## 3. PE 的三层认知（你在哪一层？）

| 层 | 表现 | 痛点 |
|---|------|------|
| L1 玄学党 | 凭感觉加"think step by step"、"重要！必须！"之类的口诀；改了 prompt 也说不清有没有变好 | 永远不收敛 |
| L2 技法党 | 知道 CoT / Few-shot / XML 标签等技法，会按场景选 | 不知道何时停，说不清 v3 比 v2 好在哪 |
| L3 流程党 | 评测驱动、版本化、监控线上漂移 | 工程基础齐全，迭代能收敛 |

这本手册想做的，是把读者从 L1、L2 带到 L3。

---

## 4. PE 的产物清单

一项做完的 PE 工作，交付物应该长这样：

```
my-feature/
├── prompts/
│   ├── v3.0.0.txt              # 当前生产版本
│   ├── v3.0.0-system.txt
│   └── CHANGELOG.md            # 每个版本改了什么、为什么改
├── evalset/
│   ├── happy_path.jsonl        # 30-50 条 happy path 样本
│   ├── edge_cases.jsonl        # 20+ 条边界 / 攻击样本
│   └── regression.jsonl        # 历史 bug 防回归
├── eval_runner.py              # 跑评测的脚本
├── results/
│   └── v3.0.0_2026-05-20.json  # 评测结果
└── README.md                   # 给团队的使用说明
```

只改了 prompt 文本、没有评测结果的 PR，我会直接打回。

---

## 5. PE 和你已有技能的关系

| 已有技能 | PE 怎么用上 |
|---------|------------|
| 软件工程（git / CI） | prompt 也进 git，CI 里跑评测 |
| 单元测试 | evalset 就是 LLM 应用的测试集 |
| 产品迭代（PMF） | prompt 迭代也是 baseline → 假设 → 实验 |
| 数据分析 | 评测结果是数据，要会读 |
| LLM API 调用 | PE 补上的是方法 |

PE 没有多少新东西，它把已有的工程方法搬到了输出不确定的 LLM 场景里。

---

## 6. 三家 API 的最小 prompt

后面的章节都要写代码，先看三家最简单的调用。

### Anthropic (Claude)

```python
import anthropic
client = anthropic.Anthropic()
resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=1024,
    system="你是简洁的助手。",
    messages=[{"role": "user", "content": "巴黎是哪国首都？"}],
)
print(resp.content[-1].text)
```

### OpenAI (GPT)

```python
from openai import OpenAI
client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-6.1-terra",
    messages=[
        {"role": "developer", "content": "你是简洁的助手。"},
        {"role": "user", "content": "巴黎是哪国首都？"},
    ],
)
print(resp.choices[0].message.content)
```

### Google (Gemini)

```python
from google import genai
client = genai.Client()
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="巴黎是哪国首都？",
    config={"system_instruction": "你是简洁的助手。"},
)
print(resp.text)
```

三家的差异：

| 维度 | Anthropic | OpenAI | Gemini |
|------|-----------|--------|--------|
| system 位置 | 顶级参数 `system` | developer（新）/ system 消息，或 Responses 的 `instructions` | config 内 system_instruction |
| 消息格式 | messages 数组 | messages 数组 | contents |
| 输出格式 | content[-1].text（5.x 默认带 thinking 块，别取 [0]） | choices[0].message.content | text |
| max_tokens | 必填 | 可选 | 可选 |

Claude 5.x 默认开着 adaptive thinking，thinking token 也算在 `max_tokens` 里。简单请求它可能不思考，复杂请求可能想很久，所以本手册里 Claude 的示例都给 `max_tokens` 留了余量；生产代码还应该检查 `stop_reason` 是不是 `max_tokens`。这些差异在 06-models 一章展开。

---

## 7. 这本手册最想说的

评测集是这本手册的主线。写代码要配测试，写 prompt 也要配评测集：没有它，改完 prompt 判断不了是变好还是变坏，上线后出了回归也发现不了。后面每一章的做法都建立在先有评测集这个前提上。

---

## 8. 常见误区

| 误区 | 实际情况 |
|------|------|
| "Prompt 里 instruction 加得越多越好" | 过度 prompting 会和训练数据的习惯冲突，效果反而变差 |
| "好 prompt 一定要 think step by step" | 简单任务加 CoT 只是多花 token、多等延迟 |
| "把所有边界条件都列上" | 注意力预算有限，重要约束放在最后 |
| "用更高级的模型就不用调 prompt 了" | 正相反：更强的模型对结构化输出和指令遵循更敏感，需要适配 |
| "prompt 越长越具体越好" | 越长信噪比越低；上下文越长，夹在中间的内容越容易被忽略 |
| "few-shot 不可或缺" | 简单任务 zero-shot 就够；指令写清楚比多放几个 example 更重要 |
| "套用别人的 prompt 模板就行" | 模板告诉你有哪些技法，不告诉你自己的任务该用哪个 |

这些误区后面各有章节细讲。

---

## 9. 下一步

- [一条 Prompt 的解剖](./02-anatomy.md)
- [模型如何读 Prompt：Tokenizer、Attention 与训练数据](./03-how-models-read.md)
- [Temperature、Top-p 与采样](./04-sampling.md)
- [评测先于 Prompt](./05-eval-first.md)：整本手册流程主线的起点

## 参考资料

- Anthropic Prompt Engineering Overview: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview
- OpenAI Prompting Guide: https://platform.openai.com/docs/guides/prompt-engineering
- Google Prompting Handbook: https://ai.google.dev/gemini-api/docs/prompting-intro
- 跨手册关联：[Pydantic AI 06：结构化输出（Output Types）](../../../02-pydantic-ai/docs/01-basics/06-output-types.md) · [LangSmith 04：Prompt Hub 与 Playground](../../../01-langchain/docs/02-langsmith/04-prompt-hub.md)
