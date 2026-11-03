# 部署形态选型

> Agent 服务的部署方式，取决于三个问题：要不要长连接，会不会跑后台长任务，高峰流量有多大。按这三点，常见的形态是脚本、FastAPI、WebSocket、Lambda 和长任务 worker。这篇逐个过一遍，再讲 Session 放哪、健康检查和日志。

---

## 1. 5 种常见部署形态

| 形态 | 适合 | 例子 |
|------|------|------|
| CLI 脚本 | 内部工具 / 批处理 | 数据清洗、定时任务 |
| FastAPI | 同步 / 流式 chat | C 端 chatbot、客服 |
| WebSocket Server | 双向语音 / 实时 | Voice agent、实时游戏 |
| Lambda / Cloud Function | 间断流量、低 QPS | 慢节奏问答 |
| Worker（Celery / SQS） | 长任务、batch | Research agent 跑 5 分钟、批量分类 |

---

## 2. CLI 脚本

```python
# tools/classify.py
import sys
from agents import Agent, Runner


agent = Agent(name="Classifier", instructions="...")


for line in sys.stdin:
    result = Runner.run_sync(agent, line.strip())
    print(result.final_output)
```

跑：

```bash
cat inputs.txt | python tools/classify.py > outputs.txt
```

一次性任务、计划任务、ETL 这类不面向用户的场景，用脚本就够了。

---

## 3. FastAPI（最常见）

完整写法见 [FastAPI / Lambda 部署](../06-integration/03-fastapi-deploy.md)。

部署：

```bash
# 1. Docker
docker build -t my-agent .
docker run -p 8000:8000 -e OPENAI_API_KEY=... my-agent

# 2. uvicorn + supervisor / systemd
uvicorn app:app --workers 4 --host 0.0.0.0 --port 8000
```

并发方面，`--workers 4` 是 4 个进程并行，每个进程内部靠 async 同时处理多个请求。进程之间不共享内存，所以 Session 要放 SQLite 或 Redis。

---

## 4. WebSocket Server

```python
# 同 FastAPI，但用 WebSocket endpoint
@app.websocket("/voice")
async def voice(ws: WebSocket):
    ...
```

部署方式一样：

```bash
uvicorn app:app --workers 4
```

WebSocket 是长连接，要多做几件事：监控连接数并设上限，处理心跳和断线重连；放在 Nginx 或 ALB 后面时要配 sticky session。

---

## 5. Lambda

流量低（< 100 QPS）、不需要长连接、按调用计费比常驻便宜的时候，可以考虑 Lambda。

```python
# lambda_handler.py
import asyncio
import json
from agents import Agent, Runner


agent = Agent(name="A", instructions="...")


def handler(event, context):
    body = json.loads(event["body"])
    result = asyncio.run(Runner.run(agent, body["message"]))
    return {
        "statusCode": 200,
        "body": json.dumps({"reply": result.final_output}),
    }
```

冷启动可以这样缓解：

- Provisioned concurrency（贵但即时）
- Layer 装常用依赖
- 在 handler 外创建 OpenAI client，复用长连接

---

## 6. Worker（异步长任务）

研究类 Agent 一跑就是 5 分钟，不能让用户挂在 HTTP 请求上等，要丢给 worker。

```python
# tasks.py - Celery
from celery import Celery
from agents import Agent, Runner
import asyncio
from agents import trace, flush_traces


celery = Celery("tasks", broker="redis://...")


research_agent = Agent(name="Researcher", instructions="...", model="gpt-6.1-sol")


@celery.task
def research_task(query: str, user_id: str):
    try:
        with trace("research_task"):
            result = asyncio.run(Runner.run(research_agent, query, max_turns=30))
    finally:
        flush_traces()         # 在 trace() 退出之后调：确保 worker 里的 trace 及时送达（默认是后台批量导出）

    # 回写 DB / 通知用户
    db.save_result(user_id, result.final_output)
    notify_user(user_id, "研究完成")
```

```python
# API endpoint：触发任务
@app.post("/research")
async def start_research(req: ChatReq):
    task_id = research_task.delay(req.message, req.user_id).id
    return {"task_id": task_id, "status": "started"}


@app.get("/research/{task_id}")
async def check_research(task_id: str):
    result = research_task.AsyncResult(task_id)
    return {"status": result.status, "result": result.result}
```

---

## 7. 长任务 / 要等人 / 要能续跑：RunState 与耐久执行

如果 Agent 要跑几分钟到几小时，中途要等人审批，进程还可能重启，光靠 Celery 就不够了。SDK 在这方面提供了两层能力。

第一层是 `RunState` 序列化，用于人在回路。run 被 `needs_approval` 暂停后，用 `state.to_string()` 存进数据库；审批人同意后（可能在另一个进程、几小时之后），`await RunState.from_string(agent, saved)` 恢复状态，`approve` 之后 `Runner.run(agent, state, session=session)` 接着跑。0.20 起还能在恢复前用 `state.add_input(...)` 追加用户输入。这套流程在 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](../02-tools/04-tool-choice.md) 第 10 节。

第二层是耐久执行。官方文档列了 Temporal、DBOS、Restate、Dapr 的集成，适合 run 要跨越长时间等待、需要自动重试、要扛住进程重启的场景。怎么选：DBOS 只需要 SQLite 或 Postgres；Restate 是一个单二进制运行时；Temporal 最通用，也最重；已经在用 Dapr sidecar 的团队选 Dapr。这几个集成的细节以各自文档为准。

对外的 API 一般长这样：`POST /tasks` 创建任务 → worker 跑到暂停点，存下 `RunState` → `GET /tasks/{id}` 返回待审批项 → `POST /tasks/{id}/approve` → worker 恢复执行。

---

## 8. 容器化最佳实践

```dockerfile
FROM python:3.12-slim      # openai-agents 0.24 要求 Python >= 3.10

# 依赖单独成层放前面：只改代码时不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码层（改动频繁，放最后）
COPY app/ /app/
WORKDIR /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

别把 `.env` 打进镜像，密钥用 K8s secret 或 SSM。`--workers` 的数量，CPU 密集型取 CPU 数 + 1，IO 密集型取 CPU 数的 2 倍。

---

## 9. K8s 关键配置

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-api
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: api
        image: my-agent:latest
        env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: openai-secret
              key: api_key
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: 2000m
            memory: 2Gi
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          periodSeconds: 30
```

要点是配好 `livenessProbe` 健康检查，设资源 limit 防止 OOM，多副本配合 HPA 做弹性伸缩。

---

## 10. Sessions 存哪

| 部署形态 | Sessions |
|----------|----------|
| 单进程脚本 | `SQLiteSession`（文件） |
| FastAPI 单实例 | `SQLiteSession`（文件）或 `AsyncSQLiteSession` |
| FastAPI 多副本 | 内置的 `RedisSession` / `SQLAlchemySession`（Postgres）/ `MongoDBSession`（不用再手写） |
| Lambda | `SQLAlchemySession` 连 RDS / 自定义 DynamoDB |
| Worker | `RedisSession` / `SQLAlchemySession` |
| 要加密 / 过期 | 外面包一层 `EncryptedSession`（TTL） |

---

## 11. 健康检查 / Readiness

```python
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    # 检查 OpenAI 能不能访问
    try:
        client = AsyncOpenAI()
        await client.models.list()
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(503, detail=str(e))
```

---

## 12. 日志 / Metrics 标配

```python
import logging
import logfire


logging.basicConfig(level=logging.INFO)
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_fastapi(app)
logfire.instrument_openai_agents()


from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)
```

`/metrics` 端点给 Prometheus 抓取。

---

## 13. 完整 demo：production-ready FastAPI

```python
import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agents import Agent, Runner, SQLiteSession
from agents.exceptions import AgentsException, MaxTurnsExceeded
import logfire


logging.basicConfig(level=logging.INFO)
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"), send_to_logfire="if-token-present")   # 不带 token 会直接报 LogfireConfigurationError，这样没 token 时只在本地


app = FastAPI(title="Agent Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"])

logfire.instrument_fastapi(app)          # 需要 pip install 'logfire[fastapi]'（不装会 RuntimeError）
logfire.instrument_openai_agents()


agent = Agent(name="A", instructions="...", model="gpt-6-luna")


class ChatReq(BaseModel):
    message: str
    user_id: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatReq):
    session = SQLiteSession(req.user_id, "sessions.db")
    try:
        result = await Runner.run(agent, req.message, session=session, max_turns=8)
        return {"reply": result.final_output, "tokens": result.context_wrapper.usage.total_tokens}
    except MaxTurnsExceeded:
        raise HTTPException(408, "timeout")
    except AgentsException:
        raise HTTPException(500, "internal")     # 详情写日志，别把 str(e) 回给用户
```

---

## 14. 下一步

- 成本与延迟 → [Cost / Latency 优化](./02-cost-latency.md)
- 错误处理 → [错误处理与重试](./03-error-handling.md)
- 安全 → [安全：Prompt Injection / 越狱防御 / 滥用监控](./04-security.md)
