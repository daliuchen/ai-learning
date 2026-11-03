# EKB 22：文档解析，把原始文件变成结构化文本

> 解析是 ingest 的第一步：把 Markdown、HTML、PDF 这些原始格式，转成纯文本加结构信息（标题层级）再加元数据。结构信息最要紧，后面的语义分块全靠它。这篇以 Markdown 为例，给出一个能直接运行的解析器。

---

## 1. 解析要产出什么

解析不只是把文件读成字符串，它要产出下面这样的结构：

```
原始文件
  │ parse
  ▼
{
  metadata: {title, space, roles, source_url, updated_at},  ← 来自 frontmatter
  blocks: [                                                  ← 带层级的内容块
    {level: 1, heading: "差旅与报销制度", path: "差旅与报销制度"},
    {level: 2, heading: "报销标准", path: "差旅与报销制度 > 报销标准", text: "..."},
    {level: 3, heading: "交通费", path: "...> 报销标准 > 交通费", text: "..."},
  ]
}
```

其中最重要的是 `path`（section_path），它记录每段内容在文档里的位置，分块和引用定位都要用。

---

## 2. 解析 frontmatter

frontmatter 存的是文档元数据，先把它抽出来：

```python
# ingest/parse.py
import re
import yaml

def split_frontmatter(raw: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    if not m:
        return {}, raw
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    return meta, body
```

`meta` 写进 `documents` 表，其中的 `roles` 字段进 `acl` 表；`body` 交给下一步切块。

---

## 3. 按标题层级切成块

遍历 Markdown 的 token，同时维护一个标题栈，用它拼出 `path`：

```python
from markdown_it import MarkdownIt

def parse_blocks(body: str) -> list[dict]:
    md = MarkdownIt()
    tokens = md.parse(body)
    blocks, heading_stack, buf = [], [], []

    def flush(level, heading):
        if buf:  # 上一段正文归到上一个标题
            blocks.append({
                "level": heading_stack[-1][0] if heading_stack else 0,
                "path": " > ".join(h[1] for h in heading_stack),
                "text": "\n".join(buf).strip(),
            })
            buf.clear()

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            level = int(t.tag[1])               # h1 → 1
            heading = tokens[i + 1].content
            flush(level, heading)
            # 维护标题栈：弹出 >= 当前层级的
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))
            i += 3
        elif t.type == "inline":
            buf.append(t.content)
            i += 1
        else:
            i += 1
    flush(0, None)
    return [b for b in blocks if b["text"]]
```

产出的每个 block 都带着 `path`（例如 `差旅与报销制度 > 报销标准 > 交通费`）和 `text`。

---

## 4. HTML 和 PDF

格式不同，解析手段也不同，但目标产物都是带层级的 blocks：

| 格式 | 工具 | 要点 |
|------|------|------|
| Markdown | markdown-it-py | 标题层级清晰，最好处理 |
| HTML | BeautifulSoup | 按 `<h1>~<h3>` 切，去掉导航/页脚噪声 |
| PDF | pdfplumber / unstructured | 最难，版面易乱，可能丢层级 |
| Confluence/Notion | 各自 API | 通常能拿到结构化数据，最省事 |

PDF 是最麻烦的来源：扫描件要 OCR，双栏排版会乱序，表格会散架。能拿到 Markdown 或 HTML 源文件，就别从 PDF 解析。企业里的文档质量参差不齐时，解析阶段的清洗工作量往往被低估。

---

## 5. 顺手清洗

解析时最好顺带清掉噪声，否则这些垃圾会一路带进检索：

- 去掉导航栏、页眉页脚、「编辑本页」之类的模板文字
- 合并被错误断开的段落
- 保留表格结构，转成 Markdown 表格，别散成一行一行
- 纯图片记录下来但跳过，或者调用多模态模型生成图片描述

解析这一步没清干净，后面的 embedding 和检索做得再好也救不回来。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只读纯文本，丢了标题层级 | 语义分块没依据 | 保留 path/level |
| frontmatter 没解析 | 权限和引用元数据丢失 | 先拆出 frontmatter |
| 从 PDF 硬解析 | 版面乱、层级丢 | 优先用 md/html 源 |
| 不清洗模板噪声 | 噪声污染检索 | 解析时去掉导航和页脚 |
| 表格散成单行 | 表格里的信息检索不到 | 保留表格结构 |

---

## 下一步

有了带层级的 blocks，下一篇讲怎么切成大小合适的 chunk：[语义分块](./03-semantic-chunking.md)
