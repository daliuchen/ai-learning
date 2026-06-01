"""
06_output_types.py
==================
Pydantic AI 结构化输出 demo：
1) 基本 Pydantic Model 输出
2) 标量 / list / dict 输出
3) Union 多 schema 输出
4) @agent.output_validator + ModelRetry
5) 发票抽取（含 model_post_init 交叉校验）

没有 API key 时使用 TestModel，TestModel 会按 schema 生成假数据。

运行：
    python demos/basics/06_output_types.py
"""
from __future__ import annotations

import os
from datetime import date
from typing import Union

from dotenv import load_dotenv
from pydantic import BaseModel, Field

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


# ---------- 1) Pydantic Model 输出 ----------
class Joke(BaseModel):
    setup: str
    punchline: str
    rating: int = Field(description="自评分 1-10")


def demo_pydantic() -> None:
    print("===== 1) Pydantic Model 输出 =====")
    agent = Agent(MODEL, output_type=Joke, system_prompt="讲一个程序员笑话。")
    r = agent.run_sync("来一个 Python 笑话")
    print(repr(r.output))
    print()


# ---------- 2) 标量 / list / dict ----------
def demo_scalar() -> None:
    print("===== 2) 标量 / list / dict =====")
    a_int = Agent(MODEL, output_type=int, system_prompt="只回数字。")
    print("int  :", a_int.run_sync("2+3 等于多少？").output)

    a_list = Agent(MODEL, output_type=list[str], system_prompt="列出名称。")
    print("list :", a_list.run_sync("中国一线城市有哪些？").output)
    print()


# ---------- 3) Union 多 schema ----------
class Invoice(BaseModel):
    amount: float
    vendor: str


class Receipt(BaseModel):
    item: str
    qty: int


def demo_union() -> None:
    print("===== 3) Union 多 schema =====")
    agent = Agent(
        MODEL,
        output_type=Union[Invoice, Receipt],
        system_prompt="根据内容判断是发票还是收据，返回对应结构。",
    )
    for prompt in ["阿里云 ¥1280", "买了 3 个苹果"]:
        r = agent.run_sync(prompt)
        print(f"  {prompt!r:25s} → {type(r.output).__name__}: {r.output}")
    print()


# ---------- 4) output_validator + ModelRetry ----------
class Greeting(BaseModel):
    text: str


def demo_validator() -> None:
    print("===== 4) output_validator + ModelRetry =====")
    agent = Agent(
        MODEL,
        output_type=Greeting,
        system_prompt="生成一句简短的问候语。",
        retries=2,
        output_retries=2,
    )

    @agent.output_validator
    def must_have_name(ctx: RunContext[None], output: Greeting) -> Greeting:
        if "Ethan" not in output.text:
            raise ModelRetry("必须在问候语中包含名字 'Ethan'")
        return output

    try:
        print(agent.run_sync("帮我生成一句问候").output)
    except Exception as e:
        print(f"重试用完仍失败：{type(e).__name__}: {e}")
    print()


# ---------- 5) 发票抽取（交叉校验） ----------
class LineItem(BaseModel):
    name: str
    qty: int
    price: float


class FullInvoice(BaseModel):
    invoice_no: str = Field(description="发票号")
    vendor: str = Field(description="开票方")
    invoice_date: date = Field(description="开票日期，ISO 格式")
    items: list[LineItem]
    total: float

    def model_post_init(self, __context) -> None:
        computed = sum(it.qty * it.price for it in self.items)
        if abs(computed - self.total) > 0.01:
            # 触发 ModelRetry 让模型重新生成
            raise ValueError(f"total {self.total} 与明细累加 {computed} 不符")


def demo_invoice() -> None:
    print("===== 5) 发票抽取 =====")
    agent = Agent(
        MODEL,
        output_type=FullInvoice,
        system_prompt="从文本中精确提取发票字段。注意 total 必须等于明细累加。",
        retries=3,
    )
    text = """
    发票号 IV-2024-001
    开票方：阿里云计算有限公司
    日期：2024-01-15
    明细：
      - 弹性计算 x1 ¥800.00
      - 对象存储 x2 ¥240.00
    合计：¥1280.00
    """
    try:
        r = agent.run_sync(text)
        print(r.output.model_dump_json(indent=2, default=str))
    except Exception as e:
        # TestModel 无法理解 prompt，可能交叉校验失败
        print(f"抽取失败：{type(e).__name__}: {e}")
    print()


def main() -> None:
    demo_pydantic()
    demo_scalar()
    demo_union()
    demo_validator()
    demo_invoice()


if __name__ == "__main__":
    main()
