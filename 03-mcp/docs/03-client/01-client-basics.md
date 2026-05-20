# MCP Client 01：写自己的 Client —— 完整握手到调工具的全流程

> **一句话**：MCP Client 不只是 Claude Code 内部那个对象——任何想消费 MCP Server 能力的 Python 程序（脚本、Agent 框架、自建产品）都可以用 `mcp` SDK 当 Client。本篇把 ClientSession 的完整用法、各种回调、错误处理一次性讲清。

---

## 1. ClientSession 总览

`mcp.ClientSession` 是 SDK 提供的 Client 主入口。一个 ClientSession 对应**一个 Server 的连接**——和 Host 内 Client/Server 一一对应的设计一致。

最小用法：

```python
import asyncio
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


async def main():
    params = StdioServerParameters(command="python", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(tools.tools)


asyncio.run(main())
```

三层 `async with`：

1. `stdio_client(params)` — 启动 Server 子进程，给你 (read, write) 流
2. `ClientSession(read, write)` — 把流绑定到 session 对象
3. `session.initialize()` — 完成三步握手

session 关闭时会自动关闭子进程。

---

## 2. 所有可调用的方法

ClientSession 提供的方法，按原语分组：

### 2.1 生命周期
| 方法 | 用途 |
|------|------|
| `await session.initialize()` | 握手（一次性） |
| `await session.send_ping()` | 心跳 |

### 2.2 Tools
| 方法 | 用途 |
|------|------|
| `await session.list_tools(cursor=None)` | 列工具，可分页 |
| `await session.call_tool(name, arguments, ...)` | 调工具 |

### 2.3 Resources
| 方法 | 用途 |
|------|------|
| `await session.list_resources(cursor=None)` | 列直接 Resource |
| `await session.list_resource_templates(cursor=None)` | 列 Resource 模板 |
| `await session.read_resource(uri)` | 读 Resource |
| `await session.subscribe_resource(uri)` | 订阅 |
| `await session.unsubscribe_resource(uri)` | 取消订阅 |

### 2.4 Prompts
| 方法 | 用途 |
|------|------|
| `await session.list_prompts(cursor=None)` | 列 prompt |
| `await session.get_prompt(name, arguments={})` | 取 prompt |

### 2.5 其它
| 方法 | 用途 |
|------|------|
| `await session.set_logging_level(level)` | 设过滤等级 |
| `await session.complete(ref, argument)` | 触发补全 |

---

## 3. 注册回调：处理 Server 主动发来的消息

Server 不只是被动响应，它会**主动**发：

- `notifications/message`（日志）
- `notifications/progress`（进度）
- `notifications/tools/list_changed` / `resources/list_changed` / `prompts/list_changed`
- `notifications/resources/updated`
- 反向请求：`sampling/createMessage`、`elicitation/create`、`roots/list`

ClientSession 构造函数提供 callback 参数：

```python
async with ClientSession(
    read, write,
    # 处理 Server 发的日志
    logging_callback=on_log,
    # 处理 Server 反向 sampling 请求
    sampling_callback=on_sample,
    # 处理 Server elicitation 请求
    elicitation_callback=on_elicit,
    # 处理通用 message（兜底）
    message_handler=on_any_message,
) as session:
    ...
```

### 3.1 日志回调

```python
async def on_log(params):
    """Server 发来的日志通知"""
    level = params.level    # debug / info / warning / error / ...
    logger = params.logger  # Server 名
    data = params.data      # 内容
    print(f"[{level.upper()}/{logger}] {data}")
```

### 3.2 进度回调

进度回调是**每次 call_tool 单独传**的，不是 session 级：

```python
async def on_progress(progress: float, total: float | None, message: str | None):
    pct = int(progress / total * 100) if total else 0
    print(f"进度 {pct}%: {message}")

result = await session.call_tool(
    "long_task",
    {"n": 100},
    progress_callback=on_progress,
)
```

### 3.3 资源更新回调

订阅了 Resource 之后，Server 推 `notifications/resources/updated` 时怎么处理？通过 message_handler：

```python
from mcp.types import ResourceUpdatedNotification

async def on_any_message(message):
    if isinstance(message, ResourceUpdatedNotification):
        uri = message.params.uri
        print(f"资源变了，重新拉: {uri}")
        new = await session.read_resource(uri)
        # 更新自己的缓存
```

> SDK 当前 message_handler 是兜底；专用回调（logging_callback / sampling_callback / elicitation_callback）优先匹配。具体 API 名称随版本演进。

---

## 4. 错误处理

### 4.1 协议错误

```python
from mcp.shared.exceptions import McpError

try:
    result = await session.call_tool("nonexistent", {})
except McpError as e:
    print(f"协议错误: code={e.error.code}, msg={e.error.message}")
```

### 4.2 工具执行错误

```python
result = await session.call_tool("book_flight", {"date_str": "wrong"})

if result.isError:
    # 工具自己说失败了，content 里有错误描述
    error_msg = result.content[0].text
    print(f"工具执行失败: {error_msg}")
else:
    print(f"成功: {result.content[0].text}")
```

### 4.3 连接级错误

子进程崩溃、网络断开等：

```python
try:
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            ...
except Exception as e:
    print(f"连接错误: {e!r}")
```

---

## 5. 实际场景：作为 Agent 框架的"工具来源"

下面这个例子把 MCP Server 暴露的工具自动转给一个简单的对话循环，模拟 Agent 框架做的事：

```python
# demos/client/01_minimal_agent.py
"""把 MCP Server 工具喂给 Claude API，做一个最小 ReAct Agent"""
import asyncio
import json
import os
from pathlib import Path

import anthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_PATH = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"


async def main():
    params = StdioServerParameters(command="python", args=[str(SERVER_PATH)])
    client = anthropic.Anthropic()

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            # 1. 从 MCP 拉所有工具
            mcp_tools = await session.list_tools()
            anth_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in mcp_tools.tools
            ]

            # 2. 用户对话
            messages = [{"role": "user", "content": "现在几点？算一下 7 乘 8"}]

            while True:
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=1024,
                    tools=anth_tools,
                    messages=messages,
                )

                # 把 assistant 消息加到历史
                messages.append({"role": "assistant", "content": resp.content})

                if resp.stop_reason != "tool_use":
                    # 模型给最终回复
                    text = next(b.text for b in resp.content if b.type == "text")
                    print(f"\n[Claude]: {text}")
                    break

                # 3. 执行所有 tool_use 并把结果塞回
                tool_results = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue
                    print(f"[Tool] {block.name}({block.input}) ...")
                    mcp_result = await session.call_tool(block.name, block.input)

                    content_text = "\n".join(
                        c.text for c in mcp_result.content if hasattr(c, "text")
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content_text,
                        "is_error": mcp_result.isError,
                    })

                messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    asyncio.run(main())
```

需要环境变量 `ANTHROPIC_API_KEY`。

这就是 Claude Code / Cursor 内部干的事——拉工具、给模型、调工具、塞回结果，循环。

---

## 6. 监控通知 + 订阅 demo

```python
# demos/client/01_subscription_listener.py
"""演示订阅 Resource 变更 + 监听 list_changed"""
import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    params = StdioServerParameters(command="python", args=["my_server.py"])

    async def on_log(params):
        print(f"[Server Log/{params.level}] {params.data}")

    async def on_message(msg):
        method = getattr(msg, "method", None)
        print(f"[Notification] method={method}")
        if method == "notifications/resources/updated":
            uri = msg.params.uri
            print(f"  → 重新拉 {uri}")

    async with stdio_client(params) as (r, w):
        async with ClientSession(
            r, w,
            logging_callback=on_log,
            message_handler=on_message,
        ) as session:
            await session.initialize()
            await session.subscribe_resource("metrics://qps")
            # 等 60 秒看变更
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 7. 超时与重试

```python
import anyio

# 整体超时
try:
    with anyio.fail_after(10):
        result = await session.call_tool("slow", {})
except TimeoutError:
    print("调用超时")

# 重试只读操作
async def list_with_retry(max_attempts=3):
    for i in range(max_attempts):
        try:
            return await session.list_tools()
        except Exception as e:
            if i == max_attempts - 1:
                raise
            print(f"第 {i+1} 次失败: {e}，重试...")
            await asyncio.sleep(2 ** i)
```

> 注意：写操作不要重试，除非 Server 用 idempotency key。

---

## 8. 资源管理

ClientSession 的 `async with` 保证：

- 退出时 SDK 自动发 `notifications/cancelled` 给还在跑的请求
- 关闭传输层（stdio 关闭 Server 子进程的 stdin → 等退出 → SIGTERM/SIGKILL）
- 释放内部 task group

**不要**手动管理 session 生命周期（不用 async with）——保证不了清理。

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **忘了 `await session.initialize()`** | 调 list_tools 会卡住或直接 error |
| **没注册 sampling_callback 但 Server 调 sampling** | Server 收到 -32601；要么注册，要么 Server 端不要用 sampling |
| **回调里 raise 异常** | 会把整个 session 弄崩，回调里用 try 包住 |
| **call_tool 拿到大对象只看 text** | 用 result.structuredContent 拿 typed 数据 |
| **同时连多个 Server 想串行写代码** | 用 asyncio.gather 并发；多个 Client 不会互相影响 |
| **进程退出后 Server 没清理** | 永远用 async with，不要 .__aenter__() 手动管理 |

---

## 10. 下一步

- 📖 传输细节（stdio / Streamable HTTP）→ [02-transports.md](./02-transports.md)
- 📖 Sampling 反向回调 → [03-sampling.md](./03-sampling.md)
- 📖 Roots + Elicitation → [04-roots-elicitation.md](./04-roots-elicitation.md)
- 📖 多 Server 聚合 → [05-multi-server-best-practices.md](./05-multi-server-best-practices.md)

## 参考资料

- Python SDK Client：https://github.com/modelcontextprotocol/python-sdk
- Build a Client 教程：https://modelcontextprotocol.io/docs/develop/build-client
