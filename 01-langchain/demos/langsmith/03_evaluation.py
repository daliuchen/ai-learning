"""
03_evaluation.py
================
LangSmith Datasets + LLM-as-Judge Evaluation 完整 demo
"""
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import Client
from langsmith.evaluation import evaluate

load_dotenv()
if not os.getenv("LANGSMITH_API_KEY"):
    raise SystemExit("请先配置 LANGSMITH_API_KEY")

client = Client()
DATASET = "lc-qa-demo"


def ensure_dataset():
    try:
        return client.read_dataset(dataset_name=DATASET)
    except Exception:
        ds = client.create_dataset(dataset_name=DATASET, description="demo qa")
        client.create_examples(
            dataset_id=ds.id,
            inputs=[
                {"q": "LCEL 是什么？"},
                {"q": "如何流式输出？"},
                {"q": "LangChain 的 RAG 怎么做？"},
            ],
            outputs=[
                {"a": "LCEL 是 LangChain Expression Language，是 Runnable 组合 DSL。"},
                {"a": "使用 stream / astream / astream_events 等 API。"},
                {"a": "Loader→Splitter→Embedding→VectorStore→Retriever→Prompt→Model。"},
            ],
        )
        return ds


chain = (
    ChatPromptTemplate.from_template("简要回答：{q}")
    | ChatOpenAI(model="gpt-4o-mini", temperature=0)
    | StrOutputParser()
)


def target(inputs: dict) -> dict:
    return {"answer": chain.invoke({"q": inputs["q"]})}


class Score(BaseModel):
    score: int = Field(description="0-10")
    reasoning: str


judge = (
    ChatPromptTemplate.from_messages([
        ("system", "你是 QA 评估官，给模型答案打 0-10 分，并给理由。"),
        ("human", "问题：{q}\n标准答：{gold}\n模型答：{pred}"),
    ])
    | ChatOpenAI(model="gpt-4o-mini", temperature=0).with_structured_output(Score)
)


def quality(outputs, reference_outputs, inputs):
    s = judge.invoke({
        "q": inputs["q"],
        "gold": reference_outputs["a"],
        "pred": outputs["answer"],
    })
    return {"key": "quality", "score": s.score / 10, "comment": s.reasoning}


def length(outputs, **_):
    return {"key": "length", "score": min(1.0, len(outputs["answer"]) / 500)}


def main():
    ensure_dataset()
    res = evaluate(
        target,
        data=DATASET,
        evaluators=[quality, length],
        experiment_prefix="qa-baseline",
        max_concurrency=2,
        metadata={"model": "gpt-4o-mini"},
    )
    print("Experiment:", res.experiment_name)
    print("URL: 打开 LangSmith → Datasets → 看 experiment")


if __name__ == "__main__":
    main()
