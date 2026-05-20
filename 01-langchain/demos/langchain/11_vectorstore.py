"""
11_vectorstore.py
=================
向量库基础：构建 Chroma 索引 + 查询
"""
import os
from pathlib import Path

from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

DOCS_DIR = Path("docs/01-langchain")
PERSIST_DIR = "./chroma_kb"
COLLECTION = "lc"


def loader(p):
    return TextLoader(p, encoding="utf-8")


def build_or_load():
    emb = OpenAIEmbeddings(model="text-embedding-3-small")
    if not os.path.exists(PERSIST_DIR):
        print(">> 首次构建索引")
        docs = DirectoryLoader(str(DOCS_DIR), glob="*.md", loader_cls=loader).load()
        chunks = RecursiveCharacterTextSplitter(
            chunk_size=600, chunk_overlap=80,
        ).split_documents(docs)
        return Chroma.from_documents(
            chunks, emb,
            persist_directory=PERSIST_DIR,
            collection_name=COLLECTION,
        )
    print(">> 加载已有索引")
    return Chroma(
        persist_directory=PERSIST_DIR,
        collection_name=COLLECTION,
        embedding_function=emb,
    )


def main():
    vs = build_or_load()
    queries = [
        "LCEL 是什么",
        "如何流式输出",
        "Few-shot prompt 怎么写",
    ]
    for q in queries:
        print(f"\n# {q}")
        for d, s in vs.similarity_search_with_score(q, k=2):
            print(f"  [{s:.4f}] {d.metadata.get('source')}")
            print(f"  {d.page_content[:150].strip()}…")


if __name__ == "__main__":
    main()
