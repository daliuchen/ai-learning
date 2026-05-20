"""
02_tracing.py
=============
@traceable + wrap_openai + trace context manager 三件套
"""
import os
from dotenv import load_dotenv

import openai
from langsmith import trace, traceable
from langsmith.wrappers import wrap_openai

load_dotenv()
if not os.getenv("LANGSMITH_API_KEY"):
    raise SystemExit("请先配置 LANGSMITH_API_KEY")

client = wrap_openai(openai.Client())


@traceable(run_type="retriever", name="kb_search")
def kb_search(q: str) -> list[str]:
    return [f"doc[{q}][{i}] 内容" for i in range(2)]


@traceable(run_type="tool", name="format_docs")
def format_docs(docs: list[str]) -> str:
    return "\n".join(docs)


@traceable(name="rag_app", tags=["demo"], metadata={"version": "v1"})
def rag(q: str) -> str:
    docs = kb_search(q)
    ctx = format_docs(docs)
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"基于以下资料回答：\n{ctx}"},
            {"role": "user", "content": q},
        ],
    )
    return r.choices[0].message.content


def main():
    qs = ["LCEL 是什么", "如何流式输出", "怎么做 RAG"]
    with trace(name="batch-eval", inputs={"size": len(qs)}) as run:
        answers = [rag(q) for q in qs]
        run.end(outputs={"count": len(answers)})
    for q, a in zip(qs, answers):
        print(f"Q: {q}\nA: {a[:80]}…\n")


if __name__ == "__main__":
    main()
