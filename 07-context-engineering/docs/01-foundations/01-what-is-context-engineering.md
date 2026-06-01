# CE 01：什么是上下文工程（Context Engineering）

> **一句话**：Prompt Engineering 管「指令这句话怎么写」，Context Engineering 管「整个上下文窗口里到底装什么、按什么顺序排、各占多少 token 预算」。当你的应用从「一句 prompt」变成「Agent + 工具 + 检索 + 多轮历史」，上下文本身就成了最大的变量，调它比调措辞重要得多。

---

## 1. 一个直观例子：同一句话，喂不同上下文

假设你的 prompt 永远是这一句不变：

```
请回答用户的问题。
```

下面三种「上下文」喂进去，结果天差地别：

| 上下文里装了什么 | 模型实际看到的输入 | 输出质量 |
|------------------|--------------------|----------|
| 只有用户问题 | `用户：我们公司退货政策是几天？` | 瞎编一个「7 天」（幻觉） |
| 问题 + 检索到的政策原文 | `[政策片段：15 天无理由退货]` + 问题 | 正确答 15 天 |
| 问题 + 30 段无关 FAQ + 政策原文埋在第 18 段 | 一大坨噪声 + 问题 | 可能答错 / 漏看（lost in the middle） |

看到没——**prompt 一个字没改，输出从幻觉到正确再到又翻车**。决定结果的不是那句指令，是「窗口里装了什么、放在哪、有多少噪声」。这就是 Context Engineering 的战场。

---

## 2. 边界：PE 管措辞，CE 管装载

很多人把两者混为一谈。划清边界很重要：

| 维度 | Prompt Engineering | Context Engineering |
|------|--------------------|--------------------|
| 核心问题 | 这句指令**怎么写**才清楚 | 窗口里**装什么、什么顺序、占多少预算** |
| 操作对象 | 一段文本（指令 / few-shot） | 整个 context window 的组装策略 |
| 典型动作 | 改措辞、加 CoT、给示例 | 检索什么、保留几轮历史、压缩、裁剪、排序 |
| 关键变量 | 语言表达 | token 分配、信息密度、位置 |
| 失败长这样 | 指令有歧义 → 跑偏 | 关键信息被噪声稀释 / 召回不到 / 超窗截断 |
| 类比 | 写好一个函数的逻辑 | 设计好整个函数的**入参**该传什么 |

一句话区分：**PE 优化「文字」，CE 优化「输入数据流」**。两者不是替代关系，是上下层关系——CE 决定窗口里有哪些块，PE 决定其中「指令块」怎么写。

---

## 3. 上下文窗口里到底有几块

任何一次 LLM 调用，进入窗口的内容大致是这几类。CE 的工作就是管理它们：

```
┌─────────────────────────────────────┐
│ System / 指令      ← 角色、规则、约束   │ ← PE 在这里写措辞
├─────────────────────────────────────┤
│ Tools / 工具定义   ← 可调用的函数 schema │ ← Agent 场景占用很大
├─────────────────────────────────────┤
│ Retrieved / 检索   ← RAG 拉来的文档片段  │ ← CE 决定拉什么、拉几条
├─────────────────────────────────────┤
│ History / 对话历史  ← 前面 N 轮 user/AI  │ ← CE 决定留几轮 / 怎么压
├─────────────────────────────────────┤
│ User / 当前输入    ← 这一轮的问题        │
└─────────────────────────────────────┘
         ↓ 全部拼成一个长序列喂给模型
```

注意：**这五块都在抢同一个有限的 token 预算**（比如 Claude 的 200K）。塞多了某一块，别的块就得让位。这种「分配」的视角，正是 CE 的核心心法（详见 [05-context-budget.md](./05-context-budget.md)）。

---

## 4. 为什么 2024-2026 这个概念突然火了

「Context Engineering」这个词在 2024 下半年开始被频繁提起，2025 几乎成了 Agent 圈的共识术语。三个推力：

### 4.1 Agent 兴起，上下文不再是「一句 prompt」

传统 chatbot：一问一答，上下文就那么点。

Agent：多轮工具调用，每一步都把「工具返回结果」塞回上下文，几个回合下来窗口就被工具输出、中间推理、历史塞满。这时候**「上下文里装什么」直接决定 Agent 走不走得通**，措辞反而是次要问题。

### 4.2 长上下文成了标配

2023 年大家还在 4K / 8K 里抠 token。到 2025-2026：

| 模型 | 上下文窗口 |
|------|-----------|
| GPT-4o | 128K |
| Claude Opus / Sonnet | 200K（部分 1M beta） |
| Gemini 2.x | 1M（部分 2M） |

窗口大了，**「能塞」不等于「该塞」**。塞满 200K 既贵又慢，还会触发 context rot（中间内容召回变差，见 [03-context-rot.md](./03-context-rot.md)）。于是「怎么用好这个大窗口」成了真问题。

### 4.3 工具调用 / 检索让上下文成为主变量

RAG、function calling、MCP 普及后，上下文里 80% 的内容不再是人手写的 prompt，而是**程序动态拼进去的**（检索片段、工具结果、记忆）。既然主体是程序拼的，优化重心自然从「写好措辞」转移到「管好拼装」——这正是 Context Engineering。

---

## 5. CE 的几条核心信条（贯穿全手册）

| 信条 | 含义 | 对应章节 |
|------|------|----------|
| 上下文是有限预算 | 200K 不是「免费随便用」，是要分配的资源 | [05-context-budget.md](./05-context-budget.md) |
| 最少必要上下文 | 恰好够完成任务的最少信息，而非越多越好 | [06-minimal-context.md](./06-minimal-context.md) |
| 越长不一定越好 | 长上下文有 rot、噪声、稀释 | [03-context-rot.md](./03-context-rot.md) |
| 每个 token 都要付费且拖慢响应 | 成本与延迟随上下文线性增长 | [04-cost-latency.md](./04-cost-latency.md) |
| 位置重要 | 重要信息放头尾，别埋中间 | [03-context-rot.md](./03-context-rot.md) |

---

## 6. 常见误区

| 误区 | 真相 |
|------|------|
| 「CE 就是 PE 换了个名字」 | PE 管措辞、CE 管装载，正交的两层 |
| 「窗口够大就把能塞的都塞进去」 | 涨成本、增延迟、稀释注意力，准确率反而降 |
| 「检索召回越多越保险」 | 召回噪声会淹没关键片段，召回质量 > 数量 |
| 「历史全留着上下文才连贯」 | 远期历史该压缩 / 摘要，不是原样堆着 |
| 「换个更强模型就不用管上下文了」 | 更强模型同样受 rot / 成本 / 延迟约束 |

---

## 7. 下一步

- 📖 上下文窗口到底是什么（token / tokenizer / 窗口大小） → [02-context-window.md](./02-context-window.md)
- 📖 Context Rot 与 lost-in-the-middle → [03-context-rot.md](./03-context-rot.md)
- 📖 上下文的成本与延迟模型 → [04-cost-latency.md](./04-cost-latency.md)
- 📖 把上下文当预算来分配 → [05-context-budget.md](./05-context-budget.md)
- 📖 核心原则：最少必要上下文 → [06-minimal-context.md](./06-minimal-context.md)
- 📖 PE 与 CE 的关系，可回看 Prompt Engineering 手册 → [04-prompt-engineering/01-foundations/01-what-is-pe.md](../../../04-prompt-engineering/docs/01-foundations/01-overview.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic Claude 上下文窗口文档：https://docs.anthropic.com/en/docs/build-with-claude/context-windows
