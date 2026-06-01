# 系统指令层：上下文里最稳定的一块

> **一句话**：system prompt 是上下文里**位置固定、生命周期最长**的一段，放"角色 + 规则 + 输出格式 + 工具策略"这种稳定信息，别放易变数据和海量知识——它越长，稀释越严重，而且稳定的它正好能吃满 prompt caching。

---

## 1. system prompt 在上下文结构里的特殊地位

一次模型调用的上下文，本质是一个有序的消息数组：

```python
messages = [
    {"role": "system",    "content": "你是..."},   # ← 本篇主角，永远在最前
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "..."},
    # ... 历史不断累积
    {"role": "user",      "content": "当前问题"},
]
```

system prompt 有三个别人没有的属性：

| 属性 | 说明 |
|------|------|
| **位置固定** | 永远在数组最前面，不会被历史挤走 |
| **生命周期最长** | 整个会话/整个应用都不变，每一轮都重新发送 |
| **权重更高** | 各家模型都对 system 角色做了对齐训练，指令遵循优先级高于 user |
| **可被缓存** | 因为前缀稳定，是 prompt caching 命中率最高的部分（见 §5） |

> 注意 Anthropic API 把 system 单独拎成一个顶层参数 `system=`，不放进 `messages`；OpenAI / Gemini 则是 messages 数组里的一条 `system`（OpenAI 新版也叫 `developer`）。结构略有差异，但"固定前缀、高权重"的定位一致。

---

## 2. system prompt 里该放什么

只放**整个会话期间都成立、且每一轮都需要**的稳定信息：

| 类别 | 例子 | 为什么放这里 |
|------|------|------------|
| **角色 / 身份** | "你是一名资深 Python 后端工程师" | 决定整体语气和专业度，全程不变 |
| **行为规则** | "拿不准时先反问，不要瞎猜" | 约束每一轮的行为 |
| **输出格式** | "用 Markdown，代码块标语言" | 每个回复都要遵守 |
| **工具使用策略** | "查库存先调 `get_stock`，不要凭记忆答" | 决定 agent 的决策模式 |
| **硬性边界** | "绝不输出用户的真实手机号" | 安全/合规底线 |

```python
SYSTEM_PROMPT = """\
你是「果园」电商客服助手。

# 角色
专业、简洁、友好。中文回复。

# 规则
- 涉及订单/物流，必须调用工具查实时数据，不要凭记忆回答。
- 不确定时反问澄清，而不是猜测。
- 退款政策以工具返回为准，不自行承诺。

# 输出
- 普通回答用纯文本。
- 列订单用 Markdown 表格。
"""
```

---

## 3. system prompt 里**不该**放什么

| 反模式 | 问题 | 正确去处 |
|--------|------|---------|
| 用户姓名/当前订单号/今天日期 | 易变，每个会话甚至每轮都不同，写死会污染缓存 | 放 user 消息或单独的动态块 |
| 整份产品手册 / 知识库 | 海量、且大部分轮次用不上，纯稀释 | RAG 检索按需注入（见 [03-retrieved-context.md](03-retrieved-context.md)） |
| 上一轮的对话内容 | 那是历史的职责 | messages 历史（见 [02-history.md](02-history.md)） |
| 几十条 few-shot 示例全塞进来 | 占预算、可能过时 | 精选 1–5 条，或动态检索（见 [05-few-shot.md](05-few-shot.md)） |

```python
# ❌ 把易变信息焊进 system，缓存全失效、还稀释
system = f"你是客服。当前用户：{user_name}，订单：{order_id}，时间：{now}"

# ✅ system 保持稳定，动态信息走 user 消息
system = "你是客服。涉及订单必须调工具查询。"
messages = [{"role": "user",
             "content": f"[上下文] 用户={user_name} 订单={order_id}\n问题：{q}"}]
```

---

## 4. 越长越稀释：system prompt 的"占比"问题

上下文工程的核心视角是**预算分配**。system prompt 占的 token 是从总预算里扣的，而且每一轮都重复占用。

- 一个 200 token 的精炼 system，在 8K 上下文里占 2.5%，几乎无感。
- 一个 4000 token 的"什么都往里塞"的 system，不光吃掉 4K 预算，还会因为关键指令被埋在大段文字里而**降低遵循率**——这就是 context rot 的一种表现：信号被噪声稀释。

经验法则：

```text
能用一句话说清的规则，不要写成一段。
能放检索/工具结果的知识，不要写进 system。
写完回头删——80% 的初版 system prompt 能砍掉一半。
```

> 这里聚焦"它在上下文结构里的位置和占比"。怎么把规则**写好**（措辞、负向指令、思维链触发）是 Prompt Engineering 手册的事，本手册不重复。

---

## 5. 稳定的 system prompt = 满分缓存命中

prompt caching 的机制是：**从头开始连续相同的前缀**才能命中缓存。system prompt 在最前面且最稳定，是天然的最佳缓存块。

```python
import anthropic

client = anthropic.Anthropic()

resp = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,                 # 大段稳定指令
            "cache_control": {"type": "ephemeral"}, # ← 标记为可缓存
        }
    ],
    messages=[{"role": "user", "content": "我的订单到哪了？"}],
)
print(resp.usage)  # 关注 cache_creation_input_tokens / cache_read_input_tokens
```

- 首次调用：写缓存（`cache_creation_input_tokens`），略贵。
- 后续命中：缓存读取（`cache_read_input_tokens`），价格通常是常规输入的 ~10%，延迟也更低。
- OpenAI 的 prompt caching 是**自动**的（≥1024 token 前缀自动缓存），同样要求前缀稳定。

这就反向印证了 §3：**任何写进 system 的易变信息，都会改变前缀、打碎缓存**。把 `{now}` 拼进 system，等于每轮都重建缓存，白白多花钱。

| 做法 | 缓存命中 | 成本影响 |
|------|---------|---------|
| system 全程不变 | ✅ 每轮命中 | 最省 |
| system 含 `今天是{date}` | ❌ 每天失效 | 浪费 |
| system 含 `用户={name}` | ❌ 每用户失效 | 严重浪费 |

---

## 6. 小结

- system prompt 是上下文里**位置固定、寿命最长、权重最高、最该缓存**的一块。
- 放：角色、规则、输出格式、工具策略、硬边界。
- 不放：易变信息、海量知识、对话历史、大量示例。
- 越短越聚焦，遵循率越高、缓存越省钱。
- 把它当成"应用级常量"来设计，动态内容一律下沉到 user 消息或检索/工具结果。

---

## 下一步

- 历史是上下文里增长最快的部分：[02-history.md](02-history.md)
- 知识按需注入而非写死：[03-retrieved-context.md](03-retrieved-context.md)
- 各部分怎么拼成清晰结构：[06-structure.md](06-structure.md)
- 缓存与成本的系统打法：[../07-long-context/03-prompt-caching.md](../07-long-context/03-prompt-caching.md)
