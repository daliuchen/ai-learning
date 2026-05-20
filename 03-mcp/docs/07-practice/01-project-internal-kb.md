# MCP Practice 01：内部知识库 MCP Server

> **一句话**：把团队内部文档（Markdown / Notion 导出 / Confluence 等）做成 MCP Server——既能让 Claude Code 直接搜，也能让你的 LangChain Agent 当 RAG 数据源。本项目用 ChromaDB + sentence-transformers 做向量检索，全程 100 行 Python。

---

## 1. 项目目标

- **输入**：一个文件夹的 markdown 文件（可换成任何文本来源）
- **输出**：MCP Server 暴露 3 个能力
  - Tool: `search_kb(query, top_k=5)` — 语义搜索
  - Tool: `keyword_search_kb(keyword)` — 关键词精确搜索
  - Resource: `kb://doc/{doc_id}` — 按 ID 读全文
- **使用方**：
  - Claude Code 直接调（写代码时问"我们 PR 流程是怎么样的"）
  - LangChain / Pydantic AI Agent 当 RAG

---

## 2. 设计决策

| 问题 | 决定 |
|------|------|
| 向量库 | ChromaDB（本地持久化、轻量） |
| Embedding | `sentence-transformers` 的 `paraphrase-multilingual-MiniLM-L12-v2`（多语言） |
| 传输 | stdio（本地用）+ 可选 streamable-http（团队共享） |
| 数据加载 | 启动时一次性 index 整个目录到 Chroma |
| 增量更新 | 文件 watcher（生产可加，本 demo 简化） |
| 鉴权 | stdio 模式不用；HTTP 模式用 Bearer Token |

---

## 3. 目录结构

```
demos/practice/internal_kb/
├── server.py              # MCP Server 主程序
├── indexer.py             # 文档索引器
├── chroma/                # ChromaDB 持久化目录（运行时创建）
├── docs/                  # 知识库源文件
│   ├── pr-process.md
│   ├── onboarding.md
│   └── faq.md
└── README.md
```

---

## 4. 完整代码

### 4.1 `indexer.py` — 文档索引

```python
# demos/practice/internal_kb/indexer.py
"""把 docs/ 下所有 .md 索引到 ChromaDB"""
from __future__ import annotations

import hashlib
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions


def get_collection(persist_dir: Path):
    """打开或创建 chroma 集合，多语言 embedding"""
    client = chromadb.PersistentClient(path=str(persist_dir))
    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2",
    )
    return client.get_or_create_collection(
        name="kb",
        embedding_function=emb_fn,
    )


def _doc_id(path: Path) -> str:
    """用相对路径 hash 作为 ID（防重复 + 稳定）"""
    return hashlib.md5(str(path).encode()).hexdigest()[:12]


def _chunks(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """简单按字数切块（生产建议按 markdown header 切）"""
    if len(text) <= chunk_size:
        return [text]
    out = []
    start = 0
    while start < len(text):
        out.append(text[start:start + chunk_size])
        start += chunk_size - overlap
    return out


def index_directory(docs_dir: Path, persist_dir: Path) -> int:
    """全量索引 docs_dir 下所有 .md"""
    persist_dir.mkdir(parents=True, exist_ok=True)
    coll = get_collection(persist_dir)

    # 先清空（demo 简化；生产应该按文件 mtime 增量）
    try:
        coll.delete(where={"_marker": "all"})
    except Exception:
        pass

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    for f in sorted(docs_dir.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        rel = str(f.relative_to(docs_dir))
        base_id = _doc_id(f)
        for i, chunk in enumerate(_chunks(text)):
            ids.append(f"{base_id}_{i}")
            docs.append(chunk)
            metas.append({
                "doc_id": base_id,
                "path": rel,
                "chunk_index": i,
                "_marker": "all",
            })
    if ids:
        coll.add(ids=ids, documents=docs, metadatas=metas)
    return len(ids)
```

### 4.2 `server.py` — MCP Server

```python
# demos/practice/internal_kb/server.py
"""内部知识库 MCP Server"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from indexer import get_collection, index_directory

ROOT = Path(__file__).resolve().parent
DOCS_DIR = Path(os.getenv("KB_DOCS_DIR", ROOT / "docs"))
PERSIST_DIR = Path(os.getenv("KB_VECTOR_DIR", ROOT / "chroma"))


@dataclass
class AppCtx:
    coll: object  # chromadb Collection


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    # 启动时索引
    chunks = index_directory(DOCS_DIR, PERSIST_DIR)
    print(f"[KB] 已索引 {chunks} 个 chunk", flush=True)  # 走 stderr 重定向
    coll = get_collection(PERSIST_DIR)
    try:
        yield AppCtx(coll=coll)
    finally:
        pass  # chromadb persistent client 自动清理


mcp = FastMCP("internal-kb", lifespan=app_lifespan)


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False}
)
async def search_kb(
    query: str,
    top_k: int = 5,
    ctx: Context = None,
) -> list[dict]:
    """语义搜索内部知识库。

    Args:
        query: 自然语言问题，例如"我们的 PR 流程是怎样的"
        top_k: 返回最相关的 N 条，默认 5

    返回 list[{doc_id, path, snippet, score}]
    """
    app: AppCtx = ctx.request_context.lifespan_context
    res = app.coll.query(query_texts=[query], n_results=top_k)
    if not res["ids"] or not res["ids"][0]:
        return []
    out = []
    for i, doc_text in enumerate(res["documents"][0]):
        meta = res["metadatas"][0][i]
        out.append({
            "doc_id": meta["doc_id"],
            "path": meta["path"],
            "snippet": doc_text[:300],
            "score": float(res["distances"][0][i]) if "distances" in res else None,
        })
    return out


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False}
)
async def keyword_search_kb(
    keyword: str,
    ctx: Context = None,
) -> list[dict]:
    """关键词精确搜索（不走向量，直接 grep）。"""
    app: AppCtx = ctx.request_context.lifespan_context
    keyword_lower = keyword.lower()
    out = []
    for f in DOCS_DIR.rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        if keyword_lower in text.lower():
            # 找到匹配上下文
            idx = text.lower().find(keyword_lower)
            start = max(0, idx - 80)
            end = min(len(text), idx + len(keyword) + 80)
            out.append({
                "path": str(f.relative_to(DOCS_DIR)),
                "snippet": text[start:end],
            })
    return out


@mcp.resource("kb://doc/{doc_id}", mime_type="text/markdown")
def get_doc(doc_id: str) -> str:
    """按 doc_id 读完整文档"""
    app = mcp.get_context().request_context.lifespan_context  # AppCtx
    # 取该 doc 所有 chunk
    res = app.coll.get(where={"doc_id": doc_id})
    if not res or not res["ids"]:
        raise FileNotFoundError(f"未找到文档: {doc_id}")
    # 按 chunk_index 排序拼回
    sorted_chunks = sorted(
        zip(res["documents"], res["metadatas"]),
        key=lambda x: x[1]["chunk_index"],
    )
    return "\n".join(c for c, _ in sorted_chunks)


@mcp.resource("kb://index", mime_type="application/json")
def index_listing() -> list[dict]:
    """列出所有索引文档"""
    app = mcp.get_context().request_context.lifespan_context
    res = app.coll.get(where={"_marker": "all"})
    seen = {}
    for meta in res["metadatas"]:
        seen.setdefault(meta["doc_id"], meta["path"])
    return [{"doc_id": k, "path": v} for k, v in seen.items()]


if __name__ == "__main__":
    mcp.run()
```

### 4.3 示例 docs

```markdown
<!-- docs/pr-process.md -->
# PR 流程

我们团队的 PR 标准流程：

1. 从 main 拉新分支：`git checkout -b feat/xxx`
2. 写代码 + 自测
3. 提 PR，标题用 conventional commit 格式
4. 至少一个 reviewer 批准 + CI 全绿才 merge
5. squash merge 后删分支
```

```markdown
<!-- docs/onboarding.md -->
# 新人入职流程

第一天：
- 拿到公司邮箱 + GitHub access
- 装好 Python 3.12 + uv
- clone monorepo

第一周：
- 跑通本地开发环境
- 看完 PR 流程文档
- 找 mentor 一对一
```

---

## 5. 跑起来

```bash
cd 03-mcp
pip install -r requirements.txt

# 准备 docs/
mkdir -p demos/practice/internal_kb/docs
cp README.md demos/practice/internal_kb/docs/  # 用本手册的 README 也行

# 跑 Server（stdio）
python demos/practice/internal_kb/server.py
```

或用 Inspector：

```bash
npx @modelcontextprotocol/inspector python demos/practice/internal_kb/server.py
```

Inspector 里：
- `search_kb(query="PR 流程", top_k=3)` 看语义搜索结果
- `keyword_search_kb(keyword="ChromaDB")` 看精确匹配
- `kb://index` 看所有文档列表
- `kb://doc/<id>` 读全文

---

## 6. 接到 Claude Code

```json
{
  "mcpServers": {
    "internal-kb": {
      "command": "python",
      "args": [
        "/abs/path/to/03-mcp/demos/practice/internal_kb/server.py"
      ],
      "env": {
        "KB_DOCS_DIR": "/abs/path/to/03-mcp/demos/practice/internal_kb/docs",
        "KB_VECTOR_DIR": "/abs/path/to/03-mcp/demos/practice/internal_kb/chroma"
      }
    }
  }
}
```

现在在 Claude Code 里直接问：

```
我们的 PR 流程是怎样的？
```

Claude 自动调 `search_kb`，拿到结果再回答。

---

## 7. 接到 LangChain

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

client = MultiServerMCPClient({
    "kb": {
        "command": "python",
        "args": ["/abs/path/internal_kb/server.py"],
        "transport": "stdio",
    },
})
tools = await client.get_tools()

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), tools)
result = await agent.ainvoke({"messages": [("user", "PR 流程是怎样的")]})
```

---

## 8. 部署成远程 Server

把 `mcp.run()` 改成：

```python
mcp.run(transport="streamable-http")
# 默认监听 127.0.0.1:8000/mcp
```

或挂 FastAPI + Docker（按 05-production/01-remote-mcp），再加 OAuth（按 05-production/02-auth-oauth）。

---

## 9. 扩展方向

| 想加 | 怎么做 |
|------|--------|
| 支持 Notion / Confluence | 重写 indexer.py，加 connector |
| 增量更新 | 用 watchdog 监听文件变化，触发 reindex（FastMCP 里发 `notifications/resources/list_changed`） |
| 重排（rerank） | 在 search_kb 里加 cross-encoder 二次排序 |
| 多租户 | 用 OAuth + collection per user |
| 中文分词 | 切块前用 jieba 切词增强 keyword search |
| 引用展示 | 在工具描述里说明返回 snippet 已带 path，让 Claude 引用源 |

---

## 10. 测试

```python
# tests/test_kb.py
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

@pytest.mark.asyncio
async def test_search():
    params = StdioServerParameters(
        command="python",
        args=["demos/practice/internal_kb/server.py"],
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_kb",
                {"query": "PR 流程", "top_k": 3},
            )
            assert not result.isError
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **首次启动慢** | sentence-transformers 下模型，第一次 ~200MB |
| **chroma 数据丢失** | 检查 PERSIST_DIR 是绝对路径，别用 /tmp |
| **中文 search 不准** | 用多语言 embedding；中文文档优先用 keyword_search 兜底 |
| **大文档切块不智能** | 用 langchain 的 MarkdownHeaderTextSplitter 或 spacy |
| **search_kb 返回 distance 而非相似度** | chroma 用距离（越小越相似），看场景做归一化 |

---

## 12. 下一步

- 📖 数据库 MCP（SQL 安全） → [02-project-db-mcp.md](./02-project-db-mcp.md)
- 📖 Claude Code 自定义工具 → [03-project-claude-code-tool.md](./03-project-claude-code-tool.md)

## 参考资料

- ChromaDB：https://docs.trychroma.com
- sentence-transformers：https://www.sbert.net
- FastMCP lifespan：02-server/04-lifespan-context.md（本手册）
