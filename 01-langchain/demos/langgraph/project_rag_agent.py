"""
project_rag_agent.py
====================
实战项目 1：完整 RAG 问答 Agent
- 内部知识库检索（kb_search）
- 互联网兜底（web_search，可选 Tavily）
- 需要审批的工具（send_email）+ HITL
- SqliteSaver 持久化
- 多轮对话
"""
import os
from pathlib import Path
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

load_dotenv()

DB_DIR = "./rag_db"
COLLECTION = "kb"
DOCS_GLOB = "docs/**/*.md"
SQLITE_PATH = "./rag_agent.db"


# ---------------- 知识库 ----------------
def ensure_kb():
    if os.path.exists(DB_DIR):
        return
    docs = DirectoryLoader(".", glob=DOCS_GLOB,
                           loader_cls=lambda p: TextLoader(p, encoding="utf-8")).load()
    if not docs:
        # 兜底：随便造一篇
        Path("./docs/01-langchain").mkdir(parents=True, exist_ok=True)
        Path("./docs/01-langchain/_demo.md").write_text(
            "# Demo\nLCEL 是 LangChain Expression Language\n", encoding="utf-8")
        docs = DirectoryLoader(".", glob=DOCS_GLOB,
                               loader_cls=lambda p: TextLoader(p, encoding="utf-8")).load()
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=600, chunk_overlap=80,
    ).split_documents(docs)
    Chroma.from_documents(
        chunks,
        OpenAIEmbeddings(model="text-embedding-3-small"),
        persist_directory=DB_DIR,
        collection_name=COLLECTION,
    )


def _vs():
    return Chroma(
        persist_directory=DB_DIR,
        collection_name=COLLECTION,
        embedding_function=OpenAIEmbeddings(model="text-embedding-3-small"),
    )


# ---------------- 工具 ----------------
@tool
def kb_search(query: str) -> str:
    """从内部知识库（LangChain 教程）搜索资料。优先使用。"""
    vs = _vs()
    docs = vs.similarity_search(query, k=4)
    if not docs:
        return "未在内部知识库找到相关资料"
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))


@tool
def web_search(query: str) -> str:
    """互联网搜索。只在内部知识库没找到资料时使用。"""
    if not os.getenv("TAVILY_API_KEY"):
        return "（未配置 TAVILY_API_KEY，跳过 web_search）"
    from langchain_community.tools.tavily_search import TavilySearchResults
    return str(TavilySearchResults(max_results=3).invoke(query))


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件。注意：此操作会经过人工审批。"""
    return f"[mock] 邮件已发送 → {to}, 主题：{subject}"


SAFE_TOOLS = [kb_search, web_search]
DANGEROUS_TOOLS = [send_email]
ALL_TOOLS = SAFE_TOOLS + DANGEROUS_TOOLS


# ---------------- Graph ----------------
class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


SYSTEM = (
    "你是公司知识库助手。回答原则：\n"
    "1. 优先用 kb_search 查内部知识库\n"
    "2. 没找到再用 web_search\n"
    "3. 中文回答，引用资料用 [n] 标注\n"
    "4. 涉及发邮件等危险操作时调用 send_email，会自动暂停等待人工审批"
)
model = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(ALL_TOOLS)


def agent(state: State):
    msgs = [SystemMessage(content=SYSTEM), *state["messages"]]
    return {"messages": [model.invoke(msgs)]}


def review(state: State):
    last = state["messages"][-1]
    dangerous_calls = [c for c in last.tool_calls if c["name"] in {"send_email"}]
    if not dangerous_calls:
        return {}
    decision = interrupt({
        "type": "approval",
        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in dangerous_calls],
    })
    if decision == "yes":
        return {}
    return {"messages": [
        ToolMessage(content="人工拒绝执行", tool_call_id=c["id"]) for c in dangerous_calls
    ]}


def route(state: State) -> str:
    last = state["messages"][-1]
    if not last.tool_calls:
        return END
    if any(c["name"] in {"send_email"} for c in last.tool_calls):
        return "review"
    return "safe_tools"


def post_review(state: State) -> str:
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and "拒绝" in last.content:
        return "agent"
    return "dangerous_tools"


def build(memory):
    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("safe_tools", ToolNode(SAFE_TOOLS))
    g.add_node("review", review)
    g.add_node("dangerous_tools", ToolNode(DANGEROUS_TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges(
        "agent", route,
        {"safe_tools": "safe_tools", "review": "review", END: END},
    )
    g.add_edge("safe_tools", "agent")
    g.add_conditional_edges(
        "review", post_review,
        {"dangerous_tools": "dangerous_tools", "agent": "agent"},
    )
    g.add_edge("dangerous_tools", "agent")
    return g.compile(checkpointer=memory)


def main():
    ensure_kb()
    with SqliteSaver.from_conn_string(SQLITE_PATH) as memory:
        app = build(memory)
        cfg = {"configurable": {"thread_id": "demo-user"}}

        for q in [
            "LCEL 是什么？",
            "它和老版本 chain 的区别？",
        ]:
            print(f"\n>>> {q}\n")
            out = app.invoke({"messages": [("human", q)]}, config=cfg)
            print(out["messages"][-1].content)

        # 危险操作
        print("\n>>> 给 boss@x.com 发一封请假邮件\n")
        out = app.invoke({"messages": [("human", "给 boss@x.com 发一封请假邮件，正文'明天请假'")]}, config=cfg)
        if "__interrupt__" in out:
            print("【待审】", out["__interrupt__"][0].value)
            final = app.invoke(Command(resume="yes"), config=cfg)
            print(final["messages"][-1].content)


if __name__ == "__main__":
    main()
