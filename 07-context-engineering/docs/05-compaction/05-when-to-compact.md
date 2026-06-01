# CE 05-05：何时触发压缩（When to Compact）

> **一句话**：压缩策略选对了还不够，**触发时机**同样决定成败。太早压会丢掉还用得着的信息、白白增加延迟；太晚压会来不及——摘要本身也要占窗口，等爆了再压就没空间了。主流触发条件有三类：token 阈值（如 80% 窗口）、固定轮次、任务阶段切换。生产 Agent（Claude Code）的做法是**阈值触发 + 增量压缩**：到达窗口阈值自动 compact，而不是每轮都压或撑爆才压。

---

## 1. 三类触发条件

| 触发方式 | 规则 | 优点 | 缺点 |
|----------|------|------|------|
| **token 阈值** | 上下文占用达窗口的 X%（如 80%）就压 | 跟实际压力挂钩，最常用 | 需要实时算 token |
| **固定轮次** | 每 N 轮压一次 | 实现简单，可预测 | 和实际 token 量脱钩（有的轮很大有的很小） |
| **任务阶段切换** | 一个子任务完成、切换目标时压 | 语义边界干净，压得自然 | 需要识别「阶段」，Agent 才好用 |

实战首选 **token 阈值**，因为它直接对准真正的约束（窗口大小）。轮次触发太粗，阶段触发最优雅但需要任务有清晰的阶段信号。

```python
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")
CONTEXT_LIMIT = 128_000        # gpt-4o
TRIGGER_RATIO = 0.80           # 占用 80% 就触发


def should_compact(messages: list[dict]) -> bool:
    used = sum(len(enc.encode(m.get("content") or "")) for m in messages)
    return used >= CONTEXT_LIMIT * TRIGGER_RATIO


# 主循环里：每轮调用前检查
if should_compact(messages):
    messages = compact(messages)   # 来自 02 / 03 / 04 的压缩函数
```

---

## 2. 阈值定多少：太早 vs 太晚的权衡

阈值不是越早越好，也不是越晚越好，中间有个甜区：

```
窗口占用 →  0% ────────────── 80% ─────── 95% ──── 100%
                  │              │           │        │
              压太早区        甜区        偏晚      爆了
              （白丢信息）   （留出余量）  （险）   （来不及）
```

| 阈值设太低（如 50%） | 阈值设太高（如 98%） |
|---------------------|---------------------|
| 还用得着的信息被提前压掉 | 摘要本身要占窗口，没空间放摘要输入 |
| 频繁触发，每次都加延迟和成本 | 留给后续生成的 token 被挤光 |
| 压缩比被迫激进 → 损失大 | 一次响应就可能直接超限报错 |

**80~85% 是常见甜区**：既留出了摘要操作和后续生成的余量，又没有过早丢信息。Claude Code 的 auto-compaction 也是在接近上限（约 90%+）时触发，但它有额外余量管理。

一个关键陷阱:**别忘了给「摘要本身」和「这一轮的输出」留预算**。如果窗口 128K、阈值卡到 99%，那剩下的 1.3K 既要装摘要 prompt 又要装模型回复——根本不够。阈值要倒推:`阈值 = 窗口 - 摘要预留 - 最大输出预留`。

---

## 3. 压缩的延迟成本

压缩不是免费的，尤其摘要式压缩要多跑一次 LLM，这个延迟会直接加到用户感知的响应时间上：

```
不压缩的一轮：     [思考 + 生成]                    → 2s
触发摘要的一轮：   [摘要 LLM 调用] + [思考 + 生成]    → 2s + 1.5s = 3.5s
                   ↑ 用户突然感觉「卡了一下」
```

缓解延迟的几个工程手段：

| 手段 | 做法 |
|------|------|
| 摘要用快模型 | Haiku / `gpt-4o-mini`，摘要不需要顶级模型 |
| 异步预压缩 | 后台提前摘要旧历史，不阻塞当前轮（用户无感） |
| 增量压缩 | 每次只压新增的一小段，而非全量重压（见下节） |
| 滑窗/剪枝优先 | 这两种零/低延迟，能用就别用摘要 |

异步预压缩是高级玩法:当占用到 70% 时**在后台**启动摘要,等真到 80% 阈值时摘要已经算好了,直接换上,零等待。

---

## 4. 增量压缩 vs 一次性压缩

| | 一次性压缩 | 增量压缩 |
|--|-----------|----------|
| 做法 | 触发时把全部旧历史一把压成摘要 | 每次只压新增的一段，滚动合并进已有摘要 |
| 延迟 | 高（要处理大量历史） | 低（每次处理量小） |
| 信息损失 | 一把压损失集中 | 多次小压，早期内容反复被压损失累积 |
| 适合 | 偶尔压一次的对话 | 持续运行的长程 Agent |

增量压缩就是 [02-summarization](./02-summarization.md) 第 4 节的**递归摘要**思路——每次触发只把「上一份摘要 + 自上次以来的新历史」重压一次，长度恒定、单次延迟可控。这是长程 Agent 的标准做法，Claude Code 的滚动 compaction 本质就是增量的。

```python
# 增量压缩骨架
prev_summary = ""            # 持续维护的滚动摘要
last_compacted_idx = 0       # 上次压到哪了

def incremental_compact(messages, prev_summary, last_idx):
    new_part = messages[last_idx:-KEEP_RECENT]   # 只取新增的中段
    if not new_part:
        return prev_summary, last_idx
    prev_summary = recursive_compact(prev_summary, new_part)  # 见 02 章
    return prev_summary, len(messages) - KEEP_RECENT
```

---

## 5. 触发策略决策表

把上面的权衡浓缩成一张可直接照着选的表：

| 你的场景 | 触发方式 | 阈值/规则 | 压缩策略 | 增量? |
|----------|----------|-----------|----------|-------|
| 短对话 QA | 不压 / 滑窗 | — | 滑动窗口 | — |
| 长对话助手 | token 阈值 | 80% | 摘要 + 近期滑窗 | 增量 |
| 长程编码 Agent | token 阈值 | 80~85% | 工具结果源头剪枝 + 旧历史摘要 | 增量 |
| 多阶段工作流 Agent | 阶段切换 | 子任务完成时 | 摘要上一阶段成果 | 一次性/段 |
| 实时低延迟场景 | token 阈值 | 75%（早一点） | 滑窗/剪枝（避开摘要延迟） | — |
| 成本敏感批处理 | 固定轮次 | 每 N 轮 | 剪枝 | 增量 |

通用建议:**token 阈值（80%）+ 增量摘要 + 工具结果源头剪枝** 是覆盖面最广的一套组合,大多数长程 Agent 直接套这个即可。

---

## 6. 常见误区

| 误区 | 真相 |
|------|------|
| 「压得越早越安全」 | 太早会丢还用得着的信息，且频繁压增延迟成本 |
| 「等窗口快满了再压」 | 摘要本身要占窗口，满了就没空间压了，要预留余量 |
| 「阈值卡到 99% 最省」 | 没给摘要输入和模型输出留预算，照样会超限 |
| 「压缩没有延迟成本」 | 摘要要多跑 LLM，要么用快模型要么异步预压 |
| 「每次都全量重压」 | 长 Agent 应增量压缩，否则每次延迟爆炸 |
| 「触发了就一定要压」 | 若历史本身没冗余（已经很精简），压了反而丢信息 |

---

## 7. 下一步

- 📖 摘要式压缩与递归/增量摘要的实现 → [02-summarization.md](./02-summarization.md)
- 📖 滑动窗口：低延迟的触发后处理 → [03-sliding-window.md](./03-sliding-window.md)
- 📖 重要性剪枝与工具结果源头剪枝 → [04-pruning.md](./04-pruning.md)
- 📖 为什么必须压缩（回看动机） → [01-why-compact.md](./01-why-compact.md)
- 📖 把上下文当预算分配（阈值倒推的基础） → [../01-foundations/05-context-budget.md](../01-foundations/05-context-budget.md)
- 📖 Agent 上下文累积，进入下一章 → [../06-agent-context/01-accumulation.md](../06-agent-context/01-accumulation.md)

## 参考资料

- Anthropic, "Manage context on Claude Code"（auto-compaction 触发时机）: https://docs.anthropic.com/en/docs/claude-code/costs
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangChain, "Add summary of conversation history"（增量摘要节点）: https://langchain-ai.github.io/langgraph/how-tos/memory/add-summary-conversation-history/
