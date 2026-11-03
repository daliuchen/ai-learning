# MCP Practice 01：内部知识库 MCP Server

> 这个项目把团队内部文档（Markdown、Notion 导出、Confluence 等）做成一个 MCP Server，Claude Code 可以直接搜索，LangChain 或 Pydantic AI 的 Agent 也可以把它当 RAG 数据源。语义检索用的是 OpenAI 兼容的 Embedding 接口（默认千问 `text-embedding-v4`）加 numpy 余弦相似度，没配 Key 时自动退回关键词检索。它不依赖 ChromaDB、PyTorch 这类重量级的包，想换成专业的向量库，只需要改 `indexer.py`。

---

## 1. 项目目标

输入是一个装着 markdown 文件的文件夹（也可以换成任何文本来源）。输出是一个 MCP Server，暴露三项能力：

- Tool：`search_kb(query, top_k=5)`，语义搜索
- Tool：`keyword_search_kb(keyword)`，关键词精确搜索
- Resource：`kb://doc/{doc_id}`（按 ID 读取全文）和 `kb://index`（文档列表）

使用方有两类：Claude Code 直接调用（比如写代码时问「我们的 PR 流程是怎样的」），以及 LangChain / Pydantic AI 的 Agent 把它当 RAG 用。

---

## 2. 设计决策

| 问题 | 决定 |
|------|------|
| 向量库 | 内存里的 numpy 矩阵加磁盘上的 `.npz` 缓存（几百篇文档足够；规模大了再换 Chroma / pgvector / Qdrant） |
| Embedding | OpenAI 兼容接口，默认千问 `text-embedding-v4`（支持多语言，便宜）。本地的 `sentence-transformers` 要装 PyTorch 和几百 MB 的权重，这里改成了可选 |
| 没有 Key 时 | 退回关键词检索；`search_kb` 返回一个对模型友好的 `ToolError`，提示改用关键词搜索 |
| 传输 | stdio（本地用），可选 streamable-http（团队共享） |
| 数据加载 | 启动时在 `lifespan` 里建一次索引，所有请求共享；内容没变就复用磁盘缓存，不用每次启动都花 embedding 的钱 |
| 增量更新 | 文件 watcher 加 `ctx.notify_resources_changed()`（这个 demo 做了简化） |
| 鉴权 | stdio 模式不需要；HTTP 模式用 `TokenVerifier`（见[远程 MCP 的 OAuth 2.1 鉴权](../05-production/02-auth-oauth.md)） |

---

## 3. 目录结构

```
demos/practice/internal_kb/
├── server.py              # MCP Server 主程序（MCPServer + lifespan）
├── indexer.py             # 切块 + embedding + 语义检索
├── docs/                  # 知识库源文件
│   ├── pr-process.md
│   ├── onboarding.md
│   └── faq.md
└── .cache/                # embedding 缓存（运行时创建，文件按内容哈希命名）
```

---

## 4. 完整代码

### 4.1 `indexer.py`：切块、向量化、检索

```python
# demos/practice/internal_kb/indexer.py
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Chunk:
    doc_id: str
    path: str
    index: int
    text: str


@dataclass
class KBIndex:
    chunks: list[Chunk] = field(default_factory=list)
    vectors: np.ndarray | None = None  # (n_chunks, dim)，已 L2 归一化
    semantic: bool = False


def doc_id_of(rel_path: str) -> str:
    """用相对路径 hash 作为稳定 ID"""
    return hashlib.md5(rel_path.encode()).hexdigest()[:12]


def split_markdown(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """简单按字数切块（生产建议按 markdown 标题切）"""
    if len(text) <= chunk_size:
        return [text]
    out, start = [], 0
    while start < len(text):
        out.append(text[start : start + chunk_size])
        start += chunk_size - overlap
    return out


def _embed(texts: list[str]) -> np.ndarray:
    from openai import OpenAI

    client = OpenAI()  # OPENAI_API_KEY / OPENAI_BASE_URL（千问兼容模式）
    model = os.environ.get("QWEN_EMBED_MODEL", "text-embedding-v4")
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 10):  # 百炼 embedding 单次最多 10 条
        resp = client.embeddings.create(model=model, input=texts[i : i + 10])
        vecs.extend(d.embedding for d in resp.data)
    arr = np.asarray(vecs, dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def build_index(docs_dir: Path, cache_dir: Path | None = None) -> KBIndex:
    idx = KBIndex()
    for f in sorted(docs_dir.rglob("*.md")):
        rel = str(f.relative_to(docs_dir))
        for i, piece in enumerate(split_markdown(f.read_text(encoding="utf-8"))):
            idx.chunks.append(Chunk(doc_id_of(rel), rel, i, piece))
    if not idx.chunks or not os.environ.get("OPENAI_API_KEY"):
        return idx  # 无 Key：降级为关键词检索

    # 内容没变就复用磁盘缓存，别每次启动都花钱
    sig = hashlib.md5(json.dumps([c.text for c in idx.chunks]).encode()).hexdigest()
    cache = (cache_dir / f"{sig}.npz") if cache_dir else None
    if cache and cache.exists():
        idx.vectors = np.load(cache)["v"]
    else:
        idx.vectors = _embed([c.text for c in idx.chunks])
        if cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.savez(cache, v=idx.vectors)
    idx.semantic = True
    return idx


def semantic_search(idx: KBIndex, query: str, top_k: int) -> list[tuple[Chunk, float]]:
    q = _embed([query])[0]
    scores = idx.vectors @ q  # 余弦相似度（向量已归一化）；越大越相似
    order = np.argsort(-scores)[:top_k]
    return [(idx.chunks[i], float(scores[i])) for i in order]
```

### 4.2 `server.py`：MCP Server

```python
# demos/practice/internal_kb/server.py
from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # 让 `python server.py` 和 Client 起的子进程都能 import indexer

from indexer import KBIndex, build_index, semantic_search  # noqa: E402

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver import Context  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

logger = logging.getLogger("kb")  # 日志走 stderr，别 print
ROOT = Path(__file__).resolve().parent
DOCS_DIR = Path(os.getenv("KB_DOCS_DIR", ROOT / "docs"))
CACHE_DIR = Path(os.getenv("KB_CACHE_DIR", ROOT / ".cache"))


_INDEX: KBIndex | None = None  # 静态资源（URI 里没有 {变量}）不能声明 Context 参数，只能读模块级状态


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[KBIndex]:
    global _INDEX
    # 启动时索引一次，所有请求共享（v2：HTTP 模式 lifespan 也只跑一次）
    idx = _INDEX = await asyncio.to_thread(build_index, DOCS_DIR, CACHE_DIR)
    logger.info("已索引 %d 个 chunk（语义检索=%s）", len(idx.chunks), idx.semantic)
    yield idx


mcp = MCPServer(
    "internal-kb",
    instructions="团队内部知识库。回答流程/规范/FAQ 类问题前，先用 search_kb 或 keyword_search_kb 查，并引用返回的 path。",
    lifespan=app_lifespan,
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@mcp.tool(annotations=READ_ONLY)
async def search_kb(query: str, ctx: Context[KBIndex], top_k: int = 5) -> list[dict]:
    """语义搜索内部知识库。

    Args:
        query: 自然语言问题，例如"我们的 PR 流程是怎样的"
        top_k: 返回最相关的 N 条，默认 5

    返回 list[{doc_id, path, snippet, score}]，score 是余弦相似度（越大越相似）。
    """
    idx = ctx.request_context.lifespan_context
    if not idx.semantic:
        raise ToolError("语义检索未启用（没有配置 embedding 的 API Key）；请改用 keyword_search_kb")
    hits = await asyncio.to_thread(semantic_search, idx, query, top_k)
    return [{"doc_id": c.doc_id, "path": c.path, "snippet": c.text[:300], "score": round(s, 4)} for c, s in hits]


@mcp.tool(annotations=READ_ONLY)
def keyword_search_kb(keyword: str, ctx: Context[KBIndex]) -> list[dict]:
    """关键词精确搜索（不走向量，直接匹配）。"""
    idx = ctx.request_context.lifespan_context
    kw = keyword.lower()
    out, seen = [], set()
    for c in idx.chunks:
        pos = c.text.lower().find(kw)
        if pos >= 0 and (c.path not in seen):
            seen.add(c.path)
            out.append({"path": c.path, "snippet": c.text[max(0, pos - 80) : pos + len(keyword) + 80]})
    return out


@mcp.resource("kb://doc/{doc_id}", mime_type="text/markdown")
def get_doc(doc_id: str, ctx: Context[KBIndex]) -> str:
    """按 doc_id 读完整文档（资源函数也能声明 ctx；v1 的 mcp.get_context() 已删除）"""
    idx = ctx.request_context.lifespan_context
    chunks = sorted((c for c in idx.chunks if c.doc_id == doc_id), key=lambda c: c.index)
    if not chunks:
        raise ResourceNotFoundError(f"未找到文档: {doc_id}")  # 客户端收到 -32602，data 里带 URI
    return "\n".join(c.text for c in chunks)  # 注意：按字符切块有重叠，这里仅作演示；生产里直接读原文件


@mcp.resource("kb://index", mime_type="application/json")
def index_listing() -> list[dict]:
    """列出所有索引文档（静态资源：不能带 ctx，所以读 lifespan 里设置的模块级 _INDEX）"""
    seen: dict[str, str] = {}
    for c in (_INDEX.chunks if _INDEX else []):
        seen.setdefault(c.doc_id, c.path)
    return [{"doc_id": k, "path": v} for k, v in seen.items()]


async def selftest() -> None:
    from mcp import Client

    async with Client(mcp) as client:
        print("语义检索:", "开启" if os.environ.get("OPENAI_API_KEY") else "关闭（无 API Key，降级为关键词）")
        print("keyword 'CI'   ->", [h["path"] for h in (await client.call_tool("keyword_search_kb", {"keyword": "CI"})).structured_content["result"]])
        r = await client.call_tool("search_kb", {"query": "怎么报销发票", "top_k": 2})
        if r.is_error:
            print("search_kb      ->", r.content[0].text)
        else:
            print("search_kb      ->", [(h["path"], h["score"]) for h in r.structured_content["result"]])
        listing = (await client.read_resource("kb://index")).contents[0].text
        print("kb://index     ->", listing.replace("\n", "")[:90], "...")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    else:
        mcp.run()
```

下面几点都是从 v1 迁到 v2 时容易出错的地方：

- 在 `lifespan` 里建索引，工具里用 `ctx.request_context.lifespan_context` 取出来；类型提示写成 `Context[KBIndex]`，IDE 就知道有哪些字段
- 静态资源（URI 里没有 `{变量}`）不能声明 `Context` 参数，否则启动时就会报 `ValueError: ... Context injection for static resources is not supported`。带模板变量的资源（`kb://doc/{doc_id}`）可以声明 `ctx`；v1 的 `mcp.get_context()` 已经删除了。静态资源只能读模块级的状态（代码里的 `_INDEX`），或者改成带变量的模板
- 资源不存在时抛 `ResourceNotFoundError`（客户端收到 `-32602`，`data` 里带着 URI），不要抛 `FileNotFoundError`
- 日志用标准库 `logging`（写 stderr），不要用 `print`，也不要用已经弃用的 `ctx.info()`
- 同步工具 `def keyword_search_kb` 在 v2 里跑在工作线程上，不会阻塞事件循环；比较耗时的 embedding 调用用 `asyncio.to_thread` 包起来
- `ToolAnnotations(read_only_hint=True)`：Claude Code 会并行执行只读工具，连接中途断开时还会自动重试
- `instructions=`：工具很多时，Claude Code 启动时只加载工具名字和这段说明（tool search），所以要写清楚什么时候应该来搜这个 Server

### 4.3 示例文档

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

（另外还有 `onboarding.md` 和 `faq.md`，内容见 `demos/practice/internal_kb/docs/`。）

---

## 5. 运行

```bash
cd 03-mcp
pip install "mcp>=2.3" openai numpy         # 不需要 chromadb / sentence-transformers

# 进程内自测：设了 Key 走语义检索，没设自动降级
export OPENAI_API_KEY=...                   # 不要写进代码或提交
export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export QWEN_EMBED_MODEL=text-embedding-v4
python demos/practice/internal_kb/server.py --selftest
```

输出示例（千问 `text-embedding-v4`，相似度分数会随模型版本略有出入）：

```
语义检索: 开启
keyword 'CI'   -> ['pr-process.md']
search_kb      -> [('faq.md', 0.5333), ('onboarding.md', 0.2047)]
kb://index     -> [  {    "doc_id": "4a1318da0678",    "path": "faq.md"  },  {    "doc_id": "1f2e2212eca4",  ...
```

问题是「怎么报销发票」，语义检索把 `faq.md`（「报销怎么走？在财务系统里提交电子发票……」）排在了第一位，而文档里根本没有「发票」和「怎么报销」这两个词的精确匹配。去掉 Key 再运行一次：

```
语义检索: 关闭（无 API Key，降级为关键词）
keyword 'CI'   -> ['pr-process.md']
search_kb      -> Error executing tool search_kb: 语义检索未启用（没有配置 embedding 的 API Key）；请改用 keyword_search_kb
```

用 Inspector 调试：

```bash
npx @modelcontextprotocol/inspector python demos/practice/internal_kb/server.py
# 或脚本化：mcp-inspector --cli python demos/practice/internal_kb/server.py --method tools/call --tool-name keyword_search_kb --tool-arg keyword=CI
```

---

## 6. 接入 Claude Code

```bash
claude mcp add internal-kb --env KB_DOCS_DIR=/abs/path/to/docs --env OPENAI_API_KEY=$OPENAI_API_KEY \
  --transport stdio -- python /abs/path/to/03-mcp/demos/practice/internal_kb/server.py
```

也可以写进项目级的 `.mcp.json`（不要把 Key 直接写进去，用环境变量展开）：

```json
{
  "mcpServers": {
    "internal-kb": {
      "command": "/abs/path/.venv/bin/python",
      "args": ["/abs/path/to/03-mcp/demos/practice/internal_kb/server.py"],
      "env": {
        "KB_DOCS_DIR": "/abs/path/to/docs",
        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
        "OPENAI_BASE_URL": "${OPENAI_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
      }
    }
  }
}
```

（`command` 要指向装了 `mcp>=2.3` 的那个环境里的 python；不要指望 stdio 子进程继承你 shell 里的环境变量，密钥显式写在 `env` 里最稳。）

然后在 Claude Code 里直接问：

```
我们的 PR 流程是怎样的？
```

Claude 会自动调用 `search_kb`，拿到结果后再回答。

---

## 7. 接入 LangChain / Pydantic AI

LangChain 1.4+（用内置的 `MCPAdapter`，与 mcp 2.x 兼容；`langchain-mcp-adapters` 依赖 `mcp<2`，见[在 LangChain 中使用 MCP](../04-integration/03-langchain-mcp.md)）：

```python
from pathlib import Path
from langchain.agents import create_agent
from langchain.mcp import MCPAdapter
from langchain_openai import ChatOpenAI

async with MCPAdapter(Path("/abs/path/internal_kb/server.py")) as adapter:
    tools = await adapter.list_tools()
    agent = create_agent(ChatOpenAI(model="qwen-flash", base_url=...), tools)
    result = await agent.ainvoke({"messages": [("user", "PR 流程是怎样的")]})
```

Pydantic AI：把 `MCPToolset(StdioTransport(command=sys.executable, args=[".../server.py"]))` 放进 `toolsets=[...]`（见[在 Pydantic AI 中使用 MCP](../04-integration/04-pydantic-ai-mcp.md)）。

---

## 8. 部署成远程 Server

```python
mcp.run("streamable-http", port=8000)      # 传输选项传给 run()，不是 MCPServer(...)
```

生产环境的做法是挂到 Starlette / FastAPI 上，再用 Docker 部署（见[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)，别忘了 Host 白名单和 `session_manager.run()`），然后加上 OAuth（见[远程 MCP 的 OAuth 2.1 鉴权](../05-production/02-auth-oauth.md)）。多副本部署时，每个副本都会各自建一份索引（lifespan 启动时一次）；embedding 缓存最好放在共享卷或对象存储里，免得每个副本都重新花钱算一遍。

---

## 9. 可以扩展的方向

| 想加什么 | 怎么做 |
|------|--------|
| 支持 Notion / Confluence | 重写 `indexer.py`，加上对应的 connector |
| 增量更新 | 用 `watchdog` 监听文件变化，重建索引后调用 `await ctx.notify_resources_changed()`（通知 2026 版订阅者）和 `ctx.session.send_resource_list_changed()`（通知 2025 版老客户端） |
| 重排（rerank） | 在 `search_kb` 里加一个 cross-encoder 或 LLM 做二次排序 |
| 多租户 | 用 OAuth，按 `get_access_token().subject` 分库（身份不要从 `ctx.headers` 取） |
| 中文分词 | 切块前用 jieba 增强关键词检索 |
| 展示引用 | 返回的 snippet 带上 path，在 `instructions` 里要求 Claude 注明来源 |
| 本地 embedding | 把 `_embed` 换成 `sentence-transformers`（要装 PyTorch，下载几百 MB 权重；国内需要配镜像） |
| 专业向量库 | Chroma / pgvector / Qdrant；保持 `build_index` / `semantic_search` 这两个接口不变即可 |

---

## 10. 测试

v2 的测试不需要启动子进程，`Client` 可以直接接收 Server 对象（进程内连接，lifespan 照常启动）：

```python
# tests/test_kb.py
import pytest
from mcp import Client
from demos.practice.internal_kb.server import mcp

@pytest.mark.asyncio
async def test_keyword_search():
    async with Client(mcp) as client:
        result = await client.call_tool("keyword_search_kb", {"keyword": "CI"})
        assert not result.is_error
        assert any(h["path"] == "pr-process.md" for h in result.structured_content["result"])
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 静态资源声明了 `ctx`，一启动就 `ValueError` | 静态资源不支持 Context 注入；加上模板变量，或者读模块级的状态 |
| `mcp.get_context()` 不存在 | v2 已移除；资源、工具、Prompt 都用 `ctx: Context` 参数 |
| 每次启动都重新做 embedding | 按内容哈希缓存向量（这个 demo 用的是 `.npz`） |
| 百炼 embedding 报批量上限 | 单次最多 10 条，代码里已经分批了 |
| `.env` 或 `MCP_*` 变量没生效 | SDK 2.x 不再自动读取 `.env` 和 `MCP_*`；通过 Claude Code 的 `env` 或 `${VAR}` 展开传入 |
| Claude Code 里读不到密钥 | 不要依赖 stdio 子进程继承 shell 环境变量；写进 `env` 或用 `--env` |
| 中文搜索不准 | 用多语言的 embedding 模型，再用关键词检索兜底 |
| 大文档切块不合理 | 按 markdown 标题切分（比如 LangChain 的 `MarkdownHeaderTextSplitter`） |
| 余弦相似度和距离搞混 | 这里的 score 是相似度（越大越相似）；Chroma 等返回的是距离（越小越相似），注意统一 |

---

## 12. 下一步

- 数据库 MCP（SQL 安全）：[只读数据库 MCP Server（两层 SQL 防护）](./02-project-db-mcp.md)
- Claude Code 自定义工具：[为 Claude Code 编写 PR 评审 MCP Server](./03-project-claude-code-tool.md)

## 参考资料

- OpenAI Embeddings API（兼容接口）：https://platform.openai.com/docs/guides/embeddings
- 阿里云百炼 Embedding：https://help.aliyun.com/zh/model-studio/
- sentence-transformers（可选的本地方案）：https://www.sbert.net
