"""
03_testing.py
=============
Pydantic AI 测试模式完整示例（pytest 风格）：
  * TestModel 冒烟
  * FunctionModel 模拟分支 / 工具调用 / 校验失败 + 重试
  * agent.override 替换 model / deps
  * 用 pytest fixture 把测试模板化

直接运行：
    pytest demos/patterns/03_testing.py -v
或：
    python demos/patterns/03_testing.py    # 也能跑（手动 main 函数）
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel


# ---------------------------------------------------------------------
# 被测对象
# ---------------------------------------------------------------------
@dataclass
class Deps:
    user_name: str


class Greeting(BaseModel):
    text: str = Field(description="问候语")
    excitement: int = Field(ge=0, le=10, description="兴奋程度 0-10")


agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=Deps,
    output_type=Greeting,
    system_prompt="生成一段问候语。如需用户的正式称呼，调用 fancy_name 工具。",
)


@agent.tool
async def fancy_name(ctx: RunContext[Deps]) -> str:
    """获取用户的正式称呼。"""
    return f"尊敬的 {ctx.deps.user_name} 大佬"


# ---------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------
@pytest.fixture
def deps() -> Deps:
    return Deps(user_name="alice")


# ---------------------------------------------------------------------
# 1) TestModel：冒烟
# ---------------------------------------------------------------------
def test_smoke_with_test_model(deps: Deps) -> None:
    """TestModel 会自动按 schema 编出占位数据。"""
    with agent.override(model=TestModel(), deps=deps):
        r = agent.run_sync("hi")
    assert isinstance(r.output, Greeting)
    # 兴奋程度受 Field 约束，TestModel 会给个合法值
    assert 0 <= r.output.excitement <= 10


def test_test_model_skip_tools(deps: Deps) -> None:
    """TestModel(call_tools=[]) 关闭自动工具调用。"""
    with agent.override(model=TestModel(call_tools=[]), deps=deps):
        r = agent.run_sync("hi")
    assert isinstance(r.output, Greeting)


def test_test_model_custom_output(deps: Deps) -> None:
    """直接指定输出字段，避免占位值不可控。"""
    with agent.override(
        model=TestModel(custom_output_args={"text": "你好 alice", "excitement": 8}),
        deps=deps,
    ):
        r = agent.run_sync("hi")
    assert r.output.text == "你好 alice"
    assert r.output.excitement == 8


# ---------------------------------------------------------------------
# 2) FunctionModel：完全控制 LLM 行为
# ---------------------------------------------------------------------
def _make_llm(plan: list[ModelResponse]):
    """构造一个按顺序返回 plan 中各 response 的 LLM 函数。"""
    idx = {"i": 0}

    def llm(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:  # noqa: ARG001
        i = idx["i"]
        idx["i"] = i + 1
        return plan[min(i, len(plan) - 1)]

    return llm


def test_function_model_uses_tool(deps: Deps) -> None:
    """模拟"先调工具，再产出最终结构化输出"两轮交互。"""
    plan = [
        ModelResponse(parts=[
            ToolCallPart(tool_name="fancy_name", args={}, tool_call_id="c1"),
        ]),
        ModelResponse(parts=[
            ToolCallPart(
                tool_name="final_result",
                args={"text": "你好 尊敬的 alice 大佬", "excitement": 9},
                tool_call_id="c2",
            ),
        ]),
    ]
    with agent.override(model=FunctionModel(_make_llm(plan)), deps=deps):
        r = agent.run_sync("hi")
    assert "alice" in r.output.text
    assert r.output.excitement == 9


def test_function_model_validates_and_retries(deps: Deps) -> None:
    """先返回非法 excitement (>10)，Pydantic 校验失败，Agent 应自动 ModelRetry。"""
    plan = [
        ModelResponse(parts=[
            ToolCallPart(
                tool_name="final_result",
                args={"text": "hi", "excitement": 99},  # 非法
                tool_call_id="bad",
            ),
        ]),
        ModelResponse(parts=[
            ToolCallPart(
                tool_name="final_result",
                args={"text": "hi", "excitement": 5},  # 合法
                tool_call_id="ok",
            ),
        ]),
    ]
    with agent.override(model=FunctionModel(_make_llm(plan)), deps=deps):
        r = agent.run_sync("hi")
    assert r.output.excitement == 5


def test_function_model_inspects_messages(deps: Deps) -> None:
    """断言模型确实收到了 system prompt + 用户消息。"""
    captured: dict = {}

    def llm(messages, info):
        captured["count"] = len(messages)
        captured["all"] = messages
        return ModelResponse(parts=[
            ToolCallPart(
                tool_name="final_result",
                args={"text": "ok", "excitement": 1},
                tool_call_id="x",
            ),
        ])

    with agent.override(model=FunctionModel(llm), deps=deps):
        agent.run_sync("我叫 bob，你好")

    assert captured["count"] >= 1
    flat = " ".join(
        str(p.content) for m in captured["all"] for p in m.parts if hasattr(p, "content")
    )
    assert "bob" in flat


# ---------------------------------------------------------------------
# 3) 参数化 + 多 case
# ---------------------------------------------------------------------
@pytest.mark.parametrize("name", ["alice", "bob", "carol"])
def test_parametrized(name: str) -> None:
    with agent.override(model=TestModel(), deps=Deps(user_name=name)):
        r = agent.run_sync("hi")
    assert isinstance(r.output, Greeting)


# ---------------------------------------------------------------------
# 4) 可直接 python 运行（不走 pytest）
# ---------------------------------------------------------------------
def _run_all() -> None:
    deps_obj = Deps(user_name="alice")
    test_smoke_with_test_model(deps_obj)
    test_test_model_skip_tools(deps_obj)
    test_test_model_custom_output(deps_obj)
    test_function_model_uses_tool(deps_obj)
    test_function_model_validates_and_retries(deps_obj)
    test_function_model_inspects_messages(deps_obj)
    for n in ["alice", "bob", "carol"]:
        test_parametrized(n)
    print("所有测试通过 ✓")


if __name__ == "__main__":
    _run_all()
