# LangChain 10：Document Loaders 与 Text Splitters

> **一句话**：Loader 把"任何源（文件/URL/数据库）" → `List[Document]`，Splitter 把"大 Document" → "可被向量化的小 chunk"。这两步是 RAG 流水线最容易决定上限的环节，质量决定一切。

---

## 1. Document 是什么

```python
from langchain_core.documents import Document

doc = Document(
    page_content="文本内容",
    metadata={"source": "guide.pdf", "page": 3, "author": "x"},
)
```

`metadata` 是关键，它会一路跟随到向量库，可以用来过滤、引用、回链原始文档。

---

## 2. Loader：常见数据源

### 2.1 文本 / Markdown

```python
from langchain_community.document_loaders import TextLoader, UnstructuredMarkdownLoader

docs = TextLoader("notes.txt", encoding="utf-8").load()
docs = UnstructuredMarkdownLoader("README.md").load()
```

### 2.2 PDF

```python
from langchain_community.document_loaders import PyPDFLoader, PyMuPDFLoader
docs = PyPDFLoader("book.pdf").load()                  # 简单，每页一个 doc
docs = PyMuPDFLoader("book.pdf").load()                # 解析更好，带 metadata 多

# 强大但慢：保留布局、表格
from langchain_community.document_loaders import UnstructuredPDFLoader
docs = UnstructuredPDFLoader("book.pdf", mode="elements").load()
```

### 2.3 HTML / 网页

```python
from langchain_community.document_loaders import WebBaseLoader, AsyncHtmlLoader
from langchain_community.document_transformers import BeautifulSoupTransformer

# 直接抓
docs = WebBaseLoader(["https://example.com/a", ".../b"]).load()

# 异步并发抓
async_docs = AsyncHtmlLoader(urls).load()
transformer = BeautifulSoupTransformer()
docs = transformer.transform_documents(async_docs, tags_to_extract=["p", "h1"])

# 用 Playwright 渲染 JS
from langchain_community.document_loaders import PlaywrightURLLoader
docs = PlaywrightURLLoader(urls=urls).load()
```

### 2.4 Office / Excel

```python
from langchain_community.document_loaders import UnstructuredWordDocumentLoader
from langchain_community.document_loaders import UnstructuredExcelLoader
docs = UnstructuredWordDocumentLoader("a.docx").load()
docs = UnstructuredExcelLoader("a.xlsx", mode="elements").load()
```

### 2.5 JSON / CSV

```python
from langchain_community.document_loaders import JSONLoader, CSVLoader
docs = CSVLoader("data.csv").load()
docs = JSONLoader(
    file_path="data.json",
    jq_schema=".items[]",          # 用 jq 表达式定位
    content_key="text",
    metadata_func=lambda r, md: {**md, "id": r["id"]},
).load()
```

### 2.6 目录批量

```python
from langchain_community.document_loaders import DirectoryLoader
docs = DirectoryLoader(
    "./knowledge",
    glob="**/*.md",
    loader_cls=TextLoader,
    show_progress=True,
    use_multithreading=True,
).load()
```

### 2.7 数据库 / 知识库 API

- `WikipediaLoader`
- `ConfluenceLoader`
- `NotionDBLoader`
- `GoogleDriveLoader`
- `S3FileLoader`
- `GitHubIssuesLoader`
- ...几十种，看 `langchain_community.document_loaders` 目录

### 2.8 自定义 Loader

```python
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

class MyLoader(BaseLoader):
    def __init__(self, path): self.path = path
    def lazy_load(self):
        for line in open(self.path):
            yield Document(page_content=line.strip(), metadata={"path": self.path})
```

实现 `lazy_load` 即可，`load()` 默认 `list(self.lazy_load())`。

---

## 3. Text Splitter：核心抽象

每个 Splitter 输入 `List[Document]`，输出 `List[Document]`（切碎后的），并保留 metadata。

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)
chunks = splitter.split_documents(docs)
```

参数含义：

| 参数 | 含义 |
|------|------|
| `chunk_size` | 每个 chunk 最大长度（字符/token） |
| `chunk_overlap` | 相邻 chunk 重叠长度，提升上下文连续性 |
| `length_function` | 长度计量函数，默认 `len`（按字符），可换 token |
| `separators` | 切分优先级列表 |
| `keep_separator` | 是否保留分隔符 |
| `add_start_index` | metadata 加 `start_index` |

---

## 4. 五大 Splitter 适用场景

### 4.1 RecursiveCharacterTextSplitter（默认首选）

按 `["\n\n", "\n", " ", ""]` 递归切分，尽量保留语义边界。

```python
RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
```

99% 通用文本用这个就够了。

### 4.2 CharacterTextSplitter

只按单一字符切，简单：

```python
from langchain_text_splitters import CharacterTextSplitter
CharacterTextSplitter(separator="\n\n", chunk_size=500, chunk_overlap=50)
```

### 4.3 Token-based Splitter

按 OpenAI/HF tokenizer 计长度，更贴合模型 context window：

```python
RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    chunk_size=400,
    chunk_overlap=50,
    encoding_name="cl100k_base",
)
# 或用 HF tokenizer
from transformers import GPT2TokenizerFast
tok = GPT2TokenizerFast.from_pretrained("gpt2")
RecursiveCharacterTextSplitter.from_huggingface_tokenizer(tok, chunk_size=...)
```

### 4.4 语言专用

```python
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

python_splitter = RecursiveCharacterTextSplitter.from_language(
    language=Language.PYTHON, chunk_size=400, chunk_overlap=0,
)
md_splitter = RecursiveCharacterTextSplitter.from_language(
    language=Language.MARKDOWN, chunk_size=600,
)
```

支持 Python/JS/Java/Go/Rust/Markdown/HTML/Latex 等十几种，内置语言专用分隔符列表。

### 4.5 结构化文档 Splitter

```python
from langchain_text_splitters import MarkdownHeaderTextSplitter, HTMLHeaderTextSplitter

md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[
    ("#", "h1"), ("##", "h2"), ("###", "h3"),
])
# 输出 metadata 里多了 h1/h2/h3，便于后续检索筛选
chunks = md_splitter.split_text(open("doc.md").read())
```

### 4.6 Semantic Splitter（语义切分）

按嵌入相似度断句：

```python
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai import OpenAIEmbeddings

splitter = SemanticChunker(
    OpenAIEmbeddings(),
    breakpoint_threshold_type="percentile",  # 或 "standard_deviation"
)
chunks = splitter.create_documents([text])
```

比按字符切质量高，但慢且贵，适合关键文档。

---

## 5. 切分策略经验

| 场景 | chunk_size | overlap | 类型 |
|------|------------|---------|------|
| 一般中文文档 | 500-800 字符 | 100 | Recursive |
| 英文长 PDF | 1000-1500 字符 | 200 | Recursive |
| 代码 | 400-800 字符 | 0-50 | Language(Python/...) |
| 表格 / Excel | 一行一个 doc | 0 | 自定义 |
| Markdown 文档 | 按 H2/H3 | 100 | MarkdownHeader + Recursive 二次 |
| FAQ / Q&A | 一条一个 doc | 0 | 自定义不切 |

常见错误：
- chunk 太大 → 检索精度下降、prompt 超长
- chunk 太小 → 上下文不足、模型答不出来
- overlap 0 → 重要句子被切断
- overlap 太大 → 重复信息

---

## 6. Document Transformers

切分之外还有变换操作：

```python
from langchain_community.document_transformers import (
    EmbeddingsRedundantFilter,
    EmbeddingsClusteringFilter,
    LongContextReorder,
    BeautifulSoupTransformer,
)
```

- `EmbeddingsRedundantFilter`：去重相似文档
- `EmbeddingsClusteringFilter`：聚类筛选代表性文档
- `LongContextReorder`：把最相关文档放头尾（缓解 "lost in the middle"）
- `BeautifulSoupTransformer`：HTML → 干净文本

---

## 7. demo

```python
# demos/langchain/10_loader_splitter.py
from pathlib import Path
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    MarkdownHeaderTextSplitter,
    Language,
)

# 1) 加载
docs = TextLoader("docs/01-langchain/01-overview.md", encoding="utf-8").load()
print(f"原始 {len(docs)} 篇, 第一篇长度 {len(docs[0].page_content)}")

# 2) 按 markdown 结构切
md = MarkdownHeaderTextSplitter([("#", "h1"), ("##", "h2"), ("###", "h3")])
md_chunks = md.split_text(docs[0].page_content)
print(f"按 header 切完 {len(md_chunks)} 块, 第一块 metadata：{md_chunks[0].metadata}")

# 3) 二次按 size 切
final = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50).split_documents(md_chunks)
print(f"最终 {len(final)} 块")
for c in final[:2]:
    print("\n---")
    print(c.metadata)
    print(c.page_content[:200])
```

---

## 8. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 中文按字符切句子被砍断 | 标点不在 separators | 加 `"。", "！", "？", "\n"` |
| PDF 解析出乱码 | PDF 是扫描件 | 用 `UnstructuredPDFLoader(mode="elements")` 或 OCR |
| metadata 丢失 | 用 `.split_text` 而非 `.split_documents` | 用后者 |
| chunk 数量爆炸 | size 太小或 overlap 太大 | 调大 size、降 overlap |
| 加载几个 GB 内存爆 | `load()` 一次性 | 用 `lazy_load()` 流式处理 |

---

## 9. 本章 demo

[`demos/langchain/10_loader_splitter.py`](../../demos/langchain/10_loader_splitter.py)

下一篇：[11-vectorstores.md](11-vectorstores.md)
