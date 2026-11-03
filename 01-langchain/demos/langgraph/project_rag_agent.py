"""
project_rag_agent.py
====================
实战项目 1：RAG 问答 Agent（langchain 1.x：create_agent + 中间件）
- 内部知识库检索（kb_search）
- 互联网兜底（web_search，可选 Tavily；没配 key 时返回提示）
- 需要审批的工具（send_email）→ HumanInTheLoopMiddleware
- SqliteSaver 持久化（多轮 + 断点续跑）

运行（在 01-langchain 目录下）：python demos/langgraph/project_rag_agent.py
模型/向量：OpenAI 兼容接口（如通义千问）。
  OPENAI_API_KEY / OPENAI_BASE_URL；QWEN_MODEL（默认 qwen-flash）；QWEN_EMBED_MODEL（默认 text-embedding-v4）
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

load_dotenv()

MODEL = os.environ.get("QWEN_MODEL", "gpt-4o-mini")
EMBED_MODEL = os.environ.get("QWEN_EMBED_MODEL", "text-embedding-3-small")
# 知识库范围：默认只索引两篇，省 embedding 调用；想全量就改成 docs/**/*.md
DOCS_GLOB = os.environ.get("KB_GLOB", "docs/01-langchain/0[15]-*.md")
SQLITE_PATH = "./rag_agent.db"


# ---------------- 知识库（内存向量库；生产换 Chroma / pgvector 等） ----------------
def build_kb() -> InMemoryVectorStore:
    docs = [
        Document(page_content=p.read_text(encoding="utf-8"), metadata={"source": str(p)})
        for p in sorted(Path(".").glob(DOCS_GLOB))
    ] or [Document(page_content="LCEL 是 LangChain Expression Language，用 | 把 Runnable 串起来。")]
    chunks = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80).split_documents(docs)
    # 通义 embedding 接口一次最多 10 条，且不接受 token 数组 → chunk_size=10 + 关掉长度检查
    emb = OpenAIEmbeddings(model=EMBED_MODEL, chunk_size=10, check_embedding_ctx_length=False)
    return InMemoryVectorStore.from_documents(chunks, emb)


_vs: InMemoryVectorStore | None = None


def vs() -> InMemoryVectorStore:
    global _vs
    if _vs is None:
        _vs = build_kb()
    return _vs


# ---------------- 工具 ----------------
@tool
def kb_search(query: str) -> str:
    """从内部知识库（LangChain 教程）搜索资料。优先使用。"""
    docs = vs().similarity_search(query, k=4)
    if not docs:
        return "未在内部知识库找到相关资料"
    return "\n\n".join(f"[{i + 1}] {d.page_content}" for i, d in enumerate(docs))


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


SYSTEM = (
    "你是公司知识库助手。回答原则：\n"
    "1. 优先用 kb_search 查内部知识库\n"
    "2. 没找到再用 web_search\n"
    "3. 中文回答，引用资料用 [n] 标注\n"
    "4. 涉及发邮件时调用 send_email，会自动暂停等待人工审批"
)


def build_app(checkpointer):
    return create_agent(
        ChatOpenAI(model=MODEL, temperature=0),
        tools=[kb_search, web_search, send_email],
        system_prompt=SYSTEM,
        middleware=[
            # 只有 send_email 需要审批；kb_search / web_search 直接放行
            HumanInTheLoopMiddleware(interrupt_on={"send_email": True}),
        ],
        checkpointer=checkpointer,  # HITL 必须有 checkpointer
    )


def main():
    with SqliteSaver.from_conn_string(SQLITE_PATH) as memory:
        app = build_app(memory)
        cfg = {"configurable": {"thread_id": "demo-user"}}

        for q in ["LCEL 是什么？", "它和老版本 chain 的区别？"]:
            print(f"\n>>> {q}\n")
            out = app.invoke({"messages": [("human", q)]}, config=cfg)
            print(out["messages"][-1].content)

        # 危险操作：会在 send_email 之前被中断
        print("\n>>> 给 boss@x.com 发一封请假邮件\n")
        out = app.invoke(
            {"messages": [("human", "给 boss@x.com 发一封请假邮件，正文'明天请假'")]}, config=cfg
        )
        if "__interrupt__" in out:
            req = out["__interrupt__"][0].value["action_requests"][0]
            print("【待审】", req["name"], req["args"])
            final = app.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)
            print(final["messages"][-1].content)


if __name__ == "__main__":
    main()
