"""
10_map_reduce.py
================
Send API 演示：并行总结多个段落，最后整合
"""
from operator import add
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

load_dotenv()


class State(TypedDict):
    paragraphs: list[str]
    summaries: Annotated[list[str], add]
    final: str


class WorkerState(TypedDict):
    paragraph: str


model = ChatOpenAI(model="gpt-4o-mini", temperature=0)


def worker(s: WorkerState):
    summary = model.invoke(f"用一句话概括：{s['paragraph']}").content
    return {"summaries": [summary]}


def fan_out(state: State) -> list[Send]:
    return [Send("worker", {"paragraph": p}) for p in state["paragraphs"]]


def merge(state: State):
    bullet = "\n".join(f"- {s}" for s in state["summaries"])
    final = model.invoke(f"将以下摘要整合成 100 字短文：\n{bullet}").content
    return {"final": final}


def main():
    g = StateGraph(State)
    g.add_node("worker", worker)
    g.add_node("merge", merge)
    g.add_conditional_edges(START, fan_out, ["worker"])
    g.add_edge("worker", "merge")
    g.add_edge("merge", END)
    app = g.compile()

    out = app.invoke({
        "paragraphs": [
            "LangChain 是 LLM 应用框架，提供 chain、agent、retriever 等核心抽象，与多家模型供应商对接。",
            "LangGraph 基于状态机与图，专门解决复杂 Agent / 多 Agent / 人在回路 / 持久化等需求。",
            "LangSmith 是观测平台，提供 trace / dataset / eval / prompt hub / monitoring 一体化能力。",
        ],
        "summaries": [],
        "final": "",
    }, config={"max_concurrency": 3})

    print("\n--- 摘要 ---")
    for s in out["summaries"]:
        print("•", s)
    print("\n--- 最终整合 ---")
    print(out["final"])


if __name__ == "__main__":
    main()
