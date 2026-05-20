# Pydantic AI 05-02：Web Chat UI、SSE 与 A2A

> **一句话**：Pydantic AI 把"Agent → Web UI"的最后一公里也覆盖了 —— 标准 SSE 事件流（UI Event Stream）+ A2A（Agent-to-Agent）协议 + 内置 chat UI 模板，让你 30 行 FastAPI 代码就能开一个支持流式 + 工具调用展示的聊天页。

---

## 1. 为什么需要标准事件协议

把 LLM 接到 Web 上，看似只是"加一个 SSE 接口"，实际上你要解决一堆事：

- **token 流式**：用户希望边生成边看到字
- **工具调用可见**：模型调用 `search_db` 时，前端要显示"正在查询数据库..."
- **结构化输出展示**：返回的不是字符串而是 JSON，前端怎么渐进式渲染？
- **断线重连**：网络抖动后能否从中间续上？
- **多 Agent 串联**：客服 → 技术支持移交时，前端要展示"已转接"
- **错误处理**：模型重试、ValidationError、tool 报错怎么暴露给前端？

每个项目自己造这套协议代价非常高。Pydantic AI 给的方案是：

```
统一事件流（UI Event Stream）+ 标准 A2A 协议 + 现成的前端组件
```

---

## 2. 三层组件速览

| 层 | 作用 | 关键 API |
|----|------|----------|
| **Agent.iter() / Agent.run_stream()** | 异步迭代 Agent 的中间事件 | `async with agent.iter(...) as run:` |
| **UI Event Stream（SSE）** | 把事件以 SSE 协议吐给前端 | `EventSource` / `fetch` ReadableStream |
| **A2A 协议** | Agent 之间或 Agent ↔ 客户端的标准通信协议 | `agent.to_a2a()` |

实际项目里这三层经常组合：FastAPI 路由用 `Agent.iter()` 拿事件 → 序列化成 SSE → 前端 JS 订阅渲染。

---

## 3. 最小 SSE 后端（FastAPI）

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent

app = FastAPI()
agent = Agent("openai:gpt-4o-mini", system_prompt="你是助手。")

@app.get("/chat")
async def chat(q: str):
    async def event_stream():
        async with agent.run_stream(q) as result:
            async for chunk in result.stream_text(delta=True):
                yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**关键点**：

1. `agent.run_stream()` 是 async context manager，进入后拿到 `StreamedRunResult`
2. `stream_text(delta=True)` 只产出**增量** token；不传 `delta` 默认产出"累计串"
3. SSE 协议要求 `data: ...\n\n` 两个换行
4. 收尾发一个 `[DONE]` 让前端知道结束

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

`EventSource` 浏览器原生支持，**自动断线重连**（默认重试 3 秒）。这点比 WebSocket 省事很多。

---

## 4. 富事件流：把工具调用也吐出去

只流 token 是不够的，你还要让前端看到"正在调用 search_db..." 这种中间事件。用 `agent.iter()`：

```python
import json
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent
from pydantic_ai.messages import (
    PartStartEvent, PartDeltaEvent, TextPart, ToolCallPart,
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
    elif isinstance(ev, PartDeltaEvent):
        payload = {"type": "text_delta", "delta": ev.delta.content_delta or ""}
    elif isinstance(ev, FunctionToolResultEvent):
        payload = {"type": "tool_result", "content": str(ev.result.content)}
    else:
        return ""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
```

这样前端就能拿到：

```
{"type":"text_delta","delta":"我先查一下"}
{"type":"tool_call","name":"search_db"}
{"type":"tool_result","content":"3 条订单"}
{"type":"text_delta","delta":"您的订单..."}
```

前端 UI 就可以分别渲染：文本气泡、工具调用 chip、最终结论。

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
| `FunctionToolResultEvent` | 工具执行结果 | `result.content` |
| `FinalResultEvent` | 最终结构化输出可用 | `output` |

前端只要把这些事件按 `type` 分流渲染就行。

---

## 6. A2A 协议（Agent-to-Agent）

A2A 是一个开放协议（Google 主导，OpenAI、Anthropic、Pydantic 都参与），目标是**让 Agent 之间能像 HTTP 一样互通**：

```python
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="...")

# 一行把 Agent 暴露成 A2A server
app = agent.to_a2a()  # 返回一个 Starlette app
```

然后用 ASGI 服务器（uvicorn / hypercorn）跑起来：

```bash
uvicorn myapp:app --host 0.0.0.0 --port 8000
```

外部客户端（不管是浏览器、另一个 Agent 还是别的语言）按 A2A 协议发请求即可，**Pydantic AI 替你处理鉴权、流式、结构化输出、错误码**。

### 6.1 用 Pydantic AI 客户端连别人的 A2A 服务

```python
from pydantic_ai.a2a import A2AClient

client = A2AClient(base_url="https://partner.example.com/a2a")
result = await client.run("帮我查询订单 1234")
print(result.output)
```

适合"内部多团队各自维护 Agent，互相调用"的场景，比自己造一套 RPC 强多了。

---

## 7. 内置 web chat UI 模板

Pydantic AI 给了一个开箱即用的最小 chat UI（HTML + JS）作为参考实现。你可以直接挂一个静态文件路由：

```python
from pathlib import Path
from fastapi.responses import HTMLResponse

@app.get("/")
async def index():
    html = Path(__file__).parent.joinpath("chat.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
```

`chat.html` 里用 `EventSource` 订阅 `/chat?q=...`，配合一点点 CSS 就是个能用的 demo。**不要把这套 UI 直接当生产前端**，它的定位是"教学示例 + 内部工具",生产环境建议接到自家 React/Vue 系统里。

---

## 8. 30 行实战：一个能跑的 chat 后端

```python
# server.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="你是中文助手。")

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

## 9. 与 Vercel AI SDK / LangServe 对比

| 维度 | Pydantic AI | Vercel AI SDK | LangServe |
|------|-------------|---------------|-----------|
| 语言 | Python | TypeScript | Python |
| 流式协议 | 标准 SSE + A2A | 自定义 AI Stream | LangChain RemoteRunnable |
| 工具调用事件 | 原生 `iter()` 拿到 | `streamObject` / tool events | `astream_events` |
| 内置 UI 组件 | HTML 模板 | React `useChat` 组件 | 自带 Playground |
| 适合的前端栈 | 任意（SSE 标准） | Next.js 生态 | 任意 |
| 多 Agent 协作 | A2A 协议 + 多 Agent 模式 | 需要自己设计 | 需要自己设计（或 LangGraph） |

**经验**：

- 后端是 Python + 前端是 React/Vue → Pydantic AI + 任意 React 流式渲染库（如 `@microsoft/fetch-event-source`）
- 全栈 Next.js → Vercel AI SDK 体验最顺
- 已经深用 LangChain 生态 → LangServe

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| Nginx 反向代理下 SSE 断流 | 默认 `proxy_buffering on` 缓冲了响应 | `proxy_buffering off; proxy_read_timeout 1d;` |
| 浏览器跨域 SSE 失败 | 没配 CORS | FastAPI 加 `CORSMiddleware`，允许 `text/event-stream` |
| EventSource 不能发 POST | 浏览器 EventSource 只支持 GET | 用 `fetch` + ReadableStream，或改用 WebSocket |
| Cloudflare / CDN 缓冲整个响应 | 默认开启响应缓冲 | 设置 `Cache-Control: no-cache, no-transform`，关 buffering |
| `stream_text()` 输出整段而非增量 | 没加 `delta=True` | `stream_text(delta=True)` |
| 多用户同时在线，token 串了 | 全局共享了 Agent state | 每次请求开新的 `run`，不复用 message_history 时要小心 |
| 重连后丢失上下文 | EventSource 不会带回历史 | 服务端按 session_id 持久化 message_history，重连时回放 |
| 工具调用 chip 闪一下就消失 | 前端只渲染了 `text_delta`，没处理 `tool_call` | 按事件 `type` 分发到独立 UI 区域 |
| A2A 客户端拿不到流式 | 没用 `run_stream` 而用了 `run` | 服务端用流式接口暴露，客户端用对应方法 |

---

## 11. 生产环境建议

1. **反向代理一定关 buffering**：Nginx / Caddy / Cloudflare 都要单独配，否则 SSE 体验为 0
2. **HTTP/2 强烈推荐**：SSE 在 HTTP/1.1 下每连接占一个 TCP，HTTP/2 可以多路复用
3. **超时设长**：`proxy_read_timeout` / ALB idle timeout 至少调到 5 分钟（默认 60s 会断长输出）
4. **保持心跳**：每 15 秒发一个 `: keepalive\n\n` 注释行，避免代理因为没流量主动断连
5. **结构化日志**：每条 SSE 事件都打 Logfire span，事故复盘的时候能精确定位卡在哪一步
6. **限流**：每个用户 / 每个 session 限制并发流，否则恶意用户能瞬间把 token 烧光
7. **错误事件**：定义一个 `{"type":"error", ...}` 事件类型，让前端能 UI 化错误（红色 chip + 重试按钮）

---

## 12. 本章 demo

完整可运行代码：[`demos/patterns/02_web_chat_ui.py`](../../demos/patterns/02_web_chat_ui.py)

跑通后：

```bash
uvicorn demos.patterns.02_web_chat_ui:app --reload
# 浏览器打开 http://localhost:8000/
```

下一篇：[03-testing.md](03-testing.md) — 怎么不烧钱地给 Agent 写单测。
