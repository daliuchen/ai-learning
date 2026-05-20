# MCP Integration 04：Pydantic AI 接 MCP

> **一句话**：Pydantic AI **原生**支持 MCP（不需要外部 adapter）——通过 `MCPServerStdio` / `MCPServerStreamableHTTP` 两个类直接把 MCP Server 当 Agent 的 toolset。本篇讲怎么配、几种典型用法、和 LangChain 路线对比。

---

## 1. 为什么 Pydantic AI 的 MCP 集成"最干净"

Pydantic AI 是 Pydantic 团队（FastAPI 作者）出的 Agent 框架，主打类型安全。它对 MCP 的态度是：**MCP 是平级原语，不是外挂**。

- LangChain 路线：MCP → adapter → LangChain Tool → Agent
- Pydantic AI 路线：MCP 直接作为 `toolsets` 之一，类型链路完整

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

agent = Agent(
    "openai:gpt-4o",
    toolsets=[
        MCPServerStdio("python", args=["/abs/path/server.py"]),
    ],
)

result = await agent.run("帮我算 17+25")
```

七行代码——Pydantic AI 自动启动 MCP Server 子进程、拉工具、塞给模型。

---

## 2. 三种 MCPServer 类

Pydantic AI 提供三个：

| 类 | 传输 | 用途 |
|----|------|------|
| `MCPServerStdio` | stdio | 本地 Server 子进程 |
| `MCPServerStreamableHTTP` | Streamable HTTP | 远程 Server |
| `MCPServerSSE` | HTTP+SSE（老） | 兼容 2024-11-05 老服务 |

### 2.1 stdio

```python
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio(
    "python",
    args=["server.py"],
    env={"DEBUG": "1"},
)
```

### 2.2 Streamable HTTP

```python
from pydantic_ai.mcp import MCPServerStreamableHTTP

server = MCPServerStreamableHTTP(
    url="https://mcp.example.com/mcp",
    headers={"Authorization": "Bearer xxx"},
)
```

### 2.3 多个 Server 混用

```python
agent = Agent(
    "openai:gpt-4o",
    toolsets=[
        MCPServerStdio("python", args=["local_server.py"]),
        MCPServerStreamableHTTP(url="https://api.example.com/mcp"),
    ],
)
```

Pydantic AI 内部用 `AsyncExitStack` 管理所有连接，agent 生命周期结束时自动清理。

---

## 3. 完整 demo

```python
# demos/integration/04_pydantic_ai_mcp.py
import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

DEMOS = Path(__file__).resolve().parents[1]


async def main():
    agent = Agent(
        "openai:gpt-4o-mini",
        toolsets=[
            MCPServerStdio(
                "python",
                args=[str(DEMOS / "basics" / "06_first_server.py")],
            ),
        ],
    )

    questions = [
        "用 add 工具算 17+25",
        "现在中国时间几点？",
    ]
    for q in questions:
        print(f"\n[USER] {q}")
        result = await agent.run(q)
        print(f"[Agent] {result.output}")


asyncio.run(main())
```

需要 `OPENAI_API_KEY`。

---

## 4. 类型安全：结构化输出 + MCP 工具

Pydantic AI 的招牌是 typed output：

```python
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio


class Booking(BaseModel):
    flight_id: str
    seat: str
    user_name: str


agent = Agent(
    "openai:gpt-4o",
    output_type=Booking,
    toolsets=[
        MCPServerStdio("python", args=["booking_server.py"]),
    ],
)

result = await agent.run("帮 u_001 订 CA1981 靠窗")
print(result.output)  # Booking(flight_id='CA1981', seat='window', user_name='Alice')
```

`output_type=Booking` 把整个工具调用链路约束成"最终返回必须能填充 Booking"。

---

## 5. Resource 和 Prompt 也能用

Pydantic AI 把 MCP Resource / Prompt 也纳入：

```python
agent = Agent(
    "openai:gpt-4o",
    toolsets=[
        MCPServerStdio("python", args=["docs_server.py"]),
    ],
)

# 取所有 Prompts
prompts = await agent.list_prompts()

# 拿具体 prompt 内容
messages = await agent.get_prompt("code-review", {"file": "auth.py"})
```

Resource 类似（具体 API 随版本可能微调）。

---

## 6. Sampling 反向请求：Server 能调你的 Agent 用的 LLM

Pydantic AI 自动处理 sampling 回调。Server 发 `sampling/createMessage` 时，Pydantic AI 用 agent 配置的同一个 model 跑——Server 不用自己拿 Key。

```python
agent = Agent("openai:gpt-4o")  # Server 发的 sampling 会用 gpt-4o
```

> Server 写的"反向调 LLM"在 Pydantic AI 里"免费"工作——前提是 Client 端有声明 sampling capability。Pydantic AI 默认声明。

---

## 7. vs LangChain 路线对比

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| MCP 集成 | **原生** | 通过 langchain-mcp-adapters |
| 工具加载 | `toolsets=[MCPServerStdio(...)]` | `tools = await client.get_tools()` |
| 类型安全 | ✅ 完整泛型 | ⚠️ 部分 |
| Sampling | ✅ 自动用 agent 的 model | ⚠️ 需要手动实现 |
| 学习曲线 | 平缓 | 中（要懂 LangGraph） |
| 生态 | 较新 | 庞大 |
| 适合 | 想要"少代码 + 强类型" | 已用 LangGraph、需要复杂状态机 |

**建议**：新项目 + 只是想接 MCP → Pydantic AI；已经在 LangGraph 写状态机 → 用 adapter 接进来。

---

## 8. 实际生产模式：多 Server + 类型化输出

```python
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP


class TripPlan(BaseModel):
    destination: str
    flight: str
    hotel: str
    total_cost: float = Field(le=5000)  # 强制不超预算


agent = Agent(
    "anthropic:claude-sonnet-4-6",
    output_type=TripPlan,
    deps_type=str,  # 用户 ID
    toolsets=[
        MCPServerStdio("python", args=["preferences_server.py"]),
        MCPServerStreamableHTTP(url="https://flight-api.com/mcp"),
        MCPServerStreamableHTTP(url="https://hotel-api.com/mcp"),
    ],
    instructions="""你是旅行助手。流程：
1. 用 preferences server 读用户偏好
2. 用 flight server 找符合偏好的航班
3. 用 hotel server 找性价比高的酒店
4. 返回完整 TripPlan，total_cost 必须 <= 5000
""",
)


result = await agent.run("帮 u_001 规划去巴塞罗那 7 天", deps="u_001")
print(result.output)  # TripPlan(...)
```

---

## 9. Debugging 与可观测性

Pydantic AI 自带 Logfire 集成：

```python
import logfire
logfire.configure()
logfire.instrument_pydantic_ai()

# 现在 agent.run 会自动把所有 MCP 调用、工具执行、模型请求记录到 Logfire
```

Logfire UI 里能看到每次 tool call、每次 sampling 的耗时、输入输出、错误堆栈——非常方便排错。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **stdio Server 启动失败** | command/args 路径绝对化；先单独跑一次 Server 看错 |
| **工具没出现** | Pydantic AI 默认全量加载，看 `agent.toolsets` |
| **output_type 强类型导致 LLM 失败** | 把约束放宽或加 instructions 指导 LLM |
| **Sampling 没生效** | Server 端要先检查 client capabilities |
| **MCP Resource 没注入** | Pydantic AI 默认不自动读 Resource，要手动 read 或写到 instructions |

---

## 11. 跨手册关联

本节内容和：

- **02-pydantic-ai 全本手册**：完整 Pydantic AI 教程
- **02-pydantic-ai/04-modules/01-mcp.md**：相同主题的对照篇（Pydantic AI 侧视角）

两本手册可以互补阅读。

---

## 12. 下一步

- 📖 vs Function Calling / OpenAPI 总对比 → [05-comparison.md](./05-comparison.md)
- 📖 远程部署 MCP Server → 05-production/01-remote-mcp
- 🔍 Pydantic AI 全本 → ../../02-pydantic-ai/

## 参考资料

- Pydantic AI MCP 文档：https://ai.pydantic.dev/mcp/
- Pydantic AI 源码：https://github.com/pydantic/pydantic-ai
