# 文档结构感知切分：PDF / Markdown / HTML / 表格

> **一句话**：文档有 heading / table / code block / list 时，**按结构切**比纯字符切好得多——chunk 保留完整逻辑单元，metadata 自动带上 section 信息。

---

## 1. Markdown：按 Heading 切

```python
from langchain_text_splitters import MarkdownHeaderTextSplitter


headers_to_split_on = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]


splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)


md = """
# 订阅管理

## 取消订阅
登录账户 → 设置 → 取消

## 退款
- 7 天内全额
- 7-30 天按比例

# 账户安全

## 修改密码
设置 → 安全 → 修改密码
"""


chunks = splitter.split_text(md)
# 每个 chunk 是 Document 对象，自带 metadata
for c in chunks:
    print(c.metadata, "→", c.page_content[:50])
```

输出：

```
{"h1": "订阅管理", "h2": "取消订阅"} → 登录账户 → 设置 → 取消
{"h1": "订阅管理", "h2": "退款"} → - 7 天内全额\n- 7-30 天按比例
{"h1": "账户安全", "h2": "修改密码"} → 设置 → 安全 → 修改密码
```

每个 chunk 自动带 heading 路径——LLM 拿到时能理解上下文。

---

## 2. Markdown 复合切分

heading 切完一些 chunk 仍太大 → 再用 recursive 切：

```python
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


# 第一步：按 heading
md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "h1"), ("##", "h2")])
header_chunks = md_splitter.split_text(md)


# 第二步：每个 header chunk 内部再切
char_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)


final_chunks = []
for hc in header_chunks:
    sub = char_splitter.split_text(hc.page_content)
    for s in sub:
        final_chunks.append({
            "text": s,
            "h1": hc.metadata.get("h1"),
            "h2": hc.metadata.get("h2"),
        })
```

---

## 3. HTML：按 tag 切

```python
from langchain_text_splitters import HTMLHeaderTextSplitter


headers = [
    ("h1", "h1"),
    ("h2", "h2"),
    ("h3", "h3"),
]


splitter = HTMLHeaderTextSplitter(headers_to_split_on=headers)


html = """
<h1>订阅管理</h1>
<h2>取消订阅</h2>
<p>登录账户 → 设置 → 取消</p>
<h2>退款</h2>
<ul><li>7 天内全额</li><li>7-30 天按比例</li></ul>
"""


chunks = splitter.split_text_from_url(url="https://...")
# 或直接 split_text(html)
```

---

## 4. PDF：用 unstructured 提结构

PDF 没明确 heading 但有视觉结构（字体大小 / 加粗 / 段落）。

```python
# pip install unstructured pypdf
from unstructured.partition.pdf import partition_pdf


elements = partition_pdf("doc.pdf")


for el in elements:
    print(el.category, el.text[:80])
    # Title / NarrativeText / ListItem / Table / FigureCaption ...
```

`unstructured` 把 PDF 拆成结构化 elements。按 category 组装 chunk：

```python
chunks = []
current = []
current_title = None


for el in elements:
    if el.category == "Title":
        if current:
            chunks.append({"title": current_title, "text": "\n".join(current)})
        current_title = el.text
        current = []
    elif el.category == "Table":
        # 表格独立成 chunk
        chunks.append({"title": current_title, "text": el.text, "type": "table"})
    else:
        current.append(el.text)


if current:
    chunks.append({"title": current_title, "text": "\n".join(current)})
```

---

## 5. 处理 Table

表格切碎了完全没用。两种处理：

### 5.1 把表格转 markdown / CSV 整体当 chunk

```python
def table_to_markdown(table_data):
    """把表格转成 markdown，整体当一个 chunk"""
    rows = table_data["rows"]
    md = "| " + " | ".join(rows[0]) + " |\n"
    md += "|" + "|".join(["---"] * len(rows[0])) + "|\n"
    for row in rows[1:]:
        md += "| " + " | ".join(row) + " |\n"
    return md
```

### 5.2 把表格每行当一个 chunk

```python
def table_to_row_chunks(table_data, doc_title):
    chunks = []
    header = table_data["rows"][0]
    for row in table_data["rows"][1:]:
        chunk_text = " | ".join(f"{h}: {v}" for h, v in zip(header, row))
        chunks.append({
            "text": chunk_text,
            "doc_title": doc_title,
            "type": "table_row",
        })
    return chunks
```

按"是否需要逐行检索"选。

---

## 6. 代码：按函数 / 类切

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language


py_splitter = RecursiveCharacterTextSplitter.from_language(
    language=Language.PYTHON,
    chunk_size=1000,
    chunk_overlap=100,
)


js_splitter = RecursiveCharacterTextSplitter.from_language(
    language=Language.JS,
    chunk_size=1000,
)
```

LangChain 内置 Python / JS / TS / Markdown / HTML / Solidity / Go / Rust / C++ 等 splitter——按对应语言的 keyword（`def`, `class`, `function`）切。

或者用 **tree-sitter** 解析 AST 后按节点切（更精准）。

---

## 7. JSON / YAML：按 key 切

```python
import json


def chunk_json(data, prefix=""):
    chunks = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)):
                chunks.append(f"{prefix}{k}: {v}")
            else:
                chunks.extend(chunk_json(v, f"{prefix}{k}."))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            chunks.extend(chunk_json(item, f"{prefix}[{i}]."))
    return chunks
```

但通常 JSON 数据更适合**字段级 embed**（每个 key-value 单独 embed）。

---

## 8. 论文 / 长报告

PDF 论文有明显结构：

```
Abstract → Intro → Method → Experiments → Conclusion → References
```

按 section 切：

```python
sections = ["Abstract", "Introduction", "Methods", "Results", "Discussion", "References"]


def split_paper(text):
    chunks = []
    current_section = None
    buffer = []
    for line in text.split("\n"):
        if any(line.strip().startswith(s) for s in sections):
            if buffer:
                chunks.append({"section": current_section, "text": "\n".join(buffer)})
            current_section = line.strip()
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        chunks.append({"section": current_section, "text": "\n".join(buffer)})
    return chunks
```

---

## 9. 完整 demo：Markdown 文档

```python
# demos/chunking/03_structure.py
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


md = """# 用户手册

## 第一章 注册账号

### 1.1 邮箱注册
访问 example.com，点击"注册"，输入邮箱和密码。
验证邮件会发到你的邮箱，点击链接激活。

### 1.2 手机号注册
输入手机号，收到短信验证码后输入。

## 第二章 订阅管理

### 2.1 选择套餐
我们提供 Free / Pro / Enterprise 三种套餐。

### 2.2 取消订阅
登录后进入设置 → 账户 → 订阅，点击"取消"。
"""


# 第一步：按 h1 / h2 / h3 切
md_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
)
header_chunks = md_splitter.split_text(md)


# 第二步：太长的再按字符切
char_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=30)


final = []
for hc in header_chunks:
    parts = char_splitter.split_text(hc.page_content)
    for p in parts:
        final.append({
            "text": p,
            "metadata": hc.metadata,
        })


for c in final:
    print(f"{c['metadata']}: {c['text'][:60]}...")
```

输出：

```
{'h1': '用户手册', 'h2': '第一章 注册账号', 'h3': '1.1 邮箱注册'}: 访问 example.com，点击"注册"，输入邮箱和密码。验证邮件...
{'h1': '用户手册', 'h2': '第一章 注册账号', 'h3': '1.2 手机号注册'}: 输入手机号，收到短信验证码后输入。...
...
```

---

## 10. metadata 自动带 path

```python
def add_section_to_text(chunk):
    """让 LLM 看到 chunk 时知道 section"""
    meta = chunk["metadata"]
    path = " > ".join(filter(None, [meta.get("h1"), meta.get("h2"), meta.get("h3")]))
    return f"[Section: {path}]\n{chunk['text']}"


for c in final:
    embed_text = add_section_to_text(c)
    # embed + index
```

LLM 拿到 chunk 时也能看到 section path，回答更准。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| PDF 用纯字符切 | 用 unstructured / pdfplumber 提取结构 |
| 表格切碎 | 整表 markdown 或行级 chunk |
| 代码按字符切 | 用 Language splitter |
| heading 切完不再细切 | 太长的 chunk 还要二次切 |
| metadata 丢失 | 切之前先记好 doc / section / page |

---

## 12. 下一步

- 📖 多粒度（small-to-big）→ [04-small-to-big.md](./04-small-to-big.md)
- 📖 chunk metadata 设计 → [05-metadata.md](./05-metadata.md)
- 📖 检索：让 small chunk 召回，big chunk 给 LLM → [05-retrieval](../05-retrieval)
