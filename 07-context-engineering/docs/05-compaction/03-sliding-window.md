# CE 05-03：滑动窗口与截断（Sliding Window）

> **一句话**：滑动窗口是最简单粗暴的压缩——只保留最近 N 轮或最近 N token，旧的直接丢。不跑 LLM、零延迟、实现五行代码。它在「不需要远期记忆」的任务上完全够用，但有两个致命陷阱：砍掉了关键的早期任务约束，以及把成对的 `tool_call`/`tool_result` 砍断导致 API 报错。正确姿势是**保头（system+任务）保尾（近期）砍中间**。

---

## 1. 核心思路：只留最近的

滑动窗口的心智模型就是一个固定大小的「窗户」在历史上往后滑：

```
全部历史：  [1][2][3][4][5][6][7][8][9][10]
                              └─── 窗口(N=4) ───┘
保留：                       [7][8][9][10]
丢弃：     [1][2][3][4][5][6]
```

新消息进来，最老的滑出去。窗口大小可以按**轮次**（最近 N 轮）或按 **token**（最近 N token）来定。

它的优点是 summarization 给不了的：

| 维度 | 滑动窗口 | 摘要式压缩 |
|------|----------|-----------|
| 额外 LLM 调用 | ❌ 不需要 | ✅ 需要 |
| 延迟 | 0（纯切片） | 几百 ms~ 几秒 |
| token 成本 | 0 | 有 |
| 实现复杂度 | 五行代码 | 要写 prompt + 处理 |
| 信息保真 | 近期完美、远期全丢 | 全程有损但都保点 |

---

## 2. 什么时候滑窗就够了

滑窗适合**「上下文局部性」强**的任务——只要最近几轮就能干活，远期信息无关紧要：

| 场景 | 滑窗够用吗 | 为什么 |
|------|-----------|--------|
| 闲聊机器人 | ✅ | 没人在乎 50 轮前聊了啥 |
| 单轮翻译 / 改写 | ✅ | 根本不需要历史 |
| 短任务 QA | ✅ | 当前问题自包含 |
| 实时语音助手 | ✅ | 延迟敏感，不能跑摘要 |
| 长程编码 Agent | ❌ | 早期的任务目标、决策不能丢 |
| 需记住用户早期偏好的助手 | ❌ | 偏好往往在开头说的 |
| 多步推理（结论依赖早期前提） | ❌ | 砍掉前提就推不下去 |

一句话判据：**「最老的那条消息丢了，会让任务做错吗？」** 不会 → 滑窗够用；会 → 上摘要或剪枝。

---

## 3. 截断的两大陷阱

### 陷阱 1：砍掉了关键早期信息

最常见的错误是「无脑保留最近 N 条」，结果把开头的**任务约束 / system 提示 / 用户核心需求**也滑出去了：

```python
# ❌ 危险：连 system 和最初的任务定义一起砍了
def naive_window(messages, n=6):
    return messages[-n:]   # 第 0 条 system 可能就这么没了！

# 用户在第 1 条说「用 TypeScript，别用 any」，30 轮后被滑出窗口
# → Agent 开始写 any，因为它「忘了」这条约束
```

### 陷阱 2：砍断了 tool_call / tool_result 对

这是 Agent 场景的隐藏地雷。OpenAI / Anthropic 的 API **要求** `tool_call` 和对应的 `tool_result` 必须成对出现。如果窗口边界正好切在中间，留下一个孤儿，API 直接报错：

```python
# 历史里的成对结构：
#   [assistant: tool_calls=[id=abc]]   ← 调用
#   [tool: tool_call_id=abc, ...]      ← 结果
# 如果窗口从中间切，只留下 tool 结果而丢了 assistant 的调用：
# ❌ openai.BadRequestError: messages with role 'tool' must be a response
#    to a preceding message with 'tool_calls'
```

所以**截断边界必须对齐到完整的「轮」**，不能落在工具调用对中间。

---

## 4. 正确姿势：保头 + 保尾 + 砍中间

参考 [01-foundations/03-context-rot](../01-foundations/03-context-rot.md) 的 lost-in-the-middle 规律——模型对**头尾**注意力最强，中间最弱。截断也该顺应这点：**头部（system + 原始任务）永远保，尾部（近期）保，砍掉的是中间最不重要的那段**。

```python
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")
MAX_TOKENS = 8000


def count(messages):
    return sum(len(enc.encode(m.get("content") or "")) for m in messages)


def is_tool_result(msg):
    return msg.get("role") == "tool"


def sliding_window(messages, max_tokens=MAX_TOKENS, keep_head=1):
    """
    保头(前 keep_head 条，通常是 system+任务) + 保尾(尽量多的近期)，砍中间。
    截断边界对齐到完整轮，不留孤儿 tool_result。
    """
    head = messages[:keep_head]
    body = messages[keep_head:]
    budget = max_tokens - count(head)

    # 从尾部往前累加，直到预算用完
    kept = []
    for msg in reversed(body):
        cost = len(enc.encode(msg.get("content") or ""))
        if cost > budget:
            break
        kept.append(msg)
        budget -= cost
    kept.reverse()

    # 修边界：如果第一条是孤儿 tool_result（前面的 tool_call 被砍了），丢掉它
    while kept and is_tool_result(kept[0]):
        kept.pop(0)

    return head + kept


# 用法
messages = sliding_window(messages, max_tokens=8000, keep_head=1)
```

要点：
- `keep_head=1` 保住 system；如果原始任务在第 2 条，设 `keep_head=2`。更稳妥的做法是把任务约束**钉死在 system 里**，这样它天然不会被滑出。
- 从尾部反向累加，保证留下的是**最近**的完整轮。
- 收尾时清理孤儿 `tool_result`，避免 API 报错。

---

## 5. 滑窗 + 摘要：两全其美

实战里滑窗很少单用，常和摘要组合：**近期用滑窗保原文，被滑出去的旧历史不直接丢、而是摘要掉**。

```
[system + 任务]              ← 永远保（头）
[旧历史的摘要]               ← 滑出窗口的部分，摘要而非丢弃
[最近 N 轮原文]              ← 滑动窗口（尾）
[当前 user]
```

这就是 [02-summarization](./02-summarization.md) 第 3 节那段代码的结构——`KEEP_RECENT` 是滑窗的尾，`summary` 顶替被滑出的中间。纯滑窗是「砍中间」，加上摘要就变成「压缩中间」，信息损失从「全丢」降到「有损保留」。

---

## 6. 常见误区

| 误区 | 真相 |
|------|------|
| 「滑窗 = `messages[-N:]`」 | 这样会把 system 和任务约束一起砍掉，要保头 |
| 「按 token 截断很安全」 | 边界可能切在 tool_call 对中间，触发 API 报错 |
| 「滑窗简单所以哪都能用」 | 只适合无远期依赖的任务，长 Agent 会丢关键约束 |
| 「砍掉的就彻底没了」 | 可以滑出时摘要 / 落盘，不必硬丢 |
| 「窗口越大越保险」 | 留太多近期照样会触发 rot 和高成本，按需取 N |

---

## 7. 下一步

- 📖 重要性评分与剪枝：比「砍中间」更聪明的取舍 → [04-pruning.md](./04-pruning.md)
- 📖 摘要式压缩：把滑出的旧历史压成短文 → [02-summarization.md](./02-summarization.md)
- 📖 何时触发截断 / 压缩 → [05-when-to-compact.md](./05-when-to-compact.md)
- 📖 为什么头尾比中间重要（lost-in-the-middle） → [../01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)
- 📖 Agent 轨迹里工具调用对怎么累积 → [../06-agent-context/01-accumulation.md](../06-agent-context/01-accumulation.md)

## 参考资料

- LangChain, "How to trim messages"（`trim_messages`，支持保头保尾 + token 策略）: https://python.langchain.com/docs/how_to/trim_messages/
- OpenAI, "Function calling"（tool_call / tool_result 配对要求）: https://platform.openai.com/docs/guides/function-calling
