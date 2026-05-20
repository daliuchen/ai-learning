"""
04_common_tools.py
==================
Pydantic AI Common Tools 演示：

1) duckduckgo_search_tool（需要 [duckduckgo] extra）
2) 调研 Agent：搜索 + 抓取 + 结构化输出
3) 自己写 "common tool" 工厂函数

如果环境没装 ddgs / 没网，会回退到 TestModel + 一个 fake_search_tool，
让脚本始终能跑完。

依赖（可选）：
    pip install "pydantic-ai-slim[duckduckgo,web-fetch]"

运行：
    python demos/tools/04_common_tools.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent, Tool
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model():
    if os.getenv('OPENAI_API_KEY'):
        return 'openai:gpt-4o-mini'
    return TestModel()


# ---------- 尝试导入官方 common tools，失败就 fallback ----------
def get_search_tool() -> Tool:
    try:
        from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
        return duckduckgo_search_tool()
    except Exception as e:
        print(f'[warn] duckduckgo_search_tool 不可用：{e}，使用 fake_search')

        def fake_search(query: str) -> list[dict]:
            """Search the web for the given query (fake fallback).

            Args:
                query: Search keywords.
            """
            return [
                {'title': f'Fake result 1 for {query}', 'url': 'https://example.com/1'},
                {'title': f'Fake result 2 for {query}', 'url': 'https://example.com/2'},
            ]

        return Tool(fake_search, takes_ctx=False, name='search')


def get_fetch_tool() -> Tool:
    try:
        from pydantic_ai.common_tools.web_fetch import web_fetch_tool
        return web_fetch_tool()
    except Exception as e:
        print(f'[warn] web_fetch_tool 不可用：{e}，使用 fake_fetch')

        def fake_fetch(url: str) -> str:
            """Fetch a URL and return markdown (fake fallback).

            Args:
                url: URL to fetch.
            """
            return f'# Fake page\n\nContent of {url}'

        return Tool(fake_fetch, takes_ctx=False, name='fetch')


# ---------- 调研 Agent ----------
class Report(BaseModel):
    """Research report"""
    summary: str
    key_points: list[str]
    sources: list[str]


def build_research_agent() -> Agent:
    return Agent(
        pick_model(),
        tools=[get_search_tool(), get_fetch_tool()],
        output_type=Report,
        instructions=(
            "You are a research assistant. "
            "1) Search the web for the topic. "
            "2) Optionally fetch 1-2 pages for details. "
            "3) Output a Report with summary, key_points, sources."
        ),
    )


# ---------- 演示：自己写一个 "common tool" 工厂 ----------
def fake_jira_search_tool(project: str | None = None) -> Tool:
    """工厂函数：固定 project 锁定，query 由 LLM 提供。"""
    fixed_project = project

    def jira_search(query: str) -> list[dict]:
        """Search Jira issues.

        Args:
            query: JQL or natural language query.
        """
        return [
            {'key': f'{fixed_project or "ABC"}-1', 'title': f'issue about {query}'},
            {'key': f'{fixed_project or "ABC"}-2', 'title': f'another about {query}'},
        ]

    return Tool(jira_search, takes_ctx=False, name='jira_search')


def main() -> None:
    print('========== 1. 调研 Agent ==========')
    agent = build_research_agent()
    result = agent.run_sync('Pydantic AI tool system overview')
    report = result.output
    if isinstance(report, Report):
        print('summary:', report.summary)
        print('key_points:', report.key_points)
        print('sources:', report.sources)
    else:
        print('output:', report)

    print('\n========== 2. 自定义工厂工具：fake jira_search ==========')
    jira_agent = Agent(
        pick_model(),
        tools=[fake_jira_search_tool(project='PYAI')],
        instructions='Use jira_search to find issues, then answer in 1 sentence.',
    )
    r2 = jira_agent.run_sync('Find issues related to "tool retry".')
    print(r2.output)


if __name__ == '__main__':
    main()
