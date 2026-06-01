# 工具上下文：定义与结果的双重膨胀

> **一句话**：工具的 JSON schema 定义**还没调用就先占 token**（几十个工具轻松上万），调用后的返回结果又把上下文撑大一波；工具描述要精简、结果要裁剪、工具太多要动态加载——否则光"装备栏"就把预算吃光。

---

## 1. 工具有两处吃 token

很多人只算工具"返回的数据"，忘了工具**定义本身**也常驻在每一轮上下文里：

| 位置 | 什么时候占 | 大小 |
|------|-----------|------|
| **工具定义**（JSON schema） | 每一轮都发，哪怕一次都没调 | 每个工具几十~几百 token，几十个就上万 |
| **工具调用请求**（assistant） | 模型决定调用时 | 函数名 + 参数，中等 |
| **工具返回结果**（tool 消息） | 调用后回灌 | 常常最大，一次几千 token |

也就是说：**装备栏（定义）和战利品（结果）双重占用**，且定义是"白交的房租"——不用也得发。

---

## 2. 工具定义的 token 占用实测

一个看似普通的工具定义：

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
enc = tiktoken.encoding_for_model("gpt-4o")
print(len(enc.encode(json.dumps(tool, ensure_ascii=False))))  # ≈ 130+ token
```

一个工具 ~130 token 看着不多，但：

```text
 1 个工具  ≈    130 token
20 个工具  ≈  2,600 token   ← 还没开始对话就占掉了
50 个工具  ≈  6,500+ token  ← 接近小窗口模型的硬上限
```

这些 token **每一轮都重发**（除非缓存），是纯固定成本。所以工具定义和 system prompt 一样，应当**稳定、精简、可缓存**。

---

## 3. 描述要精简，但别精简到模型选不准

工具的 `description` 和参数说明是给模型看的"使用说明书"。太啰嗦浪费 token，太简略模型选错工具。

```python
# ❌ 啰嗦：注释式长描述，占 token 还分散注意力
"description": "这个函数用于查询订单。当用户想知道他们的订单情况时，"
               "你应该调用它。它会返回订单列表，包括订单号、金额、状态等等..."

# ✅ 精炼：一句话说清"做什么 + 何时用"
"description": "查询用户订单列表。需要订单/物流信息时调用。"
```

平衡点：**一句话讲清功能 + 关键的何时用/何时不用**，把消歧信息留在描述里（尤其几个工具功能相近时）。

---

## 4. 返回结果的膨胀：一次返回几千 token

工具结果是 agent 历史膨胀的头号元凶。一次数据库查询、网页抓取、API 调用，原始返回动辄几 KB JSON。

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

裁剪原则：

| 手段 | 说明 |
|------|------|
| **字段投影** | 只回模型决策需要的字段，砍掉内部 ID、时间戳等噪声 |
| **条数限制** | top-N，外加"共 N 条，已截断"提示 |
| **格式压缩** | 大表用紧凑表示，别给冗长嵌套 JSON |
| **分页/二次取** | 先回摘要，模型需要细节时再调一次拿单条 |

> 同样别忘了：历史里的旧工具结果可以**过期淘汰/摘要**，不必永久保留（见 [02-history.md](02-history.md)）。

---

## 5. 工具太多 → 模型选错 → 动态加载

工具数量上去后有两个问题：占 token（§2）+ **选择困难**。研究和实践都显示，工具一多（几十个），模型的工具选择准确率明显下降，容易选错工具或漏调。

应对思路（**工具裁剪 / 动态加载**）：

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

- 按意图/阶段**动态切换工具集**，本轮只暴露相关的几个。
- 工具非常多时用**检索式工具选择**（对工具描述做向量检索，召回 top-k 工具）。
- 也有 MCP 等机制支持按需挂载工具服务器。

经验：**单轮暴露的工具尽量控制在个位数到十几个**，既省 token 又提准。

---

## 6. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 把全量几十个工具每轮全发 | 上万 token 白占 + 选错 | 动态/检索式选工具 |
| 工具描述写成长篇说明 | 占 token、稀释 | 一句话 + 消歧 |
| 工具原始结果整坨回灌 | 历史几轮就爆 | 字段投影 + 限条数 |
| 工具定义里塞易变信息 | 打碎缓存 | 定义保持稳定 |
| 多个工具描述雷同 | 模型分不清 | 描述里写清边界差异 |

工具定义稳定时同样可以缓存：

```python
import anthropic
client = anthropic.Anthropic()
client.messages.create(
    model="claude-sonnet-4-5", max_tokens=1024,
    tools=[{**t, "cache_control": {"type": "ephemeral"}} for t in tools[-1:]],
    messages=[{"role": "user", "content": "查我的订单"}],
)  # 稳定的工具集 + system 一起进缓存
```

---

## 7. 小结

- 工具在上下文里**双重占用**：定义（每轮常驻、不用也发）+ 结果（调用后回灌）。
- 一个工具 ~一两百 token，几十个就上万——定义要像 system 一样稳定、精简、可缓存。
- 描述精炼到"一句话 + 何时用"，相近工具要写清边界。
- 返回结果必须裁剪后回灌（字段投影、限条数、分页二次取）。
- 工具太多会降低选择准确率，用动态加载/检索式选工具，单轮控制在十几个内。

---

## 下一步

- 工具结果如何在历史里淘汰：[02-history.md](02-history.md)
- 工具定义的缓存与成本：[../08-production/02-cost-optimization.md](../08-production/02-cost-optimization.md)
- agent 多步执行下的上下文管理：[../06-agent-context/02-tool-results.md](../06-agent-context/02-tool-results.md)
