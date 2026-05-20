# MCP Server 02：Resources —— 把上下文喂给 AI 应用

> **一句话**：Resource 是 MCP Server 暴露给 AI **应用**（不是模型！）读取的只读数据，用 URI 寻址、支持模板、支持订阅。把不可变 / 半静态的上下文用 Resource 暴露，比塞进 Tool 干净 100 倍。

---

## 1. 一句话区分 Tool 和 Resource

记住这一条就够了：

| 维度 | Tool | Resource |
|------|------|----------|
| 谁触发 | 模型在对话中决定 | 应用（Host）在 UI 上或按策略决定 |
| 副作用 | 可以有 | 不应该有（幂等只读） |
| 寻址 | name + arguments | URI |
| 类比 | 函数调用 | GET HTTP |

把"查订单"做成 Tool 是对的（参数多变、模型自决）；把"订单表 schema"做成 Resource 是对的（应用启动时一次性塞 system prompt）。

---

## 2. 最小可用代码

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docs-server")

# 直接 Resource：固定 URI
@mcp.resource("docs://readme")
def readme() -> str:
    """项目 README"""
    with open("README.md") as f:
        return f.read()

# 资源模板：URI 含占位符
@mcp.resource("docs://chapter/{number}")
def chapter(number: str) -> str:
    """读第 N 章"""
    return f"# 第 {number} 章\n..."
```

FastMCP 自动：

1. 在 `initialize` 声明 `resources` capability
2. 注册到 `resources/list`（直接 Resource）或 `resources/templates/list`（含 `{...}` 的）
3. 处理 `resources/read` 请求

---

## 3. URI Scheme 选择

URI 是任意的，但要遵守 [RFC3986](https://datatracker.ietf.org/doc/html/rfc3986)。约定的标准 scheme：

| Scheme | 用途 |
|--------|------|
| `file://` | 本地文件系统 |
| `https://` | Web 资源（**仅当 Client 能直接 GET** 时用） |
| `git://` | Git 集成 |
| 自定义 | `db://`、`gh://`、`figma://`、`s3://` 等 |

**关键判断**：你的内容能让 Client 直接通过 HTTP 拿吗？

- 能 → 用 `https://`，告诉 Client "你自己去拿"
- 不能（需鉴权 / 业务转换） → 用自定义 scheme，让 Client 通过 `resources/read` 走 MCP 通道拿

---

## 4. 直接 Resource vs Resource Template

### 4.1 直接 Resource
固定 URI，列在 `resources/list` 里。客户端用户能直接在 UI 上选。

```python
@mcp.resource("config://app")
def app_config() -> str:
    return "log_level=info\ntimeout=30"
```

### 4.2 Resource Template
URI 带占位符 `{x}`，符合 RFC 6570 URI Template。客户端列在 `resources/templates/list`，**不会**列在 `resources/list`。需要客户端拼好具体 URI 再 read。

```python
@mcp.resource("db://tables/{table}")
def table_data(table: str) -> str:
    """读某张表前 100 行"""
    # ⚠️ 生产代码要做表名白名单！
    rows = db.fetch(f"SELECT * FROM {table} LIMIT 100")
    return "\n".join(map(str, rows))
```

参数补全（completion API）：用户在 UI 输入 `db://tables/u` 时，Client 可以问 Server "u 开头有哪些表"。详见 05-completion-pagination。

---

## 5. 返回内容类型

`resources/read` 返回的 `contents` 数组里每项可以是文本或二进制：

### 5.1 文本

```python
@mcp.resource("notes://daily/{date}")
def daily_note(date: str) -> str:
    """读某天日记"""
    return f"# {date}\n今天写了 MCP 手册..."
```

FastMCP 自动用 MIME `text/plain` 包装。

### 5.2 自定义 MIME

```python
from mcp.server.fastmcp.resources import TextResource

@mcp.resource("docs://api", mime_type="text/markdown")
def api_docs() -> str:
    return "# API\n..."
```

### 5.3 二进制（图片 / PDF / 任意 bytes）

```python
@mcp.resource("img://logo", mime_type="image/png")
def logo() -> bytes:
    with open("logo.png", "rb") as f:
        return f.read()
```

返回 `bytes` → FastMCP 转 base64 + `blob` 字段。

### 5.4 JSON / dict

```python
@mcp.resource("api://users/{user_id}")
def user(user_id: str) -> dict:
    return {"id": user_id, "name": "Alice"}
```

返回 `dict` / `list` → FastMCP 序列化成 JSON 字符串放在 `text`，并设 `mime_type="application/json"`。

---

## 6. Resource 元数据：annotations

Resource / ResourceTemplate / 内容块都支持 `annotations`，提示 Client 怎么用：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import TextResource

mcp = FastMCP("demo")

@mcp.resource(
    "docs://readme",
    name="readme",
    title="项目 README",
    mime_type="text/markdown",
)
def readme() -> str:
    """项目根目录的 README"""
    return open("README.md").read()
```

字段：

| 字段 | 含义 |
|------|------|
| `name` | 协议级唯一名 |
| `title` | UI 显示名（中文可） |
| `description` | 描述 |
| `mime_type` | MIME 类型 |
| `size` | 字节数（可选） |
| `icons` | 显示图标（可选） |

**`annotations`**（在 resource content 里附加，需要走低层 API）：

| Annotation | 含义 |
|-----------|------|
| `audience` | `["user"]` / `["assistant"]` / `["user","assistant"]`——给谁看 |
| `priority` | 0-1，越大越重要 |
| `lastModified` | ISO 8601 时间戳，让 Client 按时间排序 |

---

## 7. 订阅 Resource 变化

Server 声明 `subscribe: true` → Client 可订阅某 URI → Server 在变更时推送通知。

### 7.1 用 FastMCP 实现订阅

FastMCP 的高层 API 没直接暴露订阅 hook，要稍微低层一点：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("live-metrics")

# 数据源（模拟）
_metrics_value = {"qps": 100}

@mcp.resource("metrics://qps")
def qps() -> str:
    return str(_metrics_value["qps"])

# 通过 FastMCP 的 lifespan 启动后台任务，定期推送变更
import anyio

async def _qps_changer():
    while True:
        await anyio.sleep(2)
        _metrics_value["qps"] += 1
        # 通知所有订阅者（如果有的话）
        # 注意：FastMCP 当前需要通过 server 的 request_context 拿 session
        await mcp.get_context().session.send_resource_updated("metrics://qps")
```

> 上面的实现是**简化示意**，实际生产里订阅 ID 管理、并发推送、断线重连等要更小心。低层 `Server` 类提供完整 `subscribe` / `unsubscribe` 处理钩子。

### 7.2 Client 端订阅

```python
await session.subscribe_resource("metrics://qps")

# 注册回调（在创建 ClientSession 时通过 message handler 处理 notification）
# 或循环 read 拉取（拉模式 fallback）
```

订阅的适用场景：实时仪表盘、日志流、协作编辑同步。

---

## 8. resources/list_changed 通知

Server 声明 `listChanged: true` → 当 Resource 列表变了（新增、删除、改名），Server 发：

```json
{"method": "notifications/resources/list_changed"}
```

Client 收到后重新调 `resources/list`。

FastMCP 里如果你动态增删 Resource，通常需要手动触发：

```python
# 假装动态新增一个 Resource
mcp.add_resource_from_fn(some_function, uri="docs://newdoc")
# 通知 Client
await mcp.get_context().session.send_resource_list_changed()
```

---

## 9. 一个综合 demo：项目知识库 Server

```python
# demos/server/02_resources_kb.py
"""把当前项目作为只读知识库暴露：README / 章节 / 文件树"""
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kb-server")
ROOT = Path(__file__).resolve().parents[2]  # 指向 03-mcp/

@mcp.resource("kb://readme", title="项目 README", mime_type="text/markdown")
def readme() -> str:
    return (ROOT / "README.md").read_text()

@mcp.resource("kb://tree", title="项目文件树", mime_type="text/plain")
def file_tree() -> str:
    lines = []
    for p in sorted(ROOT.rglob("*.md")):
        rel = p.relative_to(ROOT)
        lines.append(str(rel))
    return "\n".join(lines)

@mcp.resource("kb://doc/{path}", mime_type="text/markdown")
def doc(path: str) -> str:
    """读任意 docs/ 下的 markdown 文件，path 是 docs/ 后的相对路径"""
    target = ROOT / "docs" / path
    # 防越权：必须在 docs/ 下
    if not target.resolve().is_relative_to((ROOT / "docs").resolve()):
        raise FileNotFoundError(path)
    if not target.exists():
        raise FileNotFoundError(path)
    return target.read_text()

if __name__ == "__main__":
    mcp.run()
```

试一下（用 Inspector）：

- `kb://readme` → 整本手册的 README
- `kb://tree` → 列出所有文档
- `kb://doc/01-basics/03-primitives.md` → 读这一篇

注意第三个用了**路径越权防御**——这是 Resource 模板最常见的安全坑（详见 05-production/04-security）。

---

## 10. 什么时候不该用 Resource

| 场景 | 更好的选择 |
|------|-----------|
| 数据需要参数化查询、可能慢、有 cost | Tool（让模型显式调） |
| 数据高度动态、写操作伴随读 | Tool |
| 用户主动触发的"启动一段流程" | Prompt |

**反例**：把"SQL 查询"做成 Resource Template `db://query/{sql}` 是错的——SQL 不是 URI 友好的，注入风险高，且每次都是一次新计算。这种应该是 Tool。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **URI 没填模板参数就 read** | `db://tables/{table}` 必须填具体值，URI 必须完整 |
| **Resource 里写副作用** | 永远幂等只读。如果是「读了之后改状态」，请用 Tool |
| **Resource 太大** | 大文件考虑分页 / 用 Tool 按需查；考虑 `resource_link` 返回链接而非内容 |
| **二进制忘了 base64** | FastMCP 自动处理；低层 API 要自己 base64 |
| **路径越权** | `file://` / 自定义 path 模板务必做沙盒检查 |
| **`listChanged` 没声明却动态新增** | 必须 capability 声明，否则 Client 不知道要刷新 |

---

## 12. 下一步

- 📖 Prompt 设计 → [03-prompts.md](./03-prompts.md)
- 📖 生命周期 + 上下文注入（Resource 里访问 DB 连接等）→ [04-lifespan-context.md](./04-lifespan-context.md)
- 📖 参数补全 + 分页 → [05-completion-pagination.md](./05-completion-pagination.md)
- 🔍 安全 / 路径越权 → 05-production/04-security

## 参考资料

- Resources spec：https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- URI Template (RFC 6570)：https://datatracker.ietf.org/doc/html/rfc6570
- RFC 3986 URI：https://datatracker.ietf.org/doc/html/rfc3986
