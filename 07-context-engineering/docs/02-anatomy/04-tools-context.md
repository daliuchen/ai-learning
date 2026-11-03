# 工具上下文：定义与结果的双重膨胀

> 工具的 JSON schema 定义在还没被调用时就已经占着 token，几十个工具轻松上万；调用之后，返回结果又把上下文撑大一截。工具描述要精简，结果要裁剪，工具太多就要动态加载，否则光是工具列表就能把预算吃掉一大块。

---

## 1. 工具在两个地方占 token

很多人只算了工具返回的数据，忘了工具定义本身也常驻在每一轮的上下文里：

| 位置 | 什么时候占 | 大小 |
|------|-----------|------|
| 工具定义（JSON schema） | 每一轮都发，哪怕一次都没调用 | 每个工具几十到几百 token，几十个就上万 |
| 工具调用请求（assistant） | 模型决定调用时 | 函数名加参数，中等 |
| 工具返回结果（tool 消息） | 调用后回灌 | 往往最大，一次几千 token |

定义和结果两头都占，而且定义是固定开销，不用也得发。

---

## 2. 工具定义到底占多少 token

一个看起来很普通的工具定义：

```python
tool = {
    "type": "function",
    "function": {
        "name": "search_orders",
        "description": "根据用户ID、时间范围和状态查询订单列表，支持分页",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id":   {"type": "string", "description": "用户唯一标识"},
                "start_date":{"type": "string", "description": "开始日期 YYYY-MM-DD"},
                "end_date":  {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "status":    {"type": "string", "enum": ["paid","shipped","refunded"],
                              "description": "订单状态"},
                "page":      {"type": "integer", "description": "页码，从1开始"},
            },
            "required": ["user_id"],
        },
    },
}

import tiktoken, json
enc = tiktoken.get_encoding("o200k_base")
print(len(enc.encode(json.dumps(tool, ensure_ascii=False))))  # 165（o200k_base 估算）
```

一个工具约 165 token，看着不多，但数量一上去：

```text
 1 个工具  ≈    165 token
20 个工具  ≈  3,300 token   ← 还没开始对话就占掉了
50 个工具  ≈  8,250 token   ← 每一轮都要付的固定开销
```

除非做了缓存，这些 token 每一轮都要重发，是纯固定成本。所以工具定义和 system prompt 一样，要稳定、精简、能缓存。

---

## 3. 描述要精简，但别简到模型选不准

工具的 `description` 和参数说明是写给模型看的使用说明。写得啰嗦浪费 token，写得太简略，模型会选错工具。

```python
# ❌ 啰嗦：注释式长描述，占 token 还分散注意力
"description": "这个函数用于查询订单。当用户想知道他们的订单情况时，"
               "你应该调用它。它会返回订单列表，包括订单号、金额、状态等等..."

# ✅ 精炼：一句话说清"做什么 + 何时用"
"description": "查询用户订单列表。需要订单/物流信息时调用。"
```

分寸大概是：用一个短句讲清功能，再写上关键的「什么时候用、什么时候不用」。几个工具功能相近时，用来区分它们的信息一定要留在描述里。

---

## 4. 返回结果的膨胀：一次就是几千 token

agent 的历史膨胀，工具结果是头号原因。一次数据库查询、网页抓取或 API 调用，原始返回常常是好几 KB 的 JSON。

```python
# ❌ 原始结果整坨回灌
raw = api.search(q)            # 返回 50 条记录，每条 30+ 字段
tool_msg = {"role": "tool", "tool_call_id": cid,
            "content": json.dumps(raw)}      # 可能 5000+ token

# ✅ 回灌前裁剪：只留模型需要的字段 + 限制条数
def slim(raw, top=5):
    return [{"id": r["id"], "title": r["title"], "price": r["price"]}
            for r in raw["items"][:top]]
tool_msg = {"role": "tool", "tool_call_id": cid,
            "content": json.dumps(slim(raw), ensure_ascii=False)}  # ~400 token
```

几种裁剪手段：

| 手段 | 说明 |
|------|------|
| 字段投影 | 只返回模型做决策需要的字段，去掉内部 ID、时间戳这类噪声 |
| 限制条数 | 取 top-N，再加一句「共 N 条，已截断」 |
| 压缩格式 | 大表用紧凑的表示，别给层层嵌套的 JSON |
| 分页或二次获取 | 先返回摘要，模型需要细节时再调用一次取单条 |

> 历史里旧的工具结果也可以过期淘汰或做摘要，不必一直留着（见 [对话历史：上下文里增长最快的部分](02-history.md)）。

---

## 5. 工具太多会选错，要动态加载

工具数量多了，除了占 token（见第 2 节），还会带来选择困难。Anthropic 在 tool search 的文档里也提到，可用工具到了几十个，模型选工具的准确率会明显下降，容易选错或者漏调。

应对的办法是裁剪工具集，或者动态加载：

```python
# 思路：按当前任务/意图，只把相关工具放进本轮上下文
ALL_TOOLS = {...}   # 全量注册表

def select_tools(user_query, intent):
    if intent == "order":
        return [ALL_TOOLS[n] for n in ("search_orders", "get_order", "refund")]
    if intent == "logistics":
        return [ALL_TOOLS[n] for n in ("track_package",)]
    return [ALL_TOOLS["search_orders"]]   # 默认最小集

tools = select_tools(q, classify_intent(q))   # 每轮只暴露 1~5 个
```

- 按意图或阶段动态切换工具集，这一轮只暴露相关的几个。
- 工具特别多时，可以做检索式的工具选择：对工具描述建向量索引，召回 top-k 个工具。
- MCP 这类机制也支持按需挂载工具服务器。
- 有些平台已经内置了延迟加载：Anthropic 的 tool search tool 可以把工具定义推迟到模型需要时才加载（见 Anthropic 文档的 Tool search tool 和 Manage tool context 两页）；Claude Code 里的 MCP 工具定义默认也是延迟加载的，先进上下文的只有工具名和 server 说明。自己写动态选工具之前，先看看用的平台有没有现成的。

我的经验是，单轮暴露的工具尽量控制在个位数到十几个，既省 token，选得也更准。

---

## 6. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 每一轮都把几十个工具全发 | 白占上万 token，还容易选错 | 动态选工具或检索式选工具 |
| 工具描述写成长篇说明 | 占 token，稀释注意力 | 一个短句，加上区分信息 |
| 工具原始结果整坨回灌 | 几轮下来历史就超窗 | 字段投影加限制条数 |
| 工具定义里放易变信息 | 缓存失效 | 定义保持稳定 |
| 几个工具描述雷同 | 模型分不清 | 在描述里写清各自的边界 |

工具定义稳定的话，同样可以缓存：

```python
import anthropic
client = anthropic.Anthropic()
client.messages.create(
    model="claude-sonnet-5-5", max_tokens=1024,
    # 缓存断点打在最后一个工具上 → 整个工具列表（以及它之前的前缀）都进缓存
    tools=[*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": "查我的订单"}],
)  # 稳定的工具集 + system 一起进缓存
```

---

## 下一步

- 工具结果怎么在历史里淘汰：[对话历史：上下文里增长最快的部分](02-history.md)
- 工具定义的缓存和成本：[上下文成本优化实战](../08-production/02-cost-optimization.md)
- agent 多步执行时的上下文管理：[工具结果的上下文管理](../06-agent-context/02-tool-results.md)
