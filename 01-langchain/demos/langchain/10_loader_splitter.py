"""
10_loader_splitter.py
=====================
Loader + Splitter 流水线演示。
"""
from pathlib import Path

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


def main():
    md_path = Path("docs/01-langchain/01-overview.md")
    if not md_path.exists():
        # fallback：直接构造一个临时 md
        md_path = Path("/tmp/sample.md")
        md_path.write_text(
            "# 标题\n## 节 A\n内容 A\n## 节 B\n" + "句子" * 200,
            encoding="utf-8",
        )

    docs = TextLoader(str(md_path), encoding="utf-8").load()
    print(f"原始文档数: {len(docs)} 第一篇 {len(docs[0].page_content)} 字")

    md_splitter = MarkdownHeaderTextSplitter(
        [("#", "h1"), ("##", "h2"), ("###", "h3")]
    )
    md_chunks = md_splitter.split_text(docs[0].page_content)
    print(f"按 markdown header 切: {len(md_chunks)} 块")
    print("第一块 metadata:", md_chunks[0].metadata)

    final = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    ).split_documents(md_chunks)
    print(f"最终 chunk 数: {len(final)}")
    for c in final[:3]:
        print("---")
        print("metadata:", c.metadata)
        print(c.page_content[:200])


if __name__ == "__main__":
    main()
