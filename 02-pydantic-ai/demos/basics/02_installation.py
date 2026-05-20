"""
02_installation.py
==================
验证 Pydantic AI 安装是否就绪：
1) 打印 pydantic / pydantic-ai 版本
2) 检查 Python 版本
3) 用 TestModel 跑一次"无网络 ping"
4) 如果有 API key，额外发一条真实请求

运行：
    python demos/basics/02_installation.py
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def print_versions() -> None:
    print("===== 版本信息 =====")
    print(f"Python      : {sys.version.split()[0]}")
    assert sys.version_info >= (3, 10), "Pydantic AI 需要 Python 3.10+"

    try:
        import pydantic

        print(f"pydantic    : {pydantic.VERSION}")
    except ImportError:
        print("[ERROR] pydantic 未安装，请 `pip install pydantic`")
        sys.exit(1)

    try:
        import pydantic_ai

        print(f"pydantic-ai : {pydantic_ai.__version__}")
    except ImportError:
        print("[ERROR] pydantic-ai 未安装，请 `pip install pydantic-ai`")
        sys.exit(1)

    # 可选包
    for opt in ("pydantic_evals", "pydantic_graph", "logfire"):
        try:
            mod = __import__(opt)
            ver = getattr(mod, "__version__", "unknown")
            print(f"{opt:<12}: {ver}")
        except ImportError:
            print(f"{opt:<12}: 未安装（可选）")
    print()


def ping_test_model() -> None:
    """无网络 ping：用 TestModel 验证流程通畅。"""
    print("===== TestModel ping =====")
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    agent = Agent(TestModel(), system_prompt="你是一位助手")
    result = agent.run_sync("hello")
    print(f"reply: {result.output!r}")
    print(f"usage: {result.usage()}")
    print()


def ping_real_model() -> None:
    print("===== 真实模型 ping =====")
    from pydantic_ai import Agent

    if os.getenv("OPENAI_API_KEY"):
        model = "openai:gpt-4o-mini"
    elif os.getenv("ANTHROPIC_API_KEY"):
        model = "anthropic:claude-3-5-haiku-latest"
    else:
        print("未检测到任何 API key，跳过真实请求。")
        print("（在 .env 里设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY 即可启用）")
        return

    print(f"使用模型：{model}")
    agent = Agent(model, system_prompt="一句话回答。")
    result = agent.run_sync("说出本月份的英文")
    print(f"reply: {result.output}")
    print(f"usage: {result.usage()}")
    print()


def main() -> None:
    print_versions()
    ping_test_model()
    ping_real_model()
    print("环境验证完成 ✓")


if __name__ == "__main__":
    main()
