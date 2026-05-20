"""
05_lcel.py
==========
LCEL 全部基础原语演示：
- RunnableSequence (|)
- RunnableParallel (dict)
- RunnablePassthrough.assign
- RunnableLambda
- RunnableBranch
- with_config
- configurable_fields
"""
from dotenv import load_dotenv

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import (
    ConfigurableField,
    RunnableBranch,
    RunnableLambda,
    RunnableParallel,
    RunnablePassthrough,
)
from langchain_openai import ChatOpenAI

load_dotenv()
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)


def demo_parallel():
    print("\n=== RunnableParallel ===")
    joke = ChatPromptTemplate.from_template("讲个关于 {topic} 的短笑话") | model | StrOutputParser()
    poem = ChatPromptTemplate.from_template("写一首关于 {topic} 的两行诗") | model | StrOutputParser()
    both = RunnableParallel(joke=joke, poem=poem)
    out = both.invoke({"topic": "猫"})
    print("JOKE:", out["joke"])
    print("POEM:", out["poem"])


def demo_passthrough():
    print("\n=== RunnablePassthrough.assign ===")
    chain = (
        RunnablePassthrough.assign(
            upper=lambda x: x["text"].upper(),
            length=lambda x: len(x["text"]),
        )
    )
    print(chain.invoke({"text": "hello"}))


def demo_lambda():
    print("\n=== RunnableLambda ===")
    shouter = RunnableLambda(lambda s: s.upper() + "!!!")
    print(shouter.invoke("hi"))


def demo_branch():
    print("\n=== RunnableBranch + Router ===")
    classifier = (
        ChatPromptTemplate.from_template(
            "给以下问题分类，只回复 code 或 general：{q}\n类别："
        )
        | model
        | StrOutputParser()
    )
    code_chain = ChatPromptTemplate.from_template("用 Python 代码回答：{q}") | model | StrOutputParser()
    general_chain = ChatPromptTemplate.from_template("普通中文回答：{q}") | model | StrOutputParser()
    router = (
        RunnablePassthrough.assign(kind=classifier)
        | RunnableBranch(
            (lambda x: "code" in x["kind"].lower(), code_chain),
            general_chain,
        )
    )
    print("Q1:", router.invoke({"q": "写一个冒泡排序"})[:200])
    print("Q2:", router.invoke({"q": "讲讲春天"})[:200])


def demo_configurable():
    print("\n=== configurable_fields ===")
    cfg_model = ChatOpenAI(model="gpt-4o-mini", temperature=0).configurable_fields(
        temperature=ConfigurableField(id="temp"),
    )
    chain = ChatPromptTemplate.from_template("一句话讲讲 {x}") | cfg_model | StrOutputParser()
    print("temp=0:", chain.with_config(configurable={"temp": 0.0}).invoke({"x": "宇宙"}))
    print("temp=1.5:", chain.with_config(configurable={"temp": 1.5}).invoke({"x": "宇宙"}))


def demo_graph():
    print("\n=== Graph 可视化 ===")
    chain = ChatPromptTemplate.from_template("{x}") | model | StrOutputParser()
    chain.get_graph().print_ascii()


if __name__ == "__main__":
    demo_parallel()
    demo_passthrough()
    demo_lambda()
    demo_branch()
    demo_configurable()
    demo_graph()
