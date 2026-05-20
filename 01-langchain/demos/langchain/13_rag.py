"""
13_rag.py
=========
完整 RAG 实战：构建索引 + 混合检索 + 对话式 RAG。
单文件 demo，跑通即可看到效果。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

from langchain.retrievers import EnsembleRetriever
from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.retrievers import BM25Retriever
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

DB_DIR = "./rag_db"
COLLECTION = "kb"
DOCS_GLOB = "docs/**/*.md"


def build_index_if_needed():
    if os.path.exists(DB_DIR):
        return
    print(">> 首次构建索引")
    loader = DirectoryLoader(
        ".", glob=DOCS_GLOB,
        loader_cls=lambda p: TextLoader(p, encoding="utf-8"),
        show_progress=True,
    )
    docs = loader.load()
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=600, chunk_overlap=80,
        separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    ).split_documents(docs)
    Chroma.from_documents(
        chunks,
        OpenAIEmbeddings(model="text-embedding-3-small"),
        persist_directory=DB_DIR,
        collection_name=COLLECTION,
    )
    print(f"   chunks={len(chunks)}")


def build_chain():
    emb = OpenAIEmbeddings(model="text-embedding-3-small")
    vs = Chroma(
        persist_directory=DB_DIR,
        collection_name=COLLECTION,
        embedding_function=emb,
    )
    vector = vs.as_retriever(search_kwargs={"k": 6})

    raw = vs.get(include=["documents", "metadatas"])
    all_docs = [
        Document(page_content=t, metadata=m or {})
        for t, m in zip(raw["documents"], raw["metadatas"])
    ]
    bm25 = BM25Retriever.from_documents(all_docs)
    bm25.k = 6

    retriever = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])

    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    rewrite_prompt = ChatPromptTemplate.from_messages([
        ("system", "把用户最新提问改写成可独立检索的完整问题，无需新增信息。"),
        MessagesPlaceholder("history"),
        ("human", "{q}"),
    ])
    rewriter = rewrite_prompt | model | StrOutputParser()

    def history_aware_retrieve(inputs):
        if inputs.get("history"):
            new_q = rewriter.invoke({"history": inputs["history"], "q": inputs["q"]})
        else:
            new_q = inputs["q"]
        return retriever.invoke(new_q)

    def format_docs(docs):
        return "\n\n".join(f"[{i+1}] ({d.metadata.get('source','')})\n{d.page_content}"
                           for i, d in enumerate(docs))

    answer_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "你是 LangChain 教程助手，根据资料回答。引用资料请在事实后用 [编号] 标注。\n资料：\n{ctx}"),
        MessagesPlaceholder("history"),
        ("human", "{q}"),
    ])

    chain = (
        RunnablePassthrough.assign(docs=RunnableLambda(history_aware_retrieve))
        .assign(ctx=lambda x: format_docs(x["docs"]))
        | answer_prompt
        | model
        | StrOutputParser()
    )
    return chain


def main():
    build_index_if_needed()
    chain = build_chain()
    store: dict[str, InMemoryChatMessageHistory] = {}

    def get_history(sid):
        return store.setdefault(sid, InMemoryChatMessageHistory())

    bot = RunnableWithMessageHistory(
        chain, get_history,
        input_messages_key="q",
        history_messages_key="history",
    )

    cfg = {"configurable": {"session_id": "u1"}}
    for q in [
        "LCEL 是什么？",
        "它和老版本 chain 的区别在哪？",
        "怎么实现流式输出？",
    ]:
        print(f"\n>>> {q}\n")
        print(bot.invoke({"q": q}, config=cfg))


if __name__ == "__main__":
    main()
