# Pydantic AI 实战 04：自定义 MCP 工具服务（GitHub Issue 查询）

> 最后一个实战项目：用 FastMCP（Pydantic AI 的 MCP 客户端也建在它上面）写一个对外的 MCP server，把 GitHub Issue 的查询和创建做成标准 MCP 工具，Claude Desktop、Cursor 和任意 Pydantic AI Agent 都能接进来用。

以 `pydantic-ai 2.64.0` + `fastmcp 4` 为准。server 用 `from fastmcp import FastMCP`（`mcp` 2.x 里已经没有 `mcp.server.fastmcp`），client 用 `MCPToolset`。

---

## 1. 什么是 MCP

MCP 全称 Model Context Protocol，是 Anthropic 在 2024 年底推出的 LLM 工具协议，现在已经是事实标准。

它和以前的区别：

```
传统：每个 Agent 框架自己定义 tool 接口（LangChain @tool、Pydantic @agent.tool、CrewAI Tool…）
MCP：所有 Agent 客户端都用同一个协议消费同一份 tool server
```

MCP server 提供三类资源：

| 类别 | 作用 | 类比 |
|------|------|------|
| Tool | 可被模型调用的函数 | API endpoint |
| Resource | 可被模型读取的数据 | 文件系统 |
| Prompt | 预定义提示模板 | snippet |

---

## 2. 项目目标

做一个 MCP server，对外提供三个工具：

| 工具 | 作用 |
|------|------|
| `list_issues(repo, state)` | 列出某个 repo 的 issue |
| `create_issue(repo, title, body)` | 在某个 repo 创建 issue |
| `get_issue(repo, number)` | 查单个 issue 详情 |

需求：

1. stdio 模式：被 Claude Desktop / Cursor 直接 spawn
2. HTTP 模式：部署到云端被多个客户端共享
3. Pydantic AI 作为 client 测试
4. 鉴权和限流（上生产必须有）

技术栈：

```
FastMCP 4（`fastmcp` 包）← server 框架
httpx                    ← GitHub API
Pydantic AI              ← 测试用 client
fastapi (HTTP 模式)      ← Web 部署
```

---

## 3. 最小 MCP server

### 3.1 安装

```bash
pip install fastmcp httpx pydantic-ai
```

`fastmcp` 是独立的第三方包 FastMCP 4，官方 MCP SDK 1.x 里的 `mcp.server.fastmcp` 是它的前身。官方 SDK 2.x 里对应的是 `mcp.server.MCPServer`，见 MCP 手册的[写第一个 MCP Server 和 Client](../../../03-mcp/docs/01-basics/06-first-server.md)。`pydantic-ai-slim[mcp]` 只装客户端依赖 `fastmcp-slim`，写 server 要装完整的 `fastmcp`（或 `fastmcp-slim[server]`）。

### 3.2 Hello World

```python
# mcp_demo/hello.py
from fastmcp import FastMCP

mcp = FastMCP("hello-server")

@mcp.tool()
def echo(text: str) -> str:
    """原样返回输入。"""
    return f"echo: {text}"

if __name__ == "__main__":
    mcp.run(show_banner=False)   # 默认 stdio；show_banner=False 去掉启动横幅（它走 stderr，但还是安静点好）
```

跑：

```bash
python mcp_demo/hello.py
# 进程通过 stdio 等输入；调试用 FastMCP 的 CLI（以 `fastmcp --help` 为准，如 `fastmcp dev inspector hello.py`）或官方 MCP Inspector：
npx @modelcontextprotocol/inspector python mcp_demo/hello.py
```

Inspector 会起一个本地 Web UI，可以手动调工具看返回值，调试时很好用。还有更省事的自测办法：直接把 server 对象交给 `MCPToolset(mcp)`（同进程，不起子进程）再配 `TestModel` 跑一遍（见[MCP（Model Context Protocol）集成](../04-modules/01-mcp.md)第 8.2 节）。

---

## 4. GitHub Issue MCP Server

### 4.1 项目结构

```
gh-mcp/
├── server.py        # MCP server 主入口
├── github_api.py    # GitHub API 客户端
├── requirements.txt
└── README.md
```

### 4.2 GitHub API 客户端

```python
# gh-mcp/github_api.py
import os
import httpx
from pydantic import BaseModel, Field
from typing import Literal

GH_TOKEN = os.getenv("GITHUB_TOKEN")
BASE = "https://api.github.com"

class Issue(BaseModel):
    number: int
    title: str
    state: Literal["open", "closed"]
    url: str = Field(alias="html_url")
    body: str | None = None
    user: str

    @classmethod
    def from_raw(cls, raw: dict) -> "Issue":
        return cls(
            number=raw["number"],
            title=raw["title"],
            state=raw["state"],
            html_url=raw["html_url"],
            body=raw.get("body"),
            user=raw["user"]["login"],
        )

def _client() -> httpx.AsyncClient:
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    return httpx.AsyncClient(base_url=BASE, headers=headers, timeout=20.0)

async def list_issues(repo: str, state: str = "open", limit: int = 10) -> list[Issue]:
    async with _client() as cli:
        r = await cli.get(f"/repos/{repo}/issues", params={"state": state, "per_page": limit})
        r.raise_for_status()
        # GitHub /issues 也会返回 PR，过滤掉
        return [Issue.from_raw(x) for x in r.json() if "pull_request" not in x]

async def get_issue(repo: str, number: int) -> Issue:
    async with _client() as cli:
        r = await cli.get(f"/repos/{repo}/issues/{number}")
        r.raise_for_status()
        return Issue.from_raw(r.json())

async def create_issue(repo: str, title: str, body: str = "") -> Issue:
    if not GH_TOKEN:
        raise RuntimeError("需要 GITHUB_TOKEN 才能创建 issue")
    async with _client() as cli:
        r = await cli.post(f"/repos/{repo}/issues", json={"title": title, "body": body})
        r.raise_for_status()
        return Issue.from_raw(r.json())
```

issue 用 Pydantic 模型表示，对外返回的就是结构化数据。没有 token 时走匿名访问（每小时 60 次），配了 `GITHUB_TOKEN` 是 5000 次。

### 4.3 MCP server 主体

```python
# gh-mcp/server.py
from fastmcp import FastMCP
from github_api import list_issues as gh_list, get_issue as gh_get, create_issue as gh_create

mcp = FastMCP("github-issues")

@mcp.tool()
async def list_issues(repo: str, state: str = "open", limit: int = 10) -> list[dict]:
    """列出指定 repo 的 issue。

    Args:
        repo: "owner/name" 格式，例如 "pydantic/pydantic-ai"
        state: open / closed / all，默认 open
        limit: 返回条数，默认 10，最大 100
    """
    issues = await gh_list(repo, state=state, limit=limit)
    return [i.model_dump() for i in issues]

@mcp.tool()
async def get_issue(repo: str, number: int) -> dict:
    """查询单个 issue 的详细信息。

    Args:
        repo: "owner/name"
        number: issue 编号
    """
    issue = await gh_get(repo, number)
    return issue.model_dump()

@mcp.tool()
async def create_issue(repo: str, title: str, body: str = "") -> dict:
    """在指定 repo 创建一条 issue。需要环境变量 GITHUB_TOKEN。

    Args:
        repo: "owner/name"
        title: issue 标题
        body: Markdown 正文
    """
    issue = await gh_create(repo, title, body)
    return issue.model_dump()

# ---- Resource：把当前用户的 repo 列表当成可读资源 ----
@mcp.resource("github://my-repos")
async def my_repos() -> str:
    """当前 token 持有人的 repo 列表（前 10 个）。"""
    import httpx, os, json
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "未配置 GITHUB_TOKEN"
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as cli:
        r = await cli.get("https://api.github.com/user/repos?per_page=10")
    return json.dumps([{"name": x["full_name"], "stars": x["stargazers_count"]}
                       for x in r.json()], indent=2)

# ---- Prompt：预制的"分析 issue"提示 ----
@mcp.prompt()
def triage_issue(issue_title: str, issue_body: str) -> str:
    """生成一个 issue triage 的 prompt。"""
    return (
        f"请帮我分析以下 GitHub issue：\n\n"
        f"标题：{issue_title}\n\n"
        f"正文：\n{issue_body}\n\n"
        f"输出：\n1. 类型（bug / feature / question / docs）\n"
        f"2. 优先级（P0/P1/P2/P3）\n3. 给作者的回复 draft\n"
    )

if __name__ == "__main__":
    mcp.run()
```

70 行左右，tool、resource、prompt 三类都有了。

---

## 5. 启动方式：stdio vs HTTP

### 5.1 stdio（默认）

stdio 模式下，父进程通过 stdin/stdout 和 server 通信，适合本地集成（Claude Desktop / Cursor / VS Code 插件）：

```python
if __name__ == "__main__":
    mcp.run()   # 默认 transport="stdio"
```

启动后不要往 stdout 打日志，会污染协议消息。日志走 stderr：

```python
import logging, sys
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
```

### 5.2 HTTP / SSE

用于云端部署，多个客户端共享一个 server：

```python
if __name__ == "__main__":
    # streamable-http: 新版 MCP 推荐（HTTP + 长连接）
    mcp.run(transport="http", host="127.0.0.1", port=8765)
```

或者用 SSE（已弃用，但客户端兼容性广）：

```python
mcp.run(transport="sse", host="0.0.0.0", port=8765)
```

启动后客户端通过 `http://server:8765/mcp`（streamable HTTP 默认路径）或 `http://server:8765/sse`（SSE）接入。

---

## 6. 配置 Claude Desktop 接入

Claude Desktop 的配置文件位置：

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

加一段：

```json
{
  "mcpServers": {
    "github-issues": {
      "command": "python",
      "args": ["/abs/path/to/gh-mcp/server.py"],
      "env": {
        "GITHUB_TOKEN": "ghp_xxx"
      }
    }
  }
}
```

重启 Claude Desktop，新建对话时左下角能看到 github-issues 插件，对话里就能用：

> 帮我看看 pydantic/pydantic-ai 最近 5 个 open issue

Claude 会自动调 `list_issues` 工具。

### 6.1 Cursor / Continue / VS Code 同理

`~/.cursor/mcp.json` 也是同样的 schema。

---

## 7. 用 Pydantic AI 作为 client 测试

### 7.1 连接 stdio server

```python
# test_client.py
import asyncio
from pydantic_ai import Agent
from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset

server = MCPToolset(
    StdioTransport(
        command="python",
        args=["/abs/path/to/gh-mcp/server.py"],
        env={"GITHUB_TOKEN": "ghp_xxx"},
    )
)

agent = Agent("openai-chat:qwen-flash", toolsets=[server])

async def main():
    async with agent:
        result = await agent.run("列一下 pydantic/pydantic-ai 最近 3 个 open issue")
        print(result.output)

asyncio.run(main())
```

Pydantic AI 启动时会 spawn 这个 server，把它的工具自动接入 Agent。

### 7.2 连接 HTTP server

```python
from pydantic_ai.mcp import MCPToolset

server = MCPToolset("http://localhost:8765/mcp")      # FastMCP 的 HTTP 端点默认挂在 /mcp
agent = Agent("openai-chat:qwen-flash", toolsets=[server])
```

`toolsets=[server]` 会把这个 server 上的所有工具都交给 Agent。

---

## 8. 进阶 1：鉴权 / 密钥管理

### 8.1 stdio 模式

stdio 模式直接用环境变量传凭证（如上面的 `GITHUB_TOKEN`）。server 是 client 自己 spawn 的，不需要网络鉴权。

### 8.2 HTTP 模式

HTTP 暴露到公网时必须加 token 验证。FastMCP 通过构造参数 `auth=` 接认证 provider（Bearer / JWT 校验、OAuth 等）：

```python
from fastmcp import FastMCP

# my_auth_provider：FastMCP 提供的 token 校验或 OAuth provider 实例，可用的类和参数以 fastmcp 文档为准
mcp = FastMCP("github-issues", auth=my_auth_provider)
```

简单些的做法是校验 Bearer Token，在前面套一层反向代理或 FastAPI 中间件：

```python
# 用 http 模式 + Nginx 前置，校验一个固定的 Bearer token（JWT 要在 Nginx 模块或应用里验签）
# nginx.conf
location / {
    if ($http_authorization != "Bearer xxx") { return 401; }
    proxy_pass http://localhost:8765;
}
```

### 8.3 按工具级权限

可以在工具内部检查 user 上下文：

```python
@mcp.tool()
async def create_issue(repo: str, title: str, body: str = "") -> dict:
    user = current_user.get()  # ContextVar
    if not user_can_write(user, repo):
        raise PermissionError(f"{user} 无权在 {repo} 创建 issue")
    ...
```

---

## 9. 进阶 2：限流 + 并发

### 9.1 限流

GitHub API 有 rate limit，server 自己也要做：

```python
from asyncio import Semaphore

_sem = Semaphore(5)  # 全局最多 5 并发

@mcp.tool()
async def list_issues(repo: str, ...) -> list[dict]:
    async with _sem:
        return await gh_list(repo, ...)
```

复杂场景用 `aiolimiter`：

```python
from aiolimiter import AsyncLimiter

# 每秒最多 10 次
limiter = AsyncLimiter(max_rate=10, time_period=1)

@mcp.tool()
async def list_issues(repo: str, ...) -> list[dict]:
    async with limiter:
        return await gh_list(repo, ...)
```

### 9.2 缓存

GitHub API 调用慢，还占限额，可以加一层内存缓存：

```python
from datetime import datetime, timedelta, timezone

_cache: dict[str, tuple[datetime, list]] = {}

@mcp.tool()
async def list_issues(repo: str, state: str = "open", limit: int = 10) -> list[dict]:
    key = f"{repo}:{state}:{limit}"
    now = datetime.now(timezone.utc)
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < timedelta(minutes=2):
            return val
    issues = await gh_list(repo, state, limit)
    val = [i.model_dump() for i in issues]
    _cache[key] = (now, val)
    return val
```

---

## 10. 部署到云端

### 10.1 容器化

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8765
CMD ["python", "server.py"]
```

`server.py` 改成 HTTP：

```python
if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8765"))
    mcp.run(transport="http", host="0.0.0.0", port=port)
```

### 10.2 Fly.io / Railway / 自建

```bash
fly launch
fly secrets set GITHUB_TOKEN=ghp_xxx
fly deploy
```

部署后 client 用：

```python
server = MCPToolset("https://gh-mcp.fly.dev/mcp")
```

---

## 11. 与原始 MCP SDK 对比

用低层 `mcp` SDK、不用 FastMCP 的写法如下。注意这是 `mcp` 1.x 的写法，`mcp` 2.x 的低层 API 有调整（迁移指南见 py.sdk.modelcontextprotocol.io），在 2.x 上使用前要按迁移指南调整：

```python
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("github-issues")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_issues",
            description="列出 issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "state": {"type": "string", "default": "open"},
                },
                "required": ["repo"],
            },
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "list_issues":
        issues = await gh_list(arguments["repo"], arguments.get("state", "open"))
        return [TextContent(type="text", text=str(issues))]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

asyncio.run(main())
```

| 维度 | FastMCP | 原始 MCP SDK |
|------|---------|--------------|
| 代码量 | 短（装饰器 + 函数） | 长（要手写 schema） |
| Schema 来源 | 函数签名 + docstring | 手写 JSON Schema |
| Resource / Prompt | 一行 | 自己注册 handler |
| Pydantic 集成 | 原生 | 自己包 |
| 适合 | 大多数项目 | 需要极端定制 |

除非要在协议层做很复杂的定制，否则用 FastMCP 就够了。

---

## 12. 安全建议

| 风险 | 应对 |
|------|------|
| 工具被滥用做敏感操作（如 delete） | 危险工具加 confirm 参数，或人工审批 |
| Token 泄露 | 用 env / secret manager，不要硬编码；日志脱敏 |
| 越权（A 用户的 token 被用来访问 B 的 repo） | 工具内显式检查 user → repo 关系 |
| Prompt injection（issue 正文里写着"删除所有 issue"） | 严格区分 system prompt / user input；危险工具加二次确认 |
| 限流绕过 | 服务端硬限流 + Bearer token 与 user 绑定 |
| 暴露内部错误堆栈 | 工具捕获异常，对外返回简短消息 |

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| Claude Desktop 看不到插件 | config 路径错 / JSON 不合法 | 用 jq 校验 + 看 Claude logs |
| 启动后立刻断开 | server 在 stdout 打了日志 | 日志改 stderr |
| Inspector / CLI 报 module not found | 没装在当前虚拟环境 | 用绝对路径 + 同环境 python |
| HTTP 客户端连不上 | host 写了 127.0.0.1 | host="0.0.0.0" + 防火墙放行 |
| Pydantic AI agent 看不到工具 | server 没成功握手 | 单独启动 server 看 stderr；或把 server 对象交给 `MCPToolset(mcp)` 同进程配 `TestModel` 跑一遍 |
| Tool 参数类型不匹配 | 函数签名漏写类型 | 必须有 type hint，否则不会生成 schema |
| 创建 issue 失败 401 | GITHUB_TOKEN 没传给 server 进程 | Claude Desktop config 里加 `env` 字段 |
| `ImportError: cannot import name 'FastMCP'` / `No module named 'mcp.server.fastmcp'` | `mcp` 2.x 改名 | 用 `from fastmcp import FastMCP`（`pip install fastmcp`） |
| `ImportError: cannot import name 'MCPServerStdio'` | V2 去掉旧客户端类 | 用 `MCPToolset` |
| 模型传了不存在的 repo，运行直接 `UnexpectedModelBehavior` | MCP 工具报错默认 `tool_error_behavior='retry'`，预算 1 次 | 工具里把 404 转成友好的返回值，或 `MCPToolset(..., tool_error_behavior='failed')` |

---

## 14. 工程清单

- [ ] 给 server 加 version 字段（`FastMCP("xxx", version="1.0.0")`）
- [ ] tool 加 enum / range 限制（用 Pydantic Field）
- [ ] 关键 tool 加 audit log（写文件 / 上 Logfire）
- [ ] 单元测试用 `mcp` SDK 直接连接而不是经 Claude
- [ ] HTTP 模式前置 Nginx + TLS + Bearer
- [ ] Docker 化 + healthcheck endpoint
- [ ] 写 README 告诉用户怎么配 Claude Desktop
- [ ] 工具入参限制最大长度（防 prompt injection 巨型 payload）
- [ ] 日志结构化（JSON），方便接 Logfire / ELK

---

## 15. 项目目录

```
gh-mcp/
├── server.py
├── github_api.py
├── auth.py            # 鉴权 / 限流
├── tests/
│   └── test_tools.py
├── Dockerfile
├── fly.toml           # Fly.io 部署
├── requirements.txt
└── README.md
```

---

## 16. 完整 demo

[`demos/practice/04_project_mcp_server.py`](../../demos/practice/04_project_mcp_server.py)

单文件版本，包含 server、用 Pydantic AI 作 client 的测试，以及 stdio / HTTP 两种启动方式。

跑法 1：直接做 stdio server

```bash
pip install "pydantic-ai-slim[mcp,openai]" fastmcp httpx python-dotenv   # [mcp] 只带客户端，写 server 还要完整的 fastmcp
python demos/practice/04_project_mcp_server.py --server stdio
```

跑法 2：HTTP server + 同进程 client 测试

```bash
python demos/practice/04_project_mcp_server.py --server http &
python demos/practice/04_project_mcp_server.py --client http
```

跑法 3：用 FastMCP CLI / MCP Inspector 调试

```bash
fastmcp dev inspector demos/practice/04_project_mcp_server.py
# 浏览器打开 http://127.0.0.1:6274 可视化点工具
```

---

## 17. 写在最后

把团队内部的 API（GitHub、Jira、Salesforce、公司内部系统）做成 MCP server 之后，Claude Desktop、Cursor、自建 Agent 这些客户端都能直接接入，同一套工具不用为每个框架各写一遍。Pydantic AI 在这件事里两头都能用：可以在 FastMCP 写的 server 里调 Agent，也可以用 `MCPToolset` / `MCP` capability 当 client。

这本手册到这里就结束了。
