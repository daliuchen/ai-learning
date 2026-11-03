"""
07_retries_http.py
==================
HTTP 重试 + ModelRetry + FallbackModel：构建抗抖动 + 抗幻觉的 Agent。

涵盖：
  1) 工具里 raise ModelRetry，让模型自愈
  2) output_validator 触发的重试
  3) 在工具里读 ctx.retry 计数
  4) AsyncTenacityTransport 配置（演示构造，不真发请求）
  5) FallbackModel：主备切换
  6) UsageLimits 防止重试失控
  7) 没 key 时用 TestModel 跑前三个 demo

运行：
    python demos/advanced/07_retries_http.py
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

load_dotenv()


def has_key(env: str) -> bool:
    return bool(os.getenv(env))


def scripted(steps: list[tuple[str, dict]]) -> FunctionModel:
    """按剧本依次调用工具，最后回一句话：用 FunctionModel 模拟「第一次写错、第二次改对」的模型。"""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        n = sum(isinstance(m, ModelResponse) for m in messages)  # 已经说过几轮
        if n < len(steps):
            name, args = steps[n]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart('完成')])

    return FunctionModel(fn)


def pick_model():
    if has_key('OPENAI_API_KEY'):
        return 'openai-chat:qwen-flash'
    print('[INFO] OPENAI_API_KEY 未设置，使用 TestModel 演示')
    return None  # 各 demo 自己决定 TestModel 行为


# ============================================================
# 1) 工具 raise ModelRetry，让模型自愈
# ============================================================
@dataclass
class DatabaseConn:
    users: dict[str, int]


def demo_tool_model_retry():
    print('\n===== 1) 工具里 raise ModelRetry =====')

    deps = DatabaseConn(users={'Alice Wang': 1, 'Bob Liu': 2})

    if has_key('OPENAI_API_KEY'):
        agent = Agent('openai-chat:qwen-flash', deps_type=DatabaseConn, retries=3)
    else:
        # 没 key：脚本化模型第一次传 'Alice'（会被拒），收到 ModelRetry 反馈后改传全名
        model = scripted([
            ('get_user_by_name', {'name': 'Alice'}),
            ('get_user_by_name', {'name': 'Alice Wang'}),
        ])
        agent = Agent(model, deps_type=DatabaseConn, retries=3)

    @agent.tool(retries=2)
    def get_user_by_name(ctx: RunContext[DatabaseConn], name: str) -> int:
        """Get a user's ID from their FULL name."""
        user_id = ctx.deps.users.get(name)
        if user_id is None:
            print(f'  [tool] name={name!r} 不存在，raise ModelRetry')
            raise ModelRetry(
                f'No user found with name {name!r}, '
                f'remember to provide the FULL name like "Alice Wang" or "Bob Liu".'
            )
        return user_id

    result = agent.run_sync('帮我查 Alice 的用户 ID', deps=deps)
    print('结果:', result.output)


# ============================================================
# 2) output_validator 触发的重试
# ============================================================
class Issue(BaseModel):
    title: str
    severity: str


def demo_output_validator_retry():
    print('\n===== 2) output_validator 触发重试 =====')

    if has_key('OPENAI_API_KEY'):
        agent = Agent('openai-chat:qwen-flash', output_type=Issue, retries=3)
    else:
        # TestModel 会一直给同样的输出，第一次就过验证
        agent = Agent(
            TestModel(custom_output_args={'title': '示例', 'severity': 'high'}),
            output_type=Issue,
            retries=3,
        )

    @agent.output_validator
    def validate(ctx: RunContext, out: Issue) -> Issue:
        if out.severity not in {'low', 'medium', 'high'}:
            print(f'  [validator] severity={out.severity!r} 非法，raise ModelRetry')
            raise ModelRetry(
                f'severity must be one of low/medium/high, got {out.severity!r}'
            )
        return out

    result = agent.run_sync('登录页 500 报错，需要尽快修')
    print('结果:', result.output)


# ============================================================
# 3) 工具里读 ctx.retry，按重试次数降级
# ============================================================
def demo_retry_counter():
    print('\n===== 3) ctx.retry 计数 =====')

    if has_key('OPENAI_API_KEY'):
        agent = Agent('openai-chat:qwen-flash', retries=3)
    else:
        agent = Agent(scripted([
            ('flaky_search', {'query': 'GIL'}),
            ('flaky_search', {'query': 'GIL 全局解释器锁'}),
        ]), retries=3)

    @agent.tool(retries=3)
    def flaky_search(ctx: RunContext, query: str) -> str:
        print(f'  [tool] 第 {ctx.retry} 次调用 flaky_search(query={query!r})')
        if ctx.retry < 1:
            raise ModelRetry('请用更具体的关键词重试')
        return f'结果: 关于 {query} 的资料 X、Y、Z'

    result = agent.run_sync('搜索 GIL')
    print('结果:', result.output)


# ============================================================
# 4) HTTP 层 transport 构造（只展示构造，不真发请求）
# ============================================================
def demo_http_transport_config():
    print('\n===== 4) AsyncHTTPX2TenacityTransport 构造演示 =====')

    try:
        import httpx2
        from tenacity import (
            retry_if_exception_type, stop_after_attempt,
            wait_exponential, wait_combine, wait_random,
        )
        from pydantic_ai.models import create_async_httpx2_client
        from pydantic_ai.retries import (
            AsyncHTTPX2TenacityTransport, RetryConfig, wait_retry_after,
        )
    except ImportError as e:
        print(f'[SKIP] 缺少依赖：{e}')
        print('运行 pip install "pydantic-ai-slim[retries]" 启用')
        return

    def raise_for_retryable(response):
        # 只有 429 / 5xx 才触发重试；其它状态码原样返回
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()

    transport = AsyncHTTPX2TenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((httpx2.HTTPStatusError, httpx2.ConnectError)),
            wait=wait_retry_after(  # 优先尊重服务端的 Retry-After
                fallback_strategy=wait_combine(
                    wait_exponential(multiplier=1, max=60),
                    wait_random(0, 2),
                ),
                max_wait=120,
            ),
            stop=stop_after_attempt(4),
            reraise=True,
        ),
        validate_response=raise_for_retryable,
    )
    client = create_async_httpx2_client(transport=transport)  # 保留 Pydantic AI 默认的超时 / UA
    print(f'  客户端已构造: {type(client).__name__}')
    print("  → OpenAIChatModel(name, provider=OpenAIProvider(http_client=client)),")
    print("    并设 model_settings={'max_retries': 0}，让传输层成为唯一的重试方（否则和 SDK 自带重试相乘）")


# ============================================================
# 5) FallbackModel：主备切换
# ============================================================
def demo_fallback_model():
    print('\n===== 5) FallbackModel 主备 =====')

    from pydantic_ai.models.fallback import FallbackModel

    if has_key('OPENAI_API_KEY') and has_key('ANTHROPIC_API_KEY'):
        fallback = FallbackModel('openai-chat:qwen-flash', 'anthropic:claude-haiku-4-5')
        agent = Agent(fallback)
        result = agent.run_sync('一句话讲清楚什么是 fallback')
        print('结果:', result.output)
        return

    # 用两个 TestModel 模拟：第一个抛错，第二个返回正确
    from pydantic_ai.exceptions import ModelHTTPError

    class BoomModel(TestModel):
        # FallbackModel 默认只对 ModelAPIError（含 HTTP 4xx/5xx）切换备用模型
        async def request(self, *args, **kwargs):  # type: ignore[override]
            raise ModelHTTPError(status_code=503, model_name='boom', body='模拟主模型挂了')

    primary = BoomModel()
    backup = TestModel(custom_output_text='backup 模型回答')
    fallback = FallbackModel(primary, backup)
    try:
        agent = Agent(fallback)
        result = agent.run_sync('hello')
        print('结果:', result.output)
    except Exception as e:
        print(f'[NOTE] FallbackModel 演示失败：{e}')


# ============================================================
# 6) UsageLimits 防失控
# ============================================================
def demo_usage_limits():
    print('\n===== 6) UsageLimits 防失控 =====')

    if has_key('OPENAI_API_KEY'):
        agent = Agent('openai-chat:qwen-flash', retries=5)
    else:
        agent = Agent(TestModel(), retries=5)

    result = agent.run_sync(
        '简单打个招呼',
        usage_limits=UsageLimits(request_limit=3, total_tokens_limit=5_000),
    )
    print('结果:', result.output)
    print('用量:', result.usage)


def main():
    demo_tool_model_retry()
    demo_output_validator_retry()
    demo_retry_counter()
    demo_http_transport_config()
    demo_fallback_model()
    demo_usage_limits()


if __name__ == '__main__':
    main()
