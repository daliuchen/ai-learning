"""长任务：官方 Python SDK 2.3 还没实现 Tasks 扩展，这是目前可用的「Server 铸造的句柄」模式

做法：start_job 立即返回 job_id（句柄），get_job 轮询状态，cancel_job 取消。
这正是 2026-07-28 规范推荐的跨调用状态做法：「Server 铸造显式句柄，作为普通工具参数传递」。

跑法（进程内自测）:
    python demos/server/07_long_job_handle.py
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from mcp import Client
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


async def main() -> None:
    async with Client(mcp) as client:
        r = await client.call_tool("start_render", {"input_url": "x", "steps": 5})
        jid = r.structured_content["job_id"]
        print("提交:", r.structured_content["status"])
        while True:  # 客户端 / Agent 的轮询循环
            s = (await client.call_tool("get_render_status", {"job_id": jid})).structured_content
            print(f"  轮询: {s['status']} {s['progress']}%")
            if s["status"] != "working":
                break
            await asyncio.sleep(s["poll_after_ms"] / 1000 / 5)  # 演示加速
        print("结果:", s["result"])

        r = await client.call_tool("start_render", {"input_url": "y", "steps": 50})
        jid2 = r.structured_content["job_id"]
        c = (await client.call_tool("cancel_render", {"job_id": jid2})).structured_content
        print("取消后:", c["status"])
        bad = await client.call_tool("get_render_status", {"job_id": "guess"})
        print("猜 id:", bad.is_error, bad.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
