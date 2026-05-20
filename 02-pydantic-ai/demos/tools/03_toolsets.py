"""
03_toolsets.py
==============
Pydantic AI Toolset 演示：

1) FunctionToolset：打包一组工具
2) CombinedToolset + prefixed：合并并加前缀，避免冲突
3) filtered / renamed：动态过滤、改名
4) WrapperToolset：包一层日志钩子
5) agent.override(toolsets=...)：测试用 mock 替换

没 OPENAI_API_KEY 时切到 TestModel。

运行：
    python demos/tools/03_toolsets.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from pydantic_ai import (
    Agent,
    CombinedToolset,
    FunctionToolset,
    RunContext,
    WrapperToolset,
)
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model():
    if os.getenv('OPENAI_API_KEY'):
        return 'openai:gpt-4o-mini'
    return TestModel()


# ========== Toolset 1：数据库 ==========
db = FunctionToolset(
    instructions='Use DB tools for any account/order question.'
)


@db.tool_plain
def find_user(user_id: str) -> dict:
    """Find user by id.

    Args:
        user_id: User id like 'u-001'.
    """
    return {'id': user_id, 'name': 'Alice', 'plan': 'pro'}


@db.tool_plain
def delete_user(user_id: str) -> str:
    """Delete a user permanently (destructive!).

    Args:
        user_id: User id to delete.
    """
    return f'deleted {user_id}'


# ========== Toolset 2：搜索 ==========
search = FunctionToolset(
    instructions='Use search tools for current world events.'
)


@search.tool_plain
def web_search(query: str) -> str:
    """Search the web for a query.

    Args:
        query: Search keywords.
    """
    return f'[fake-result] top hit for "{query}"'


@search.tool_plain
def news_search(query: str) -> str:
    """Search news for a query.

    Args:
        query: News keywords.
    """
    return f'[fake-news] latest news about "{query}"'


# ========== 组合：加前缀、过滤掉危险操作给 guest ==========
combined = CombinedToolset([
    db.prefixed('db'),          # → db_find_user / db_delete_user
    search.prefixed('search'),  # → search_web_search / search_news_search
])


# guest 不允许调任何 delete_ 工具
def guest_filter(ctx: RunContext, td) -> bool:
    if getattr(ctx, 'deps', None) == 'guest' and 'delete' in td.name:
        return False
    return True


guest_safe = combined.filtered(guest_filter)


# ========== WrapperToolset：每次调用打 log ==========
class LoggingToolset(WrapperToolset):
    async def call_tool(self, name, tool_args, ctx, tool):
        print(f'  [LOG] >>> {name}({tool_args})')
        result = await super().call_tool(name, tool_args, ctx, tool)
        # 截断打印
        r_str = repr(result)
        print(f'  [LOG] <<< {name} -> {r_str[:80]}')
        return result


logged_ts = LoggingToolset(guest_safe)


# ========== Agent ==========
agent = Agent(
    pick_model(),
    deps_type=str,  # role: 'admin' / 'user' / 'guest'
    toolsets=[logged_ts],
    instructions='Use tools as needed.',
)


def show_tools(role: str) -> None:
    """打印当前角色看得到哪些工具（不真正 run）"""
    # 借助 TestModel 的"调用所有工具"行为粗略观察可见工具
    print(f'\n----- role={role} -----')
    result = agent.run_sync('please use all available tools', deps=role)
    print('Output:', result.output)
    calls = [
        p.tool_name
        for m in result.all_messages()
        for p in m.parts
        if type(p).__name__ == 'ToolCallPart'
    ]
    print('Tools the model used:', calls)


def demo_override() -> None:
    """测试场景：用 mock toolset 整组替换"""
    print('\n========== override（测试场景） ==========')
    mock_ts = FunctionToolset()

    @mock_ts.tool_plain
    def find_user(user_id: str) -> dict:
        """Mock find_user that always returns a fake user."""
        return {'id': user_id, 'name': 'MOCK_USER'}

    with agent.override(toolsets=[mock_ts]):
        r = agent.run_sync('find user u-999', deps='user')
        print('Output under override:', r.output)


def main() -> None:
    print('========== Toolset 组合 + 角色过滤 ==========')
    show_tools('guest')   # delete_* 应该被过滤
    show_tools('admin')   # 全开
    demo_override()


if __name__ == '__main__':
    main()
