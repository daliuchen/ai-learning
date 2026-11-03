# MCP Client 03：Sampling 让 Server 借用 Host 的 LLM（已弃用）

> Sampling 让 Server 在执行过程中请 Client 用 Host 的 LLM 做一次 completion，这样 Server 既不用持有 API Key，也不用绑定某一家模型。2026-07-28 把它标成了 Deprecated（SEP-2577），功能至少保留 12 个月，但不建议新设计再依赖它，官方建议改成 Server 直接调用自己的 LLM API。线上还有大量老客户端和老 Server 在用 Sampling，所以这篇仍然把它的工作方式讲清楚，再给出推荐的替代做法。

同一个版本里还有另一项相关变化：2026-07-28 去掉了所有 Server 主动发给 Client 的请求。Sampling 并没有因此失效，只是换了传递方式：Server 在工具结果里返回「我需要一次 sampling」（`InputRequiredResult`），Client 用 `sampling_callback` 回答后重试原请求（多轮请求 MRTR，详见[Elicitation、Roots 与多轮请求（MRTR）](04-roots-elicitation.md)）。写 Server 时用 `Resolve(Sample(...))`，一份代码就能同时服务 2025 和 2026 版的客户端。

---

## 1. 为什么需要 Sampling，又为什么被弃用

设想一个 MCP Server 要做的事是：分析 50 个航班，按用户偏好挑出最合适的 3 个。让模型逐个调工具，上下文会爆；把 50 个航班全塞进对话，上下文也会爆。比较理想的是让 Server 端做一次独立的 LLM 调用。问题是 Server 是后端代码，它该怎么调 LLM？

方案 A 是 Sampling，让 Host 替 Server 跑一次模型。Server 不用管 Key，但 Client 得实现审批 UI、模型路由、限流等一整套东西。方案 B 是 Server 自己持有 OpenAI、Anthropic 或千问的 Key，Server 作者要负责 Key 和账单，但简单可控，行为确定。

实际情况是，Sampling 的客户端支持度很低，声明了 `sampling` 能力的 Host 寥寥无几；而在无状态的 2026 协议下，它还得走多轮重试，复杂度更高。所以规范最终决定弃用它，改走方案 B。

| 弃用的 | 官方建议的替代 |
|--------|---------------|
| Sampling（`sampling/createMessage`） | Server 直接集成 LLM 提供方的 API |
| Roots（`roots/list`） | 通过工具参数、资源 URI 或 Server 配置传递目录 |
| 协议层 Logging | stderr / OpenTelemetry |

---

## 2. 流程（2025 版：独立请求）

```
Server                 Client (Host)              User             LLM
  │ sampling/createMessage   │                     │                │
  ├─────────────────────────>│                     │                │
  │                          │ 展示给用户审批       │                │
  │                          ├────────────────────>│                │
  │                          │ 用户批准（可改）     │                │
  │                          │<────────────────────┤                │
  │                          │ 转发到真实 LLM API                    │
  │                          ├──────────────────────────────────────>│
  │                          │<─── completion ───────────────────────│
  │                          │ 展示生成结果给用户（可改可拒）         │
  │ result                   │                                       │
  │<─────────────────────────┤                                       │
```

这里有两道人工把关：用户既审核 prompt，也审核 response。

## 2.1 流程（2026 版：多轮请求）

```
Client → Server: tools/call find_best_flight {user_pref}                    (id=1)
Server → Client: result { resultType:"input_required",
                          inputRequests:{ pick:{ method:"sampling/createMessage", params:{messages:[...],maxTokens:20} } },
                          requestState:"<加密的不透明字符串>" }
   （Client 审批 → 调模型 → 得到 CreateMessageResult）
Client → Server: tools/call find_best_flight {user_pref,                    (id=2，新 ID)
                          inputResponses:{ pick:{role:"assistant",content:{...},model:"..."} },
                          requestState:"<原样回传>" }
Server → Client: result { resultType:"complete", content:[...] }
```

语义完全相同，只是方向变了：Server 不再回头问 Client，而是把问题放在答案里，由 Client 带着答案再请求一次。所以 Server 不需要保存任何连接状态。

---

## 3. Capability 声明

Client 必须显式声明这个能力。在 `Client(...)` 里注册 `sampling_callback` 就等于声明：

```json
{"capabilities": {"sampling": {}}}
```

可选的子能力有两个：`sampling.tools` 表示支持模型在 sampling 时使用工具（传 `sampling_capabilities=SamplingCapability(tools=SamplingToolsCapability())`）；`sampling.context` 里 `includeContext` 的 `thisServer` / `allServers` 已经 Deprecated，不要用。

Server 不会把请求发给没声明这个能力的 Client。在 Python SDK 里，这种情况会直接以 `-32021 MissingRequiredClientCapability` 失败，而不是发出一个对方处理不了的请求。

---

## 4. 协议消息

### 4.1 简单请求

```json
{
  "method": "sampling/createMessage",
  "params": {
    "messages": [
      {"role": "user", "content": {"type": "text", "text": "法国的首都是哪里？"}}
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

（在 2026 版里，这个对象放在 `inputRequests` 的某个键下面，不再作为顶层请求发送。）

### 4.2 响应

```json
{
  "role": "assistant",
  "content": {"type": "text", "text": "巴黎是法国首都。"},
  "model": "qwen-flash",
  "stopReason": "endTurn"
}
```

---

## 5. modelPreferences：让 Client 帮你挑模型

Server 不知道 Client 有哪些模型可用，但可以给出偏好：

```python
modelPreferences = {
    "hints": [
        {"name": "qwen-flash"},      # 优先某类模型
        {"name": "qwen"}              # 退而求其次
    ],
    "costPriority": 0.3,              # 0-1，越大越想便宜
    "speedPriority": 0.8,             # 0-1，越大越想快
    "intelligencePriority": 0.5,      # 0-1，越大越想聪明
}
```

Client 根据这些偏好做模型路由。如果用户没装某家的模型，就按 hints 的含义和优先级映射到其它厂商同档位的模型；如果用户更看重成本，就选 mini、flash 一类的模型。

Hints 按子串模糊匹配，`"flash"` 能匹配到 `"qwen-flash"`、`"gemini-flash"` 等。

---

## 6. Python SDK：Server 端用 `Resolve(Sample(...))`

v2 推荐的写法是：写一个解析器返回 `Sample(...)`，工具参数用 `Resolve(...)` 接收 `CreateMessageResult`。SDK 会根据连接的协议版本选择传递方式（2025 是独立请求，2026 是多轮请求），你的代码不用改：

```python
from typing import Annotated
from mcp.server import MCPServer
from mcp.server.mcpserver import Resolve, Sample
from mcp.types import CreateMessageResult, SamplingMessage, TextContent

mcp = MCPServer("flight-server")

def ask_host_to_pick(user_pref: str) -> Sample:
    prompt = f"用户偏好: {user_pref}\n航班选项: ...\n只返回最推荐的航班 ID。"
    return Sample([SamplingMessage(role="user", content=TextContent(type="text", text=prompt))], max_tokens=20)

@mcp.tool()
async def find_best_flight(user_pref: str, pick: Annotated[CreateMessageResult, Resolve(ask_host_to_pick)]) -> str:
    """借 Host 的 LLM 挑最优航班"""
    return pick.content.text if pick.content.type == "text" else ""
```

- 解析器的参数按名字对应工具参数（`user_pref`）；`pick` 不会出现在工具的 `inputSchema` 里，模型既填不了也看不到
- 传了 `tools=` / `tool_choice=` 时，注入的类型会变成 `CreateMessageResultWithTools`（Client 需要声明 `sampling.tools`）
- 多轮请求有一条规则要注意：在 2026 版里，这个 Sample 请求在各轮重试之间必须逐字相同（SDK 用它做绑定校验），所以只能用工具参数这类稳定数据来构造，不要放时间戳、随机数或 `uuid4()`
- `include_context` 保持默认：非 `"none"` 的值本身也已弃用
- 老写法 `await ctx.sample(...)` / `ctx.session.create_message(...)` 只在 2025 式、有反向通道的连接上可用，在 2026 连接上会抛 `NoBackChannelError`，还会触发弃用警告，不要再用

## 7. Python SDK：Client 端处理 Sampling

注册 `sampling_callback`，签名是 `async (context, params) -> CreateMessageResult`。模型由你来跑，SDK 只负责转发请求：

```python
from mcp import Client
from mcp.client import ClientRequestContext
from mcp.types import CreateMessageRequestParams, CreateMessageResult, TextContent
from openai import AsyncOpenAI

async def on_sample(context: ClientRequestContext, params: CreateMessageRequestParams) -> CreateMessageResult:
    # 1. 把 MCP messages 转成模型 API 的格式
    msgs = [{"role": m.role, "content": m.content.text} for m in params.messages if m.content.type == "text"]
    if params.system_prompt:
        msgs.insert(0, {"role": "system", "content": params.system_prompt})
    # 2. （真实 Host 在这里弹审批 UI，让用户看 / 改 prompt）
    # 3. 调真实模型
    resp = await AsyncOpenAI().chat.completions.create(model="qwen-flash", messages=msgs, max_tokens=params.max_tokens)
    # 4. 包回 MCP 结果（也可以返回 ErrorData(...) 来拒绝）
    return CreateMessageResult(role="assistant", content=TextContent(type="text", text=resp.choices[0].message.content or ""), model="qwen-flash")

async with Client(url, sampling_callback=on_sample) as client:   # 注册 = 声明 sampling 能力
    ...
```

> 和 v1 相比：参数对象的字段改成了 snake_case（`params.max_tokens`、`params.system_prompt`、`params.model_preferences`）；回调是并发执行的，不再一次只跑一个；同一个回调既能回答 2025 的独立请求，也能回答 2026 的多轮请求。另外，要让老式的「Server 主动发请求」生效，测试或连接时得用 `Client(..., mode="legacy")`；在默认的 `mode="auto"` 下遇到 2026 版 Server 时走多轮请求，回调照样会被调用。

---

## 8. 带工具的 Sampling（在 Sampling 里跑 Agent 循环）

2025-11-25 引入了这样的能力：Sampling 请求里也可以带 `tools`，Client 端的模型在响应里直接发出 tool_use，Server 端循环执行。对应到 SDK 里，`Sample([...], tools=[...], tool_choice=...)` 会注入 `CreateMessageResultWithTools`，Client 需要声明 `sampling.tools`。不过在 2026 版里，每一轮都是一次完整的请求重试，而且请求内容必须保持稳定，所以复杂的 agent 循环更适合直接放在 Server 自己的 LLM 调用里（方案 B）。

---

## 9. 安全：人工把关与沙盒

Sampling 相当于 Server 反过来花 Client 的钱跑 LLM。如果你还在实现 Host，下面这些控制都要有：

| 控制 | 说明 |
|------|------|
| 审批 UI | 默认每次 sampling 都把 prompt 弹给用户看 |
| 可编辑 | 用户可以修改 prompt 后再放行 |
| 响应审核 | 生成的内容也给用户看，可以修改或拒绝 |
| 限流 | 每分钟最多 N 次，防止 Server 滥用 |
| 信任白名单 | 用户可以对信任的 Server 关闭审批 |
| Token 上限 | 用 maxTokens 强制截断，防止响应过大 |
| 审计日志 | 所有 sampling 都写日志 |

写 Server 的一方也要克制。Sampling 是稀缺资源，明显可以写死的逻辑，不要交给 LLM 来决定。

---

## 10. 使用建议与选型

| 适合让 LLM 做 | 不适合 |
|---------------|--------|
| 自然语言摘要、对比、推荐 | 简单的字段提取 |
| 模糊匹配（用户偏好与选项） | 精确逻辑 |
| 非结构化数据转结构化 | 查询已经结构化的数据 |
| 复杂的多步推理（带工具） | 单条规则判断 |

现在写新 Server，我建议用方案 B，直接调 LLM：Key 配在 Server 自己的环境变量里，不依赖 Client 是否支持。只有在必须使用用户自己的模型或配额、并且确认目标 Host 支持时，才考虑 Sampling。

---

## 11. 完整 demo：两种方案对比

下面这个文件包含方案 A（`Resolve(Sample)`，同一份代码同时适配 2025/2026 客户端）和方案 B（Server 直连 LLM），以及用来演示的 Client（分别用一个确定性的假模型和真实的千问充当 Host LLM）：

```python
# demos/server/03_sampling_flight.py
import asyncio
import json
import os
from typing import Annotated

from mcp import Client, MCPError
from mcp.client import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import Resolve, Sample
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    CreateMessageResultWithTools,
    SamplingMessage,
    TextContent,
)

mcp = MCPServer("flight-server")

FLIGHTS = [
    {"id": "CA1981", "dep": "08:00", "arr": "11:20", "price": 1280, "stops": 0},
    {"id": "MU5102", "dep": "13:00", "arr": "16:30", "price": 1100, "stops": 0},
    {"id": "HU7191", "dep": "22:00", "arr": "01:40", "price": 980, "stops": 0},
    {"id": "CZ3115", "dep": "06:30", "arr": "11:50", "price": 1050, "stops": 1},
]


# ============ 方案 A：Sampling，用 Resolve + Sample（同一份代码同时适配 2025 与 2026 的客户端）============
def ask_host_to_pick(user_pref: str) -> Sample:
    """解析器：描述想让 Client 的模型做什么。注意：这个请求在 MRTR 重试轮次间必须逐字相同，
    所以只能由工具参数等『稳定数据』构造（不要放时间戳、随机数）。"""
    prompt = f"用户偏好: {user_pref}\n航班选项:\n{json.dumps(FLIGHTS, ensure_ascii=False)}\n只返回最推荐的航班 ID，比如 CA1981。"
    return Sample([SamplingMessage(role="user", content=TextContent(type="text", text=prompt))], max_tokens=20)


@mcp.tool()
async def find_best_flight(
    user_pref: str,
    pick: Annotated[CreateMessageResult | CreateMessageResultWithTools, Resolve(ask_host_to_pick)],
) -> str:
    """给定用户偏好，借 Host 的 LLM 挑最优航班"""
    text = pick.content.text.strip().strip("'\"") if pick.content.type == "text" else ""
    best = next((f for f in FLIGHTS if f["id"] == text), None)
    return json.dumps(best, ensure_ascii=False) if best else f"模型返回了未知 ID: {text!r}"


# ============ 方案 B（推荐）：Server 直接调自己的 LLM，不再依赖 Sampling ============
@mcp.tool()
async def find_best_flight_direct(user_pref: str) -> str:
    """Server 自己持有 Key，直接调 OpenAI 兼容接口（如千问 qwen-flash）"""
    from openai import AsyncOpenAI

    llm = AsyncOpenAI()  # 读 OPENAI_API_KEY / OPENAI_BASE_URL（Server 作者自己管 Key 和账单）
    resp = await llm.chat.completions.create(
        model=os.environ.get("QWEN_MODEL", "qwen-flash"),
        max_tokens=20,
        messages=[{"role": "user", "content": f"用户偏好: {user_pref}\n航班: {json.dumps(FLIGHTS, ensure_ascii=False)}\n只返回最推荐的航班 ID。"}],
    )
    return (resp.choices[0].message.content or "").strip()


# ============ 演示 Client：sampling_callback = 你的「Host LLM」（这里用确定性假模型）============
async def host_llm(context: ClientRequestContext, params: CreateMessageRequestParams) -> CreateMessageResult:
    # 真实 Host 在这里：弹审批 UI → 路由到某个模型 → 再让用户审阅结果。这里直接挑最便宜的
    print("  [Host] 收到 sampling 请求，max_tokens =", params.max_tokens)
    cheapest = min(FLIGHTS, key=lambda f: f["price"])["id"]
    return CreateMessageResult(role="assistant", content=TextContent(type="text", text=cheapest), model="fake-llm")


async def host_llm_real(context: ClientRequestContext, params: CreateMessageRequestParams) -> CreateMessageResult:
    """真实 Host 的 sampling 回调：把 MCP 消息转成 OpenAI 格式，调真模型（千问 qwen-flash），再包回 MCP 结果"""
    from openai import AsyncOpenAI

    msgs = [{"role": m.role, "content": m.content.text} for m in params.messages if m.content.type == "text"]
    if params.system_prompt:
        msgs.insert(0, {"role": "system", "content": params.system_prompt})
    model = os.environ.get("QWEN_MODEL", "qwen-flash")
    resp = await AsyncOpenAI().chat.completions.create(model=model, messages=msgs, max_tokens=params.max_tokens)
    return CreateMessageResult(role="assistant", content=TextContent(type="text", text=resp.choices[0].message.content or ""), model=model)


async def main() -> None:
    for mode in ("auto", "legacy"):  # auto → 2026-07-28（多轮请求 MRTR）；legacy → 2025-11-25（独立的 sampling/createMessage 请求）
        async with Client(mcp, mode=mode, sampling_callback=host_llm) as client:
            r = await client.call_tool("find_best_flight", {"user_pref": "最便宜"})
            print(f"[{mode:6}] 协议 {client.protocol_version}: {r.content[0].text}")

    async with Client(mcp) as client:  # 没注册 sampling_callback = 没声明 sampling 能力
        try:
            await client.call_tool("find_best_flight", {"user_pref": "最便宜"})
        except MCPError as e:  # Server 不会把请求发给没声明能力的 Client，而是直接 -32021
            print("[no cb ]", e.code, e)

    if os.environ.get("OPENAI_API_KEY"):
        async with Client(mcp, sampling_callback=host_llm_real) as client:  # 真模型当 Host 的 LLM
            r = await client.call_tool("find_best_flight", {"user_pref": "最便宜"})
            print("[real  ] 真模型当 Host LLM:", r.content[0].text)
        async with Client(mcp) as client:
            r = await client.call_tool("find_best_flight_direct", {"user_pref": "最便宜"})
            print("[direct] 直连 LLM:", r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
```

输出（省略了 `[Host]` 开头的行；最后两行只在设置了 `OPENAI_API_KEY` 时出现，模型的回答每次可能不同）：

```
[auto  ] 协议 2026-07-28: {"id": "HU7191", "dep": "22:00", "arr": "01:40", "price": 980, "stops": 0}
[legacy] 协议 2025-11-25: {"id": "HU7191", "dep": "22:00", "arr": "01:40", "price": 980, "stops": 0}
[no cb ] -32021 Client did not declare the sampling capability required by resolver '__main__:ask_host_to_pick'
[real  ] 真模型当 Host LLM: {"id": "HU7191", "dep": "22:00", "arr": "01:40", "price": 980, "stops": 0}
[direct] 直连 LLM: HU7191
```

同一个工具，`auto` 模式（走 2026 的多轮请求）和 `legacy` 模式（走 2025 的独立请求）得到的答案相同；没注册 `sampling_callback` 的 Client 会被 `-32021` 拒绝，而不是收到一个处理不了的请求。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| Server 调用 sampling，但 Client 没声明能力 | Python SDK 会直接返回 `-32021`；Server 要先用 `ctx.session.check_client_capability(...)` 检查，或者准备好降级方案 |
| 在 2026 连接里调用 `ctx.sample()` / `create_message()` | 会抛 `NoBackChannelError`，改用 `Resolve(Sample(...))` |
| `Sample` 里放了时间戳或随机数 | 各轮重试的请求必须逐字一致，否则会被拒；只用稳定数据 |
| 没限制 token，响应巨大 | 一定要设置 `max_tokens` |
| modelPreferences 的 hints 写得太死 | hints 是模糊匹配，要和优先级配合使用 |
| sampling 里嵌套 sampling | 递归会导致栈溢出和账单失控；要么禁止，要么限制深度 |
| 把用户拒绝 sampling 当成成功 | 回调可以返回 `ErrorData`，Server 端要捕获并降级 |
| sampling 的 prompt 里带了用户隐私数据 | Host 会把 prompt 展示出来，要检查有没有泄露敏感信息 |
| 新项目还在围绕 Sampling 做设计 | 它已经弃用，客户端支持也很少，优先直连 LLM |

---

## 13. 下一步

- Roots、Elicitation 与多轮请求（Sampling 现在也通过它传递）：[Elicitation、Roots 与多轮请求（MRTR）](./04-roots-elicitation.md)
- 多 Server 聚合：[多 Server 聚合与客户端最佳实践](./05-multi-server-best-practices.md)
- 长任务（Tasks 扩展与句柄模式）：[用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)

## 参考资料

- 弃用清单：https://modelcontextprotocol.io/specification/2026-07-28/deprecated
- Sampling spec：https://modelcontextprotocol.io/specification/2026-07-28/client/sampling
- 多轮请求：https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr
- Python SDK Sampling and roots：https://py.sdk.modelcontextprotocol.io/handlers/sampling-and-roots/
