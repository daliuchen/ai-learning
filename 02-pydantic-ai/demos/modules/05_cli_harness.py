"""
05_cli_harness.py
=================
clai CLI 与 Harness 风格 demo。

包含：
- Demo A：定义一个 Agent，演示 `agent.to_cli_sync()` 直接进交互
- Demo B：用 subprocess 调用 `clai -a ... "prompt"` 验证一次性模式
- Demo C：官方 harness 包 pydantic-ai-harness 的 Coder（工作区 + 文件工具 + shell）
- 没 API Key 时全部 fallback 到 TestModel

依赖：
    pip install "pydantic-ai-slim[openai,cli]" clai "pydantic-ai-harness[coder]"

运行：
    python demos/modules/05_cli_harness.py             # 默认跑 test
    python demos/modules/05_cli_harness.py cli         # Demo A：进 to_cli 交互
    python demos/modules/05_cli_harness.py subprocess  # Demo B：subprocess 调 clai
    python demos/modules/05_cli_harness.py harness     # Demo C：Coder harness 在临时工作区里写代码并运行
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
            "openai-chat:qwen-flash",
            instructions=(
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
        instructions="SQL 助手（TestModel）",
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
        print("       pip install clai   # 或 uv tool install clai（clai 现在是独立的包）")
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
# Demo C：官方 harness —— Coder = 工作区 + 文件工具 + shell + 仓库上下文 + 子运行 + 上下文控制
# =====================================================================
def demo_harness() -> None:
    import pathlib
    import tempfile

    from pydantic_ai.capabilities import LocalWorkspace
    from pydantic_ai_harness.coder import Coder

    workdir = tempfile.mkdtemp(prefix="coder-demo-")  # 一个临时目录当工作区，别直接对着你的仓库玩
    if os.getenv("OPENAI_API_KEY"):
        model = "openai-chat:qwen-flash"
    elif os.getenv("ANTHROPIC_API_KEY"):
        model = "anthropic:claude-haiku-4-5"
    else:
        print("[info] 没设 API Key，用 TestModel 演示 Harness 形状（它会机械地各调一次文件工具）")
        model = TestModel(call_tools=["write_file", "list_files"])

    # LocalWorkspace 不是沙箱：命令以你的身份在本机执行。生产里换 Docker / E2B / Modal 等沙箱 capability。
    coder = Agent(model, capabilities=[LocalWorkspace(workdir), Coder()])

    user_req = "在工作目录里写一个 fib.py，打印斐波那契数列前 10 项，然后用 python3 运行它确认输出正确。"
    print(f"[demo C] 工作区：{workdir}\n[demo C] 用户需求：{user_req}\n")
    result = coder.run_sync(user_req)
    print("===== Agent 产出 =====")
    print(result.output)
    print("\n===== 工作区里的文件 =====")
    for p in sorted(pathlib.Path(workdir).rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(workdir).parts):  # 跳过 harness 自己的 .pydantic-ai-harness 状态目录
            print(f"--- {p.relative_to(workdir)} ---")
            print(p.read_text(encoding="utf-8")[:400])


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
        "跑官方 Coder harness 请跑：\n"
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
