# CE 05-01：为什么要压缩上下文（Why Compact）

> 长会话和长 Agent 轨迹迟早会把上下文窗口撑满。撑满之后，结果不外乎三种：报错、静默截断，或者质量明显下滑。压缩（compaction）的做法是在窗口爆掉之前，主动花一点算力（多跑一次 LLM，或多算一轮评分），换回大量 token，保住信息密度，丢掉冗余。Claude Code、Cursor 这类长程 Agent 都内置了 auto-compaction，对长任务来说，这是必须有的能力。

---

## 1. 窗口满了之后会发生什么

很多人以为窗口满了只是一条温和的边界，实际上它会以三种很不一样的方式出问题：

| 处理方式 | 谁这么干 | 现象 | 危害 |
|----------|----------|------|------|
| 直接报错 | 大多数原生 API（OpenAI / Anthropic 裸调用） | `400 context_length_exceeded` | 请求整个失败，Agent 当场卡死 |
| 静默截断 | 部分框架 / 网关默认行为 | 偷偷砍掉最早的消息，不报错 | 最隐蔽：模型忘了开头的任务约束，却没人告诉你 |
| 质量下滑 | 窗口没满但接近满 | 能跑，但答非所问 / 漏看中间 | context rot：长输入里中间内容召回率大幅下降 |

先看一段会触发 `400` 的裸调用：

```python
import openai

client = openai.OpenAI()

# 一个塞了 120 万 token 历史的 messages 列表
messages = build_huge_history()  # 超过 gpt-6.1-sol 的 1.05M 窗口

resp = client.chat.completions.create(
    model="gpt-6.1-sol",
    messages=messages,
)
# ❌ openai.BadRequestError（报错文本示意）: This model's maximum context length is
#    1050000 tokens. However, your messages resulted in 1203210 tokens.
```

裸 API 至少会明确报错，你知道出了问题，这反而是好事。最危险的是第二种：某些中间层默默帮你截断，模型行为悄悄退化，你还以为是模型变笨了。

---

## 2. 长会话 / 长 Agent 轨迹为什么一定会撑满

这不是概率问题，算一下 token 怎么累积就明白了：

```
单轮聊天：       user(50) + assistant(200)              ≈ 250 token
50 轮闲聊：      250 × 50                                ≈ 12.5K token   （还好）

Agent 单步：     思考(300) + tool_call(100) + tool_result(2000)  ≈ 2.4K token
                                              ↑ 工具结果是大头！
Agent 跑 80 步： 2.4K × 80                                ≈ 192K token    （对 200K 窗口已见底；1M 窗口也只是晚一点见底）
```

Agent 的工具返回结果是最大的 token 消耗。一次 `read_file` 可能返回几千 token，一次网页抓取上万 token，一次 SQL 查询返回几百行。这些结果原样塞回历史，而且不会自己消失，下一步推理还得带着前面所有结果一起发。

```
Agent 第 N 步的输入 = system + tools定义
                    + 第1步的[思考+调用+结果]
                    + 第2步的[思考+调用+结果]
                    + ...
                    + 第(N-1)步的[思考+调用+结果]   ← 全部历史累加
                    + 第N步的新指令
```

这是一个单调递增的序列，任务够长，触顶只是时间问题。Claude Code 做一次大重构，Cursor 做一个跨多文件的 feature，几十步下来撑满 200K 窗口很常见。1M 窗口只是把这一天推后，质量衰减和成本问题照样存在。

---

## 3. 压缩是用算力换 token

压缩有代价，这笔账是这样的：

```
# ✅ 压缩的交易
  花费：一次额外的 LLM 调用（摘要）或一轮评分计算（剪枝）
       → 几百毫秒到几秒延迟 + 一点点 token 成本
  换回：把 100K 历史压成 5K 摘要
       → 腾出 95K 预算，会话得以继续，且后续每轮都更便宜更快
```

为什么划算？因为 token 成本是每一轮重复计费的。一段 100K 的历史不压缩，接下来每一次调用都要为它再付一次钱、再慢一次。压一次，后面所有轮次都受益。算力是一次性投入，省下的 token 是持续的收益。

```python
import tiktoken

enc = tiktoken.get_encoding("o200k_base")

raw_history = "...50 轮工具调用的完整记录..."
summary = "...用 LLM 把上面总结成的一页摘要..."

before = len(enc.encode(raw_history))   # 比如 98000
after = len(enc.encode(summary))        # 比如 1100
print(f"压缩比 {before / after:.0f}x，每后续轮次省 {before - after} token")
# 压缩比 89x，每后续轮次省 96900 token
```

---

## 4. 压缩的目标：保留信息密度，丢弃冗余

压缩不等于随便删。好的压缩是提高单位 token 的信息密度，用更少的 token 装下同样多的有用信息。哪些是冗余、哪些必须留，可以这样分：

| 类别 | 示例 | 压缩时怎么处理 |
|------|------|----------------|
| 寒暄 / 客套 | 「好的我来帮你」「明白了」 | 直接丢 |
| 工具的原始大输出 | 完整的 8000 行日志、整个 JSON | 只留关键字段 / 结论 |
| 重复确认 | 模型反复复述同一个事实 | 合并成一句 |
| 已做的关键决策 | 「确定用 PostgreSQL 而非 MySQL，因为需要 JSONB」 | 必须保留 |
| 已确认的事实 | 「用户的 API key 在 .env 第 3 行」 | 必须保留 |
| 未完成的任务 / TODO | 「还差 migration 文件没写」 | 必须保留 |
| 错误与教训 | 「试过方案 A 失败了，因为权限不足」 | 必须保留（防止重蹈覆辙） |

判断标准是：压缩后的内容，要能让一个失忆的模型接着干活。摘要里没写「试过 A 失败了」，模型很可能再试一次 A，这就是压缩丢信息最常见的代价。

---

## 5. 主流压缩策略一览（本章地图）

常用的压缩策略有三种，按成本从低到高排列：

| 策略 | 思路 | 成本 | 信息损失 | 本章 |
|------|------|------|----------|------|
| 滑动窗口 / 截断 | 只留最近 N 轮 / N token | 极低（纯切片） | 高（远期全丢） | [03](./03-sliding-window.md) |
| 重要性剪枝 | 给每条消息打分，留高分的 | 中（评分计算） | 中（按价值取舍） | [04](./04-pruning.md) |
| 摘要式压缩 | 用 LLM 把旧历史总结成短文 | 高（额外 LLM 调用） | 中低（保留语义） | [02](./02-summarization.md) |

实际项目里三者常常组合：近期原样保留（滑窗的思路），远期做摘要，大块工具结果做剪枝。

---

## 6. 现实中的长程 Agent 都内置了 compaction

压缩已经是生产 Agent 的标配。到 2026 年，它已经下沉成 API 和框架的内置能力：

| 产品 | 压缩机制 | 触发 |
|------|----------|------|
| Claude Code | auto-compaction：接近 auto-compact 窗口时自动把历史摘要成一段「会话总结」；也可手动 `/compact`，并可用 `/compact <重点>` 或 CLAUDE.md 的「Compact instructions」告诉它保留什么 | 接近 auto-compact 窗口（可配置，见 Claude Code 文档 model-config） |
| Claude API（服务端 compaction，beta） | 请求里带 `context_management.edits: [{"type": "compact_20260112"}]`（beta header `compact-2026-01-12`），超过 `trigger`（默认 150K input token，最小 50K）后服务端自动写摘要，响应以一个 `compaction` 块开头，之后的请求里 API 会忽略这个块之前的内容；还有「按需压缩」（`compaction={"type": "summarize"}`，header `compact-2026-09-04`）由你决定何时压 | API 按阈值触发 / 你主动请求 |
| Claude API（context editing） | 按规则清理而非摘要：`clear_tool_uses_20250919` 清旧工具结果、`clear_thinking_20251015` 清旧 thinking（header `context-management-2025-06-27`） | `trigger` 默认 input_tokens 100000，保留最近 3 组 |
| OpenAI Responses API | 服务端 compaction：`context_management=[{"type": "compaction", "compact_threshold": 200000}]`，或独立的 `client.responses.compact(...)` 端点；返回的 compaction 项带加密内容，原样传回，不可读 | 渲染后 token 超过 `compact_threshold` |
| LangChain 1.x | `SummarizationMiddleware`（摘要）与 `ContextEditingMiddleware` + `ClearToolUsesEdit`（清旧工具结果） | `trigger` / `keep` 配置 |

> 上表里两家 API 的 compaction 语法、header 和默认值，都取自 2026-11 的官方文档（Anthropic compaction / context-editing、OpenAI compaction 指南）。这些功能大多还在 beta，header 名带日期，变化很快，落地前请再核对一遍。本章后面的 02～05 篇讲原理和自己实现，平台不提供、或者你想定制摘要策略时，也能自己做。

自己实现压缩时，任务约束和未完成项应当优先保留，这比机械地留最近 N 条有用。对长程 Agent 来说，「我现在要干什么、干到哪了」比「上一句说了什么」重要得多。

---

## 7. 常见误区

| 误区 | 实际情况 |
|------|------|
| 「窗口够大（200K/1M）就不用压缩」 | 大窗口照样会被长 Agent 撑满，塞满后既有 rot，又贵又慢 |
| 「压缩就是删最早的消息」 | 那是最粗糙的截断，关键的早期约束往往就在最前面 |
| 「压缩免费」 | 摘要要额外跑 LLM，有延迟和 token 成本，是用算力换 token |
| 「压缩了就会丢信息所以别压」 | 不压会直接报错或质量下滑，压得好信息损失可控，两害相权取其轻 |
| 「等满了再压」 | 满了就来不及了（摘要本身也要占窗口），要在阈值之前触发（见 [05](./05-when-to-compact.md)） |

---

## 8. 下一步

- 用 LLM 把旧历史总结成短文：[摘要式压缩](./02-summarization.md)
- 最简单的保头保尾砍中间：[滑动窗口与截断](./03-sliding-window.md)
- 比滑窗更聪明的取舍：[重要性评分与剪枝](./04-pruning.md)
- 阈值、时机与权衡：[何时触发压缩](./05-when-to-compact.md)
- 为什么 token 这么贵：[上下文的成本与延迟模型](../01-foundations/04-cost-latency.md)
- Agent 长轨迹里上下文怎么累积：[Agent 的上下文累积问题](../06-agent-context/01-accumulation.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, "Manage context on Claude Code"（auto-compaction）: https://code.claude.com/docs/en/costs
- LangChain, Short-term memory（trim / summarize / 中间件）: https://docs.langchain.com/oss/python/langchain/short-term-memory
