"""
12_retrievers.py
================
Retriever 类型对比：BM25 / Vector / Ensemble / MultiQuery
依赖前一节 11_vectorstore.py 已构建好 ./chroma_kb
"""
from dotenv import load_dotenv

from langchain.retrievers import EnsembleRetriever
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

load_dotenv()


def main():
    emb = OpenAIEmbeddings(model="text-embedding-3-small")
    vs = Chroma(persist_directory="./chroma_kb", collection_name="lc", embedding_function=emb)

    vector = vs.as_retriever(search_kwargs={"k": 4})

    # 从 Chroma 取出全部文档构造 BM25
    raw = vs.get(include=["documents", "metadatas"])
    all_docs = [
        Document(page_content=t, metadata=m or {})
        for t, m in zip(raw["documents"], raw["metadatas"])
    ]
    bm25 = BM25Retriever.from_documents(all_docs)
    bm25.k = 4

    ensemble = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])
    mq = MultiQueryRetriever.from_llm(
        retriever=ensemble,
        llm=ChatOpenAI(model="gpt-4o-mini"),
    )

    queries = [
        "怎么实现流式输出",
        "LCEL 怎么写并行",
    ]
    for q in queries:
        print(f"\n# Query: {q}")
        print("\n[Vector]")
        for d in vector.invoke(q):
            print(" -", d.metadata.get("source"), "|", d.page_content[:60])
        print("\n[BM25]")
        for d in bm25.invoke(q):
            print(" -", d.metadata.get("source"), "|", d.page_content[:60])
        print("\n[Ensemble]")
        for d in ensemble.invoke(q):
            print(" -", d.metadata.get("source"), "|", d.page_content[:60])
        print("\n[MultiQuery]")
        for d in mq.invoke(q):
            print(" -", d.metadata.get("source"), "|", d.page_content[:60])


if __name__ == "__main__":
    main()
