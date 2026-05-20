"""
05_cli_harness.py
=================
clai CLI 与 Harness 风格 demo。

包含：
- Demo A：定义一个 Agent，演示 `agent.to_cli_sync()` 直接进交互
- Demo B：用 subprocess 调用 `clai -a ... "prompt"` 验证一次性模式
- Demo C：极简 Harness——一个会写 Pydantic AI 代码的 coding agent
- 没 API Key 时全部 fallback 到 TestModel

运行：
    python demos/modules/05_cli_harness.py             # 默认跑 test
    python demos/modules/05_cli_harness.py cli         # Demo A：进 to_cli 交互
    python demos/modules/05_cli_harness.py subprocess  # Demo B：subprocess 调 clai
    python demos/modules/05_cli_harness.py harness     # Demo C：让 Agent 写 Pydantic AI 代码
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

load_dotenv()


# =====================================================================
# 共享：构造一个 SQL 助手 Agent（作为 clai 加载的目标）
# =====================================================================
def _make_sql_agent() -> Agent:
    if os.getenv("OPENAI_API_KEY"):
        return Agent(
            "openai:gpt-4o-mini",
            system_prompt=(
                "你是 SQL 助手。用户给你自然语言需求，你只输出合法 SQL。"
                "假设有 orders(id, user_id, gmv, created_at) 和 products(id, name) 两张表。"
            ),
        )
    return Agent(
        TestModel(
            custom_output_text=(
                "SELECT p.name, SUM(o.gmv) AS gmv "
                "FROM orders o JOIN products p ON o.id = p.id "
                "WHERE o.created_at >= date_trunc('month', now() - interval '1 month') "
                "GROUP BY p.name ORDER BY gmv DESC LIMIT 10;"
            )
        ),
        system_prompt="SQL 助手（TestModel）",
    )


# 模块级变量，供 `clai -a 05_cli_harness:agent` 加载
agent = _make_sql_agent()


# =====================================================================
# Demo A：直接 to_cli_sync 进交互
# =====================================================================
def demo_cli() -> None:
    print("[demo A] 把 SQL Agent 直接喂进 clai 风格交互模式。")
    print("[hint] 输入 /exit 退出；可以问『上个月 GMV top 10 商品』。\n")
    # to_cli_sync 是 Pydantic AI 内置的"把 Agent 转成 clai 交互"的入口
    if hasattr(agent, "to_cli_sync"):
        agent.to_cli_sync()
    else:
        print("[skip] 当前版本 Pydantic AI 没有 to_cli_sync，请升级到最新版")


# =====================================================================
# Demo B：用 subprocess 调 clai 一次性模式
# =====================================================================
def demo_subprocess() -> None:
    if shutil.which("clai") is None:
        print("[skip] 本机没装 clai。安装方式：")
        print("       pip install 'pydantic-ai[cli]'   # 或 uv tool install clai")
        return

    # 注意：这里直接调系统里的 clai，把当前文件的 agent 变量传进去
    cmd = [
        "clai",
        "-a", f"{__name__.replace('.', '/')}:agent" if "/" in __name__ else "05_cli_harness:agent",
        "找出上个月 GMV top 10 商品",
    ]
    print(f"[demo B] 调 clai 一次性模式:\n  $ {' '.join(cmd)}\n")
    # 让子进程能 import 到当前文件
    env = {**os.environ, "PYTHONPATH": os.path.dirname(os.path.abspath(__file__))}
    try:
        result = subprocess.run(cmd, env=env, check=False, capture_output=True, text=True)
        print("----- stdout -----")
        print(result.stdout)
        if result.stderr:
            print("----- stderr -----")
            print(result.stderr)
    except FileNotFoundError:
        print("[skip] clai 不可执行")


# =====================================================================
# Demo C：极简 Harness——让 Agent 写 Pydantic AI 代码
# =====================================================================
HARNESS_SYSTEM_PROMPT = """\
你是 Pydantic AI 资深工程师。用户描述需求时，你**直接产出一段可运行的 Python 代码**，
遵循以下规范：

1. 必须 `from pydantic_ai import Agent`
2. 结构化输出必须用 Pydantic BaseModel
3. 工具用 @agent.tool_plain（不依赖上下文）或 @agent.tool（需要 ctx: RunContext[...]）
4. model 字符串形如 "openai:gpt-4o-mini"
5. 入口写 `if __name__ == "__main__":`，里面用 `run_sync(...)` 演示
6. 不要解释，只给代码块
"""


def demo_harness() -> None:
    if os.getenv("OPENAI_API_KEY"):
        coder = Agent(
            "openai:gpt-4o-mini",
            system_prompt=HARNESS_SYSTEM_PROMPT,
        )
    elif os.getenv("ANTHROPIC_API_KEY"):
        coder = Agent(
            "anthropic:claude-3-5-haiku-latest",
            system_prompt=HARNESS_SYSTEM_PROMPT,
        )
    else:
        print("[info] 没设 API Key，用 TestModel 演示 Harness 形状（输出是 stub）")
        coder = Agent(
            TestModel(
                custom_output_text=(
                    "```python\n"
                    "from pydantic import BaseModel\n"
                    "from pydantic_ai import Agent\n\n"
                    "class Weather(BaseModel):\n"
                    "    city: str\n"
                    "    temp: float\n"
                    "    condition: str\n\n"
                    'agent = Agent("openai:gpt-4o-mini", output_type=Weather)\n\n'
                    "@agent.tool_plain\n"
                    "def get_temp(city: str) -> float:\n"
                    "    \"\"\"返回某城市的气温\"\"\"\n"
                    "    return {'北京': 22.0, '上海': 25.0}.get(city, 20.0)\n\n"
                    'if __name__ == "__main__":\n'
                    '    print(agent.run_sync("北京天气怎么样？").output)\n'
                    "```"
                )
            ),
            system_prompt=HARNESS_SYSTEM_PROMPT,
        )

    user_req = "写一个查天气的 Pydantic AI Agent，输出包含城市、气温、天气状况"
    print(f"[demo C] 用户需求：{user_req}\n")
    result = coder.run_sync(user_req)
    print("===== Agent 产出 =====")
    print(result.output)


# =====================================================================
# 默认入口
# =====================================================================
def demo_test() -> None:
    """默认跑一次最小可验证流程：调 sql agent 一次"""
    r = agent.run_sync("最近一个月 GMV 最高的 3 个商品")
    print("===== SQL Agent 输出 =====")
    print(r.output)
    print(
        "\n[hint] 真要进交互模式请跑：\n"
        "  python demos/modules/05_cli_harness.py cli\n"
        "用 clai 命令行调试请跑：\n"
        "  python demos/modules/05_cli_harness.py subprocess\n"
        "用 Harness 风格写代码请跑：\n"
        "  python demos/modules/05_cli_harness.py harness"
    )


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    {
        "cli": demo_cli,
        "subprocess": demo_subprocess,
        "harness": demo_harness,
        "test": demo_test,
    }.get(mode, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
