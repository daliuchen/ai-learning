# CE 05-05：何时触发压缩（When to Compact）

> 压缩策略选对了还不够，什么时候触发同样要紧。压得太早，会丢掉还用得着的信息，白白多出延迟；压得太晚就来不及了，因为摘要本身也要占窗口，窗口爆了再压已经没有空间。常见的触发条件有三类：token 阈值（比如窗口的 80%）、固定轮次、任务阶段切换。Claude Code 这类生产 Agent 用的是阈值触发：接近阈值自动 compact，既不每轮都压，也不等撑爆才压。

---

## 1. 三类触发条件

| 触发方式 | 规则 | 优点 | 缺点 |
|----------|------|------|------|
| token 阈值 | 上下文占用达窗口的 X%（如 80%）就压 | 跟实际压力挂钩，最常用 | 需要实时算 token |
| 固定轮次 | 每 N 轮压一次 | 实现简单，可预测 | 和实际 token 量脱钩（有的轮很大有的很小） |
| 任务阶段切换 | 一个子任务完成、切换目标时压 | 语义边界干净，压得自然 | 需要识别「阶段」，Agent 才好用 |

我的首选是 token 阈值，它直接对应实际的约束（窗口大小）。按轮次触发太粗；按阶段触发最优雅，但要求任务有清晰的阶段信号。

```python
import tiktoken

enc = tiktoken.get_encoding("o200k_base")
CONTEXT_LIMIT = 128_000        # 示意值：按你实际用的模型窗口或自定的预算填（1M 窗口见第 2 节的说明）
TRIGGER_RATIO = 0.80           # 占用 80% 就触发


def should_compact(messages: list[dict]) -> bool:
    used = sum(len(enc.encode(m.get("content") or "")) for m in messages)
    return used >= CONTEXT_LIMIT * TRIGGER_RATIO


# 主循环里：每轮调用前检查
if should_compact(messages):
    messages = compact(messages)   # 来自 02 / 03 / 04 的压缩函数
```

---

## 2. 阈值定多少：太早和太晚的权衡

阈值既不是越早越好，也不是越晚越好，中间有一段合适的区间：

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

80~85% 是常见的选择：给摘要操作和后续生成都留了余量，又不会过早丢信息。Claude Code 的 auto-compaction 也是在接近一个可配置的 auto-compact 窗口时触发。

> 窗口到了 1M 之后，这个算法要修正。「百分比 × 窗口」在 128K～200K 的窗口下很自然，但 1M 窗口的 80% 是 80 万 token，远远超出质量和成本能接受的范围（见 [Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)、[上下文的成本与延迟模型](../01-foundations/04-cost-latency.md)）。所以平台 API 给的都是绝对 token 阈值：Anthropic 服务端 compaction 的 `trigger` 默认 150,000 input token，最小 50,000；Anthropic context editing 默认从 100,000 开始清工具结果；OpenAI `compact_threshold` 的官方示例是 200,000。实际做法是按质量和成本能接受的上下文长度来定阈值，而不是按窗口比例。

别忘了给摘要本身和这一轮的输出留预算。窗口 128K、阈值卡在 99% 的话，剩下的 1.3K 既要装摘要 prompt，又要装模型回复，根本不够。阈值应该倒推：`阈值 = 窗口 - 摘要预留 - 最大输出预留`。

---

## 3. 压缩的延迟成本

压缩有成本，摘要式压缩尤其明显：多跑一次 LLM，这段延迟会直接加到用户感受到的响应时间上。

```
不压缩的一轮：     [思考 + 生成]                    → 2s
触发摘要的一轮：   [摘要 LLM 调用] + [思考 + 生成]    → 2s + 1.5s = 3.5s
                   ↑ 用户突然感觉「卡了一下」
```

几种缓解延迟的办法：

| 手段 | 做法 |
|------|------|
| 摘要用快模型 | Haiku / `gpt-6-luna`，摘要不需要顶级模型 |
| 异步预压缩 | 后台提前摘要旧历史，不阻塞当前轮（用户无感） |
| 增量压缩 | 每次只压新增的一小段，而非全量重压（见下节） |
| 滑窗/剪枝优先 | 这两种零/低延迟，能用就别用摘要 |

异步预压缩可以这样做：占用到 70% 时在后台启动摘要，等到了 80% 的阈值，摘要已经算好，直接换上，用户不用等。

---

## 4. 增量压缩 vs 一次性压缩

| | 一次性压缩 | 增量压缩 |
|--|-----------|----------|
| 做法 | 触发时把全部旧历史一把压成摘要 | 每次只压新增的一段，滚动合并进已有摘要 |
| 延迟 | 高（要处理大量历史） | 低（每次处理量小） |
| 信息损失 | 一把压损失集中 | 多次小压，早期内容反复被压损失累积 |
| 适合 | 偶尔压一次的对话 | 持续运行的长程 Agent |

增量压缩就是 [摘要式压缩](./02-summarization.md) 第 5 节讲的递归摘要：每次触发只把「上一份摘要 + 上次以来的新历史」重新压一次，长度恒定，单次延迟可控。长程 Agent 一般都这么做。

```python
# 增量压缩骨架
prev_summary = ""            # 持续维护的滚动摘要
last_compacted_idx = 0       # 上次压到哪了

def incremental_compact(messages, prev_summary, last_idx):
    new_part = messages[last_idx:-KEEP_RECENT]   # 只取新增的中段
    if not new_part:
        return prev_summary, last_idx
    prev_summary = recursive_compact(prev_summary, new_part)  # 见本章第 02 篇
    return prev_summary, len(messages) - KEEP_RECENT
```

---

## 5. 触发策略决策表

把上面的权衡整理成一张可以直接对照的表：

| 你的场景 | 触发方式 | 阈值/规则 | 压缩策略 | 增量? |
|----------|----------|-----------|----------|-------|
| 短对话 QA | 不压 / 滑窗 | — | 滑动窗口 | — |
| 长对话助手 | token 阈值 | 80% | 摘要 + 近期滑窗 | 增量 |
| 长程编码 Agent | token 阈值 | 80~85% | 工具结果源头剪枝 + 旧历史摘要 | 增量 |
| 多阶段工作流 Agent | 阶段切换 | 子任务完成时 | 摘要上一阶段成果 | 一次性/段 |
| 实时低延迟场景 | token 阈值 | 75%（早一点） | 滑窗/剪枝（避开摘要延迟） | — |
| 成本敏感批处理 | 固定轮次 | 每 N 轮 | 剪枝 | 增量 |

如果不知道怎么选，token 阈值（80%）加增量摘要、再加工具结果源头剪枝，是覆盖面最广的组合，大多数长程 Agent 直接用这套就行。

---

## 6. 常见误区

| 误区 | 实际情况 |
|------|------|
| 「压得越早越安全」 | 太早会丢还用得着的信息，频繁压缩也会增加延迟和成本 |
| 「等窗口快满了再压」 | 摘要本身要占窗口，满了就没空间压了，要预留余量 |
| 「阈值卡到 99% 最省」 | 没给摘要输入和模型输出留预算，照样会超限 |
| 「压缩没有延迟成本」 | 摘要要多跑一次 LLM，要么用快模型，要么异步预压 |
| 「每次都全量重压」 | 长程 Agent 应该增量压缩，否则每次延迟都很高 |
| 「触发了就一定要压」 | 如果历史本身已经很精简，压了反而丢信息 |

---

## 7. 下一步

- 摘要式压缩与递归/增量摘要的实现：[摘要式压缩](./02-summarization.md)
- 低延迟的触发后处理：[滑动窗口与截断](./03-sliding-window.md)
- 重要性剪枝与工具结果源头剪枝：[重要性评分与剪枝](./04-pruning.md)
- 回看压缩的动机：[为什么要压缩上下文](./01-why-compact.md)
- 阈值倒推的基础：[Context Budget](../01-foundations/05-context-budget.md)
- 进入下一章：[Agent 的上下文累积问题](../06-agent-context/01-accumulation.md)

## 参考资料

- Anthropic, "Manage context on Claude Code"（auto-compaction 触发时机）: https://code.claude.com/docs/en/costs
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangChain, "Add summary of conversation history"（增量摘要节点）: https://langchain-ai.github.io/langgraph/how-tos/memory/add-summary-conversation-history/
