"""
01_overview.py
==============
LangSmith Hello World：@traceable + LangChain 自动追踪。

- 配了 LANGSMITH_API_KEY 且 LANGSMITH_TRACING=true：trace 会上传到 LangSmith
- 没配：代码照常运行，只是不上传

模型走 OpenAI 兼容接口：OPENAI_API_KEY / OPENAI_BASE_URL（如通义千问），模型名取 QWEN_MODEL。
"""
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import get_current_run_tree, traceable

load_dotenv()

MODEL = os.environ.get("QWEN_MODEL", "gpt-4o-mini")


@traceable(name="prepare")
def prepare(topic: str) -> str:
    return topic.strip().lower()


chain = (
    ChatPromptTemplate.from_template("用中文写一首关于 {x} 的两行诗")
    | ChatOpenAI(model=MODEL)
    | StrOutputParser()
)


@traceable(name="my_poem_app")
def app(topic: str) -> str:
    cleaned = prepare(topic)
    rt = get_current_run_tree()  # 开启 tracing 时才有；可用来拿 run id 做反馈关联
    if rt:
        print("当前 run:", rt.name, rt.id)
    return chain.invoke(
        {"x": cleaned},
        config={"tags": ["demo"], "metadata": {"topic": cleaned}},
    )


if __name__ == "__main__":
    print(app("春天"))
    if os.getenv("LANGSMITH_API_KEY") and os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        print("访问 https://smith.langchain.com 查看 trace")
    else:
        print("（未开启 tracing，本次没有上传 trace）")
