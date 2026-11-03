# MCP Server 07：用 Tasks 扩展异步执行长请求

> 普通请求是发出去之后一直同步等结果，Tasks 把它改成先拿到一个 task 句柄，之后再轮询、取回结果，适合跑几十秒到几小时的任务，比如视频转码、批处理、CI、人工审批。2026-07-28 把 Tasks 从核心协议里移出来，变成官方扩展 `io.modelcontextprotocol/tasks`，并重新设计了一遍；官方 Python SDK 2.3 目前还没有实现它。所以这篇先讲新协议，再给出现在就能在 Python 里用的替代做法。

2025-11-25 的「实验性 Tasks」（`tasks/result`、`tasks/list`、请求里的 `task` 字段、`execution.taskSupport`）和 2026-07-28 的 Tasks 扩展互不兼容。Python SDK 从 2.0 起整个去掉了 v1 的 `mcp.*.experimental` Tasks API（迁移指南里的 "Experimental Tasks support removed"），`call_tool_as_task`、`server_capabilities_overrides` 之类的写法都不要再用。

---

## 1. 为什么需要 Tasks

普通的 MCP 请求是同步阻塞的，`tools/call` 发出去后一直等响应。这会带来几个问题：

- 很多 HTTP 客户端和网关有几十秒级的超时，长任务会被切断
- Client 端的 LLM 在等待期间什么也做不了
- 任务跑到一半 Client 崩溃，重连之后什么都拿不到
- 远程 Server 多节点部署时，把一个请求绑在某条连接上很脆弱（2026 版取消了会话，这个问题更明显）

Tasks 把请求拆成两个阶段：

```
[tools/call]
   ↓
[Server 立即返回 CreateTaskResult：taskId + 状态 working]   ← 任务在返回之前就已持久化
   ↓
[Client 按 pollIntervalMs 轮询 tasks/get]
   ↓
[status = completed → result 字段就是原请求同步返回时的结果]
```

---

## 2. 2026-07-28 版 Tasks 扩展

### 2.1 协商：走扩展机制，不是核心 capability

- Client 在每个请求的 `_meta` 里声明：`io.modelcontextprotocol/clientCapabilities` → `extensions` → `"io.modelcontextprotocol/tasks": {}`
- Server 在 `server/discover` 的 `capabilities.extensions` 里声明同一个标识符
- 每个请求要不要创建 task 由 Server 决定。没声明这个扩展的 Client 永远不应该收到 task 结果（要么同步等，要么报错）。请求里的 `task` 字段和工具级的 `taskSupport` 都没有了

### 2.2 创建：结果的 `resultType` 是 `"task"`

2026 版的所有结果都有 `resultType`。普通结果是 `"complete"`，被转成任务的请求返回 `CreateTaskResult`（`resultType: "task"`）。

下面是一个示意，字段名取自扩展 schema `ext-tasks/schema/2026-07-28`：

```json
{ "jsonrpc": "2.0", "id": 7, "result": {
    "resultType": "task",
    "task": { "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840",
              "status": "working", "ttlMs": 3600000, "pollIntervalMs": 5000,
              "createdAt": "2026-11-03T08:00:00Z", "lastUpdatedAt": "2026-11-03T08:00:00Z" } } }
```

### 2.3 状态机

| 状态 | 含义 |
|------|------|
| `working` | 处理中 |
| `input_required` | 需要客户端输入，`inputRequests` 里是具体问题（elicitation 等） |
| `completed` | 完成，`result` 是最终结果 |
| `failed` | 执行中出现 JSON-RPC 错误，详情在 `error` 字段 |
| `cancelled` | 任务已被取消并停止。发出 `tasks/cancel` 不等于一定会进入这个状态（取消是协作式的，见下表） |

后三个是终态，进入之后不会再变。`Task` 还带有 `statusMessage`（可读的进度说明）、`createdAt` / `lastUpdatedAt`、`ttlMs`（可以是 `null`，表示不过期）和 `pollIntervalMs`。

### 2.4 方法

| 方法 | 作用 |
|------|------|
| `tasks/get` | 轮询，返回当前状态；到终态时直接带上 `result` / `error`（取代了 2025 版阻塞式的 `tasks/result`） |
| `tasks/update` | Client 交回 `inputResponses`，回答 `input_required` 里的 `inputRequests`；Server 返回空结果 |
| `tasks/cancel` | 协作式取消：Server 确认收到取消意图，但不一定能停下来 |
| `notifications/tasks` | 可选的状态推送，Client 通过 `subscriptions/listen` 订阅，每条通知都带完整的任务状态 |

被删掉的有 `tasks/list`（不再列出任务）和 `tasks/result`（阻塞取结果）。

### 2.5 中途需要用户输入：和 MRTR 的关系

任务执行中需要问用户时（比如「确认扣费吗？」），任务进入 `input_required` 状态，下一次 `tasks/get` 会带回 `inputRequests`（形状和 [Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md) 讲的 MRTR 的 `inputRequests` 一样），Client 再用 `tasks/update` 把答案交回去。整个过程不需要第二条连接，也不需要 Server 主动向 Client 发请求，符合 2026 版无状态设计的要求。

---

## 3. Python SDK 现状（mcp 2.3.0，2026 年 11 月）

官方 `mcp` 包没有实现 Tasks 扩展，迁移指南和 What's new 里都写明了 "this SDK does not implement yet"。SDK 提供了通用的 `Extension` 机制（`mcp.server.mcpserver.Extension`、`MCPServer(extensions=[...])`，见官方 Extensions 文档），理论上可以自己实现 `tasks/get` 等方法，但持久化、鉴权绑定、TTL 都要自己处理，工作量不小。

TypeScript 那边有官方包 `@modelcontextprotocol/ext-tasks`（提供 requester 生命周期 API，以及对 2025-11-25 receiver 的支持）。

第三方独立包 `fastmcp`（当前 4.1.x，另有附加包 `fastmcp-tasks`）有自己的后台任务实现，API 和官方 SDK 不一样。按 Pydantic AI 官方文档的说法，FastMCP 4 用的是新的 Tasks 扩展（SEP-2663，由 Server 决定是否创建任务），FastMCP 3 用的是 2025-11-25 版（SEP-1686，按工具的 `execution.taskSupport` 声明），Pydantic AI 的 `MCPToolset` 作为 Client 两种都能用（见[在 Pydantic AI 中使用 MCP](../04-integration/04-pydantic-ai-mcp.md) 第 8 节）。Claude Code 作为 Client 也支持 MCP Tasks 扩展（公开 beta，见[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)）。

所以，目前在官方 Python SDK 上做长任务，用句柄模式。

---

## 4. 现在就能用的做法：Server 生成句柄

2026-07-28 的变更清单里写得很明白：需要跨调用保持状态的 Server，应当使用由 Server 生成的显式句柄，作为普通的工具参数传递。长任务正好适用：`start_*` 立即返回 `job_id`，`get_*_status` 用来轮询，`cancel_*` 用来取消。它和 Tasks 扩展的语义一一对应，区别只是由模型（而不是协议层）去学会「先提交、再轮询」：

```python
import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("render-server")


@dataclass
class Job:
    owner: str  # 绑定调用者，防止别人猜 job_id 拿结果（生产里从鉴权上下文取）
    status: Literal["working", "completed", "failed", "cancelled"] = "working"
    progress: int = 0
    result: str | None = None
    created: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None


JOBS: dict[str, Job] = {}
TTL_SECONDS = 3600


class JobView(BaseModel):
    job_id: str
    status: str
    progress: int
    result: str | None = None
    poll_after_ms: int = 500  # 对应 Tasks 扩展里的 pollIntervalMs


async def _render(job: Job, steps: int) -> None:
    try:
        for i in range(steps):
            await asyncio.sleep(0.1)  # 模拟耗时工作
            job.progress = int((i + 1) / steps * 100)
        job.status, job.result = "completed", "video_url=https://output/xxx.mp4"
    except asyncio.CancelledError:
        job.status = "cancelled"
        raise
    except Exception:
        job.status = "failed"


def _gc() -> None:
    now = time.monotonic()
    for jid in [j for j, v in JOBS.items() if now - v.created > TTL_SECONDS and v.status != "working"]:
        del JOBS[jid]  # 强制 TTL 清理，避免泄漏


def _get(job_id: str, owner: str) -> Job:
    job = JOBS.get(job_id)
    if job is None or job.owner != owner:  # 对「不存在」和「不是你的」返回同样的错误
        raise ToolError(f"没有这个任务: {job_id}")
    return job


@mcp.tool()
async def start_render(input_url: str, steps: int = 10) -> JobView:
    """提交渲染任务，立即返回 job_id。随后用 get_render_status 轮询（按 poll_after_ms 间隔）。"""
    _gc()
    job_id = secrets.token_urlsafe(16)  # 高熵、不可猜
    job = Job(owner="demo-user")
    job.task = asyncio.create_task(_render(job, steps))
    JOBS[job_id] = job
    return JobView(job_id=job_id, status=job.status, progress=0)


@mcp.tool()
async def get_render_status(job_id: str) -> JobView:
    """查询任务状态；status=completed 时 result 就是最终结果。"""
    job = _get(job_id, "demo-user")
    return JobView(job_id=job_id, status=job.status, progress=job.progress, result=job.result)


@mcp.tool()
async def cancel_render(job_id: str) -> JobView:
    """请求取消任务（协作式：已完成的任务取消无效）。"""
    job = _get(job_id, "demo-user")
    if job.status == "working" and job.task:
        job.task.cancel()
        await asyncio.sleep(0)
    return JobView(job_id=job_id, status=job.status, progress=job.progress, result=job.result)
```

自测（`python demos/server/07_long_job_handle.py`，进程内运行，实际输出）：

```
提交: working
  轮询: working 0%
  轮询: working 20%
  ...
  轮询: completed 100%
结果: video_url=https://output/xxx.mp4
取消后: cancelled
猜 id: True Error executing tool get_render_status: 没有这个任务: guess
```

这个版本有几处是有意做的安全设计，对应 Tasks 规范对 Receiver 的要求：

| 风险 | 做法（代码里已经实现） |
|------|---------------------|
| 句柄被猜到 | 用 `secrets.token_urlsafe(16)` 生成，熵足够高 |
| 读到别人的任务 | `Job.owner` 绑定调用者；「不存在」和「不是你的」返回同一条错误，不暴露任务是否存在 |
| 资源泄漏 | `_gc()` 强制按 TTL 清理；生产环境再加上每用户并发上限和队列长度监控 |
| 取消不彻底 | 取消是协作式的，`task.cancel()` 之后以任务自己写入的状态为准 |

> 这个示例把任务放在进程内存里，进程一重启任务就丢了；多副本部署时，句柄也只在创建它的那个副本上有效。要做到崩溃后还能取回结果，任务状态必须存到共享存储（Redis、数据库）里，工作线程也要能从存储中恢复。Tasks 扩展要求任务在返回响应之前就完成持久化，原因就在这里。

### 要不要让模型来轮询

句柄模式把轮询交给了 LLM，每次 `get_render_status` 都是一轮工具调用，既费 token 也不稳定。有几种折中办法：

- 在 Server 端多等一会儿：`get_render_status(job_id, wait_seconds=20)` 在 Server 里最多等 20 秒再返回，减少无效轮询
- 任务本身只要一两分钟的话，直接用 `ctx.report_progress`（见[日志、进度、取消与 Ping](06-logging-progress-ping.md)）并调高客户端超时就够了
- 由 Host 轮询：Agent 框架在 Host 端封装轮询逻辑，模型只看到「提交」和「完成」

---

## 5. 什么时候该用异步长任务

| 场景 | 要不要 |
|------|-----------|
| 快速查询，几百毫秒 | 不需要，普通调用即可 |
| 1–10 秒，可能有进度 | 不必，progress 通知就够了 |
| 10–30 秒 | 先看客户端和网关的超时设置：够用就用普通调用加 progress 通知，接近上限就按下一行处理 |
| 30 秒到几分钟 | 用句柄模式（或 Tasks 扩展），同时调高客户端超时 |
| 几十分钟到几小时 | 必须用，而且状态要持久化 |
| 需要中途让用户确认 | 句柄模式下让 `get_*` 返回「待确认」，再配一个 `confirm_*` 工具；协议层面用 `input_required` + `tasks/update` |
| 希望客户端崩溃重连后还能拿到结果 | 句柄必须持久化 |

---

## 6. 安全考虑

| 风险 | 缓解 |
|------|------|
| task id 被猜到 | 用高熵的 UUID v4/v7 或 `secrets.token_urlsafe` |
| 拿到别人的 task | 把任务绑定到鉴权主体（OAuth `subject`） |
| DoS（用户大量提交任务） | 限流、每用户并发上限、强制的 TTL 上限 |
| 资源泄漏 | 严格按 TTL 清理，监控队列长度 |
| 任务结果里有敏感数据 | TTL 到期就删除，日志里不要打印结果 |

---

## 7. 常见坑

| 坑 | 排查 |
|----|------|
| 照着 2025 版的文章写 `tasks/result`、`tasks/list`、`taskSupport` | 2026-07-28 的扩展里这些都没有了，改用 `tasks/get`（终态直接带结果）、`tasks/update`、`tasks/cancel` |
| 以为 `mcp` 2.3 自带 Tasks | 没有；用句柄模式，或者基于 `Extension` 自己实现 |
| 轮询太频繁 | 遵守 `pollIntervalMs`（句柄模式下自己返回 `poll_after_ms`），别每 100ms 查一次 |
| 任务只存在进程内存里 | 重启就丢；多副本时句柄只在创建它的副本上有效 |
| TTL 过期后结果没了 | 拿到结果就马上处理，别放着不取 |
| 以为取消一定生效 | 取消是协作式的，以任务最终的状态为准 |

---

## 8. 下一步

- 错误处理：[错误处理与参数校验](./08-errors-validation.md)
- Server 中途问用户（MRTR，Tasks 的 `input_required` 和它形状相同）：[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)
- 远程部署，多副本下的任务状态：[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)

## 参考资料

- Tasks 扩展概览：https://modelcontextprotocol.io/extensions/tasks/overview
- 扩展规范与 schema：https://github.com/modelcontextprotocol/ext-tasks
- SEP-2663（Tasks 扩展）：https://modelcontextprotocol.io/seps/2663-tasks-extension
- 变更清单（Tasks 移出核心）：https://modelcontextprotocol.io/specification/2026-07-28/changelog
- Python SDK 迁移指南 "Experimental Tasks support removed"：https://py.sdk.modelcontextprotocol.io/migration/
- 2025-11-25 版 Tasks：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks
