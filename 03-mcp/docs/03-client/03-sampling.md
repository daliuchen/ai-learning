# MCP Client 03：Sampling —— Server 反过来调 LLM 的杀手锏

> **一句话**：Sampling 让 Server 在执行过程中"反向"请求 Client 用 Host 的 LLM 做一次 completion——Server 不用持有 API Key、不用绑某家模型、不用包 SDK。这是 MCP 让 Server 端 Agent 工作流变可能的关键能力。

---

## 1. 场景：为什么需要 Sampling

想象一个 MCP Server：「分析这 50 个航班，按用户偏好挑最优的 3 个」。

笨办法：让模型自己调 50 次工具看每个航班，然后比较——上下文窗口炸了。
另一个笨办法：把 50 个航班塞进对话，让模型自己分析——上下文又炸了。

理想方案：**Server 端做一次"独立"LLM 调用**，传入 50 个航班的 JSON，拿到推荐的 3 个 id，返回给模型。

但 Server 是后端代码——它怎么调 LLM？

- 选项 A：让 Server 自己拿 OpenAI / Anthropic Key → ❌ Server 作者要管 Key、要管账单、不灵活
- 选项 B：**Sampling**——让 Host 帮 Server 跑一次

后者就是 Sampling 的设计。

---

## 2. 流程

```
Server                 Client (Host)              User             LLM
  │                          │                     │                │
  │ sampling/createMessage   │                     │                │
  ├─────────────────────────>│                     │                │
  │                          │ 展示给用户审批       │                │
  │                          ├────────────────────>│                │
  │                          │ 用户批准（可改）     │                │
  │                          │<────────────────────┤                │
  │                          │                                       │
  │                          │ 转发到真实 LLM API                    │
  │                          ├────────────────────────────────────--> │
  │                          │<─── completion ─────────────────────--│
  │                          │                                       │
  │                          │ 展示生成结果给用户                     │
  │                          ├────────────────────>│                  │
  │                          │ 用户批准（可改）     │                  │
  │                          │<────────────────────┤                  │
  │ result                   │                                       │
  │<─────────────────────────┤                                       │
```

**双重人在回路**：用户既审 prompt 又审 response。

---

## 3. Capability 声明

Client 必须显式声明：

```json
{"capabilities": {"sampling": {}}}
```

可选子能力：

```json
{
  "capabilities": {
    "sampling": {
      "tools": {},      // 支持 sampling 时模型用工具（agentic loop）
      "context": {}     // 已 soft-deprecated，支持 includeContext
    }
  }
}
```

**Server 端**：默认能发 sampling 请求（不需要单独声明），但 Server **必须**先确认 Client 声明了对应能力。

---

## 4. 协议消息

### 4.1 简单请求

```json
{
  "method": "sampling/createMessage",
  "params": {
    "messages": [
      {"role": "user", "content": {"type": "text", "text": "巴黎首都是？"}}
    ],
    "modelPreferences": {
      "hints": [{"name": "claude-sonnet"}],
      "intelligencePriority": 0.8,
      "speedPriority": 0.5
    },
    "systemPrompt": "你是有帮助的助手。",
    "maxTokens": 100
  }
}
```

### 4.2 响应

```json
{
  "result": {
    "role": "assistant",
    "content": {"type": "text", "text": "巴黎是法国首都。"},
    "model": "claude-sonnet-4-6",
    "stopReason": "endTurn"
  }
}
```

---

## 5. modelPreferences：让 Client 帮你挑模型

Server 不知道 Client 有哪些模型可选，但可以给"偏好":

```python
modelPreferences = {
    "hints": [
        {"name": "claude-sonnet"},   # 优先 Sonnet 类
        {"name": "claude"}            # 退而求其次任意 Claude
    ],
    "costPriority": 0.3,              # 0-1，越大越想便宜
    "speedPriority": 0.8,             # 0-1，越大越想快
    "intelligencePriority": 0.5,      # 0-1，越大越想聪明
}
```

Client 看到这些做模型路由：

- 用户没装 Claude？映射到 GPT-4 类（看 hints 含义 + 优先级匹配）
- 用户优先成本？选 mini / haiku 类

Hints 是**子串模糊匹配**——`"sonnet"` 会匹到 `"claude-sonnet-4-6"`、`"claude-3.5-sonnet"`、`"sonnet-20240620"` 等。

---

## 6. Python SDK：Server 端发 Sampling

```python
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent

mcp = FastMCP("flight-analyzer")


@mcp.tool()
async def find_best_flight(flights_json: str, user_pref: str, ctx: Context) -> str:
    """让模型从一堆航班里挑最优的"""
    # 调 Host 的 LLM
    result = await ctx.sample(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"用户偏好: {user_pref}\n航班列表:\n{flights_json}\n\n返回最推荐的航班的 id（只返回 id，纯文本）",
                ),
            )
        ],
        system_prompt="你是擅长比价的旅行助手。",
        max_tokens=100,
        model_preferences={
            "hints": [{"name": "sonnet"}],
            "intelligencePriority": 0.9,
            "speedPriority": 0.5,
            "costPriority": 0.3,
        },
    )

    # result 是 CreateMessageResult
    return result.content.text
```

`ctx.sample(...)` 是 FastMCP 的便捷封装，会自动：

- 构造 sampling/createMessage 请求
- 等响应
- 抛错（Client 端用户拒绝时 raise）

---

## 7. Python SDK：Client 端处理 Sampling

```python
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
import anthropic

client = anthropic.Anthropic()


async def on_sample(params, request_ctx) -> dict:
    """Server 发来 sampling 请求时被调"""
    # 1. 把 MCP messages 转成 Anthropic API 的格式
    anth_messages = [
        {
            "role": m.role,
            "content": m.content.text if hasattr(m.content, "text") else m.content,
        }
        for m in params.messages
    ]

    # 2. 用户审批（这里简化为直接通过）
    # 真实 Host 应该弹 UI
    print(f"[Sampling] Server 想做一次 LLM 调用，prompt 头部: {anth_messages[0]['content'][:100]}...")

    # 3. 调真实模型
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=params.maxTokens or 1024,
        system=params.systemPrompt or "",
        messages=anth_messages,
    )

    # 4. 把响应包成 MCP 格式
    text = resp.content[0].text
    return {
        "role": "assistant",
        "content": {"type": "text", "text": text},
        "model": resp.model,
        "stopReason": "endTurn",
    }


async with ClientSession(read, write, sampling_callback=on_sample) as session:
    await session.initialize()
    ...
```

> SDK 的 sampling_callback 接口随版本演进，参数命名可能微调。核心思想不变：你提供一个 async 函数，签名是 (request_params, ctx) → SamplingResult。

---

## 8. Sampling with Tools（Agent Loop in Sampling）

2025-11-25 引入：Sampling 请求里也可以带 `tools`，让 Client 端模型在响应里直接发 tool_use，Server 端循环执行。

```python
# Server 端发带 tools 的 sampling
result = await ctx.sample(
    messages=[...],
    tools=[
        {
            "name": "get_weather",
            "description": "查城市天气",
            "inputSchema": {...},
        }
    ],
    tool_choice={"mode": "auto"},
    max_tokens=1000,
)

# result 可能是 stopReason="toolUse" 含 tool_use 内容
if result.stopReason == "toolUse":
    for block in result.content:
        if block.type == "tool_use":
            tool_result = await execute_tool(block.name, block.input)
            # 把 tool_result 加到 messages 里再调一次 sample
            ...
```

这让 Server 端实现复杂 Agent 流变得简洁——模型推理和工具执行可以在一个 sampling 调用链里完成。

---

## 9. 安全：人在回路 + 沙盒

Sampling 是 Server 反过来"花 Client 的钱跑 LLM"。Host 端必须：

| 控制 | 说明 |
|------|------|
| **审批 UI** | 默认每次 sampling 弹给用户看 prompt |
| **可编辑** | 用户可以改 prompt 再放行 |
| **响应审核** | 生成内容也给用户看，可改可拒 |
| **限流** | 每分钟最多 N 次（防 Server 滥用） |
| **trusted servers 白名单** | 用户可对信任 Server 关闭审批 |
| **Token 上限** | maxTokens 强制截断防爆 |
| **Audit log** | 所有 sampling 写日志 |

**Server 写代码时也要克制**——sampling 是宝贵资源，不要把"明显能 hardcode"的逻辑用 LLM 决定。

---

## 10. 使用建议

| 适合 sampling | 不适合 |
|---------------|--------|
| 自然语言摘要、对比、推荐 | 简单字段提取 |
| 模糊匹配（用户偏好 vs 选项） | 精确逻辑 |
| 非结构化 → 结构化转换 | 已结构化数据查询 |
| 复杂多步 reasoning（with tools） | 单一规则判断 |

---

## 11. 完整 demo

```python
# demos/server/03_sampling_flight.py
"""Server 用 sampling 让 Host LLM 帮挑航班"""
import json
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent

mcp = FastMCP("flight-server")

FLIGHTS = [
    {"id": "CA1981", "dep": "08:00", "arr": "11:20", "price": 1280, "stops": 0},
    {"id": "MU5102", "dep": "13:00", "arr": "16:30", "price": 1100, "stops": 0},
    {"id": "HU7191", "dep": "22:00", "arr": "01:40", "price": 980,  "stops": 0},
    {"id": "CZ3115", "dep": "06:30", "arr": "11:50", "price": 1050, "stops": 1},
]

@mcp.tool()
async def find_best_flight(user_pref: str, ctx: Context) -> dict:
    """给定用户偏好，返回最优航班"""
    flights_text = json.dumps(FLIGHTS, ensure_ascii=False, indent=2)

    result = await ctx.sample(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        f"用户偏好: {user_pref}\n"
                        f"航班选项:\n{flights_text}\n\n"
                        f"返回最推荐的航班 ID，只返回 ID 不要别的，比如 'CA1981'。"
                    ),
                ),
            )
        ],
        system_prompt="你是简洁的旅行助手。",
        max_tokens=20,
        model_preferences={
            "hints": [{"name": "haiku"}, {"name": "sonnet"}],
            "speedPriority": 0.9,
            "costPriority": 0.6,
        },
    )

    best_id = result.content.text.strip().strip("'\"")
    best = next((f for f in FLIGHTS if f["id"] == best_id), None)
    if not best:
        return {"error": f"模型返回了未知 ID: {best_id}", "raw": result.content.text}
    return best

if __name__ == "__main__":
    mcp.run()
```

Client（带 sampling 回调）见 `demos/client/03_sampling_client.py`。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **Server 调 sampling 但 Client 没声明能力** | Client 返回 -32601；Server 端要先检查 capabilities |
| **没限 token 导致响应巨大** | 永远设 maxTokens |
| **modelPreferences hints 写得太死** | hints 模糊匹配 + 优先级才是组合用法 |
| **sampling 嵌套 sampling** | 递归会爆栈和爆账单；要么禁、要么限深度 |
| **用户拒绝 sampling 当成功** | Server 端 `await ctx.sample()` 会 raise，要捕获并降级 |
| **sampling 里塞了用户私有数据** | 检查 prompt 不要泄漏敏感信息（Host 会展示） |

---

## 13. 下一步

- 📖 Roots + Elicitation（另外两个客户端能力） → [04-roots-elicitation.md](./04-roots-elicitation.md)
- 📖 多 Server 聚合 → [05-multi-server-best-practices.md](./05-multi-server-best-practices.md)
- 📖 Sampling 与 Tasks 结合（异步长 sampling） → 02-server/07-tasks

## 参考资料

- Sampling spec：https://modelcontextprotocol.io/specification/2025-11-25/client/sampling
- Model Preferences 设计：https://modelcontextprotocol.io/specification/2025-11-25/client/sampling#model-preferences
