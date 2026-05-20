"""
02_evals.py
===========
Pydantic Evals 完整 demo：评测一个客服意图分类 Agent。

包含：
- 8 条 Case 的数据集
- 内置 EqualsExpected / MaxDuration evaluator
- 自定义 IsLowercase evaluator
- LLMJudge 评测"回答是否在允许枚举值内"
- 没 API Key 时自动 fallback 到 TestModel + 假分类函数

运行：
    python demos/modules/02_evals.py
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import (
    Evaluator,
    EvaluatorContext,
    EqualsExpected,
    MaxDuration,
)

load_dotenv()


# =====================================================================
# 自定义 Evaluator：检查输出是否全小写
# =====================================================================
@dataclass
class IsLowercase(Evaluator[str, str]):
    """输出必须全小写，否则 0 分"""

    def evaluate(self, ctx: EvaluatorContext[str, str]) -> bool:
        out = ctx.output if isinstance(ctx.output, str) else ""
        return out == out.lower() and out != ""


# =====================================================================
# 自定义 Evaluator：多分项打分
# =====================================================================
@dataclass
class FormatChecks(Evaluator[str, str]):
    """同时检查"非空 / 不超过 32 字符 / 是预定义类别"三个维度"""

    valid_labels: tuple[str, ...] = ("question", "complaint", "praise")

    def evaluate(self, ctx: EvaluatorContext[str, str]) -> dict[str, bool]:
        out = (ctx.output or "").strip()
        return {
            "non_empty": bool(out),
            "short_enough": len(out) <= 32,
            "valid_label": out.lower() in self.valid_labels,
        }


# =====================================================================
# 构造数据集
# =====================================================================
def build_dataset() -> Dataset[str, str]:
    cases = [
        Case(name="refund_slow",   inputs="退款怎么这么慢！",          expected_output="complaint"),
        Case(name="cs_praise",     inputs="你们家客服小姐姐真好。",     expected_output="praise"),
        Case(name="shipping_q",    inputs="请问周末发货吗？",          expected_output="question"),
        Case(name="bug_report",    inputs="App 打不开了，气死我了。",   expected_output="complaint"),
        Case(name="how_to",        inputs="怎么修改收货地址？",         expected_output="question"),
        Case(name="thanks",        inputs="昨天的售后处理得很赞！",     expected_output="praise"),
        Case(name="ambiguous",     inputs="嗯。",                       expected_output="question"),
        Case(name="invoice",       inputs="发票什么时候能寄出？",       expected_output="question"),
    ]

    evaluators: list = [
        EqualsExpected(),
        MaxDuration(seconds=5),
        IsLowercase(),
        FormatChecks(),
    ]

    # 如果有 API Key，再加上 LLMJudge
    if os.getenv("OPENAI_API_KEY"):
        from pydantic_evals.evaluators import LLMJudge

        evaluators.append(
            LLMJudge(
                rubric=(
                    "回答必须严格是 question / complaint / praise 三者之一（小写）。"
                    "完全匹配 → 1.0；包含但不只这一个词 → 0.5；都不是 → 0.0。"
                ),
                model="openai:gpt-4o-mini",
                include_input=True,
            )
        )

    return Dataset(name="intent_classifier", cases=cases, evaluators=evaluators)


# =====================================================================
# 被评测的任务（Agent or 假函数）
# =====================================================================
async def build_task():
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    if os.getenv("OPENAI_API_KEY"):
        agent = Agent(
            "openai:gpt-4o-mini",
            system_prompt=(
                "你是客服意图分类器。把用户消息分类成 question / complaint / praise "
                "三类之一。只输出小写英文类别名，不要其他任何字符。"
            ),
        )

        async def task(text: str) -> str:
            r = await agent.run(text)
            return r.output.strip().lower()

        return task, "openai:gpt-4o-mini"

    # ===== fallback：TestModel 永远回 "question" =====
    print("[info] OPENAI_API_KEY 未设置，fallback 到 TestModel（输出全是 question）")
    agent = Agent(TestModel(custom_output_text="question"))

    async def task(text: str) -> str:
        r = await agent.run(text)
        return r.output.strip().lower()

    return task, "TestModel"


# =====================================================================
# 入口
# =====================================================================
async def main() -> None:
    dataset = build_dataset()
    task, model_name = await build_task()

    print(f"===== 跑评测：model={model_name}，{len(dataset.cases)} 条 case =====\n")
    report = await dataset.evaluate(task)

    report.print(include_input=True, include_output=True)

    print("\n===== 平均分 =====")
    for name, avg in report.averages().items():
        print(f"  {name}: {avg}")


if __name__ == "__main__":
    asyncio.run(main())
