"""
02_multimodal.py
================
Pydantic AI 多模态输入：演示图片、PDF、本地二进制三种主要用法。

1) ImageUrl：传公网图 URL
2) BinaryContent：传本地图片 bytes（自动生成或从磁盘读）
3) DocumentUrl：传公网 PDF URL（推荐 Claude / Gemini）

没设置 API key 时自动 fallback 到 TestModel，仅校验代码路径。

运行：
    python demos/advanced/02_multimodal.py
"""
from __future__ import annotations

import io
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from pydantic_ai import Agent, BinaryContent, DocumentUrl, ImageUrl
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_vision_model() -> str | TestModel:
    """vision 任务首选 gpt-4o，否则 Claude，否则 TestModel"""
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-sonnet-latest"
    print("[warn] 未检测到 API key，使用 TestModel。\n")
    return TestModel()


def pick_doc_model() -> str | TestModel:
    """PDF 任务首选 Claude，其次 Gemini"""
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-sonnet-latest"
    if os.getenv("GEMINI_API_KEY"):
        return "google-gla:gemini-1.5-flash"
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    return TestModel()


# ----------------------------------------------------------------------------
# 1) ImageUrl：公网 URL
# ----------------------------------------------------------------------------
def demo_image_url() -> None:
    print("===== 1) ImageUrl — 公网图片 URL =====")
    agent = Agent(pick_vision_model())
    try:
        result = agent.run_sync(
            [
                "用一句话描述这张图里有什么",
                ImageUrl(url="https://iili.io/3Hs4FMg.png"),
            ]
        )
        print(result.output)
    except Exception as e:
        print(f"[skip] {e}")
    print()


# ----------------------------------------------------------------------------
# 2) BinaryContent：本地 / 内存图片
# ----------------------------------------------------------------------------
def make_demo_png() -> bytes:
    """生成一张 1x1 红色 PNG（实际项目应从磁盘读真图）"""
    # 一张最小合法 PNG 的二进制（1x1 红点）
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000"
        "00907753de0000000c4944415408d76360f8cf00000003000180fe8a"
        "39000000000049454e44ae426082"
    )


def demo_binary_content() -> None:
    print("===== 2) BinaryContent — 本地图片 bytes =====")
    agent = Agent(pick_vision_model())
    img_bytes = make_demo_png()
    try:
        result = agent.run_sync(
            [
                "这张图大致是什么颜色？",
                BinaryContent(data=img_bytes, media_type="image/png"),
            ]
        )
        print(result.output)
    except Exception as e:
        print(f"[skip] {e}")
    print()


# ----------------------------------------------------------------------------
# 3) DocumentUrl：PDF URL → 结构化抽取
# ----------------------------------------------------------------------------
class PaperSummary(BaseModel):
    title: str = Field(default="", description="论文标题")
    authors: list[str] = Field(default_factory=list, description="作者列表")
    contribution: str = Field(default="", description="主要贡献一句话")


def demo_document_url() -> None:
    print("===== 3) DocumentUrl — PDF → 结构化抽取 =====")
    agent = Agent(
        pick_doc_model(),
        output_type=PaperSummary,
        system_prompt="从给定 PDF 中抽取论文标题、作者、核心贡献。",
    )
    try:
        result = agent.run_sync(
            [
                "总结这份 PDF",
                DocumentUrl(
                    url="https://arxiv.org/pdf/2307.06435.pdf",
                ),
            ]
        )
        print(result.output)
    except Exception as e:
        print(f"[skip] {e}")
    print()


# ----------------------------------------------------------------------------
# 4) 实战：发票图 → 结构化字段（用本地 fake 图演示流程）
# ----------------------------------------------------------------------------
class Invoice(BaseModel):
    vendor: str = Field(default="", description="开票方")
    amount: float = Field(default=0.0, description="金额")
    date: str = Field(default="", description="日期 YYYY-MM-DD")


def demo_invoice_pipeline() -> None:
    print("===== 4) 实战：发票图 → Invoice =====")
    agent = Agent(
        pick_vision_model(),
        output_type=Invoice,
        system_prompt="你是发票识别专家，从图中抽取关键字段。",
    )
    img_bytes = make_demo_png()  # 真实项目用 Path("invoice.png").read_bytes()
    try:
        result = agent.run_sync(
            [
                "请抽取这张发票的字段",
                BinaryContent(data=img_bytes, media_type="image/png"),
            ]
        )
        print(result.output)
    except Exception as e:
        print(f"[skip] {e}")
    print()


def main() -> None:
    demo_image_url()
    demo_binary_content()
    demo_document_url()
    demo_invoice_pipeline()


if __name__ == "__main__":
    main()
