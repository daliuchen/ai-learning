# 部署形态选型

> **一句话**：Agent 不是"装个 Web App"——按"会不会长连接"、"会不会跑 background"、"高峰量大不大"分形态：脚本 / FastAPI / Lambda / 长任务 worker。

---

## 1. 5 种典型部署形态

| 形态 | 适合 | 例子 |
|------|------|------|
| **CLI 脚本** | 内部工具 / 批处理 | 数据清洗、定时任务 |
| **FastAPI** | 同步 / 流式 chat | C 端 chatbot、客服 |
| **WebSocket Server** | 双向语音 / 实时 | Voice agent、实时游戏 |
| **Lambda / Cloud Function** | 间断流量、低 QPS | 慢节奏问答 |
| **Worker（Celery / SQS）** | 长任务、batch | Research agent 跑 5 分钟、批量分类 |

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

适合：

- 一次性 / 计划任务
- ETL 数据流
- 不需要 user-facing

---

## 3. FastAPI（最常见）

详见 [06-integration/03-fastapi-deploy.md](../06-integration/03-fastapi-deploy.md)。

部署：

```bash
# 1. Docker
docker build -t my-agent .
docker run -p 8000:8000 -e OPENAI_API_KEY=... my-agent

# 2. uvicorn + supervisor / systemd
uvicorn app:app --workers 4 --host 0.0.0.0 --port 8000
```

⚠️ **并发**：

- `--workers 4` 4 个进程并行
- 每个进程 async 内部能跑多个并发 request
- Worker 之间不共享内存（Session 用 SQLite / Redis）

---

## 4. WebSocket Server

```python
# 同 FastAPI，但用 WebSocket endpoint
@app.websocket("/voice")
async def voice(ws: WebSocket):
    ...
```

部署相同：

```bash
uvicorn app:app --workers 4
```

⚠️ WebSocket 长连接：

- 监控连接数，设上限
- 心跳 / 断线重连
- 部署在 Nginx / ALB 后要配 sticky session

---

## 5. Lambda

适合：

- 流量低（< 100 QPS）
- 不需要长连接
- 用按调用计费比常驻便宜

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

冷启动优化：

- Provisioned concurrency（贵但即时）
- Layer 装常用依赖
- 用 OpenAI 长连接（在 handler 外创建 client）

---

## 6. Worker（异步长任务）

Agent 跑研究任务 5 分钟 → 用户不能在 HTTP 等。

```python
# tasks.py - Celery
from celery import Celery
from agents import Agent, Runner
import asyncio


celery = Celery("tasks", broker="redis://...")


research_agent = Agent(name="Researcher", instructions="...", model="gpt-4o")


@celery.task
def research_task(query: str, user_id: str):
    result = asyncio.run(Runner.run(research_agent, query, max_turns=30))

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

## 7. 容器化最佳实践

```dockerfile
FROM python:3.11-slim

# 多阶段：编译依赖独立层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码层（改动频繁，放最后）
COPY app/ /app/
WORKDIR /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

注意：

- 别 ship `.env`（用 K8s secret / SSM）
- `--workers` 数 = CPU 数 + 1（CPU 密集）or 2× CPU（IO 密集）

---

## 8. K8s 关键配置

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

关键：

- `livenessProbe` 健康检查
- 设资源 limit（防 OOM）
- 多副本 + HPA 弹性

---

## 9. Sessions 存哪

| 部署形态 | Sessions |
|----------|----------|
| 单进程脚本 | SQLite 文件 |
| FastAPI 单实例 | SQLite 文件 |
| FastAPI 多副本 | Redis / Postgres 自定义 Session |
| Lambda | DynamoDB 自定义 |
| Worker | Redis / Postgres |

---

## 10. 健康检查 / Readiness

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

## 11. 日志 / Metrics 标配

```python
import logging
import logfire


logging.basicConfig(level=logging.INFO)
logfire.configure()
logfire.instrument_fastapi(app)
logfire.instrument_openai_agents()


from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)
```

`/metrics` 端点给 Prometheus。

---

## 12. 完整 demo：production-ready FastAPI

```python
# demos/production/01_app.py
import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agents import Agent, Runner, SQLiteSession
from agents.exceptions import AgentsException, MaxTurnsExceeded
import logfire


logging.basicConfig(level=logging.INFO)
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))


app = FastAPI(title="Agent Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"])

logfire.instrument_fastapi(app)
logfire.instrument_openai_agents()


agent = Agent(name="A", instructions="...", model="gpt-4o-mini")


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
        return {"reply": result.final_output, "tokens": result.usage.total_tokens}
    except MaxTurnsExceeded:
        raise HTTPException(408, "timeout")
    except AgentsException as e:
        raise HTTPException(500, str(e))
```

---

## 13. 下一步

- 📖 Cost / Latency 优化 → [02-cost-latency.md](./02-cost-latency.md)
- 📖 Error Handling → [03-error-handling.md](./03-error-handling.md)
- 📖 安全 → [04-security.md](./04-security.md)
