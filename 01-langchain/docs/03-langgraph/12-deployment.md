# LangGraph 12：部署与 LangGraph Studio

> **一句话**：写好的 graph 怎么对外服务？官方提供三种部署形态——**LangGraph Cloud**（SaaS）、**Self-hosted LangGraph Platform**（企业自托管）、**自己写 FastAPI 包**（最灵活）。配套有 **Studio** 可视化调试工具。

---

## 1. 三条部署路径

| 方式 | 难度 | 适合 |
|------|------|------|
| LangGraph Cloud（SaaS） | 低 | 中小团队、快速上线 |
| LangGraph Platform 自托管 | 中 | 企业内网、合规要求 |
| 自包 FastAPI | 高 | 已有微服务栈，深度定制 |

不论哪种，**核心代码不变**：你写的 `StateGraph` / `@entrypoint` 不动，部署框架围绕它跑。

---

## 2. 项目结构：langgraph.json

任何 LangGraph 部署都要一个 `langgraph.json`：

```json
{
  "dependencies": ["."],
  "graphs": {
    "react_agent": "./src/agent.py:graph",
    "research_team": "./src/research.py:graph"
  },
  "env": ".env",
  "python_version": "3.11"
}
```

`graphs` 的 value 是 `path:variable` 格式，指向已编译的图。

文件结构：

```
my-agent/
├── langgraph.json
├── pyproject.toml / requirements.txt
├── .env
└── src/
    ├── agent.py          # 定义 graph
    └── research.py
```

`agent.py`：

```python
graph = build_graph().compile(checkpointer=...)
```

---

## 3. 本地起：langgraph dev

安装 CLI：

```bash
pip install -U langgraph-cli
```

启动本地开发服务：

```bash
langgraph dev
```

会启动一个本地 server（FastAPI），暴露：

- `POST /threads` 创建 thread
- `POST /threads/{id}/runs` 触发执行
- `GET /threads/{id}/state` 查 state
- `WS /threads/{id}/stream` 流式

同时打开 **LangGraph Studio**（浏览器 UI）：

- 可视化图结构
- 在 UI 调用 / 流式查看
- 调试 state、修改 checkpoint
- HITL 一键 resume

可以在 LangSmith 项目页一键跳转到 Studio。

---

## 4. 上线：LangGraph Cloud

```bash
langgraph deploy
```

把代码推到 LangGraph Cloud，自动构建容器：

- API 接口：`https://your-app.us.langgraph.app`
- 内置 Postgres Checkpointer
- Auto-scale
- 与 LangSmith 一键关联

Cloud 适合不想运维 server 的小团队。

---

## 5. 自托管：LangGraph Platform

企业内网部署：

```bash
langgraph build --tag my-agent:v1
# 得到一个 Docker 镜像，自己 docker run / k8s 部署
```

依赖：
- Postgres（Checkpointer）
- Redis（队列）

官方提供 Helm chart 和 docker-compose。

---

## 6. 自己写 FastAPI 包

如果不想用 LangGraph Platform，自己包：

```python
# server.py
from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from langgraph.checkpoint.postgres import PostgresSaver

from .agent import build_graph

memory = PostgresSaver.from_conn_string("postgresql://...")
graph = build_graph().compile(checkpointer=memory)

app = FastAPI()

class ChatReq(BaseModel):
    thread_id: str
    text: str

@app.post("/chat")
def chat(req: ChatReq):
    cfg = {"configurable": {"thread_id": req.thread_id}}
    out = graph.invoke({"messages": [("human", req.text)]}, config=cfg)
    return {"answer": out["messages"][-1].content}

@app.get("/stream")
def stream(thread_id: str, text: str):
    cfg = {"configurable": {"thread_id": thread_id}}
    async def gen():
        async for token, meta in graph.astream(
            {"messages": [("human", text)]}, config=cfg, stream_mode="messages",
        ):
            if token.content:
                yield {"event": "token", "data": token.content}
    return EventSourceResponse(gen())
```

跑：

```bash
uvicorn server:app --reload
```

---

## 7. LangGraph Studio 详解

主要功能：

- **Graph 拓扑可视化**：实时高亮当前执行的 node
- **Run 调试**：每步 input/output、state 时间线
- **State 编辑**：在某 checkpoint 改字段然后 resume
- **HITL UI**：interrupt 时弹窗等用户决策
- **Thread 列表**：查看历史会话
- **A/B**：同一输入跑不同分支对比

Studio 是 LangGraph 杀手锏调试工具，比命令行 trace 直观 10 倍。

---

## 8. 客户端 SDK：langgraph-sdk

提供 Python / JS 客户端：

```python
from langgraph_sdk import get_client

client = get_client(url="https://your-app.us.langgraph.app")

# 创建 thread
thread = await client.threads.create()

# 触发 run
run = await client.runs.create(thread["thread_id"], "react_agent", input={"messages":[("human","hi")]})

# 流式
async for chunk in client.runs.stream(thread["thread_id"], "react_agent", input={...}, stream_mode="messages"):
    print(chunk)
```

接 LangGraph Cloud / 自托管都用同一套 SDK。

---

## 9. 监控与告警

LangGraph Platform 自带：

- 每个 thread 的延迟分布
- 错误率
- 与 LangSmith 自动关联

也可以接 Prometheus（自托管时暴露 `/metrics`）。

---

## 10. 部署清单

- [ ] `langgraph.json` 正确指向已编译 graph
- [ ] 使用 `PostgresSaver`（不要用内存）
- [ ] 环境变量从 secret manager 注入
- [ ] LangSmith 关联（`LANGSMITH_API_KEY`）
- [ ] healthcheck endpoint
- [ ] Token / 费用上限保护
- [ ] HITL 流程前端实现
- [ ] 部署版本号写进 metadata，便于回滚

---

## 11. demo

```python
# demos/langgraph/12_deploy_local.py
# 本地用 langgraph dev 跑起来的最小工程
```

参考目录：

```
demos/langgraph/deploy/
├── langgraph.json
├── requirements.txt
└── src/agent.py
```

在该目录 `langgraph dev` 即可。

---

## 12. 至此 LangGraph 12 篇完成

接下来：

- [横向对比](../04-comparison/01-frameworks.md)
- [实战项目 1：RAG 问答 Agent](../04-comparison/02-project-rag-agent.md)
- [实战项目 2：多 Agent 研究助手](../04-comparison/03-project-research-team.md)
