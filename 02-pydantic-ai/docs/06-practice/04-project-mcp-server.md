# Pydantic AI 实战 04：自定义 MCP 工具服务（GitHub Issue 查询）

> **一句话**：用 **FastMCP**（Pydantic AI 官方推荐的 MCP server 框架）写一个对外的 MCP server，把"GitHub Issue 查询 + 创建"暴露成标准 MCP 协议工具，让 Claude Desktop / Cursor / 任意 Pydantic AI Agent 都能接入。

---

## 1. 什么是 MCP

MCP = **Model Context Protocol**，Anthropic 在 2024 年底主推、现在已成为事实标准的"LLM 工具协议"。

简单理解：

```
传统：每个 Agent 框架自己定义 tool 接口（LangChain @tool、Pydantic @agent.tool、CrewAI Tool…）
MCP：所有 Agent 客户端都用同一个协议消费同一份 tool server
```

类比 USB-C —— 一根线插所有设备。

MCP server 提供三类资源：

| 类别 | 作用 | 类比 |
|------|------|------|
| **Tool** | 可被模型调用的函数 | API endpoint |
| **Resource** | 可被模型读取的数据 | 文件系统 |
| **Prompt** | 预定义提示模板 | snippet |

---

## 2. 项目目标

做一个 MCP server，对外提供两类工具：

| 工具 | 作用 |
|------|------|
| `list_issues(repo, state)` | 列出某个 repo 的 issue |
| `create_issue(repo, title, body)` | 在某个 repo 创建 issue |
| `get_issue(repo, number)` | 查单个 issue 详情 |

需求：

1. stdio 模式：被 Claude Desktop / Cursor 直接 spawn
2. HTTP 模式：部署到云端被多个客户端共享
3. Pydantic AI 作为 client 测试
4. 鉴权 + 限流（生产必备）

技术栈：

```
FastMCP / mcp ≥ 1.0     ← server 框架
httpx                    ← GitHub API
Pydantic AI              ← 测试用 client
fastapi (HTTP 模式)      ← Web 部署
```

---

## 3. 最小 MCP server

### 3.1 安装

```bash
pip install "mcp[cli]>=1.0.0" httpx pydantic-ai
```

`mcp[cli]` 装的就是官方 SDK，里头自带 `FastMCP`。

### 3.2 Hello World

```python
# mcp_demo/hello.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hello-server")

@mcp.tool()
def echo(text: str) -> str:
    """原样返回输入。"""
    return f"echo: {text}"

if __name__ == "__main__":
    mcp.run()   # 默认 stdio
```

跑：

```bash
python mcp_demo/hello.py
# 进程通过 stdio 等输入；用 mcp CLI 测试：
mcp dev mcp_demo/hello.py
```

`mcp dev` 启动一个本地 Web UI，让你手动调工具看返回值。**强烈推荐**调试时用。

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

注意：

- 用 Pydantic 模型表达 issue，对外返回的就是结构化数据
- 默认走匿名（每小时 60 次），有 `GITHUB_TOKEN` 走 5000 次

### 4.3 MCP server 主体

```python
# gh-mcp/server.py
from mcp.server.fastmcp import FastMCP
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

70 行就拿到一个生产可用的 MCP server，包含 tool / resource / prompt 三类。

---

## 5. 启动方式：stdio vs HTTP

### 5.1 stdio（默认）

stdio 模式被父进程通过 stdin/stdout 喂数据，**适合本地集成**（Claude Desktop / Cursor / VS Code 插件）：

```python
if __name__ == "__main__":
    mcp.run()   # 默认 transport="stdio"
```

启动后**不要往 stdout 打日志**——会污染协议消息。日志走 stderr：

```python
import logging, sys
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
```

### 5.2 HTTP / SSE

用于云端部署，多个客户端共享一个 server：

```python
if __name__ == "__main__":
    # streamable-http: 新版 MCP 推荐（HTTP + 长连接）
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8765)
```

或者用 SSE（旧版，但客户端兼容性广）：

```python
mcp.run(transport="sse", host="0.0.0.0", port=8765)
```

启动后客户端通过 `http://server:8765` 接入。

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

重启 Claude Desktop，新建对话时左下角能看到"🔌 github-issues"插件，对话里就能用：

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
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio(
    command="python",
    args=["/abs/path/to/gh-mcp/server.py"],
    env={"GITHUB_TOKEN": "ghp_xxx"},
)

agent = Agent("openai:gpt-4o-mini", toolsets=[server])

async def main():
    async with agent:
        result = await agent.run("列一下 pydantic/pydantic-ai 最近 3 个 open issue")
        print(result.output)

asyncio.run(main())
```

Pydantic AI 启动时会 spawn 这个 server，把它的工具自动接入 Agent。

### 7.2 连接 HTTP server

```python
from pydantic_ai.mcp import MCPServerStreamableHTTP

server = MCPServerStreamableHTTP(url="http://localhost:8765")
agent = Agent("openai:gpt-4o-mini", toolsets=[server])
```

`toolsets=[server]` 表示这个 server 上**所有工具都被注入** Agent。

---

## 8. 进阶 1：鉴权 / 密钥管理

### 8.1 stdio 模式

stdio 模式的鉴权直接走环境变量（如上面的 `GITHUB_TOKEN`）——因为 server 是 client 自己 spawn 的，**不需要网络鉴权**。

### 8.2 HTTP 模式

HTTP 暴露到公网时，必须加 token 验证。FastMCP 支持 OAuth：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.provider import OAuthAuthorizationServerProvider

mcp = FastMCP("github-issues", auth_server_provider=MyOAuthProvider())
```

简化方案：用 Bearer Token + Header 校验（前面套一层 reverse proxy / FastAPI 中间件）：

```python
# 用 streamable-http 模式 + Nginx 前置 + JWT 校验
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

GitHub API 调用慢且贵，用 in-memory cache：

```python
from functools import lru_cache
from datetime import datetime, timedelta

_cache: dict[str, tuple[datetime, list]] = {}

@mcp.tool()
async def list_issues(repo: str, state: str = "open", limit: int = 10) -> list[dict]:
    key = f"{repo}:{state}:{limit}"
    now = datetime.utcnow()
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
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
```

### 10.2 Fly.io / Railway / 自建

```bash
fly launch
fly secrets set GITHUB_TOKEN=ghp_xxx
fly deploy
```

部署后 client 用：

```python
server = MCPServerStreamableHTTP(url="https://gh-mcp.fly.dev")
```

---

## 11. 与原始 MCP SDK 对比

低层 `mcp` SDK（不用 FastMCP）：

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
| 适合 | 90% 项目 | 需要极端定制 |

**结论**：除非要做特别复杂的协议层定制，**始终用 FastMCP**。

---

## 12. 安全建议

| 风险 | 应对 |
|------|------|
| 工具被滥用做敏感操作（如 delete） | 危险工具加 confirm 参数，或人工审批 |
| Token 泄露 | 用 env / secret manager，不要硬编码；日志脱敏 |
| 越权（A 用户的 token 被用来访问 B 的 repo） | 工具内显式检查 user → repo 关系 |
| Prompt injection（issue 正文里塞"删除所有 issue"） | 严格区分 system prompt / user input；危险工具加二次确认 |
| 限流绕过 | 服务端硬限流 + Bearer token 与 user 绑定 |
| 暴露内部错误堆栈 | 工具捕获异常，对外返回简短消息 |

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| Claude Desktop 看不到插件 | config 路径错 / JSON 不合法 | 用 jq 校验 + 看 Claude logs |
| 启动后立刻断开 | server 在 stdout 打了日志 | 日志改 stderr |
| `mcp dev` 报 module not found | 没装在当前虚拟环境 | 用绝对路径 + 同环境 python |
| HTTP 客户端连不上 | host 写了 127.0.0.1 | host="0.0.0.0" + 防火墙放行 |
| Pydantic AI agent 看不到工具 | server 没成功握手 | 加 `print(server.list_tools())` 调试 |
| Tool 参数类型不匹配 | 函数签名漏写类型 | 必须有 type hint，否则不会生成 schema |
| 创建 issue 失败 401 | GITHUB_TOKEN 没传给 server 进程 | Claude Desktop config 里加 `env` 字段 |
| 流式响应只看到第一个字符 | `mcp.run(transport="sse")` 但客户端不支持 | 用 streamable-http |
| `ImportError: cannot import name 'FastMCP'` | mcp 版本太老 | `pip install -U "mcp>=1.0.0"` |

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

单文件版本：包含 server + 用 Pydantic AI 作 client 测试 + stdio / HTTP 两种启动方式。

跑法 1：直接做 stdio server

```bash
pip install "mcp[cli]>=1.0.0" httpx pydantic-ai python-dotenv
python demos/practice/04_project_mcp_server.py --server stdio
```

跑法 2：HTTP server + 同进程 client 测试

```bash
python demos/practice/04_project_mcp_server.py --server http &
python demos/practice/04_project_mcp_server.py --client http
```

跑法 3：用 `mcp dev` 调试

```bash
mcp dev demos/practice/04_project_mcp_server.py
# 浏览器打开 http://127.0.0.1:6274 可视化点工具
```

---

## 17. 总结

MCP 让"工具 server"变成跨框架共享的标准件。把团队内部的 API（GitHub / Jira / Salesforce / 公司内部系统）做成 MCP server，所有 LLM client（Claude Desktop / Cursor / 自建 Agent）就能立即接入。这是 2025-2026 年企业内部 AI 工具化的最优形态。

Pydantic AI 同时支持**做 server**（用 FastMCP）和**做 client**（用 `MCPServerStdio` / `MCPServerStreamableHTTP`），是目前唯一两端都做得很顺的 Agent 框架。

恭喜你跑完整个实战部分。Pydantic AI 学习手册到这里结束 —— 但**真正的开始是把这套工具用进你自己的项目里**。
