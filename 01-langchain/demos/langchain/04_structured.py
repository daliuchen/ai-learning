"""
04_structured.py
================
Output Parsers 与结构化输出对比演示。
"""
from typing import Union

import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()
MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")  # 任意 OpenAI 兼容模型；默认千问，base_url 读 OPENAI_BASE_URL


class Issue(BaseModel):
    """GitHub Issue 信息"""
    title: str = Field(description="简短标题")
    severity: str = Field(description="严重程度 low/medium/high")
    tags: list[str] = Field(description="标签列表")


class Joke(BaseModel):
    setup: str
    punchline: str


class FactStatement(BaseModel):
    fact: str
    source: str


def demo_with_structured():
    print("\n=== with_structured_output (Pydantic) ===")
    model = ChatOpenAI(model=MODEL).with_structured_output(Issue)
    issue = model.invoke("登录页 500 报错，需要尽快修复，影响所有用户")
    print(issue)
    print("type:", type(issue).__name__)


def demo_with_structured_dict():
    print("\n=== with_structured_output (TypedDict-like raw schema) ===")
    schema = {
        "title": "Person",
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name", "age"],
    }
    model = ChatOpenAI(model=MODEL).with_structured_output(schema)
    print(model.invoke("我叫张三今年28"))


def demo_json_parser():
    print("\n=== JsonOutputParser + Pydantic ===")
    parser = JsonOutputParser(pydantic_object=Issue)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "提取 issue 信息：\n{format_instructions}"),
        ("human", "{input}"),
    ]).partial(format_instructions=parser.get_format_instructions())
    chain = prompt | ChatOpenAI(model=MODEL) | parser
    print(chain.invoke({"input": "登录页 500 报错"}))


def demo_union():
    print("\n=== Union schema 自路由 ===")
    # 新版 langchain-openai 默认走 json_schema 严格模式，顶层不能直接放 Union，
    # 要包一层对象，让 Union 作为它的一个字段
    class Answer(BaseModel):
        result: Union[Joke, FactStatement]

    model = ChatOpenAI(model=MODEL).with_structured_output(Answer)
    print(model.invoke("讲个笑话").result)
    print(model.invoke("告诉我一个有趣的天文事实").result)


def demo_stream_json():
    print("\n=== JsonOutputParser 流式 ===")
    parser = JsonOutputParser()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "返回 JSON: {{\"fruits\": [...10 个水果...]}}"),
        ("human", "{input}"),
    ])
    chain = prompt | ChatOpenAI(model=MODEL) | parser
    for partial in chain.stream({"input": "go"}):
        print(partial)


if __name__ == "__main__":
    demo_with_structured()
    demo_with_structured_dict()
    demo_json_parser()
    demo_union()
    demo_stream_json()
