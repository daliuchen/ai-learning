# MCP Server 07：Tasks 扩展 —— 让长请求异步、可恢复、可查询

> **一句话**：Tasks 是 2025-11-25 版规范引入的「**实验性**」扩展，把"一次请求 = 一次同步等待"改成"一次请求 = 拿到一个 task 句柄，后续轮询/订阅结果"。适合**几十秒到几小时**的长任务（视频转码、爬取、批处理、Agent 工作流）。

> ⚠️ **实验性**：标准 2025-11-25 引入、Python SDK 实现仍在演进中。本篇讲清协议设计，落地代码以 spec 为准。

---

## 1. 为什么需要 Tasks

传统 MCP 请求是同步阻塞——`tools/call` 发出去就一直等响应。问题：

- HTTP 默认超时 30s，长任务被切断
- Client 端 LLM 等待时无事可做，浪费上下文窗口
- 任务跑一半 Client 崩了 → 重连后什么都拿不到
- 远程 Server 多节点部署时，session 漂移到别的节点就丢了

Tasks 把请求拆成两阶段：

```
[请求]
   ↓
[立即返回 taskId + working 状态]
   ↓
[Client 干别的、做轮询、订阅通知]
   ↓
[任务完成]
   ↓
[Client 调 tasks/result 拿真实结果]
```

---

## 2. 核心概念

| 术语 | 含义 |
|------|------|
| **Requestor** | 发起请求方（可能是 Client 也可能是 Server——sampling 反向请求也支持 task） |
| **Receiver** | 接收方，生成 taskId、执行任务 |
| **Task** | 一个状态机：`working` → `completed` / `failed` / `cancelled`，可能经过 `input_required` |
| **TTL** | 任务保留时长（毫秒），过期后 Receiver 可清理 |
| **pollInterval** | Receiver 建议的轮询间隔 |

任务状态机：

```
                  ┌─────────────┐
                  │   working   │◀───┐
                  └───┬─────┬───┘    │
              ┌───────┘     └────────┤
              ▼                      ▼
       ┌────────────┐         ┌──────────────┐
       │ completed  │         │input_required│ (need user/LLM intervention)
       └────────────┘         └──────────────┘
       ┌──────────┐
       │  failed  │           最终态：completed / failed / cancelled
       └──────────┘
       ┌────────────┐
       │ cancelled  │
       └────────────┘
```

---

## 3. Capability 协商

Server 声明：

```json
{
  "capabilities": {
    "tasks": {
      "list": {},
      "cancel": {},
      "requests": {
        "tools": { "call": {} }
      }
    }
  }
}
```

含义：
- `tasks.list` / `tasks.cancel`：支持列任务 / 取消任务
- `tasks.requests.tools.call`：`tools/call` 可被 task 包装

Client 声明：

```json
{
  "capabilities": {
    "tasks": {
      "list": {},
      "cancel": {},
      "requests": {
        "sampling": { "createMessage": {} },
        "elicitation": { "create": {} }
      }
    }
  }
}
```

含义：Client 同意 Server 把 sampling / elicitation 请求做成 task。

---

## 4. 工具级声明（tool-level negotiation）

光 Server 声明能力还不够，**单个工具**还可以指定支不支持 task：

```json
{
  "name": "render_video",
  "inputSchema": {...},
  "execution": {
    "taskSupport": "required"   // 或 "optional" / "forbidden"
  }
}
```

三种值：

| 值 | 含义 |
|----|------|
| `forbidden`（默认） | 这工具**不能**走 task 模式 |
| `optional` | Client 可以选择走或不走 |
| `required` | Client **必须**走 task 模式，普通调用 Server 会拒 |

适合"required"的工具：视频转码、批量爬取、Agent 子任务（跑几十分钟）。
适合"optional"的工具：可能快可能慢，让 Client 决定。
适合"forbidden"的工具：纯计算 / 简单查询（默认）。

---

## 5. 创建 Task：在请求里加 `task` 字段

普通请求：

```json
{"method":"tools/call","params":{"name":"render","arguments":{...}}}
```

Task 版：

```json
{
  "method": "tools/call",
  "params": {
    "name": "render",
    "arguments": {...},
    "task": {
      "ttl": 3600000   // 1 小时，单位 ms
    }
  }
}
```

立即返回（**不是真正结果**）：

```json
{
  "result": {
    "task": {
      "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840",
      "status": "working",
      "createdAt": "2026-05-20T10:30:00Z",
      "lastUpdatedAt": "2026-05-20T10:30:00Z",
      "ttl": 3600000,
      "pollInterval": 5000
    }
  }
}
```

---

## 6. 轮询 + 拿结果

### 6.1 轮询状态

```json
{"method":"tasks/get","params":{"taskId":"786512e2-..."}}
```

响应里看 `status`。Client 按 `pollInterval` 节奏轮询。

### 6.2 拿真实结果

任务进入终态后调：

```json
{"method":"tasks/result","params":{"taskId":"786512e2-..."}}
```

返回**和不带 task 时一样的**结果（`CallToolResult`）：

```json
{
  "result": {
    "content": [{"type":"text","text":"渲染完成，文件: xxx.mp4"}],
    "isError": false,
    "_meta": {
      "io.modelcontextprotocol/related-task": {"taskId": "786512e2-..."}
    }
  }
}
```

`tasks/result` 在任务**未结束**时**阻塞**，直到终态——所以 Client 也可以"一上来就调 tasks/result 等"。

### 6.3 异常路径

任务失败：`status: failed`，`tasks/result` 返回 `isError: true` 的 CallToolResult。

任务被取消：`status: cancelled`，`tasks/result` 返回 JSON-RPC error。

---

## 7. 状态变更通知（可选）

Server 可以主动推：

```json
{
  "method": "notifications/tasks/status",
  "params": {
    "taskId": "786512e2-...",
    "status": "completed",
    "lastUpdatedAt": "2026-05-20T10:50:00Z",
    "ttl": 3600000
  }
}
```

但 **Client 不能依赖**这个通知（spec 允许 Server 不发）——必须有轮询兜底。

---

## 8. 取消与列出

### 8.1 取消

```json
{"method":"tasks/cancel","params":{"taskId":"786512e2-..."}}
```

Server 尽力停（best-effort），并把 status 改成 `cancelled`。**已经终态**的任务不能取消，会返回 `-32602`。

### 8.2 列出（带分页）

```json
{"method":"tasks/list","params":{"cursor":null}}
```

响应是 task 数组 + 可选 `nextCursor`（同 pagination 章节）。

**只列出自己有权限的任务**——Receiver 必须做 auth context 绑定。

---

## 9. input_required：任务中途需要更多信息

特殊状态：任务跑一半发现需要用户输入（elicitation）或需要 LLM 采样（sampling）。

### 9.1 流程

```
Server 在跑 task
   ↓
发现需要 elicitation（"请提供信用卡号"）
   ↓
Server 把 task 状态改为 input_required
   ↓
Client 轮询 tasks/get 时看到 input_required
   ↓
Client 立即调 tasks/result（不是为了拿结果，而是为了打开"渠道"）
   ↓
Server 在这条渠道上发 elicitation/create
   ↓
Client 弹 UI → 用户回答 → Client 返回 elicitation response
   ↓
Server 收到输入，task 回到 working
   ↓
继续跑直到 completed
```

这套机制让 task 支持"长流程 + 中途交互"——Agent 工作流的关键。

### 9.2 `io.modelcontextprotocol/related-task`

整个 task 生命周期里所有相关消息（progress、elicitation、log）都要带 `_meta`：

```json
{
  "_meta": {
    "io.modelcontextprotocol/related-task": {"taskId": "786512e2-..."}
  }
}
```

这样 Client 端能把消息关联到正确的 task。

---

## 10. Python SDK 现状

> ⚠️ 写作时（2026-05），Python SDK 对 Tasks 的支持还在落地。本节给出**理论代码框架**，实际用法以 SDK release notes 为准。

### 10.1 Server 端声明工具支持 task

```python
from mcp.server.fastmcp import Context, FastMCP
import asyncio

mcp = FastMCP(
    "render-server",
    # 声明 task 能力（具体 API 可能微调）
    server_capabilities_overrides={
        "tasks": {
            "list": {},
            "cancel": {},
            "requests": {"tools": {"call": {}}},
        }
    },
)


@mcp.tool(
    # 工具级别声明
    execution={"taskSupport": "required"},
)
async def render_video(input_url: str, ctx: Context) -> str:
    """渲染视频（耗时几十分钟）"""
    for i in range(60):
        await asyncio.sleep(60)  # 每分钟一次进度
        await ctx.report_progress(progress=i + 1, total=60)
    return "video_url=https://output/xxx.mp4"
```

### 10.2 Client 端走 task 模式

```python
# 伪代码——实际 SDK API 待定
result = await session.call_tool_as_task(
    name="render_video",
    arguments={"input_url": "..."},
    ttl=3600_000,
)

task_id = result.task.taskId

# 轮询
while True:
    status = await session.get_task(task_id)
    if status.status in ("completed", "failed", "cancelled"):
        break
    await asyncio.sleep(status.pollInterval / 1000)

# 拿结果
final = await session.get_task_result(task_id)
print(final.content[0].text)
```

### 10.3 自行实现的过渡方案

在 SDK 完整支持前，可以用**普通 Tool + 内部 Job 表**自己实现 task 语义：

```python
# 一个粗糙但能用的"task 雏形"
import uuid

_jobs: dict[str, dict] = {}

@mcp.tool()
async def submit_render(input_url: str) -> dict:
    """提交渲染任务，返回 jobId"""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}
    asyncio.create_task(_do_render(job_id, input_url))
    return {"jobId": job_id, "status": "running"}

@mcp.tool()
async def get_render_status(job_id: str) -> dict:
    return _jobs.get(job_id, {"status": "not_found"})

@mcp.tool()
async def get_render_result(job_id: str) -> str:
    job = _jobs[job_id]
    if job["status"] != "completed":
        raise ToolError(f"任务未完成: {job['status']}")
    return job["result"]
```

这是不规范的"穷人版 task"——LLM 要学会先调 submit、再 status、再 result。规范版 task 让协议层处理这些。

---

## 11. 什么时候**该**用 Tasks

| 场景 | 用 Task 吗 |
|------|-----------|
| 快速查询、几百毫秒 | ❌ 普通调用 |
| 1-10 秒，可能有进度 | ⚠️ 不必，progress notification 就够 |
| 30 秒 - 几分钟 | ✅ 强烈推荐 |
| 几十分钟 - 数小时 | ✅ 必需 |
| 需要中途让用户确认 | ✅ 用 input_required |
| 失败可能要重试拿结果 | ✅ task 有 TTL 缓存 |
| 跨 session 也想拿到结果 | ✅ task 是持久化的 |

---

## 12. 安全考虑

| 风险 | 缓解 |
|------|------|
| **task id 被猜出来** | 必须高熵 UUID v4 / v7 |
| **跨用户拿别人的 task** | Receiver 必须把 task 绑定到 auth context |
| **DoS（用户疯狂提交 task）** | 限流 + 每用户并发上限 + 强制 TTL 上限 |
| **资源泄漏** | 严格 TTL 清理；监控 task 队列长度 |

---

## 13. 常见坑

| 坑 | 排查 |
|----|------|
| **Client 没声明 tasks 能力却发 task 请求** | Server 应忽略 task 字段、按普通请求处理 |
| **Server 要求 required taskSupport 但 Client 不走 task** | Server 返回 -32601 |
| **轮询太频繁** | 遵守 `pollInterval`，别 100ms 一次 |
| **拿到 input_required 但不知道做什么** | 立即调 tasks/result 开渠道，等 elicitation/sampling |
| **TTL 过期后丢结果** | 程序设计要把"拿到结果就立刻处理掉"放优先级，别长时间不取 |

---

## 14. 下一步

- 📖 错误处理（Tasks 错误码 + 普通错误） → [08-errors-validation.md](./08-errors-validation.md)
- 📖 客户端 Sampling（task 的杀手锏场景） → 03-client/03-sampling
- 📖 远程部署（task 在多节点场景） → 05-production/01-remote-mcp

## 参考资料

- Tasks spec：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks
- SEP-1686 Tasks 提案：https://modelcontextprotocol.io/seps/1686-tasks
- Tools 中 taskSupport 字段：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
