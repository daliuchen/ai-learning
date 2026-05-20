"""
04_models_providers.py
======================
模型与 Provider 演示：
1) 字符串简写 vs Model 对象
2) ModelSettings 控制温度、max_tokens
3) FallbackModel 自动降级
4) TestModel 与 FunctionModel
5) 自定义 base_url（OpenAI 兼容服务）
6) 同一段代码切多个模型

没有 API key 时只跑 TestModel / FunctionModel。

运行：
    python demos/basics/04_models_providers.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings

load_dotenv()


# ---------- 1) 字符串简写 vs Model 对象 ----------
def demo_two_ways() -> None:
    print("===== 1) 字符串 vs Model 对象 =====")

    if not os.getenv("OPENAI_API_KEY"):
        print("跳过：未设置 OPENAI_API_KEY\n")
        return

    # 字符串
    a = Agent("openai:gpt-4o-mini", system_prompt="一句话回答。")
    print("string :", a.run_sync("Python 是什么？").output)

    # Model 对象
    from pydantic_ai.models.openai import OpenAIChatModel

    b = Agent(OpenAIChatModel("gpt-4o-mini"), system_prompt="一句话回答。")
    print("object :", b.run_sync("Python 是什么？").output)
    print()


# ---------- 2) ModelSettings ----------
def demo_settings() -> None:
    print("===== 2) ModelSettings =====")

    model = "openai:gpt-4o-mini" if os.getenv("OPENAI_API_KEY") else TestModel()
    agent = Agent(
        model,
        model_settings=ModelSettings(temperature=0.0, max_tokens=100),
        system_prompt="用 1 句话回答。",
    )
    print(agent.run_sync("中国首都是哪里？").output)
    print()


# ---------- 3) FallbackModel ----------
def demo_fallback() -> None:
    print("===== 3) FallbackModel =====")
    from pydantic_ai.models.fallback import FallbackModel

    # 故意让"主"模型挂掉：用一个错误的 OpenAI Key
    try:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        bad_primary = OpenAIChatModel(
            "gpt-4o-mini",
            provider=OpenAIProvider(api_key="sk-invalid-on-purpose"),
        )
    except Exception:
        bad_primary = TestModel()

    backup = TestModel(custom_output_text="（兜底回答）")
    agent = Agent(FallbackModel(bad_primary, backup))
    # 主模型如果是 OpenAI，会因为 401 失败，但 401 属于鉴权错而非网络错
    # FallbackModel 默认只对网络/限流类降级，所以这里更可能直接报错
    # 真实场景请用主模型不可用作为触发点
    try:
        r = agent.run_sync("hi")
        print("output :", r.output)
    except Exception as e:
        print(f"FallbackModel 演示：主模型鉴权失败属于业务错不降级（{type(e).__name__}）")
    print()


# ---------- 4) TestModel / FunctionModel ----------
def demo_test_model() -> None:
    print("===== 4) TestModel & FunctionModel =====")

    # 4a) TestModel：自动生成假数据
    from pydantic import BaseModel

    class Joke(BaseModel):
        setup: str
        punchline: str

    agent_a = Agent(TestModel(), output_type=Joke)
    print("TestModel  :", agent_a.run_sync("讲笑话").output)

    # 4b) FunctionModel：自己写假模型逻辑
    def fake_call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        user = ""
        for m in messages:
            for p in m.parts:
                content = getattr(p, "content", "")
                if content:
                    user = content
        return ModelResponse(parts=[TextPart(content=f"[ECHO] {user}")])

    agent_b = Agent(FunctionModel(fake_call))
    print("FuncModel  :", agent_b.run_sync("你好世界").output)
    print()


# ---------- 5) 自定义 base_url（OpenAI 兼容） ----------
def demo_custom_baseurl() -> None:
    print("===== 5) 自定义 base_url（演示 Ollama 风格） =====")

    ollama_url = os.getenv("OLLAMA_BASE_URL")
    if not ollama_url:
        print("跳过：未设置 OLLAMA_BASE_URL（如 http://localhost:11434）\n")
        return

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(base_url=f"{ollama_url}/v1", api_key="ollama")
    model = OpenAIChatModel("qwen2.5:7b", provider=provider)
    try:
        agent = Agent(model, system_prompt="一句话回答。")
        print(agent.run_sync("地球围着谁转？").output)
    except Exception as e:
        print(f"Ollama 调用失败：{type(e).__name__}: {e}")
    print()


# ---------- 6) 一段代码切多个模型 ----------
def demo_multi_provider() -> None:
    print("===== 6) 同一段代码切多个模型 =====")
    candidates: list[object] = [TestModel(custom_output_text="（TestModel 输出）")]
    if os.getenv("OPENAI_API_KEY"):
        candidates.append("openai:gpt-4o-mini")
    if os.getenv("ANTHROPIC_API_KEY"):
        candidates.append("anthropic:claude-3-5-haiku-latest")

    for m in candidates:
        agent = Agent(m, system_prompt="只回答一句话。")
        try:
            out = agent.run_sync("Python 的发明者是谁？").output
        except Exception as e:
            out = f"[ERROR] {type(e).__name__}: {e}"
        label = m if isinstance(m, str) else type(m).__name__
        print(f"[{label}] → {out}")
    print()


def main() -> None:
    demo_two_ways()
    demo_settings()
    demo_fallback()
    demo_test_model()
    demo_custom_baseurl()
    demo_multi_provider()


if __name__ == "__main__":
    main()
