# PE 01：Prompt Engineering 是什么 / 不是什么

> **一句话**：Prompt Engineering 不是"用更花哨的写法骗 LLM 输出更好"，而是「**建立一套以评测为驱动、能持续迭代、可被团队复用的 prompt 工程流程**」。它的核心产物**不是一条 prompt**，而是 prompt + 评测集 + 迭代记录 + 监控指标的组合。

---

## 1. 一个很常见的误会

「我也是写 prompt 的」+「PE 不就是写得讲究点吗」+「ChatGPT 不是出几个咒语就行了？」——这种理解会让你卡在"凭感觉调"阶段一辈子。

PE 真正的工作流是这样的：

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

这是一个**闭环工程流程**，不是"想出一句更好的 prompt"。

---

## 2. PE 解决什么 / 不解决什么

### 2.1 PE 能解决的

| 问题 | PE 怎么帮 |
|------|----------|
| LLM 输出格式不稳定 | 结构化输出（JSON / XML / Schema）+ few-shot anchor |
| 复杂推理出错 | CoT + 任务拆解 |
| 角色 / 边界跑偏 | system message 写好 persona 与 refuse 边界 |
| 不知道结果好不好 | 评测集 + LLM-as-judge |
| 多人协作改坏 | Prompt Hub + 版本化 + 评测把关 |
| 跨家不兼容 | 抽象 prompt 模板 + 适配层 |

### 2.2 PE 不能解决的

| 问题 | 真实解法 |
|------|----------|
| 模型本身能力不够 | 换更强的模型 / 微调 |
| 需要私有数据知识 | RAG 或微调 |
| 实时数据 | Tool call（连搜索 / DB / API） |
| 任务太长一次跑不完 | 拆 sub-agent + 工作流 |
| 性能 / 成本 | 用更小的模型、prompt caching、批处理 |

> ⚠️ **重要**：很多团队把"模型能力问题"误当成"prompt 问题"，疯狂调 prompt 调了三周——最后换了 Sonnet → Opus 就解决了。**先怀疑能力上限，再调 prompt**。

---

## 3. PE 的三层认知（你在哪一层？）

| 层 | 表现 | 痛点 |
|---|------|------|
| **L1 玄学党** | 凭感觉加"think step by step"、"重要！必须！"等口诀；改了 prompt 不知道有没有变好 | 永远不收敛 |
| **L2 技法党** | 知道 CoT / Few-shot / XML 标签等技法，会按场景选 | 不知道何时停、不知道 v3 比 v2 好在哪 |
| **L3 流程党** | 评测驱动、版本化、监控线上漂移 | 工程化基础完备，迭代有收敛 |

本手册目标：**把你从 L1/L2 带到 L3**。

---

## 4. PE 的产物清单

一个"做完了"的 PE 工作交付的不是一句 prompt，而是这套东西：

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

这是 PE 工作的"完整可交付物"。一份只有 prompt 文本的 PR 应该被打回。

---

## 5. PE 和你已有技能的关系

| 已有技能 | PE 怎么用上 |
|---------|------------|
| 软件工程（git / CI） | prompt 也走 git，CI 跑评测 |
| 单元测试 | evalset 就是 LLM 时代的"测试集" |
| 产品迭代（PMF） | prompt 迭代 = baseline → 假设 → 实验 |
| 数据分析 | 评测结果是数据，要会读 |
| LLM API 调用 | PE 给你方法论 |

PE 不是新东西，是把已有工程方法适配到 LLM 不确定输出场景。

---

## 6. 三家 API 的最小 prompt（建立体感）

接下来要写代码示例了，先看一下三家最简调用——后续章节都基于这个基础。

### Anthropic (Claude)

```python
import anthropic
client = anthropic.Anthropic()
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="你是简洁的助手。",
    messages=[{"role": "user", "content": "巴黎是哪国首都？"}],
)
print(resp.content[0].text)
```

### OpenAI (GPT)

```python
from openai import OpenAI
client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "你是简洁的助手。"},
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
    model="gemini-2.0-flash",
    contents="巴黎是哪国首都？",
    config={"system_instruction": "你是简洁的助手。"},
)
print(resp.text)
```

**注意三家差异**：

| 维度 | Anthropic | OpenAI | Gemini |
|------|-----------|--------|--------|
| system 位置 | 顶级参数 | messages 数组第一条 | config 内 system_instruction |
| 消息格式 | messages 数组 | messages 数组 | contents |
| 输出格式 | content[0].text | choices[0].message.content | text |
| max_tokens | 必填 | 可选 | 可选 |

这些差异在后续 06-models 章节会展开。

---

## 7. 本手册的核心断言

读到这里如果你只能带走一句话，希望是：

> **"Prompt is the new code. Eval is the new test."**
>
> 不写测试的代码上线会出 bug；不带评测集的 prompt 上线一定出问题。

---

## 8. 常见误区

| 误区 | 真相 |
|------|------|
| "Prompt 加越多 instruction 越好" | 过度 prompting 会和训练数据冲突，反而效果变差 |
| "好 prompt 一定要 think step by step" | 简单任务加 CoT 是浪费 token + 增延迟 |
| "把所有边界条件都列上" | 有限的注意力预算，重要约束放最后 |
| "用更高级的模型就不用调 prompt 了" | 反过来：更强模型对结构化输出 / 指令遵循反而更敏感，要适配 |
| "prompt 越长越具体越好" | 越长信噪比越低；超过几千 token 模型开始"忽略中间" |
| "few-shot 不可或缺" | 简单任务 zero-shot 就够；指令清晰比 example 多更重要 |
| "用别人的 prompt 工程模板套上就行" | 模板告诉你"用什么技法"，不告诉你"你的任务该用哪个" |

后面每个误区都会有专门章节深入。

---

## 9. 下一步

- 📖 一条 prompt 的解剖 → [02-anatomy.md](./02-anatomy.md)
- 📖 模型怎么"读" prompt（tokenization / attention） → [03-how-models-read.md](./03-how-models-read.md)
- 📖 不确定性的源头 → [04-sampling.md](./04-sampling.md)
- 📖 **评测先于 prompt（本手册中轴线的起点）** → [05-eval-first.md](./05-eval-first.md)

## 参考资料

- Anthropic Prompt Engineering Overview: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
- OpenAI Prompting Guide: https://platform.openai.com/docs/guides/prompt-engineering
- Google Prompting Handbook: https://ai.google.dev/gemini-api/docs/prompting-intro
- 跨手册关联：[02-pydantic-ai/06-output-types](../../02-pydantic-ai/docs/01-basics/06-output-types.md) · [01-langchain/02-langsmith/04-prompt-hub](../../01-langchain/docs/02-langsmith/04-prompt-hub.md)
