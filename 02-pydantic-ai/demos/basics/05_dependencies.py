"""
05_dependencies.py
==================
Pydantic AI 依赖注入 demo：
1) 最小例子：deps_type + RunContext
2) 动态系统提示拿 deps
3) 复合依赖（Infra + RequestCtx）
4) 用 override 在测试中替换 deps
5) output_validator 里用 deps 做内容审查

没有 API key 时使用 TestModel。

运行：
    python demos/basics/05_dependencies.py
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model():
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-haiku-latest"
    print("[warn] 未检测到 API key，使用 TestModel\n")
    return TestModel()


MODEL = pick_model()


# ---------- 1) 最小例子 ----------
@dataclass
class WeatherDeps:
    api_key: str
    fake_db: dict[str, str]


def demo_basic() -> None:
    print("===== 1) 最小依赖注入 =====")
    agent = Agent(MODEL, deps_type=WeatherDeps, system_prompt="你是一位天气助手。")

    @agent.tool
    def get_weather(ctx: RunContext[WeatherDeps], city: str) -> str:
        """查询城市天气"""
        # 用 deps 里的"DB"
        return ctx.deps.fake_db.get(city, "未知")

    deps = WeatherDeps(
        api_key="fake",
        fake_db={"北京": "晴 26°C", "上海": "雨 18°C"},
    )
    print(agent.run_sync("北京和上海哪个更暖？", deps=deps).output)
    print()


# ---------- 2) 动态系统提示拿 deps ----------
@dataclass
class CustomerDeps:
    user_id: str
    db: dict


def demo_dynamic_prompt() -> None:
    print("===== 2) 动态系统提示用 deps =====")
    agent = Agent(MODEL, deps_type=CustomerDeps, system_prompt="你是一位电商客服。")

    @agent.system_prompt
    def add_user(ctx: RunContext[CustomerDeps]) -> str:
        user = ctx.deps.db["users"].get(ctx.deps.user_id, {})
        return f"当前用户：{user.get('name', '未知')}，VIP 等级：{user.get('vip', 0)}"

    @agent.tool
    def query_orders(ctx: RunContext[CustomerDeps]) -> list[dict]:
        """查询当前用户的订单"""
        return ctx.deps.db["orders"].get(ctx.deps.user_id, [])

    deps = CustomerDeps(
        user_id="u-001",
        db={
            "users": {"u-001": {"name": "刘晨", "vip": 3}},
            "orders": {"u-001": [{"id": "o-1", "item": "键盘", "status": "已发货"}]},
        },
    )
    print(agent.run_sync("我最近买了啥？", deps=deps).output)
    print()


# ---------- 3) 复合依赖 ----------
@dataclass
class Infra:
    cache: dict = field(default_factory=dict)


@dataclass
class RequestCtx:
    infra: Infra
    user_id: str


def demo_composite_deps() -> None:
    print("===== 3) 复合依赖 =====")
    agent = Agent(MODEL, deps_type=RequestCtx, system_prompt="你是一位助手。")

    @agent.tool
    def get_balance(ctx: RunContext[RequestCtx]) -> str:
        """查询当前用户余额"""
        key = f"balance:{ctx.deps.user_id}"
        if key in ctx.deps.infra.cache:
            return f"{ctx.deps.infra.cache[key]} 元（缓存）"
        # 模拟查询
        ctx.deps.infra.cache[key] = 1234
        return "1234 元"

    infra = Infra()  # 全进程共享
    # 第一次请求
    deps_a = RequestCtx(infra=infra, user_id="u-001")
    print("first :", agent.run_sync("我余额多少？", deps=deps_a).output)
    # 第二次请求（共享 Infra，命中缓存）
    deps_b = RequestCtx(infra=infra, user_id="u-001")
    print("second:", agent.run_sync("再查一次余额", deps=deps_b).output)
    print()


# ---------- 4) override 替换 deps ----------
@dataclass
class AppDeps:
    db: dict


app_agent = Agent(MODEL, deps_type=AppDeps, system_prompt="你是一位查询助手。")


@app_agent.tool
def lookup(ctx: RunContext[AppDeps], key: str) -> str:
    """根据 key 查表"""
    return ctx.deps.db.get(key, "not found")


async def application_code(prompt: str, deps: AppDeps) -> str:
    r = await app_agent.run(prompt, deps=deps)
    return r.output


async def demo_override() -> None:
    print("===== 4) override 替换 deps =====")
    real_deps = AppDeps(db={"orange": "橙子"})
    fake_deps = AppDeps(db={"orange": "[TEST] 这是测试值"})

    # 真实调用
    print("real :", await application_code("查 orange", real_deps))

    # 测试覆盖
    with app_agent.override(deps=fake_deps):
        print("test :", await application_code("查 orange", real_deps))
    print()


# ---------- 5) output_validator 用 deps ----------
class Answer(BaseModel):
    text: str


@dataclass
class ModerationDeps:
    blocked_words: set[str]


def demo_validator() -> None:
    print("===== 5) output_validator 用 deps =====")
    agent = Agent(
        MODEL,
        deps_type=ModerationDeps,
        output_type=Answer,
        system_prompt="生成一段对产品的友好评价。",
        retries=2,
    )

    @agent.output_validator
    def moderate(ctx: RunContext[ModerationDeps], output: Answer) -> Answer:
        for w in ctx.deps.blocked_words:
            if w in output.text:
                raise ModelRetry(f"输出包含敏感词 {w!r}，请换一种说法")
        return output

    deps = ModerationDeps(blocked_words={"垃圾", "差评"})
    print(agent.run_sync("帮我写一段对一款打印机的评价", deps=deps).output)
    print()


async def main_async() -> None:
    demo_basic()
    demo_dynamic_prompt()
    demo_composite_deps()
    await demo_override()
    demo_validator()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
