# CE 05-01：为什么要压缩上下文（Why Compact）

> **一句话**：长会话和长 Agent 轨迹**必然**会把上下文窗口撑满，撑满之后等着你的是报错、静默截断、或者质量崩盘三选一。压缩（compaction）就是在窗口爆掉之前，主动用一点算力（多跑一次 LLM / 多算一次评分）换回大量 token——保留信息密度，丢弃冗余。Claude Code、Cursor 这类长程 Agent 全都内置了 auto-compaction，这不是可选项，是长程任务的生存必需品。

---

## 1. 窗口满了之后，到底会发生什么

很多人以为「窗口满了」是个温柔的边界，其实它会以三种很不一样的方式咬你：

| 处理方式 | 谁这么干 | 现象 | 危害 |
|----------|----------|------|------|
| **直接报错** | 大多数原生 API（OpenAI / Anthropic 裸调用） | `400 context_length_exceeded` | 请求整个失败，Agent 当场卡死 |
| **静默截断** | 部分框架 / 网关默认行为 | 偷偷砍掉最早的消息，不报错 | 最阴险——模型「忘了」开头的任务约束，却没人告诉你 |
| **质量崩盘** | 窗口没满但接近满 | 能跑，但答非所问 / 漏看中间 | context rot：长输入里中间内容召回率暴跌 |

第二种最坑。看一段真实会触发 `400` 的裸调用：

```python
import openai

client = openai.OpenAI()

# 一个塞了 50 万 token 历史的 messages 列表
messages = build_huge_history()  # 远超 gpt-4o 的 128K

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
)
# ❌ openai.BadRequestError: This model's maximum context length is
#    128000 tokens. However, your messages resulted in 503210 tokens.
```

裸 API 至少会**明确报错**，这是好事——你知道出问题了。真正危险的是某些「贴心」中间层默默帮你截断，模型行为悄悄退化你还以为是模型变笨了。

---

## 2. 长会话 / 长 Agent 轨迹为什么「必然」撑满

不是「可能」，是数学上的必然。看 token 怎么累积：

```
单轮聊天：       user(50) + assistant(200)              ≈ 250 token
50 轮闲聊：      250 × 50                                ≈ 12.5K token   （还好）

Agent 单步：     思考(300) + tool_call(100) + tool_result(2000)  ≈ 2.4K token
                                              ↑ 工具结果是大头！
Agent 跑 80 步： 2.4K × 80                                ≈ 192K token    （Claude 200K 见底）
```

关键洞察：**Agent 的工具返回结果是 token 黑洞**。一次 `read_file` 可能返回几千 token，一次网页抓取上万 token，一次 SQL 查询返回几百行。这些结果会被原样塞回历史，而且**永远不会自己消失**——下一步推理还得带着上一步的全部结果一起喂。

```
Agent 第 N 步的输入 = system + tools定义
                    + 第1步的[思考+调用+结果]
                    + 第2步的[思考+调用+结果]
                    + ...
                    + 第(N-1)步的[思考+调用+结果]   ← 全部历史累加
                    + 第N步的新指令
```

这是个**单调递增**的序列。只要任务够长，触顶只是时间问题。Claude Code 跑一个大重构任务、Cursor 跑一个跨多文件的 feature，几十步下来撑满 200K 是家常便饭。

---

## 3. 压缩的本质：用算力换 token

压缩不是免费午餐，它的等式是：

```
# ✅ 压缩的交易
  花费：一次额外的 LLM 调用（摘要）或一轮评分计算（剪枝）
       → 几百毫秒到几秒延迟 + 一点点 token 成本
  换回：把 100K 历史压成 5K 摘要
       → 腾出 95K 预算，会话得以继续，且后续每轮都更便宜更快
```

为什么这笔买卖划算？因为 **token 成本是按每一轮重复计费的**。一段 100K 的历史，如果不压缩，它会在接下来**每一次**调用里都被重新计费、重新拖慢响应。压一次，受益所有后续轮次。算力是一次性投入，token 节省是持续收益。

```python
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")

raw_history = "...50 轮工具调用的完整记录..."
summary = "...用 LLM 把上面总结成的 800 字..."

before = len(enc.encode(raw_history))   # 比如 98000
after = len(enc.encode(summary))        # 比如 1100
print(f"压缩比 {before / after:.0f}x，每后续轮次省 {before - after} token")
# 压缩比 89x，每后续轮次省 96900 token
```

---

## 4. 压缩的目标：保留信息密度，丢弃冗余

压缩 ≠ 无脑删。好的压缩是**提高单位 token 的信息密度**，把同样的「有用信息」装进更少的 token。哪些是冗余、哪些必须留：

| 类别 | 示例 | 压缩时怎么处理 |
|------|------|----------------|
| 寒暄 / 客套 | 「好的我来帮你」「明白了」 | ✅ 直接丢 |
| 工具的原始大输出 | 完整的 8000 行日志、整个 JSON | ✅ 只留关键字段 / 结论 |
| 重复确认 | 模型反复复述同一个事实 | ✅ 合并成一句 |
| **已做的关键决策** | 「确定用 PostgreSQL 而非 MySQL，因为需要 JSONB」 | ❌ 必须保留 |
| **已确认的事实** | 「用户的 API key 在 .env 第 3 行」 | ❌ 必须保留 |
| **未完成的任务 / TODO** | 「还差 migration 文件没写」 | ❌ 必须保留 |
| **错误与教训** | 「试过方案 A 失败了，因为权限不足」 | ❌ 必须保留（防止重蹈覆辙） |

信息密度的直觉：**压缩后的内容要能让一个「失忆」的模型接着干活**。如果摘要里没写「试过 A 失败了」，模型很可能再试一次 A——这就是压缩丢信息的典型代价。

---

## 5. 主流压缩策略一览（本章地图）

压缩不是单一技术，是一组策略，按「聪明程度」和「成本」排开：

| 策略 | 思路 | 成本 | 信息损失 | 本章 |
|------|------|------|----------|------|
| **滑动窗口 / 截断** | 只留最近 N 轮 / N token | 极低（纯切片） | 高（远期全丢） | [03](./03-sliding-window.md) |
| **重要性剪枝** | 给每条消息打分，留高分的 | 中（评分计算） | 中（按价值取舍） | [04](./04-pruning.md) |
| **摘要式压缩** | 用 LLM 把旧历史总结成短文 | 高（额外 LLM 调用） | 中低（保留语义） | [02](./02-summarization.md) |

实战里这三者常**组合使用**：近期原样保留（滑窗思想）+ 远期摘要（summarization）+ 大工具结果剪枝（pruning）。Claude Code 的 auto-compaction 就是这种混合体。

---

## 6. 真实世界：长程 Agent 都内置了 compaction

这不是学术概念，是 2025-2026 生产 Agent 的标配：

| 产品 | 压缩机制 | 触发 |
|------|----------|------|
| **Claude Code** | auto-compaction：接近窗口上限时自动把历史摘要成一段「会话总结」，保留任务目标、已改文件、待办；也可手动 `/compact` | 约 90%+ 窗口占用自动触发 |
| **Cursor** | 长会话自动摘要旧轮次，保留近期 + 当前文件上下文 | 接近窗口阈值 |
| **Anthropic Agent SDK** | 提供 context 管理 / memory 工具，支持把历史外置 + 摘要回填 | 开发者配置 |
| **LangGraph / LangChain** | `trim_messages`、`SummarizationNode` 等内置组件 | 开发者配置 |

注意 Claude Code 的设计哲学：它在压缩时**优先保留任务约束和未完成项**，而不是机械地保留「最近 N 条」——因为对长程 Agent 来说，「我现在到底要干嘛、干到哪了」比「上一句说了啥」重要得多。这正是第 4 节信息密度原则的工程化体现。

---

## 7. 常见误区

| 误区 | 真相 |
|------|------|
| 「窗口够大（200K/1M）就不用压缩」 | 大窗口照样会被长 Agent 撑满，且塞满后 rot + 贵 + 慢三连击 |
| 「压缩就是删最早的消息」 | 那是最蠢的截断，关键早期约束往往就在最早 |
| 「压缩免费」 | 摘要要额外跑 LLM，有延迟和 token 成本，是用算力换 token |
| 「压缩了就会丢信息所以别压」 | 不压会直接报错 / 崩盘，压得好信息损失可控，两害取其轻 |
| 「等满了再压」 | 满了来不及（摘要本身也要占窗口），要在阈值前触发（见 [05](./05-when-to-compact.md)） |

---

## 8. 下一步

- 📖 摘要式压缩：用 LLM 把旧历史总结成短文 → [02-summarization.md](./02-summarization.md)
- 📖 滑动窗口与截断：最简单的保头保尾砍中间 → [03-sliding-window.md](./03-sliding-window.md)
- 📖 重要性评分与剪枝：比滑窗更聪明的取舍 → [04-pruning.md](./04-pruning.md)
- 📖 何时触发压缩：阈值、时机与权衡 → [05-when-to-compact.md](./05-when-to-compact.md)
- 📖 上下文成本与延迟模型（为什么 token 这么贵） → [../01-foundations/04-cost-latency.md](../01-foundations/04-cost-latency.md)
- 📖 Agent 长轨迹里上下文怎么累积 → [../06-agent-context/01-accumulation.md](../06-agent-context/01-accumulation.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, "Manage context on Claude Code"（auto-compaction）: https://docs.anthropic.com/en/docs/claude-code/costs
- LangChain, "How to trim messages" / SummarizationNode: https://python.langchain.com/docs/how_to/trim_messages/
