"""
05_native_tools.py
==================
Pydantic AI Native Tools 演示：

1) WebSearchTool（Anthropic / OpenAI Responses）
2) CodeExecutionTool
3) 动态启用：NativeTool(callable)
4) 跨 provider 用 WebSearch capability fallback

注意：native tool 必须模型 provider 支持。
- 没 ANTHROPIC_API_KEY / OPENAI_API_KEY 时会跳过实际请求
- 自动 fallback 到 TestModel + 一个 function-tool 模拟搜索

运行：
    python demos/tools/05_native_tools.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

load_dotenv()


def has_anthropic() -> bool:
    return bool(os.getenv('ANTHROPIC_API_KEY'))


def has_openai_responses() -> bool:
    return bool(os.getenv('OPENAI_API_KEY'))


# ========== 1. WebSearchTool（Anthropic） ==========
def demo_anthropic_web_search() -> None:
    print('\n========== 1. Anthropic WebSearchTool ==========')
    if not has_anthropic():
        print('(skip) 没 ANTHROPIC_API_KEY')
        return
    try:
        from pydantic_ai import WebSearchTool
        from pydantic_ai.capabilities import NativeTool

        agent = Agent(
            'anthropic:claude-sonnet-4-6',
            capabilities=[NativeTool(WebSearchTool(max_uses=2))],
            instructions='Answer concisely with web search.',
        )
        r = agent.run_sync('Give me one sentence about the biggest AI news this week.')
        print('Output:', r.output)
        print('Native tool calls:', getattr(r.response, 'native_tool_calls', None))
    except Exception as e:
        print(f'(error) {e}')


# ========== 2. CodeExecutionTool ==========
def demo_code_execution() -> None:
    print('\n========== 2. CodeExecutionTool ==========')
    if not has_anthropic():
        print('(skip) 没 ANTHROPIC_API_KEY')
        return
    try:
        from pydantic_ai import CodeExecutionTool
        from pydantic_ai.capabilities import NativeTool

        agent = Agent(
            'anthropic:claude-sonnet-4-6',
            capabilities=[NativeTool(CodeExecutionTool())],
            instructions='Use code execution to answer numerical questions.',
        )
        r = agent.run_sync('Compute the factorial of 20 and tell me how many digits it has.')
        print('Output:', r.output)
    except Exception as e:
        print(f'(error) {e}')


# ========== 3. 动态启用 WebSearchTool ==========
def demo_dynamic_native_tool() -> None:
    print('\n========== 3. 动态 NativeTool（按 deps 启用） ==========')
    if not has_anthropic():
        print('(skip) 没 ANTHROPIC_API_KEY')
        return
    try:
        from pydantic_ai import WebSearchTool
        from pydantic_ai.capabilities import NativeTool

        async def prepared(ctx: RunContext[dict]) -> WebSearchTool | None:
            if not ctx.deps.get('enable_search'):
                return None
            return WebSearchTool(max_uses=2)

        agent = Agent(
            'anthropic:claude-sonnet-4-6',
            capabilities=[NativeTool(prepared)],
            deps_type=dict,
            instructions='Use web search when allowed.',
        )

        r1 = agent.run_sync('Who won the Nobel Physics 2025?', deps={'enable_search': False})
        print('Without search:', r1.output)
        r2 = agent.run_sync('Who won the Nobel Physics 2025?', deps={'enable_search': True})
        print('With search:', r2.output)
    except Exception as e:
        print(f'(error) {e}')


# ========== 4. 跨 provider：WebSearch capability ==========
def demo_capability_fallback() -> None:
    """
    WebSearch capability 自动选 native or common tool。
    没 key 时把 model 换 TestModel，用一个 fake function tool 兜底，
    展示同一套业务代码"换模型零改动"。
    """
    print('\n========== 4. 跨 provider：capability 自动 fallback ==========')
    try:
        from pydantic_ai.capabilities import WebSearch

        if has_anthropic():
            model = 'anthropic:claude-sonnet-4-6'
        elif has_openai_responses():
            model = 'openai-responses:gpt-5.2'
        else:
            # 没 key → TestModel + 模拟工具
            model = TestModel()

        agent = Agent(
            model,
            capabilities=[WebSearch()] if model != TestModel() else [],
            instructions='Try to search the web when needed.',
        )

        if isinstance(model, TestModel):
            # 没 native 能力，加一个本地 function tool 来兜底演示
            @agent.tool_plain
            def fake_search(query: str) -> str:
                """Fallback search tool when no provider is available.

                Args:
                    query: search keywords
                """
                return f'[fake] result for {query}'

        r = agent.run_sync('What is Pydantic AI?')
        print('Output:', r.output)
    except Exception as e:
        print(f'(error) {e}')


def main() -> None:
    demo_anthropic_web_search()
    demo_code_execution()
    demo_dynamic_native_tool()
    demo_capability_fallback()


if __name__ == '__main__':
    main()
