"""
05_direct_requests.py
=====================
直接调模型（Direct Model Requests）：跳过 Agent，用最薄的壳调任何 LLM。

涵盖：
  1) model_request_sync —— 同步一次性
  2) 手撸多轮 messages
  3) ModelSettings 调参
  4) model_request_stream —— 流式
  5) 工具调用 + 自己跑工具循环
  6) 没 key 时用 TestModel 演示

运行：
    python demos/advanced/05_direct_requests.py
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import ModelRequest, ToolDefinition
from pydantic_ai.direct import model_request, model_request_stream, model_request_sync
from pydantic_ai.messages import (
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings

load_dotenv()


# 没 OpenAI key 时回退到 TestModel，保证 demo 一定能跑
def pick_model():
    if os.getenv('OPENAI_API_KEY'):
        return 'openai:gpt-4o-mini'
    print('[INFO] OPENAI_API_KEY 未设置，使用 TestModel 演示')
    return TestModel(custom_output_text='这是 TestModel 的伪造回答')


# ============================================================
# 1) 最小例子：一次性同步推理
# ============================================================
def demo_basic():
    print('\n===== 1) model_request_sync 最小例子 =====')
    response = model_request_sync(
        pick_model(),
        [ModelRequest.user_text_prompt('用一句话解释什么是 GIL')],
    )
    print('text:', response.parts[0].content)
    print('usage:', response.usage)
    print('model_name:', response.model_name)


# ============================================================
# 2) 手撸多轮对话
# ============================================================
def demo_multi_turn():
    print('\n===== 2) 手撸多轮 messages =====')
    messages = [
        ModelRequest(parts=[
            SystemPromptPart(content='你是简洁的助理，每次回答不超过 20 字'),
            UserPromptPart(content='Python 是什么？'),
        ]),
        ModelResponse(parts=[TextPart(content='一种解释型动态语言')]),
        ModelRequest(parts=[UserPromptPart(content='它最大的缺点呢？')]),
    ]
    response = model_request_sync(pick_model(), messages)
    print('回答:', response.parts[0].content)


# ============================================================
# 3) ModelSettings：温度 / max_tokens / timeout
# ============================================================
def demo_settings():
    print('\n===== 3) ModelSettings 调参 =====')
    response = model_request_sync(
        pick_model(),
        [ModelRequest.user_text_prompt('写一句春天的诗')],
        model_settings=ModelSettings(temperature=0.9, max_tokens=60, timeout=30.0),
    )
    print('诗句:', response.parts[0].content)


# ============================================================
# 4) 流式
# ============================================================
async def demo_stream():
    print('\n===== 4) model_request_stream 流式 =====')
    model = pick_model()
    # TestModel 不支持流式文本拆分，这里跳过
    if isinstance(model, TestModel):
        print('[SKIP] TestModel 流式输出不演示')
        return

    async with model_request_stream(
        model,
        [ModelRequest.user_text_prompt('用三句话解释 asyncio')],
    ) as stream:
        async for chunk in stream.stream_text():
            print(chunk, end='', flush=True)
        print()
        full = stream.get()
        print('usage:', full.usage)


# ============================================================
# 5) 工具调用 + 自己跑循环
# ============================================================
class Divide(BaseModel):
    """Divide two numbers."""
    numerator: float
    denominator: float
    on_inf: Literal['error', 'infinity'] = 'infinity'


def divide(args: dict) -> str:
    """真实工具实现（业务代码）"""
    a, b = args['numerator'], args['denominator']
    if b == 0:
        return 'infinity' if args.get('on_inf') == 'infinity' else 'error'
    return str(a / b)


async def demo_tool_call():
    print('\n===== 5) direct + 工具：自己跑工具循环 =====')
    model = pick_model()
    if isinstance(model, TestModel):
        print('[SKIP] TestModel 不支持外部工具循环演示')
        return

    messages: list = [ModelRequest.user_text_prompt('帮我算 123 / 456')]
    tool_def = ToolDefinition(
        name='divide',
        description='Divide two numbers.',
        parameters_json_schema=Divide.model_json_schema(),
    )
    params = ModelRequestParameters(function_tools=[tool_def], allow_text_output=True)

    # 一轮：让模型决定是否调工具
    response = await model_request(model, messages, model_request_parameters=params)
    messages.append(response)

    # 检查 parts，把工具调用执行掉
    tool_returns: list[ToolReturnPart] = []
    for part in response.parts:
        if isinstance(part, ToolCallPart):
            args = part.args if isinstance(part.args, dict) else json.loads(part.args)
            result = divide(args)
            print(f'  -> 调用 {part.tool_name}({args}) = {result}')
            tool_returns.append(ToolReturnPart(
                tool_name=part.tool_name,
                tool_call_id=part.tool_call_id,
                content=result,
            ))

    # 把工具结果塞回去，再让模型生成最终答案
    if tool_returns:
        messages.append(ModelRequest(parts=list(tool_returns)))
        final = await model_request(model, messages, model_request_parameters=params)
        for part in final.parts:
            if isinstance(part, TextPart):
                print('最终回答:', part.content)


# ============================================================
# 6) 实战：轻量级翻译 SDK
# ============================================================
def translate(text: str, target_lang: str = 'en') -> str:
    messages = [
        ModelRequest(parts=[
            SystemPromptPart(content=(
                f'You are a professional translator. '
                f'Translate user input into {target_lang}. '
                f'Output translation only.'
            )),
            UserPromptPart(content=text),
        ]),
    ]
    resp = model_request_sync(
        pick_model(),
        messages,
        model_settings=ModelSettings(temperature=0.2),
    )
    return resp.parts[0].content


def demo_translator_sdk():
    print('\n===== 6) 轻量翻译 SDK =====')
    print('zh→en:', translate('今天天气真好', 'en'))
    print('en→zh:', translate('Good morning, world!', 'zh'))


def main():
    demo_basic()
    demo_multi_turn()
    demo_settings()
    asyncio.run(demo_stream())
    asyncio.run(demo_tool_call())
    demo_translator_sdk()


if __name__ == '__main__':
    main()
