"""
03_evaluation.py
================
LangSmith Evaluation：target × evaluators × 数据集 → experiment。

- 配了 LANGSMITH_API_KEY：数据集建在 LangSmith 上，结果上传（UI 里看 experiment）
- 没配：用本地 Example 列表 + upload_results=False 离线评估，结果只在本地打印
"""
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import Client, evaluate
from langsmith.schemas import Example
from pydantic import BaseModel, Field

load_dotenv()

MODEL = os.environ.get("QWEN_MODEL", "gpt-4o-mini")
ONLINE = bool(os.getenv("LANGSMITH_API_KEY"))
DATASET = "lc-qa-demo"

INPUTS = [{"q": "LCEL 是什么？"}, {"q": "如何流式输出？"}]
OUTPUTS = [
    {"a": "LCEL 是 LangChain Expression Language，是 Runnable 组合 DSL。"},
    {"a": "使用 stream / astream / astream_events 等 API。"},
]


def get_data():
    if not ONLINE:
        now = datetime.now(timezone.utc)
        ds_id = uuid.uuid4()
        return [
            Example(id=uuid.uuid4(), dataset_id=ds_id, created_at=now, inputs=i, outputs=o)
            for i, o in zip(INPUTS, OUTPUTS)
        ]
    client = Client()
    if not client.has_dataset(dataset_name=DATASET):
        ds = client.create_dataset(dataset_name=DATASET, description="demo qa")
        client.create_examples(dataset_id=ds.id, inputs=INPUTS, outputs=OUTPUTS)
    return DATASET


chain = (
    ChatPromptTemplate.from_template("用一句话简要回答：{q}")
    | ChatOpenAI(model=MODEL, temperature=0)
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
    | ChatOpenAI(model=MODEL, temperature=0).with_structured_output(Score)
)


# evaluator 新签名：按参数名注入 inputs / outputs / reference_outputs
def quality(outputs: dict, reference_outputs: dict, inputs: dict) -> dict:
    s = judge.invoke({"q": inputs["q"], "gold": reference_outputs["a"], "pred": outputs["answer"]})
    return {"key": "quality", "score": s.score / 10, "comment": s.reasoning}


def length(outputs: dict) -> dict:
    return {"key": "length", "score": min(1.0, len(outputs["answer"]) / 500)}


def main():
    res = evaluate(
        target,
        data=get_data(),
        evaluators=[quality, length],
        experiment_prefix="qa-baseline",
        max_concurrency=2,
        metadata={"model": MODEL},
        upload_results=ONLINE,
        disable_evaluator_tracing=not ONLINE,
    )
    print("Experiment:", res.experiment_name)
    for r in res:
        scores = {x.key: x.score for x in r["evaluation_results"]["results"]}
        print(r["example"].inputs["q"], "→", scores)


if __name__ == "__main__":
    main()
