"""
03_thinking.py
==============
Pydantic AI Thinking：演示如何启用模型的"显式思考链"并取出 ThinkingPart。

1) 用统一开关 model_settings={"thinking": "high"} 启用思考
2) 从 result.all_messages() 里挑出 ThinkingPart
3) 对比"开 vs 关"在复杂推理题上的差异

没设置 API key 时自动 fallback 到 TestModel（TestModel 不会真思考，只演示 API）。

运行：
    python demos/advanced/03_thinking.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from pydantic_ai import Agent
from pydantic_ai.messages import TextPart, ThinkingPart
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_thinking_model() -> str | TestModel:
    """thinking 任务首选支持的型号"""
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-7-sonnet-latest"
    if os.getenv("OPENAI_API_KEY"):
        # o-series / gpt-5 系列才支持 reasoning
        return "openai:o4-mini"
    if os.getenv("GEMINI_API_KEY"):
        return "google-gla:gemini-2.0-flash-thinking-exp"
    print("[warn] 未检测到支持 thinking 的 API key，使用 TestModel。\n")
    return TestModel()


def pick_normal_model() -> str | TestModel:
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-haiku-latest"
    return TestModel()


HARD_QUESTION = (
    "若 a, b, c 都是质数，且 a^2 + b^2 = c^2，求所有满足条件的 (a, b, c)。"
)


def extract_parts(result) -> tuple[str, str]:
    """从 result 里分别拼出思考文本和回答文本"""
    thinking_text: list[str] = []
    answer_text: list[str] = []
    for msg in result.all_messages():
        for part in getattr(msg, "parts", []):
            if isinstance(part, ThinkingPart):
                thinking_text.append(part.content)
            elif isinstance(part, TextPart):
                answer_text.append(part.content)
    return "\n".join(thinking_text), "\n".join(answer_text)


# ----------------------------------------------------------------------------
# 1) 启用 Thinking
# ----------------------------------------------------------------------------
def demo_with_thinking() -> None:
    print("===== 1) 启用 Thinking =====")
    model = pick_thinking_model()
    agent = Agent(
        model,
        model_settings={"thinking": "high"},
        system_prompt="你是一位严谨的数学老师。",
    )
    try:
        result = agent.run_sync(HARD_QUESTION)
    except Exception as e:
        print(f"[skip] {e}")
        return

    thinking, answer = extract_parts(result)
    print(f"[思考长度] {len(thinking)} 字")
    if thinking:
        # 只打前 300 字，避免刷屏
        print(f"[思考片段] {thinking[:300]}...\n")
    print(f"[最终回答] {answer or result.output}")
    print(f"[usage] {result.usage()}\n")


# ----------------------------------------------------------------------------
# 2) 不启用 Thinking 对比
# ----------------------------------------------------------------------------
def demo_without_thinking() -> None:
    print("===== 2) 不启用 Thinking 对比 =====")
    model = pick_normal_model()
    agent = Agent(model, system_prompt="你是一位严谨的数学老师。")
    try:
        result = agent.run_sync(HARD_QUESTION)
    except Exception as e:
        print(f"[skip] {e}")
        return
    print(f"[回答] {result.output}")
    print(f"[usage] {result.usage()}\n")


# ----------------------------------------------------------------------------
# 3) Anthropic 原生设置（更精细控制）
# ----------------------------------------------------------------------------
def demo_anthropic_native() -> None:
    print("===== 3) Anthropic 原生 thinking 配置（仅当有 ANTHROPIC_API_KEY 时跑） =====")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[skip] 无 ANTHROPIC_API_KEY\n")
        return
    try:
        from pydantic_ai.models.anthropic import AnthropicModelSettings
    except ImportError:
        print("[skip] pydantic-ai-slim[anthropic] 未安装\n")
        return

    settings = AnthropicModelSettings(
        anthropic_thinking={"type": "enabled", "budget_tokens": 2048},
    )
    agent = Agent(
        "anthropic:claude-3-7-sonnet-latest",
        model_settings=settings,
        system_prompt="你是一位严谨的数学老师。",
    )
    try:
        result = agent.run_sync("用反证法证明 √2 是无理数")
    except Exception as e:
        print(f"[skip] {e}")
        return
    thinking, answer = extract_parts(result)
    print(f"[思考长度] {len(thinking)} 字")
    if thinking:
        print(f"[思考片段] {thinking[:300]}...\n")
    print(f"[最终回答] {answer or result.output}")
    print(f"[usage] {result.usage()}\n")


def main() -> None:
    demo_with_thinking()
    demo_without_thinking()
    demo_anthropic_native()


if __name__ == "__main__":
    main()
