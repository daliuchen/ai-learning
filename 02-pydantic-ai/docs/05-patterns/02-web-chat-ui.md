# Pydantic AI 05-02：Web Chat UI 与 UI 事件流

> 把 Agent 接到 Web 界面上，Pydantic AI 提供了几条路：开发时有内置的网页聊天（`agent.to_web()` / `clai web`），生产环境有标准的 UI 事件流适配器（Vercel AI Data Stream / AG-UI），也可以用 `run_stream_events()` 自己手写 SSE。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. 为什么需要标准事件协议

把 LLM 接到 Web 上，看起来只是加一个 SSE 接口，实际上要处理不少事：

- token 流式输出：用户希望边生成边看到字
- 工具调用可见：模型调用 `search_db` 时，前端要显示"正在查询数据库..."
- 结构化输出展示：返回的是 JSON 而不是字符串时，前端怎么渐进地渲染
- 断线重连：网络抖动之后能不能从中间接上
- 多 Agent 串联：从客服移交到技术支持时，前端要显示"已转接"
- 错误处理：模型重试、ValidationError、工具报错怎么告诉前端

每个项目都自己设计一套协议，成本很高。Pydantic AI 的方案是：

```
统一事件流（UI Event Stream）+ UI 协议适配器（Vercel AI、AG-UI）+ 现成的前端组件
```

---

## 2. 四种接法速览

| 方式 | 适合 | 关键 API |
|------|------|----------|
| 内置网页聊天 | 本地开发 / 调试 / 内部演示 | `agent.to_web()`、`clai web -a module:agent` |
| UI 适配器（推荐用于生产） | 前端用 Vercel AI SDK UI 或 AG-UI | `VercelAIAdapter.dispatch_request(request, agent=agent)`、`AGUIAdapter` |
| 手写 SSE | 自定义协议 / 非标准前端 | `agent.run_stream()` / `run_stream_events()` / `iter()` |
| 自己写 `UIAdapter` 子类 | 接别的事件流协议 | `pydantic_ai.ui.UIAdapter` |

### 2.1 内置网页聊天（开发用）

```python
# pip install "pydantic-ai-slim[web]"
from pydantic_ai import Agent

agent = Agent("openai-chat:qwen-flash", instructions="你是助手。")

@agent.tool_plain
def get_weather(city: str) -> str:
    return f"The weather in {city} is sunny"

app = agent.to_web()          # 一个 Starlette ASGI app；也可 agent.to_web(models=[...], instructions="...")
# uvicorn my_module:app --host 127.0.0.1 --port 7932
```

可以配置多个可选模型、原生工具开关和额外的 instructions，需要审批的工具会渲染成批准 / 拒绝按钮。官方明确说它只用于本地开发和调试：聊天端点会执行客户端传回来的审批结果，所以任何能访问它的客户端都能批准 `requires_approval` 的工具。只接受 `application/json` 能挡住跨站表单请求，但绑定 localhost 本身算不上安全边界。

### 2.2 UI 适配器：Vercel AI / AG-UI

```python
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from pydantic_ai import Agent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter      # pip install "pydantic-ai-slim[ui]"

agent = Agent("openai:gpt-6.1-sol")
app = FastAPI()

@app.post("/chat")
async def chat(request: Request) -> Response:
    return await VercelAIAdapter.dispatch_request(request, agent=agent)
```

适配器把前端送来的消息（AI SDK 格式）转成 `agent.run_stream_events()` 的参数，再把 agent 的事件翻译成协议事件，通过 SSE 发回去。一次纯文本回答的事件序列大致是 `start`、`start-step`、`text-start`、若干 `text-delta`，最后是 `finish`；中间有工具调用时会插入对应的工具事件。`dispatch_request` 还接收 `run_stream_events` 的同一批可选参数、`on_complete` / `on_cancel` 回调（用来持久化完整历史，取消时还能存下可续跑的历史）。用的不是 Starlette 系框架（比如 Django、Flask），或者需要更细的控制时，就用 `build_run_input` → `run_stream` → `encode_stream` 手动串起来。默认对齐 AI SDK v5，要用更新版本的特性（比如 v6 起的工具审批事件）设 `sdk_version=6` 或 `7`。AG-UI 同理（`AGUIAdapter`），并支持审批打断（resume payload）。

还有几点需要知道：

- 在工具或 `iter()` 里可以发自定义事件（`CustomEvent`），两种协议都会把它转成各自的 custom-data 事件，适合推送长任务的进度；
- 客户端调 `stop()`，服务端看到的是断连（外部的 `asyncio.CancelledError`），而不是一次正常的取消。想让"停止"之后还能续跑，前端应该调一个单独的取消接口，由服务端执行 `CancellationToken.cancel()`；
- 前端协议的消息格式装不下 Pydantic AI 的全部字段，不要拿它做持久化格式，存 Pydantic AI 自己的格式（见[消息与对话历史（Messages & History）](../01-basics/07-messages-history.md)）。来自浏览器的历史要先过一遍 `sanitize_messages`；
- 通过适配器对外服务时，审批结果是客户端提交的，**不能当作授权边界**（见[Deferred Tools 与人审批](../03-advanced/08-deferred-tools.md)第 15 节）。

---

## 3. 最小 SSE 后端（FastAPI）

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent

app = FastAPI()
agent = Agent("openai-chat:qwen-flash", instructions="你是助手。")

@app.get("/chat")
async def chat(q: str):
    async def event_stream():
        async with agent.run_stream(q) as result:
            async for chunk in result.stream_text(delta=True):
                yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

`agent.run_stream()` 是一个 async context manager，进入后拿到 `StreamedRunResult`。`stream_text(delta=True)` 只输出增量 token，不传 `delta` 时默认输出的是累计到当前的完整文本。SSE 协议要求每条消息以 `data: ...\n\n` 结尾（两个换行），最后发一个 `[DONE]` 让前端知道结束了。

### 3.1 前端订阅（最短示例）

```html
<script>
const es = new EventSource("/chat?q=你好");
es.onmessage = (e) => {
  if (e.data === "[DONE]") { es.close(); return; }
  document.getElementById("out").innerText += e.data;
};
</script>
```

`EventSource` 是浏览器原生支持的，断线后会自动重连（间隔由浏览器决定，通常几秒，服务端可以用 `retry:` 字段指定），这一点比 WebSocket 省事。

---

## 4. 富事件流：把工具调用也吐出去

只流式输出 token 还不够，前端还要看到"正在调用 search_db..."这样的中间事件，这就要用 `agent.iter()`：

```python
import json
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent
from pydantic_ai.messages import (
    PartStartEvent, PartDeltaEvent, TextPart, TextPartDelta, ToolCallPart,
    FunctionToolCallEvent, FunctionToolResultEvent,
)

@app.get("/chat")
async def chat(q: str):
    async def gen():
        async with agent.iter(q) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for ev in stream:
                            yield _serialize(ev)
                elif Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for ev in stream:
                            yield _serialize(ev)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

def _serialize(ev) -> str:
    if isinstance(ev, PartStartEvent) and isinstance(ev.part, ToolCallPart):
        payload = {"type": "tool_call", "name": ev.part.tool_name}
    elif isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
        payload = {"type": "text_delta", "delta": ev.part.content}       # 第一段文字在 start 事件里
    elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
        payload = {"type": "text_delta", "delta": ev.delta.content_delta or ""}
    elif isinstance(ev, FunctionToolResultEvent):
        payload = {"type": "tool_result", "content": str(ev.part.content)}
    else:
        return ""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
```

前端收到的数据是这样的：

```
{"type":"text_delta","delta":"我先查一下"}
{"type":"tool_call","name":"search_db"}
{"type":"tool_result","content":"3 条订单"}
{"type":"text_delta","delta":"您的订单..."}
```

前端可以据此分别渲染文本气泡、工具调用标签和最终结论。

---

## 5. UI Event Stream 协议字段速查

下面是常见事件（具体字段以 `pydantic_ai.messages` 为准）：

| 事件类型 | 含义 | 关键字段 |
|----------|------|----------|
| `PartStartEvent(TextPart)` | 开始生成新一段文本 | `index` |
| `PartDeltaEvent(TextPartDelta)` | 文本增量 | `delta.content_delta` |
| `PartStartEvent(ToolCallPart)` | 开始调用工具 | `tool_name`, `args` |
| `PartDeltaEvent(ToolCallPartDelta)` | 工具参数流式拼装 | `args_delta` |
| `FunctionToolCallEvent` | 工具调用即将执行 | `part` |
| `FunctionToolResultEvent` | 工具执行结果 | `part.content` |
| `FinalResultEvent` | 已经确定哪一部分是最终输出（之后的增量就是最终结果） | `tool_name`, `tool_call_id`（文本输出时为 `None`） |

前端按 `type` 分别渲染这些事件就行。

---

## 6. 关于 A2A

以前有 `agent.to_a2a()` 和 `pydantic_ai.a2a.A2AClient`，在 2.64.0 里这两个 API 都不存在了，官方文档目录里也没有 A2A 页面（原先通过 FastA2A 提供，后来不在主包里了）。如果确实需要 A2A，以官方当前文档为准，或者用独立的 A2A 实现。在 Pydantic AI 里，Agent 之间互通更常见的做法是 MCP（见[MCP（Model Context Protocol）集成](../04-modules/01-mcp.md)）和 UI 事件流。

---

## 7. 内置 chat UI 模板与 `clai web`

除了 `agent.to_web()`，命令行也能直接开：`clai web -a my_module:agent --host 127.0.0.1 --port 7932`（可加 `-m` 多模型、`-t web_search` 开原生工具、`-i` 追加指令、`--html-source` 换自己的前端）。它适合做示例和内部工具，不要直接当生产前端用。生产环境前端接进自己的 React/Vue 系统，后端用 §2.2 的 UI 适配器。

---

## 8. 30 行实战：一个能跑的 chat 后端

```python
# server.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent

agent = Agent("openai-chat:qwen-flash", instructions="你是中文助手。")

app = FastAPI()

@app.get("/chat")
async def chat(q: str):
    async def gen():
        async with agent.run_stream(q) as r:
            async for delta in r.stream_text(delta=True):
                yield f"data: {delta}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

启动：

```bash
uvicorn server:app --reload
curl -N "http://localhost:8000/chat?q=hello"
```

完整版（含工具调用事件 + 前端 HTML）见 [`demos/patterns/02_web_chat_ui.py`](../../demos/patterns/02_web_chat_ui.py)。

---

## 9. 与 Vercel AI SDK / LangGraph Platform 对比

LangChain 早期的 LangServe 已经不再加新功能，官方推荐改用 LangGraph Platform，所以下表第三列对比的是后者。

| 维度 | Pydantic AI | Vercel AI SDK | LangGraph Platform |
|------|-------------|---------------|-----------|
| 语言 | Python | TypeScript | Python |
| 流式协议 | 原生支持 Vercel AI Data Stream 与 AG-UI，也可手写 SSE | 自家 AI SDK stream protocol | LangGraph Platform / `useStream` |
| 工具调用事件 | 适配器自动翻译；或 `run_stream_events()` / `iter()` | tool events | `stream` / `astream_events` |
| 内置 UI 组件 | `agent.to_web()`（开发用） | React `useChat` 组件 | LangGraph Studio |
| 适合的前端栈 | 任意；Next.js + AI SDK UI 最顺手 | Next.js 生态 | 任意 |
| 多 Agent 协作 | 多 Agent 模式 + MCP | 需要自己设计 | LangGraph |

怎么选：后端 Python、前端 Next.js 的话，Pydantic AI + `VercelAIAdapter` + AI SDK UI 的 `useChat` 是最省事的组合；前端用的是别的协议（AG-UI / CopilotKit），就用 `AGUIAdapter`；全栈 TypeScript 直接用 Vercel AI SDK；已经深度使用 LangChain 生态的，用 LangGraph Platform。

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| Nginx 反向代理下 SSE 断流 | 默认 `proxy_buffering on` 缓冲了响应 | `proxy_buffering off; proxy_read_timeout 1d;` |
| 浏览器跨域 SSE 失败 | 没配 CORS | FastAPI 加 `CORSMiddleware`，允许 `text/event-stream` |
| EventSource 不能发 POST | 浏览器 EventSource 只支持 GET | 用 `fetch` + ReadableStream，或改用 WebSocket |
| Cloudflare / CDN 缓冲整个响应 | 默认开启响应缓冲 | 设置 `Cache-Control: no-cache, no-transform`，关 buffering |
| `stream_text()` 输出累计串而非增量 | 没加 `delta=True` | `stream_text(delta=True)` |
| 多用户同时在线，输出串到别人那里 | 全局共享了 Agent state | 每次请求开新的 `run`，不复用 message_history 时要小心 |
| 重连后丢失上下文 | EventSource 不会带回历史 | 服务端按 session_id 持久化 message_history，重连时回放 |
| 工具调用标签闪一下就消失 | 前端只渲染了 `text_delta`，没处理 `tool_call` | 按事件 `type` 分发到独立 UI 区域 |
| 手写 SSE 时 `AttributeError: ... has no attribute 'result'`（`FunctionToolResultEvent`） | V2 里结果在 `ev.part`（`ToolReturnPart`） | `str(ev.part.content)` |
| 前端「停止」后服务端状态不一致 | `stop()` 在服务端是断连，不是取消 | 另开取消接口 + `CancellationToken`；用 `on_cancel` 存历史 |

---

## 11. 生产环境建议

1. 反向代理一定要关掉 buffering。Nginx、Caddy、Cloudflare 都要单独配置，否则流式输出会被攒成一整块才发出来
2. 尽量用 HTTP/2。SSE 在 HTTP/1.1 下每个连接占一个 TCP 连接，HTTP/2 可以多路复用
3. 超时设长一些。`proxy_read_timeout` 和 ALB idle timeout 至少调到 5 分钟，默认的 60 秒会切断长输出
4. 保持心跳。每 15 秒发一行 `: keepalive\n\n` 注释，避免代理因为没有流量主动断开
5. 记结构化日志。每条 SSE 事件都打 Logfire span，事后复盘时能定位卡在哪一步
6. 限流。按用户或 session 限制并发流，否则恶意用户很快就能把 token 额度用光
7. 定义错误事件。比如 `{"type":"error", ...}`，前端据此展示错误（红色标签加重试按钮）

---

## 12. 本章 demo

完整可运行代码：[`demos/patterns/02_web_chat_ui.py`](../../demos/patterns/02_web_chat_ui.py)

启动：

```bash
uvicorn demos.patterns.02_web_chat_ui:app --reload
# 浏览器打开 http://localhost:8000/
```

下一篇：[用 TestModel、FunctionModel 与 override 测试 Agent](03-testing.md)，讲怎样不花 API 费用给 Agent 写单元测试。
