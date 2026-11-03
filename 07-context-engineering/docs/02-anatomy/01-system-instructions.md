# 系统指令层：上下文里最稳定的一块

> system prompt 在上下文里位置固定、存活时间最长，适合放角色、规则、输出格式、工具策略这类稳定信息，不适合放易变的数据和大量知识。它越长，指令被稀释得越厉害；而它足够稳定，又正好是 prompt caching 最容易命中的部分。这篇讲 system prompt 该放什么、不该放什么，以及它和缓存的关系。

---

## 1. system prompt 在上下文结构里的特殊位置

一次模型调用的上下文，就是一个有序的消息数组：

```python
messages = [
    {"role": "system",    "content": "你是..."},   # ← 本篇主角，永远在最前
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "..."},
    # ... 历史不断累积
    {"role": "user",      "content": "当前问题"},
]
```

system prompt 有几个其他部分没有的特点：

| 属性 | 说明 |
|------|------|
| 位置固定 | 永远在数组最前面，不会被历史挤走 |
| 生命周期最长 | 整个会话、整个应用都不变，每一轮都重新发送 |
| 权重更高 | 各家模型都针对 system 角色做过对齐训练，指令遵循的优先级高于 user |
| 可以缓存 | 前缀稳定，是 prompt caching 命中率最高的部分（见第 5 节） |

> Anthropic API 把 system 单独做成一个顶层参数 `system=`，不放进 `messages`；OpenAI 和 Gemini 则是 messages 数组里的一条 `system`（OpenAI 新版也叫 `developer`）。结构上略有差别，但都是固定前缀、高权重。

---

## 2. system prompt 里该放什么

只放整个会话期间都成立、每一轮都用得上的稳定信息：

| 类别 | 例子 | 为什么放这里 |
|------|------|------------|
| 角色 / 身份 | "你是一名资深 Python 后端工程师" | 决定整体语气和专业度，全程不变 |
| 行为规则 | "拿不准时先反问，不要瞎猜" | 约束每一轮的行为 |
| 输出格式 | "用 Markdown，代码块标语言" | 每个回复都要遵守 |
| 工具使用策略 | "查库存先调 `get_stock`，不要凭记忆答" | 决定 agent 怎么做决策 |
| 硬性边界 | "绝不输出用户的真实手机号" | 安全和合规底线 |

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

## 3. system prompt 里不该放什么

| 反模式 | 问题 | 应该放哪 |
|--------|------|---------|
| 用户姓名、当前订单号、今天的日期 | 易变，每个会话甚至每一轮都不同，写死会让缓存失效 | 放 user 消息或单独的动态块 |
| 整份产品手册或知识库 | 量大，而且大多数轮次用不上，只会稀释 | 用 RAG 按需注入（见 [检索内容：按需放进窗口的外部知识](03-retrieved-context.md)） |
| 上一轮的对话内容 | 那是历史负责的 | messages 历史（见 [对话历史：上下文里增长最快的部分](02-history.md)） |
| 几十条 few-shot 示例全塞进来 | 占预算，还可能过时 | 精选 1–5 条，或动态检索（见 [Few-shot 示例：用预算换确定性](05-few-shot.md)） |

```python
# ❌ 把易变信息焊进 system，缓存全失效、还稀释
system = f"你是客服。当前用户：{user_name}，订单：{order_id}，时间：{now}"

# ✅ system 保持稳定，动态信息走 user 消息
system = "你是客服。涉及订单必须调工具查询。"
messages = [{"role": "user",
             "content": f"[上下文] 用户={user_name} 订单={order_id}\n问题：{q}"}]
```

---

## 4. 越长越稀释：system prompt 的占比

从预算的角度看，system prompt 占的 token 是从总预算里扣的，而且每一轮都要重复占一次。

一个 200 token 的精炼 system，在 8K 的上下文里只占 2.5%，几乎感觉不到。一个 4000 token、什么都往里塞的 system，不光吃掉 4K 预算，关键指令还会被埋在大段文字里，**遵循率随之下降**。这也是 context rot 的一种表现：信号被噪声稀释了。

几条经验：

```text
能用一句话说清的规则，不要写成一段。
能放检索/工具结果的知识，不要写进 system。
写完回头删，初版 system prompt 通常都有不少可以砍的。
```

> 这一篇只讲 system prompt 在上下文结构里的位置和占比。怎么把规则写好（措辞、负向指令、怎么触发思维链）属于 Prompt Engineering 手册的内容，这里不重复。

---

## 5. 稳定的 system prompt 最适合缓存

prompt caching 要求从头开始连续相同的前缀才能命中。system prompt 在最前面，又最稳定，天然适合做缓存块。

```python
import anthropic

client = anthropic.Anthropic()

resp = client.messages.create(
    model="claude-sonnet-5-5",
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

- 第一次调用会写缓存（`cache_creation_input_tokens`），稍贵一点。
- 之后命中时走缓存读取（`cache_read_input_tokens`），Anthropic 默认是基础输入价的 0.1×，示例用的 Sonnet 5.5 是 0.05×，延迟也更低。
- OpenAI 的 prompt caching 是自动的（1024 token 以上的前缀自动缓存），同样要求前缀稳定。

这也从另一面说明了第 3 节的结论：写进 system 的任何易变信息都会改变前缀，让缓存失效。把 `{now}` 拼进 system，等于每一轮都重建缓存，白花钱。

| 做法 | 缓存命中 | 成本影响 |
|------|---------|---------|
| system 全程不变 | 每轮命中 | 最省 |
| system 含 `今天是{date}` | 每天失效 | 浪费 |
| system 含 `用户={name}` | 换一个用户就失效 | 严重浪费 |

---

## 6. 小结

我习惯把 system prompt 当成应用级的常量来设计：只放角色、规则、输出格式、工具策略和硬边界，动态内容一律放到 user 消息或者检索、工具结果里，这样它也能稳定地吃到缓存。

---

## 下一步

- 上下文里增长最快的部分：[对话历史：上下文里增长最快的部分](02-history.md)
- 知识按需注入，而不是写死：[检索内容：按需放进窗口的外部知识](03-retrieved-context.md)
- 各部分怎么拼成清晰的结构：[组织结构：分隔符、XML/Markdown 与顺序](06-structure.md)
- 缓存和成本的完整做法：[Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)
