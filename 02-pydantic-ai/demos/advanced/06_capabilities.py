"""
06_capabilities.py
==================
ModelProfile 与 Capabilities：搞懂"模型支持哪些能力"以及如何 fallback。

涵盖：
  1) 打印不同模型的 profile
  2) NativeOutput / PromptedOutput / ToolOutput 三种结构化输出模式
  3) 自定义 ModelProfile（模拟接国产 OpenAI-compatible 模型）
  4) capability-aware 的 Agent 工厂
  5) 没 key 时用 TestModel 演示

运行：
    python demos/advanced/06_capabilities.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import ModelProfile

load_dotenv()


class Issue(BaseModel):
    """GitHub Issue"""
    title: str
    severity: str  # low / medium / high


def has_key(env: str) -> bool:
    return bool(os.getenv(env))


# ============================================================
# 1) 打印一个模型的 profile
# ============================================================
def demo_inspect_profile():
    print('\n===== 1) 检视模型 Profile =====')

    test_model = TestModel()
    print('-- TestModel --')
    dump_profile(test_model)

    if has_key('OPENAI_API_KEY'):
        from pydantic_ai.models.openai import OpenAIChatModel
        print('-- OpenAI gpt-4o-mini --')
        dump_profile(OpenAIChatModel('gpt-4o-mini'))
    else:
        print('[SKIP] 无 OPENAI_API_KEY，跳过 OpenAI profile')

    if has_key('ANTHROPIC_API_KEY'):
        from pydantic_ai.models.anthropic import AnthropicModel
        print('-- Anthropic claude-sonnet-4-5 --')
        dump_profile(AnthropicModel('claude-sonnet-4-5'))
    else:
        print('[SKIP] 无 ANTHROPIC_API_KEY，跳过 Anthropic profile')


def dump_profile(model: Model):
    p = model.profile
    print(f'  model_name = {model.model_name}')
    print(f'  supports_tools = {p.supports_tools}')
    print(f'  supports_json_schema_output = {p.supports_json_schema_output}')
    print(f'  supports_json_object_output = {p.supports_json_object_output}')
    print(f'  default_structured_output_mode = {p.default_structured_output_mode}')
    print(f'  supports_thinking = {p.supports_thinking}')
    print(f'  thinking_always_enabled = {p.thinking_always_enabled}')
    print(f'  supported_native_tools = {p.supported_native_tools}')


# ============================================================
# 2) 三种结构化输出模式对比
# ============================================================
def demo_output_modes():
    print('\n===== 2) NativeOutput / PromptedOutput / ToolOutput 对比 =====')

    if not has_key('OPENAI_API_KEY'):
        print('[INFO] 无 OPENAI_API_KEY，三种模式都用 TestModel 演示')
        test_model = TestModel(custom_output_args={'title': '测试 Issue', 'severity': 'low'})
        models = {
            'tool': test_model,
            'native': test_model,
            'prompted': test_model,
        }
    else:
        models = {
            'tool': 'openai:gpt-4o-mini',
            'native': 'openai:gpt-4o-mini',
            'prompted': 'openai:gpt-4o-mini',
        }

    prompt = '登录页 500 报错，需要尽快修复'

    # tool 模式（默认）
    agent_tool = Agent(models['tool'], output_type=ToolOutput(Issue))
    print('[tool]    ', agent_tool.run_sync(prompt).output)

    # native 模式：走 JSON Schema 严格输出
    agent_native = Agent(models['native'], output_type=NativeOutput(Issue))
    print('[native]  ', agent_native.run_sync(prompt).output)

    # prompted 模式：把 schema 拼进 prompt
    agent_prompted = Agent(models['prompted'], output_type=PromptedOutput(Issue))
    print('[prompted]', agent_prompted.run_sync(prompt).output)


# ============================================================
# 3) 自定义 ModelProfile：模拟接国产 OpenAI-compatible 模型
# ============================================================
def demo_custom_profile():
    print('\n===== 3) 自定义 ModelProfile =====')

    # 比如我接了 DeepSeek，假设它支持 tools + json_object，但不支持 native schema
    deepseek_profile = ModelProfile(
        supports_tools=True,
        supports_json_object_output=True,
        supports_json_schema_output=False,
        default_structured_output_mode='tool',
    )
    print('DeepSeek 风格 profile:')
    print(f'  supports_tools = {deepseek_profile.supports_tools}')
    print(f'  supports_json_schema_output = {deepseek_profile.supports_json_schema_output}')
    print(f'  default_mode = {deepseek_profile.default_structured_output_mode}')

    # 真要跑：把 profile 传给 OpenAIChatModel，base_url 指向 DeepSeek
    # （这里不真跑，避免误用 key）
    print('[NOTE] 真实场景下：OpenAIChatModel(..., profile=deepseek_profile, provider=OpenAIProvider(base_url=...))')


# ============================================================
# 4) capability-aware 的 Agent 工厂
# ============================================================
def make_agent(model: Model | str, schema=Issue) -> Agent:
    """根据模型 profile 自动挑最合适的结构化输出模式。"""
    if isinstance(model, str):
        try:
            model_obj = infer_model(model)
        except Exception as e:
            print(f'[WARN] infer_model 失败：{e}，回退 TestModel')
            model_obj = TestModel(custom_output_args={'title': 'fallback', 'severity': 'low'})
    else:
        model_obj = model

    profile = model_obj.profile

    if profile.supports_json_schema_output:
        output, mode = NativeOutput(schema), 'native'
    elif profile.supports_tools:
        output, mode = ToolOutput(schema), 'tool'
    else:
        output, mode = PromptedOutput(schema), 'prompted'

    print(f'[make_agent] model={model_obj.model_name} → {mode}')
    return Agent(model_obj, output_type=output)


def demo_factory():
    print('\n===== 4) capability-aware Agent 工厂 =====')

    # a) TestModel（默认 profile）
    agent = make_agent(TestModel(custom_output_args={'title': '示例', 'severity': 'medium'}))
    print('TestModel 输出:', agent.run_sync('登录 bug').output)

    # b) 真实 OpenAI 模型
    if has_key('OPENAI_API_KEY'):
        agent2 = make_agent('openai:gpt-4o-mini')
        print('OpenAI 输出:', agent2.run_sync('登录 bug').output)

    # c) 模拟"完全不支持 tools 的小模型"——直接构造一个 profile
    no_tool_model = TestModel(
        custom_output_args={'title': 'prompted 模式', 'severity': 'low'},
    )
    # 强制把 profile 改成 supports_tools=False
    no_tool_model._profile = ModelProfile(
        supports_tools=False,
        supports_json_schema_output=False,
    )
    agent3 = make_agent(no_tool_model)
    print('无 tools 模型输出:', agent3.run_sync('登录 bug').output)


def main():
    demo_inspect_profile()
    demo_output_modes()
    demo_custom_profile()
    demo_factory()


if __name__ == '__main__':
    main()
