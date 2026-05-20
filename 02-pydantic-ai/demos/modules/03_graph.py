"""
03_graph.py
===========
Pydantic Graph 实战 demo：发票处理工作流。

流程：
    [user msg] ──► Extract ──► Validate ─┬─► Save ──► Notify ──► [End]
                                  │
                                  └─► Reject ──► [End]

特点：
- 每个节点是 dataclass，继承 BaseNode
- 节点之间的连接靠 run() 的返回类型自动识别
- 没 API Key 时 Agent 自动 fallback 到 TestModel
- 末尾打印 mermaid_code 给非技术同事看

运行：
    python demos/modules/03_graph.py
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

load_dotenv()


# =====================================================================
# 业务数据模型
# =====================================================================
class Invoice(BaseModel):
    vendor: str = Field(description="供应商名")
    amount: float = Field(description="金额（人民币）")
    date: str = Field(description="日期 YYYY-MM-DD")


# =====================================================================
# 图共享 State
# =====================================================================
@dataclass
class State:
    raw: str
    invoice: Invoice | None = None
    saved_id: int | None = None
    log: list[str] = field(default_factory=list)


# =====================================================================
# 提取 Agent（或 fallback TestModel）
# =====================================================================
def build_extract_agent() -> Agent[None, Invoice]:
    if os.getenv("OPENAI_API_KEY"):
        return Agent(
            "openai:gpt-4o-mini",
            output_type=Invoice,
            system_prompt="从用户输入里提取发票字段。",
        )
    # TestModel 默认会按 schema 凭空造一个合法对象
    return Agent(TestModel(), output_type=Invoice)


extract_agent = build_extract_agent()


# =====================================================================
# 节点定义
# =====================================================================
@dataclass
class Extract(BaseNode[State]):
    """步骤 1：把自然语言提取成结构化 Invoice"""

    async def run(self, ctx: GraphRunContext[State]) -> Validate:
        r = await extract_agent.run(ctx.state.raw)
        ctx.state.invoice = r.output
        ctx.state.log.append(f"extracted: {r.output.model_dump()}")
        return Validate()


@dataclass
class Validate(BaseNode[State]):
    """步骤 2：校验金额合法性，分支到 Save 或 Reject"""

    async def run(self, ctx: GraphRunContext[State]) -> Save | Reject:
        inv = ctx.state.invoice
        if inv is None or inv.amount <= 0:
            ctx.state.log.append("validated: invalid amount")
            return Reject(reason=f"金额非正：{inv.amount if inv else 'N/A'}")
        if inv.amount > 100_000:
            ctx.state.log.append("validated: too large")
            return Reject(reason=f"金额超过 10w：{inv.amount}")
        ctx.state.log.append("validated: ok")
        return Save()


@dataclass
class Save(BaseNode[State]):
    """步骤 3：入库（假 DB）"""

    async def run(self, ctx: GraphRunContext[State]) -> Notify:
        # 假装写 DB
        ctx.state.saved_id = 42
        ctx.state.log.append(f"saved: id={ctx.state.saved_id}")
        return Notify()


@dataclass
class Notify(BaseNode[State, None, str]):
    """步骤 4a：成功 → 通知 → End"""

    async def run(self, ctx: GraphRunContext[State]) -> End[str]:
        msg = (
            f"✅ 已入库 id={ctx.state.saved_id}，"
            f"供应商={ctx.state.invoice.vendor}，金额={ctx.state.invoice.amount}"
        )
        ctx.state.log.append(f"notified: {msg}")
        return End(msg)


@dataclass
class Reject(BaseNode[State, None, str]):
    """步骤 4b：拒绝 → End"""

    reason: str

    async def run(self, ctx: GraphRunContext[State]) -> End[str]:
        msg = f"❌ 拒绝：{self.reason}"
        ctx.state.log.append(f"rejected: {self.reason}")
        return End(msg)


# =====================================================================
# 组装图
# =====================================================================
graph: Graph[State, None, str] = Graph(
    nodes=[Extract, Validate, Save, Notify, Reject],
)


# =====================================================================
# 运行
# =====================================================================
async def run_case(raw: str) -> None:
    state = State(raw=raw)
    result = await graph.run(Extract(), state=state)

    print(f"\n========== Input ==========\n{raw}")
    print(f"========== Output ==========\n{result.output}")
    print("========== Log ==========")
    for line in state.log:
        print(" -", line)


async def main() -> None:
    # Demo A：跑三个 case（一个会被 reject，两个成功）
    cases = [
        "发票：阿里云 2024-01-15 ¥1280",
        "发票：腾讯云 2024-02-20 ¥0",            # 金额 0，会被 Reject
        "采购小米键盘 2024-03-01 共计 ¥299",
    ]
    for c in cases:
        await run_case(c)

    # Demo B：导出 mermaid
    print("\n========== Mermaid 拓扑 ==========")
    print(graph.mermaid_code(start_node=Extract))


if __name__ == "__main__":
    asyncio.run(main())
