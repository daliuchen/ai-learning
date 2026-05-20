"""
08_deferred_tools.py
====================
Deferred Tools：让工具调用"暂停"等人审批 / 跨进程执行。

涵盖：
  1) 电商下单 Agent：自动工具 + 审批工具混用
  2) 审批通过 vs 拒绝 两条路径
  3) 多轮 deferred 循环范式
  4) 跨进程：消息序列化 / 反序列化
  5) 没 key 时用 TestModel + FunctionModel 模拟"模型选择敏感工具"

运行：
    python demos/advanced/08_deferred_tools.py
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import (
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    ToolDenied,
)
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.test import TestModel

load_dotenv()


def has_key() -> bool:
    return bool(os.getenv('OPENAI_API_KEY'))


# ============================================================
# 业务模型
# ============================================================
class Order(BaseModel):
    order_id: str
    total: float


# ============================================================
# 构造电商 Agent
# ============================================================
def build_agent() -> Agent:
    if has_key():
        model = 'openai:gpt-4o-mini'
    else:
        # TestModel 默认会调每一个工具，恰好能演示"敏感工具触发审批"
        print('[INFO] OPENAI_API_KEY 未设置，使用 TestModel')
        model = TestModel()

    agent = Agent(
        model,
        output_type=[Order, str, DeferredToolRequests],
        instructions=(
            '你是电商助理。涉及提交订单 / 退款的操作必须用对应工具。'
            '调用 get_cart_items 拿购物车，calculate_total 算总价，'
            '最后调 submit_order 提交订单。'
        ),
    )

    @agent.tool_plain
    def get_cart_items() -> list[dict]:
        return [
            {'sku': 'A1', 'name': 'iPhone 17', 'price': 7999, 'qty': 1},
            {'sku': 'B2', 'name': 'AirPods', 'price': 999, 'qty': 2},
        ]

    @agent.tool_plain
    def calculate_total(items: list[dict]) -> float:
        return sum(i['price'] * i['qty'] for i in items)

    @agent.tool_plain(requires_approval=True)
    def submit_order(items: list[dict], total: float) -> Order:
        """提交订单（需要用户确认）"""
        return Order(order_id='ORD-001', total=total)

    @agent.tool_plain(requires_approval=True)
    def refund(order_id: str, amount: float) -> str:
        """退款（需要用户确认）"""
        return f'退款 ¥{amount} → {order_id} 完成'

    return agent


# ============================================================
# 1) 审批通过路径
# ============================================================
def demo_approval_yes():
    print('\n===== 1) 审批通过路径 =====')
    agent = build_agent()

    result = agent.run_sync('帮我结算购物车')
    messages = result.all_messages()

    if isinstance(result.output, DeferredToolRequests):
        requests = result.output
        print(f'  收到 {len(requests.approvals)} 个待审批 + {len(requests.calls)} 个外部调用')

        results = DeferredToolResults()
        for call in requests.approvals:
            print(f'  > 审批 {call.tool_name}({call.args})  → 通过')
            results.approvals[call.tool_call_id] = True

        final = agent.run_sync(
            message_history=messages,
            deferred_tool_results=results,
        )
        print('  最终输出:', final.output)
    else:
        print('  直接输出（未触发审批）:', result.output)


# ============================================================
# 2) 审批拒绝路径
# ============================================================
def demo_approval_no():
    print('\n===== 2) 审批拒绝路径 =====')
    agent = build_agent()

    result = agent.run_sync('帮我结算购物车')
    messages = result.all_messages()

    if isinstance(result.output, DeferredToolRequests):
        requests = result.output
        results = DeferredToolResults()
        for call in requests.approvals:
            print(f'  > 审批 {call.tool_name}  → 拒绝')
            results.approvals[call.tool_call_id] = ToolDenied('用户取消了下单')

        final = agent.run_sync(
            message_history=messages,
            deferred_tool_results=results,
        )
        print('  Agent 应对拒绝后的输出:', final.output)
    else:
        print('  直接输出（未触发审批）:', result.output)


# ============================================================
# 3) 多轮 deferred 循环范式
# ============================================================
def demo_multi_round_loop():
    print('\n===== 3) 多轮 deferred 循环 =====')
    agent = build_agent()

    result = agent.run_sync('结算并把 ORD-001 退款 999')
    messages = result.all_messages()

    rounds = 0
    while isinstance(result.output, DeferredToolRequests):
        rounds += 1
        print(f'  -- 第 {rounds} 轮 deferred --')
        requests = result.output
        results = DeferredToolResults()
        for call in requests.approvals:
            print(f'     批准 {call.tool_name}')
            results.approvals[call.tool_call_id] = True
        result = agent.run_sync(
            message_history=messages,
            deferred_tool_results=results,
        )
        messages = result.all_messages()
        if rounds > 5:
            print('  [SAFETY] 超过 5 轮，强制退出')
            break

    print(f'  完成，最终输出 ({type(result.output).__name__}):', result.output)


# ============================================================
# 4) 跨进程序列化 messages
# ============================================================
def demo_cross_process_serialize():
    print('\n===== 4) 跨进程序列化 / 反序列化 =====')
    agent = build_agent()

    result = agent.run_sync('帮我结算购物车')

    # ---- 进程 A：序列化存储 ----
    payload_bytes = ModelMessagesTypeAdapter.dump_json(result.all_messages())
    saved = payload_bytes.decode()
    print(f'  存储 messages 长度: {len(saved)} 字符')
    print(f'  片段示例: {saved[:120]}...')

    # ---- 进程 B：取回反序列化 ----
    messages = ModelMessagesTypeAdapter.validate_json(saved)
    print(f'  反序列化得到 {len(messages)} 条消息')

    # 如果是审批挂起，进程 B 也能恢复
    if isinstance(result.output, DeferredToolRequests):
        results = DeferredToolResults()
        for call in result.output.approvals:
            results.approvals[call.tool_call_id] = True
        final = agent.run_sync(
            message_history=messages,
            deferred_tool_results=results,
        )
        print(f'  进程 B 完成: {final.output}')


def main():
    demo_approval_yes()
    demo_approval_no()
    demo_multi_round_loop()
    demo_cross_process_serialize()


if __name__ == '__main__':
    main()
