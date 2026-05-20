# FastAPI / Lambda 部署

> **一句话**：把 Agent 包到 FastAPI / AWS Lambda 里，用 async handler + Sessions + 流式响应，做成生产级 chat API。

---

## 1. FastAPI 最简

```python
# app.py
from fastapi import FastAPI
from pydantic import BaseModel
from agents import Agent, Runner, SQLiteSession


app = FastAPI()


agent = Agent(name="A", instructions="...", model="gpt-4o-mini")


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

前端用 `EventSource` 接收：

```javascript
const es = new EventSource("/chat/stream", { method: "POST", ... });
es.onmessage = (e) => {
  if (e.data === "[DONE]") es.close();
  else appendToken(e.data);
};
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

Agent 里的 tools 通过 `ctx.context.db` 拿连接池。

---

## 5. 错误处理中间件

```python
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    AgentsException,
)
from fastapi import HTTPException


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


@app.exception_handler(AgentsException)
async def agent_error_handler(req, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "internal", "detail": str(exc)},
    )
```

---

## 6. Background sessions cleanup

定期清理 SQLite session：

```python
import asyncio


async def cleanup_sessions():
    while True:
        # 删除 7 天没活动的 session
        # ...
        await asyncio.sleep(3600 * 24)


@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_sessions())
```

---

## 7. Lambda 部署

```python
# lambda_handler.py
import asyncio
import json
from agents import Agent, Runner


agent = Agent(name="A", instructions="...", model="gpt-4o-mini")


def handler(event, context):
    body = json.loads(event["body"])
    result = asyncio.run(Runner.run(agent, body["message"]))

    return {
        "statusCode": 200,
        "body": json.dumps({"reply": result.final_output}),
        "headers": {"Content-Type": "application/json"},
    }
```

打包：

```bash
pip install -t package/ openai-agents
cp lambda_handler.py package/
cd package && zip -r ../function.zip . && cd ..
aws lambda update-function-code --function-name agent-chat --zip-file fileb://function.zip
```

⚠️ Lambda 限制：

- 15 分钟 timeout（够用）
- 6 MB payload（streaming 需要 Function URL with streaming）
- 冷启动 ~1-2s（备 provisioned concurrency）

---

## 8. Streaming + Lambda

Lambda Function URL 支持 streaming：

```python
# 用 awslambdaric / aws-lambda-streamfy
import asyncio


def handler(event, context, response_stream):
    async def run():
        from agents import Agent, Runner
        agent = Agent(...)
        result = Runner.run_streamed(agent, event["body"])
        async for ev in result.stream_events():
            if ev.type == "raw_response_event":
                response_stream.write(ev.data.delta.encode())
        response_stream.end()

    asyncio.run(run())
```

详见 AWS Lambda streaming response docs。

---

## 9. Docker 部署

```dockerfile
# Dockerfile
FROM python:3.11-slim

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
# demos/integration/03_fastapi.py
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
    model="gpt-4o-mini",
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
            "tokens": result.usage.total_tokens,
        }
    except AgentsException as e:
        raise HTTPException(500, detail=str(e))


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

- 📖 跟 Pydantic AI / LangChain 互操作 → [04-vs-others.md](./04-vs-others.md)
- 📖 生产 cost / latency 优化 → [07-production/02-cost-latency.md](../07-production/02-cost-latency.md)
- 📖 实战完整服务 → [08-practice/01-customer-triage.md](../08-practice/01-customer-triage.md)
