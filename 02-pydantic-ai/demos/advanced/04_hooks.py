"""
04_hooks.py
===========
Pydantic AI Hooks：用钩子做日志、token 预算、限流、缓存四个典型切面。

1) before_run / after_run：基本审计
2) before_model_request：发请求前打 trace
3) after_model_request：累计 token 检查预算
4) wrap_model_request：内存缓存（同一 prompt 不重复打 LLM）
5) before_tool_execute：工具调用前打日志 + PII 脱敏示意

没设置 API key 时自动 fallback 到 TestModel（钩子完整触发，便于看流程）。

运行：
    python demos/advanced/04_hooks.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model() -> str | TestModel:
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-haiku-latest"
    print("[warn] 未检测到 API key，使用 TestModel（钩子仍会触发）。\n")
    return TestModel()


# ----------------------------------------------------------------------------
# 一些"全局状态"，生产里应该用 redis / db
# ----------------------------------------------------------------------------
_response_cache: dict[str, Any] = {}
_audit_log: list[dict[str, Any]] = []
PII_RE = re.compile(r"\d{11}|\d{18}|\d{17}[\dX]")
TOKEN_BUDGET = 50_000


# ----------------------------------------------------------------------------
# 1) 构造一个收集所有钩子的 Hooks 对象
# ----------------------------------------------------------------------------
def build_hooks() -> Hooks:
    hooks = Hooks()

    # ----- 1.1 Run 生命周期：审计 -----
    @hooks.on.before_run
    async def on_start(ctx: RunContext[None]) -> None:
        _audit_log.append({"kind": "run_start", "ts": time.time()})
        print(f"  [hook] before_run")

    @hooks.on.after_run
    async def on_done(ctx: RunContext[None]) -> None:
        _audit_log.append(
            {
                "kind": "run_end",
                "ts": time.time(),
                "tokens": ctx.usage.total_tokens,
            }
        )
        print(f"  [hook] after_run  total_tokens={ctx.usage.total_tokens}")

    # ----- 1.2 模型请求级：trace + token 预算 -----
    @hooks.on.before_model_request
    async def trace_request(ctx, request_context):
        # PII 脱敏：发出前清洗 user 消息
        for msg in request_context.messages:
            for part in getattr(msg, "parts", []):
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    cleaned = PII_RE.sub("[REDACTED]", content)
                    if cleaned != content:
                        part.content = cleaned
                        print(f"  [hook] scrubbed PII from user prompt")
        print(f"  [hook] before_model_request")
        return request_context

    @hooks.on.after_model_request
    async def check_budget(ctx, *, request_context, response):
        used = ctx.usage.total_tokens
        if used > TOKEN_BUDGET:
            raise RuntimeError(f"超出 token 预算：{used} > {TOKEN_BUDGET}")
        print(f"  [hook] after_model_request  used={used}/{TOKEN_BUDGET}")
        return response

    # ----- 1.3 wrap_model_request：缓存 -----
    @hooks.on.model_request
    async def cache_request(ctx, *, request_context, handler):
        try:
            key_src = json.dumps(
                [m.model_dump(mode="json") for m in request_context.messages],
                default=str,
                sort_keys=True,
            )
        except Exception:
            key_src = str(request_context.messages)
        key = hashlib.md5(key_src.encode()).hexdigest()
        if key in _response_cache:
            print(f"  [hook] cache HIT  key={key[:8]}")
            return _response_cache[key]
        print(f"  [hook] cache MISS key={key[:8]}")
        response = await handler(request_context)
        _response_cache[key] = response
        return response

    # ----- 1.4 工具执行级：trace + 限流示意 -----
    @hooks.on.before_tool_execute
    async def trace_tool(ctx, *, call, tool_def, args):
        print(f"  [hook] before_tool_execute  tool={call.tool_name} args={args}")
        _audit_log.append(
            {"kind": "tool_call", "tool": call.tool_name, "args": dict(args)}
        )
        return args

    @hooks.on.after_tool_execute
    async def trace_tool_done(ctx, *, call, tool_def, result):
        print(f"  [hook] after_tool_execute   tool={call.tool_name}")
        return result

    # ----- 1.5 错误兜底 -----
    @hooks.on.model_request_error
    async def recover(ctx, *, request_context, error):
        print(f"  [hook] model_request_error: {type(error).__name__}: {error}")
        raise error  # 默认行为，保留错误传播

    return hooks


# ----------------------------------------------------------------------------
# 2) demo：跑两次同一 prompt，第二次走缓存
# ----------------------------------------------------------------------------
def demo_cached_runs() -> None:
    print("===== 1) 两次同 prompt，演示缓存 =====")
    model = pick_model()
    hooks = build_hooks()
    agent = Agent(model, capabilities=[hooks], system_prompt="一句话回答。")

    print("--- 第一次 ---")
    r1 = agent.run_sync("Python 的 GIL 是什么？")
    print(f"  output: {r1.output}\n")

    print("--- 第二次（应当命中缓存）---")
    r2 = agent.run_sync("Python 的 GIL 是什么？")
    print(f"  output: {r2.output}\n")


# ----------------------------------------------------------------------------
# 3) demo：带工具 + PII 脱敏
# ----------------------------------------------------------------------------
def demo_tool_with_hooks() -> None:
    print("===== 2) 工具调用 + PII 脱敏 =====")
    model = pick_model()
    hooks = build_hooks()
    agent = Agent(model, capabilities=[hooks], system_prompt="你是天气助手。")

    @agent.tool_plain
    def get_weather(city: str) -> str:
        """查询城市天气"""
        fake = {"北京": "晴 26°C", "上海": "多云 24°C"}
        return fake.get(city, "未知")

    # prompt 里包含手机号，会被钩子脱敏后再发模型
    r = agent.run_sync("我是 13800138000，帮我查北京和上海的天气")
    print(f"  output: {r.output}\n")


# ----------------------------------------------------------------------------
# 4) demo：审计日志
# ----------------------------------------------------------------------------
def demo_audit_dump() -> None:
    print("===== 3) 审计日志条目 =====")
    for entry in _audit_log:
        print(f"  {entry}")
    print()


def main() -> None:
    demo_cached_runs()
    demo_tool_with_hooks()
    demo_audit_dump()


if __name__ == "__main__":
    main()
