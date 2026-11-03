# CE 05-03：滑动窗口与截断（Sliding Window）

> 滑动窗口是最简单的压缩：只保留最近 N 轮或最近 N 个 token，更早的直接丢掉。不跑 LLM，没有额外延迟，五行代码就能实现。不需要远期记忆的任务用它完全够了，但有两个坑要避开：一是把关键的早期任务约束砍掉，二是把成对的 `tool_call`/`tool_result` 切断导致 API 报错。稳妥的做法是保头（system 和任务）、保尾（近期），砍中间。

---

## 1. 思路：只留最近的

可以把滑动窗口想成一扇固定大小的窗户，沿着历史往后滑：

```
全部历史：  [1][2][3][4][5][6][7][8][9][10]
                              └─── 窗口(N=4) ───┘
保留：                       [7][8][9][10]
丢弃：     [1][2][3][4][5][6]
```

新消息进来，最老的滑出去。窗口大小可以按轮次（最近 N 轮）定，也可以按 token（最近 N 个 token）定。

和摘要式压缩相比，它有几项摘要做不到的优点：

| 维度 | 滑动窗口 | 摘要式压缩 |
|------|----------|-----------|
| 额外 LLM 调用 | 不需要 | 需要 |
| 延迟 | 0（纯切片） | 几百 ms~ 几秒 |
| token 成本 | 0 | 有 |
| 实现复杂度 | 五行代码 | 要写 prompt + 处理 |
| 信息保真 | 近期完美、远期全丢 | 全程有损但都保点 |

---

## 2. 什么时候滑窗就够了

滑窗适合上下文局部性强的任务，也就是只看最近几轮就能干活、远期信息无关紧要的场景：

| 场景 | 滑窗够用吗 | 为什么 |
|------|-----------|--------|
| 闲聊机器人 | 够 | 没人在乎 50 轮前聊过什么 |
| 单轮翻译 / 改写 | 够 | 根本不需要历史 |
| 短任务 QA | 够 | 当前问题自包含 |
| 实时语音助手 | 够 | 延迟敏感，不能跑摘要 |
| 长程编码 Agent | 不够 | 早期的任务目标、决策不能丢 |
| 需记住用户早期偏好的助手 | 不够 | 偏好往往在开头说的 |
| 多步推理（结论依赖早期前提） | 不够 | 砍掉前提就推不下去 |

判断时问自己一句：最老的那条消息丢了，任务会做错吗？不会，滑窗就够；会，就上摘要或剪枝。

---

## 3. 截断的两个陷阱

### 陷阱 1：砍掉了关键的早期信息

最常见的错误是只管保留最近 N 条，结果把开头的任务约束、system 提示、用户的核心需求一起滑了出去：

```python
# ❌ 危险：连 system 和最初的任务定义一起砍了
def naive_window(messages, n=6):
    return messages[-n:]   # 第 0 条 system 可能就这么没了！

# 用户在第 1 条说「用 TypeScript，别用 any」，30 轮后被滑出窗口
# → Agent 开始写 any，因为它「忘了」这条约束
```

### 陷阱 2：切断了 tool_call / tool_result 对

这是 Agent 场景里不容易发现的问题。OpenAI 和 Anthropic 的 API 都要求 `tool_call` 和对应的 `tool_result` 成对出现。窗口边界如果正好切在两者之间，留下一条孤立的结果，API 会直接报错：

```python
# 历史里的成对结构：
#   [assistant: tool_calls=[id=abc]]   ← 调用
#   [tool: tool_call_id=abc, ...]      ← 结果
# 如果窗口从中间切，只留下 tool 结果而丢了 assistant 的调用：
# ❌ openai.BadRequestError: messages with role 'tool' must be a response
#    to a preceding message with 'tool_calls'
```

所以截断边界必须对齐到完整的一轮，不能落在一对工具调用中间。

---

## 4. 稳妥的做法：保头、保尾、砍中间

[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md) 讲过，模型对头尾的注意力最强，中间最弱。截断也应该顺着这个规律来：头部（system 和原始任务）始终保留，尾部（近期）保留，砍掉的是中间最不重要的那段。

```python
import tiktoken

enc = tiktoken.get_encoding("o200k_base")
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

几点说明：

- `keep_head=1` 保住 system；原始任务如果在第 2 条，就设 `keep_head=2`。更稳妥的办法是把任务约束直接写进 system，它就不可能被滑出去。
- 从尾部往前累加，保证留下的是最近的完整轮次。
- 最后清掉开头孤立的 `tool_result`，避免 API 报错。

---

## 5. 滑窗 + 摘要

实际项目里滑窗很少单独用，通常和摘要组合：近期用滑窗保留原文，被滑出去的旧历史不直接丢，而是做成摘要。

```
[system + 任务]              ← 永远保（头）
[旧历史的摘要]               ← 滑出窗口的部分，摘要而非丢弃
[最近 N 轮原文]              ← 滑动窗口（尾）
[当前 user]
```

[摘要式压缩](./02-summarization.md) 第 3 节那段代码就是这个结构：`KEEP_RECENT` 是滑窗的尾，`summary` 顶替了被滑出的中间段。纯滑窗是把中间砍掉，加上摘要后变成把中间压缩，信息从全丢变成有损保留。

---

## 6. 常见误区

| 误区 | 实际情况 |
|------|------|
| 「滑窗 = `messages[-N:]`」 | 这样会把 system 和任务约束一起砍掉，要保头 |
| 「按 token 截断很安全」 | 边界可能切在 tool_call 对中间，触发 API 报错 |
| 「滑窗简单所以哪都能用」 | 只适合没有远期依赖的任务，长程 Agent 会丢关键约束 |
| 「砍掉的就彻底没了」 | 可以在滑出时做摘要或落盘，不必硬丢 |
| 「窗口越大越保险」 | 近期留太多照样会触发 rot、推高成本，按需取 N |

---

## 7. 下一步

- 比砍中间更聪明的取舍：[重要性评分与剪枝](./04-pruning.md)
- 把滑出的旧历史压成短文：[摘要式压缩](./02-summarization.md)
- 截断和压缩什么时候触发：[何时触发压缩](./05-when-to-compact.md)
- 为什么头尾比中间重要：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
- Agent 轨迹里工具调用对怎么累积：[Agent 的上下文累积问题](../06-agent-context/01-accumulation.md)

## 参考资料

- LangChain, Short-term memory（`trim_messages`，支持保头保尾 + token 策略）: https://docs.langchain.com/oss/python/langchain/short-term-memory
- OpenAI, "Function calling"（tool_call / tool_result 配对要求）: https://developers.openai.com/api/docs/guides/function-calling
