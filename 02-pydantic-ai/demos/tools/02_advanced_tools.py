"""
02_advanced_tools.py
====================
Pydantic AI 高级工具特性演示：

1) prepare 钩子：按角色启用工具
2) Annotated + Field 约束参数
3) ToolReturn：三层返回（return_value / content / metadata）
4) ModelRetry：校验失败让模型改

没 OPENAI_API_KEY 时切到 TestModel，无网也能跑。
TestModel 会调用每个可见工具，便于看 prepare 是否按预期过滤。

运行：
    python demos/tools/02_advanced_tools.py
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field

from pydantic_ai import (
    Agent,
    BinaryContent,
    ModelRetry,
    RunContext,
    ToolDefinition,
    ToolReturn,
)
from pydantic_ai.models.test import TestModel

load_dotenv()


# ---------- 依赖 ----------
@dataclass
class UserCtx:
    user_id: str
    role: str  # 'admin' / 'user' / 'guest'


def pick_model():
    if os.getenv('OPENAI_API_KEY'):
        return 'openai:gpt-4o-mini'
    return TestModel()


agent = Agent(
    pick_model(),
    deps_type=UserCtx,
    instructions='You are a permission-aware assistant. Use tools when needed.',
)


# ---------- prepare 钩子 1：管理员独占 ----------
async def admin_only(
    ctx: RunContext[UserCtx], td: ToolDefinition
) -> ToolDefinition | None:
    return td if ctx.deps.role == 'admin' else None


# ---------- prepare 钩子 2：访客屏蔽 + 按角色改 description ----------
async def hide_for_guest(
    ctx: RunContext[UserCtx], td: ToolDefinition
) -> ToolDefinition | None:
    if ctx.deps.role == 'guest':
        return None
    return replace(td, description=f'[{ctx.deps.role}] {td.description}')


# ---------- 工具 1：仅管理员可见 ----------
@agent.tool(prepare=admin_only)
def delete_user(ctx: RunContext[UserCtx], target_id: str) -> str:
    """Permanently delete a user. ADMIN ONLY.

    Args:
        target_id: User id to delete.
    """
    return f'Deleted {target_id} (by {ctx.deps.user_id})'


# ---------- 工具 2：访客看不见 ----------
@agent.tool(prepare=hide_for_guest)
def list_my_orders(ctx: RunContext[UserCtx]) -> list[dict]:
    """List the current user's orders."""
    return [{'order_id': 1, 'user': ctx.deps.user_id}]


# ---------- 工具 3：Annotated + Field 参数约束 + ModelRetry ----------
@agent.tool_plain
def create_post(
    title: Annotated[str, Field(min_length=1, max_length=80, description='Post title')],
    rating: Annotated[int, Field(ge=1, le=5, description='Rating 1-5')],
    tags: Annotated[list[str], Field(max_length=5, description='Up to 5 tags')] = [],
) -> dict:
    """Create a blog post with constraints baked into the schema."""
    if title.lower().startswith('spam'):
        raise ModelRetry("Title starts with 'spam', try a more meaningful one.")
    return {'title': title, 'rating': rating, 'tags': tags}


# ---------- 工具 4：ToolReturn 三层返回 ----------
@agent.tool_plain
def click_and_capture(x: int, y: int) -> ToolReturn:
    """Simulate a UI click and return before/after screenshots.

    Args:
        x: X coordinate.
        y: Y coordinate.
    """
    fake_png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
    return ToolReturn(
        return_value=f'Clicked at ({x}, {y})',
        content=[
            'Screenshot before click:',
            BinaryContent(data=fake_png, media_type='image/png'),
            'Screenshot after click:',
            BinaryContent(data=fake_png, media_type='image/png'),
        ],
        metadata={'coordinates': {'x': x, 'y': y}, 'duration_ms': 42},
    )


def run_for_role(role: str) -> None:
    deps = UserCtx(user_id='u-001', role=role)
    print(f'\n========== role={role} ==========')
    result = agent.run_sync(
        'I want to: list my orders, delete user 42, and create a post titled "Hello" rating 5.',
        deps=deps,
    )
    print('Output:', result.output)
    # 看看模型实际能用哪些工具
    tool_calls = [
        p.tool_name
        for m in result.all_messages()
        for p in m.parts
        if type(p).__name__ == 'ToolCallPart'
    ]
    print('Tool calls:', tool_calls)


def demo_tool_return() -> None:
    print('\n========== ToolReturn 三层返回 ==========')
    deps = UserCtx(user_id='u-001', role='user')
    result = agent.run_sync('Click at coordinates (100, 200).', deps=deps)
    print('Output:', result.output)
    # 业务侧能读 metadata，但模型读不到
    for m in result.all_messages():
        for p in m.parts:
            if type(p).__name__ == 'ToolReturnPart' and p.tool_name == 'click_and_capture':
                print('  metadata seen by app:', getattr(p, 'metadata', None))


def main() -> None:
    # prepare 钩子按角色过滤工具
    for role in ('guest', 'user', 'admin'):
        run_for_role(role)
    # ToolReturn
    demo_tool_return()


if __name__ == '__main__':
    main()
