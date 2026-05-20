"""
01_overview.py
==============
LangSmith Hello World：@traceable + LangChain 自动追踪。

跑之前请配置：
  LANGSMITH_TRACING=true
  LANGSMITH_API_KEY=lsv2_pt_xxx
  LANGSMITH_PROJECT=langchain-tutorial
"""
import os

from dotenv import load_dotenv

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import traceable

load_dotenv()
if not os.getenv("LANGSMITH_API_KEY"):
    raise SystemExit("请先配置 LANGSMITH_API_KEY")


@traceable(name="prepare")
def prepare(topic: str) -> str:
    return topic.strip().lower()


chain = (
    ChatPromptTemplate.from_template("用中文写一首关于 {x} 的两行诗")
    | ChatOpenAI(model="gpt-4o-mini")
    | StrOutputParser()
)


@traceable(name="my_poem_app")
def app(topic: str) -> str:
    cleaned = prepare(topic)
    return chain.invoke(
        {"x": cleaned},
        config={"tags": ["demo"], "metadata": {"topic": cleaned}},
    )


if __name__ == "__main__":
    print(app("春天"))
    print("访问 https://smith.langchain.com 查看 trace")
