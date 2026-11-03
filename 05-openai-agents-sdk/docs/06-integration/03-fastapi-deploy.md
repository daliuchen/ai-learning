# FastAPI / Lambda 部署

> 这篇把 Agent 包进 FastAPI 和 AWS Lambda，用 async handler、Sessions 和流式响应搭一个能上线的 chat API，顺带讲错误映射、鉴权和 Lambda 上 trace 丢失的问题。

---

## 1. FastAPI 最简

```python
# app.py
from fastapi import FastAPI
from pydantic import BaseModel
from agents import Agent, Runner, SQLiteSession


app = FastAPI()


agent = Agent(name="A", instructions="...", model="gpt-6-luna")


class ChatReq(BaseModel):
    message: str
    user_id: str


@app.post("/chat")
async def chat(req: ChatReq):
    session = SQLiteSession(req.user_id, "sessions.db")
    result = await Runner.run(agent, req.message, session=session)
    return {"reply": result.final_output}
```

跑：

```bash
uvicorn app:app --reload
curl -X POST localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "你好", "user_id": "u1"}'
```

---

## 2. 流式响应（SSE）

```python
from fastapi.responses import StreamingResponse
from openai.types.responses import ResponseTextDeltaEvent


@app.post("/chat/stream")
async def chat_stream(req: ChatReq):
    session = SQLiteSession(req.user_id, "sessions.db")
    result = Runner.run_streamed(agent, req.message, session=session)

    async def gen():
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                yield f"data: {event.data.delta}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

> SSE 的一条 `data:` 只能占一行。模型吐出的 delta 一旦带换行，上面直接 `f"data: {delta}"` 的写法就会把消息切坏。生产里先把 delta 序列化再发：`yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"`，前端再 `JSON.parse`。
>
> 官方对流式还有三条要求：一定要把 `stream_events()` 迭代到底，`final_output`、session 落库、`interruptions` 都要等它结束才齐；流式过程中的失败是在 `stream_events()` 里抛出来的；客户端断开时调用 `result.cancel()`，然后继续把事件消费完。用千问这类 Chat Completions 端点时，要设 `ModelSettings(include_usage=True)` 才能拿到 token 用量。

前端要注意，浏览器原生的 `EventSource` 只支持 GET。POST 接口得用 `fetch` + `ReadableStream` 自己解析 SSE（或者把接口改成 GET，或用 `@microsoft/fetch-event-source`）：

```javascript
const res = await fetch("/chat/stream", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message, user_id}) });
const reader = res.body.getReader(); const dec = new TextDecoder();
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  for (const line of dec.decode(value).split("\n\n")) {
    if (line.startsWith("data: ")) { const d = line.slice(6); if (d !== "[DONE]") appendToken(d); }
  }
}
```

---

## 3. WebSocket（双向）

```python
from fastapi import WebSocket


@app.websocket("/ws")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    user_id = ws.headers.get("X-User-Id", "anon")
    session = SQLiteSession(user_id)

    while True:
        msg = await ws.receive_text()
        result = Runner.run_streamed(agent, msg, session=session)

        async for event in result.stream_events():
            if event.type == "raw_response_event":
                from openai.types.responses import ResponseTextDeltaEvent
                if isinstance(event.data, ResponseTextDeltaEvent):
                    await ws.send_text(event.data.delta)
            elif event.type == "agent_updated_stream_event":
                await ws.send_json({"event": "switched_to", "agent": event.new_agent.name})

        await ws.send_text("\n[DONE]")
```

---

## 4. Context 注入（用户身份 / DB 连接）

```python
from dataclasses import dataclass
from fastapi import Depends


@dataclass
class AppCtx:
    user_id: str
    db: object


def get_ctx(user_id: str = "anon") -> AppCtx:
    return AppCtx(user_id=user_id, db=my_db_pool)


@app.post("/chat")
async def chat(req: ChatReq, ctx: AppCtx = Depends(get_ctx)):
    session = SQLiteSession(ctx.user_id)
    result = await Runner.run(agent, req.message, session=session, context=ctx)
    return {"reply": result.final_output}
```

Agent 里的工具通过 `ctx.context.db` 拿到连接池。

---

## 5. 错误处理中间件

```python
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelTimeoutError,
    AgentsException,
)
from fastapi.responses import JSONResponse


@app.exception_handler(InputGuardrailTripwireTriggered)
async def input_guardrail_handler(req, exc):
    info = exc.guardrail_result.output.output_info
    return JSONResponse(
        status_code=400,
        content={"error": "input_blocked", "info": info},
    )


@app.exception_handler(MaxTurnsExceeded)
async def max_turns_handler(req, exc):
    return JSONResponse(
        status_code=408,
        content={"error": "timeout"},
    )


@app.exception_handler(ModelTimeoutError)
async def model_timeout_handler(req, exc):
    return JSONResponse(status_code=504, content={"error": "model_timeout"})


@app.exception_handler(AgentsException)
async def agent_error_handler(req, exc):
    # 别把 str(exc) 原样回给用户：可能含工具参数 / 内部信息；详情写日志
    return JSONResponse(status_code=500, content={"error": "internal"})
```

> Starlette 的 `exception_handler` 按异常类的继承关系匹配最具体的那个。上面几个都是 `AgentsException` 的子类，它们各自的 handler 会优先于兜底的 `AgentsException`。

---

## 6. Background sessions cleanup

定期清理 SQLite session。注意 `@app.on_event("startup")` 已经被 FastAPI 弃用，改用 `lifespan`：

```python
import asyncio
from contextlib import asynccontextmanager


async def cleanup_sessions():
    while True:
        # 删除 7 天没活动的 session
        # ...
        await asyncio.sleep(3600 * 24)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(cleanup_sessions())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
```

多副本部署时别用本地 SQLite 文件，换成 `RedisSession` 或 `SQLAlchemySession`（见 [Sessions：会话状态自动管理](../01-basics/06-sessions.md)），多个实例共享同一份存储；`RedisSession` 还可以设 `ttl` 让过期会话自动清掉。

---

## 7. Lambda 部署

```python
# lambda_handler.py
import asyncio
import json
from agents import Agent, Runner, flush_traces


agent = Agent(name="A", instructions="...", model="gpt-6-luna")


def handler(event, context):
    body = json.loads(event["body"])
    try:
        result = asyncio.run(Runner.run(agent, body["message"]))
    finally:
        flush_traces()          # Lambda 冻结后后台线程不再跑，不 flush 的话 trace 可能丢

    return {
        "statusCode": 200,
        "body": json.dumps({"reply": result.final_output}),
        "headers": {"Content-Type": "application/json"},
    }
```

打包：

```bash
pip install -t package/ openai-agents      # 注意 Lambda 运行时的 Python 版本要 >= 3.10
cp lambda_handler.py package/
cd package && zip -r ../function.zip . && cd ..
aws lambda update-function-code --function-name agent-chat --zip-file fileb://function.zip
```

Lambda 的几个限制：

- 15 分钟 timeout（够用）
- 6 MB payload（streaming 需要 Function URL with streaming）
- 冷启动有额外延迟，依赖越多越明显（在意的话配 provisioned concurrency）
- 响应返回后后台线程会被冻结，而 tracing 默认是后台批量导出的，所以 handler 结束前要调 `flush_traces()`（官方说 Celery 等后台任务同理，在 `trace()` 退出之后调）

---

## 8. Streaming + Lambda

Lambda Function URL 支持响应流式（invoke mode 设为 `RESPONSE_STREAM`），但 AWS 托管运行时里原生支持流式 handler 的是 Node.js。Python 的常见做法是用 Lambda Web Adapter 把上面第 2 节的 FastAPI 流式接口原样跑在 Lambda 上，或者自己写自定义运行时。下面只是结构示意，`response_stream` 代表运行时提供的输出流：

```python
import asyncio
from openai.types.responses import ResponseTextDeltaEvent


def handler(event, context, response_stream):     # 示意：签名取决于你用的运行时 / 适配层
    async def run():
        from agents import Agent, Runner
        agent = Agent(...)
        result = Runner.run_streamed(agent, event["body"])
        async for ev in result.stream_events():
            if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                response_stream.write(ev.data.delta.encode())
        response_stream.end()

    asyncio.run(run())
```

细节见 AWS Lambda response streaming 和 Lambda Web Adapter 的文档。

---

## 9. Docker 部署

```dockerfile
# Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .
COPY sessions.db .

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t my-agent .
docker run -e OPENAI_API_KEY=sk-... -p 8000:8000 my-agent
```

---

## 10. 速率限制 + 鉴权

```python
from fastapi import Header, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address


limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.post("/chat")
@limiter.limit("10/minute")
async def chat(req: ChatReq, x_api_key: str = Header(None)):
    if x_api_key != "secret":
        raise HTTPException(401)
    # ...
```

---

## 11. 完整 demo

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agents import Agent, Runner, SQLiteSession, function_tool
from agents.exceptions import InputGuardrailTripwireTriggered, AgentsException


@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


agent = Agent(
    name="ChatBot",
    instructions="友好聊天，能查天气",
    tools=[get_weather],
    model="gpt-6-luna",
)


app = FastAPI(title="Agent Chat API")


class ChatReq(BaseModel):
    message: str
    user_id: str = "anon"


@app.post("/chat")
async def chat(req: ChatReq):
    session = SQLiteSession(req.user_id, "sessions.db")
    try:
        result = await Runner.run(agent, req.message, session=session)
        return {
            "reply": result.final_output,
            "agent": result.last_agent.name,
            "tokens": result.context_wrapper.usage.total_tokens,
        }
    except AgentsException:
        raise HTTPException(500, detail="internal")      # 详情写日志，别把 str(e) 回给用户


@app.post("/chat/stream")
async def chat_stream(req: ChatReq):
    session = SQLiteSession(req.user_id, "sessions.db")
    result = Runner.run_streamed(agent, req.message, session=session)

    async def gen():
        from openai.types.responses import ResponseTextDeltaEvent
        async for ev in result.stream_events():
            if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
                yield f"data: {ev.data.delta}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

## 12. 下一步

- 和 Pydantic AI / LangChain 互操作 → [与 Pydantic AI / LangChain 互操作](./04-vs-others.md)
- 生产环境的成本与延迟优化 → [Cost / Latency 优化](../07-production/02-cost-latency.md)
- 一个完整的服务实战 → [实战 1：客服 Triage Agent](../08-practice/01-customer-triage.md)
