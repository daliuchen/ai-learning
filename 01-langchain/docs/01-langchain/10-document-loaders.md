# LangChain 10：Document Loaders 与 Text Splitters

> Loader 负责把文件、URL、数据库里的内容读成 `List[Document]`，Splitter 再把大文档切成适合向量化的小块。RAG 效果的上限往往在这两步就定了：文本读错、切得不好，后面检索和生成怎么调都补不回来。

版本：以 `langchain 1.4`、`langchain-community 0.4`、`langchain-text-splitters 1.x` 为准。Loader 和切分器都不在 `langchain` 主包里，要单独安装这两个包。

---

## 1. Document 是什么

```python
from langchain_core.documents import Document

doc = Document(
    page_content="文本内容",
    metadata={"source": "guide.pdf", "page": 3, "author": "x"},
)
```

`metadata` 会一路跟到向量库里，之后过滤、标注出处、回链原文都靠它，加载时就值得把字段填全。

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

## 4. 各种 Splitter 的适用场景

### 4.1 RecursiveCharacterTextSplitter（默认首选）

按 `["\n\n", "\n", " ", ""]` 递归切分，尽量保留语义边界。

```python
RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
```

绝大多数通用文本用它就够了。

### 4.2 CharacterTextSplitter

只按一个分隔符切，逻辑最简单：

```python
from langchain_text_splitters import CharacterTextSplitter
CharacterTextSplitter(separator="\n\n", chunk_size=500, chunk_overlap=50)
```

### 4.3 Token-based Splitter

用 OpenAI 或 HF 的 tokenizer 计长度，和模型的 context window 对得更准：

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

支持 Python、JS、Java、Go、Rust、Markdown、HTML、Latex 等十几种语言，每种都内置了对应的分隔符列表。

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

切出来的块比按字符切更完整，但要为每句话算 embedding，又慢又花钱，更适合留给重要文档。

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

chunk 太大，检索精度会下降，拼进 prompt 也太长；太小则上下文不够，模型答不出来。overlap 设成 0 容易把关键句子切断，设得太大又会塞进大量重复内容。

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
- `LongContextReorder`：把最相关的文档放到头尾，缓解 lost in the middle 问题
- `BeautifulSoupTransformer`：HTML → 干净文本

---

## 7. 本章 demo

完整代码：[`demos/langchain/10_loader_splitter.py`](../../demos/langchain/10_loader_splitter.py)

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
| PDF 解析出空文本或乱码 | 扫描件没有文字层；乱码多半是字体编码问题 | 扫描件要走 OCR（如 `UnstructuredPDFLoader(..., strategy="ocr_only")`）；乱码换 `PyMuPDFLoader` 等其他解析器试试 |
| metadata 丢失 | 用 `.split_text` 而非 `.split_documents` | 用后者 |
| chunk 数量爆炸 | size 太小或 overlap 太大 | 调大 size、降 overlap |
| 加载几个 GB 内存爆 | `load()` 一次性 | 用 `lazy_load()` 流式处理 |
| `ImportError: ... pip install pypdf / unstructured ...` | Loader 在 `langchain-community`，具体解析库是各自的可选依赖 | 按报错提示装 `pypdf`、`beautifulsoup4`、`unstructured` 等 |
| `from langchain.document_loaders import ...` 找不到 | 1.x 的 `langchain` 主包不再转发 | 改 `from langchain_community.document_loaders import ...`；切分器用 `langchain_text_splitters` |
| `SemanticChunker` 导入失败 | 它在实验包里 | `pip install langchain-experimental`，`from langchain_experimental.text_splitter import SemanticChunker` |

---

下一篇：[LangChain 11：Embeddings 与 Vector Stores](11-vectorstores.md)
