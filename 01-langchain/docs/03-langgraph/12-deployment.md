# LangGraph 12：部署与 Studio

> 图写好以后，怎么对外提供服务？官方把这件事收进了 LangSmith Deployment（以前叫 LangGraph Platform / LangGraph Cloud），运行时叫 Agent Server，本地开发、构建、部署都用 `langgraph` CLI。不想用官方方案，也可以自己拿 FastAPI 包一层。调试则有配套的 Studio。这篇按部署环境、项目结构、本地开发、上线、自托管、自己包 FastAPI 的顺序讲下来。

版本：以 2026 年 11 月 docs.langchain.com 的部署文档为准；`LangGraph Platform` / `LangGraph Cloud` 这两个名字官方文档里已经不用了。

---

## 1. 部署路径

LangSmith Deployment 的官方页面列了四种环境，它们跑的都是同一个 Agent Server：

| 方式 | 控制面 / 数据面 | 适合 |
|------|----------------|------|
| Cloud | 全由 LangChain 托管（AWS / GCP），GitHub 或 `langgraph deploy` 发布，需 Plus 以上 | 中小团队、快速上线 |
| Hybrid | LangChain 托管控制面，Agent Server 与数据库跑在你的基础设施里 | 数据要留在自己 VPC |
| Self-hosted with control plane | 控制面 + Agent Server 都在你自己的 K8s，需 Enterprise | 合规、内网、隔离 |
| Standalone server | 用 Docker / Compose / K8s 跑 Agent Server 容器，PostgreSQL、Redis 和 LangSmith license key 要自备，无控制面 | 只要运行时 |

另外还有第五条路：自己用 FastAPI 包（第 6 节）。这条路最灵活，但持久化、队列、流式恢复都得自己做。

不管选哪种，业务代码都不用改：`StateGraph`、`@entrypoint`、`create_agent` 返回的图都能直接部署。

---

## 2. 项目结构：langgraph.json

所有 Agent Server 部署都需要一个 `langgraph.json`，CLI 默认读当前目录下的这个文件：

```json
{
  "dependencies": ["."],
  "graphs": {
    "react_agent": "./src/agent.py:graph",
    "research_team": "./src/research.py:graph"
  },
  "env": ".env",
  "python_version": "3.12"
}
```

常用字段（Python）：

| 字段 | 含义 |
|------|------|
| `dependencies` | 必填：`"."` 表示当前目录的本地包，也可写 pip 包名 |
| `graphs` | 必填：图 ID → `路径:变量`，变量是已编译的图，或返回图的函数 |
| `env` | `.env` 文件路径，或直接写环境变量映射 |
| `python_version` | `3.11` / `3.12` / `3.13`，默认 `3.11` |
| `store` | 给 Store 配语义检索（`index.embed` / `dims`）或 TTL |
| `checkpointer` | 检查点后端：`default`（PostgreSQL）/ `mongo` / `custom` |
| `http` | 自定义 FastAPI/Starlette 应用、CORS 等 |
| `auth` | 鉴权处理器路径 |

目录结构：

```
my-agent/
├── langgraph.json
├── pyproject.toml / requirements.txt
├── .env
└── src/
    ├── agent.py          # 定义 graph
    └── research.py
```

`agent.py` 里有一点要注意：在 Agent Server 里运行时，不要自己传 checkpointer，平台会注入持久化后端。

```python
from langchain.agents import create_agent

graph = create_agent(model, tools=[...])      # create_agent 返回的就是编译好的图
# 或者：graph = build_graph().compile()
```

---

## 3. 本地起：langgraph dev

安装 CLI（`dev` 命令需要 `inmem` 额外依赖）：

```bash
pip install -U "langgraph-cli[inmem]"
```

启动本地开发服务。这一步不需要 Docker，默认端口 2024，支持热重载：

```bash
langgraph dev
```

它会在本地起一个 Agent Server，暴露 Agent Server API（assistants / threads / runs），比如：

- `POST /threads` 创建 thread
- `POST /threads/{id}/runs` 创建 run；`/threads/{id}/runs/stream` 流式
- `GET /threads/{id}/state`、`/history` 查 state 与历史
- `/docs` 本地 API 文档

同时会自动打开 Studio，一个跑在浏览器里的调试界面。在里面能查看图结构并实时高亮当前节点，发起对话并流式查看输出，检查或修改 state 后从某个 checkpoint 重跑，遇到 `interrupt` 时直接在界面上 resume。

`langgraph dev` 的状态存在内存里，并落盘到本地目录，只适合开发。上线前想做接近生产的验证，可以用 `langgraph up`，它在 Docker 里起 Server + PostgreSQL + Redis，默认端口 8123。

---

## 4. 上线：langgraph deploy

```bash
langgraph deploy        # beta：本地（或远程）构建镜像、推到托管 registry、创建/更新部署
```

需要 `LANGSMITH_API_KEY`，写进 `.env` 就行。默认创建 Serverless 部署，加 `--deployment-type dedicated` 则创建常驻部署；本地没有 Docker 时会自动改为远程构建。也可以在 LangSmith UI 里连 GitHub 仓库来创建部署。部署完成后你会得到一个 Agent Server API 地址、托管的 PostgreSQL 检查点与 Store，LangSmith 追踪和 Studio 也会自动关联上。

---

## 5. 自托管：Standalone / Hybrid / 自建控制面

不想用托管，可以自己构建镜像：

```bash
langgraph build -t my-agent:v1
# 得到 Docker 镜像，用 docker / Compose / K8s 部署
```

Standalone 模式需要你自备 PostgreSQL（`DATABASE_URI`）、Redis（`REDIS_URI`）和 LangSmith license key（`LANGGRAPH_CLOUD_LICENSE_KEY`）。官方的 Docker Compose 示例里带了 Postgres 和 Redis 服务，但只建议用于本地开发和测试；Hybrid 和自建控制面则由控制面来管理部署。具体步骤见官方 "Deploy standalone server" / "Hybrid" 文档。

---

## 6. 自己写 FastAPI 包

不用 LangSmith Deployment 的话，可以自己包一层：

```python
# server.py
from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from psycopg import Connection
from langgraph.checkpoint.postgres import PostgresSaver

from agent import build_graph

# from_conn_string 是上下文管理器，长驻服务里直接建连接交给 PostgresSaver
conn = Connection.connect("postgresql://...", autocommit=True, prepare_threshold=0)
memory = PostgresSaver(conn)
memory.setup()
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
    def gen():   # 同步的 PostgresSaver 配同步 stream；要用 astream 就换 AsyncPostgresSaver
        for token, meta in graph.stream(
            {"messages": [("human", text)]}, config=cfg, stream_mode="messages",
        ):
            if token.content:
                yield {"event": "token", "data": token.content}
    return EventSourceResponse(gen())
```

运行：

```bash
uvicorn server:app --reload
```

---

## 7. Studio 详解

Studio 的主要功能：

- Graph 拓扑可视化：实时高亮正在执行的 node
- Run 调试：每一步的 input/output 和 state 时间线
- State 编辑：在某个 checkpoint 改字段后 resume
- HITL 界面：interrupt 时弹出来等用户决策
- Thread 列表：查看历史会话
- A/B：同一个输入跑不同分支做对比

Studio 连接的是本地或已部署的 Agent Server，调试图的时候我基本都开着它。

---

## 8. 客户端 SDK：langgraph-sdk

官方提供 Python 和 JS 客户端：

```python
from langgraph_sdk import get_client

client = get_client(url="http://127.0.0.1:2024")   # 或你的部署地址

# 创建 thread
thread = await client.threads.create()

# 触发 run
run = await client.runs.create(thread["thread_id"], "react_agent", input={"messages":[("human","hi")]})

# 流式
async for chunk in client.runs.stream(thread["thread_id"], "react_agent", input={...}, stream_mode="messages"):
    print(chunk)
```

本地 `langgraph dev`（`http://127.0.0.1:2024`）、Cloud 和自托管用的都是同一套 SDK（`pip install langgraph-sdk`）。想把远端的图当本地图来用，还可以用 `RemoteGraph`。

---

## 9. 监控与告警

LangSmith Deployment 的控制面能看到部署的运行状态、错误率和延迟，并和 LangSmith 追踪自动关联（Standalone 模式可以单独把 trace 发到 LangSmith Cloud 或自托管实例）。具体有哪些指标，以所选部署环境的官方文档为准。

---

## 10. 部署清单

- [ ] `langgraph.json` 正确指向已编译 graph
- [ ] 用 Agent Server 部署时不要自己传 checkpointer（平台提供 PostgreSQL 后端）；自己包 FastAPI 才需要 `PostgresSaver`（不要用内存）
- [ ] 环境变量从 secret manager 注入
- [ ] LangSmith 关联（`LANGSMITH_API_KEY`）
- [ ] healthcheck endpoint
- [ ] Token / 费用上限保护
- [ ] HITL 流程前端实现
- [ ] 部署版本号写进 metadata，便于回滚

---

## 11. demo

用 `langgraph dev` 在本地跑起来的最小工程放在 `demos/langgraph/deploy/`：

```
demos/langgraph/deploy/
├── langgraph.json
├── requirements.txt
└── src/agent.py
```

在这个目录下运行 `langgraph dev` 即可。

---

## 12. LangGraph 部分到此结束

后面是对比和两个实战项目：

- [横向对比：LangGraph vs CrewAI / Microsoft Agent Framework / LlamaIndex Workflows / OpenAI Agents SDK / Pydantic AI](../04-comparison/01-frameworks.md)
- [实战项目 1：RAG 知识库问答 Agent（create_agent + LangGraph）](../04-comparison/02-project-rag-agent.md)
- [实战项目 2：多 Agent 研究助手（子图 + Map-Reduce + HITL）](../04-comparison/03-project-research-team.md)
