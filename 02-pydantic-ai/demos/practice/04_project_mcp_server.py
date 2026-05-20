"""
实战 4：自定义 MCP 工具服务（GitHub Issue 查询）。

一个文件里包含三种用法：
  1) stdio MCP server（被 Claude Desktop / mcp dev 调用）
  2) HTTP MCP server（云端共享）
  3) Pydantic AI 作 client，连上面的 server 跑一次问答

用法：
    pip install "mcp[cli]>=1.0.0" httpx pydantic-ai python-dotenv
    export GITHUB_TOKEN=ghp_xxx   # 可选；不给则走匿名（每小时 60 次）

    # 1) 作为 stdio server 运行（Claude Desktop / mcp dev）
    python demos/practice/04_project_mcp_server.py --server stdio

    # 2) 作为 HTTP server（监听 8765）
    python demos/practice/04_project_mcp_server.py --server http

    # 3) 跑 client（默认连 stdio server）
    export OPENAI_API_KEY=...
    python demos/practice/04_project_mcp_server.py --client stdio

    # 4) 用 mcp dev 工具调试
    mcp dev demos/practice/04_project_mcp_server.py

Claude Desktop config（macOS：~/Library/Application Support/Claude/claude_desktop_config.json）：

    {
      "mcpServers": {
        "github-issues": {
          "command": "python",
          "args": ["/abs/path/to/04_project_mcp_server.py", "--server", "stdio"],
          "env": {"GITHUB_TOKEN": "ghp_xxx"}
        }
      }
    }
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# 日志走 stderr，避免污染 stdio 协议
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("gh-mcp")

GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_BASE = "https://api.github.com"


# ============================================================
# 1) GitHub API 客户端
# ============================================================

class Issue(BaseModel):
    number: int
    title: str
    state: Literal["open", "closed"]
    url: str
    body: str | None = None
    user: str

    @classmethod
    def from_raw(cls, raw: dict) -> "Issue":
        return cls(
            number=raw["number"],
            title=raw["title"],
            state=raw["state"],
            url=raw["html_url"],
            body=raw.get("body"),
            user=raw["user"]["login"],
        )


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return h


async def gh_list(repo: str, state: str = "open", limit: int = 10) -> list[Issue]:
    async with httpx.AsyncClient(base_url=GH_BASE, headers=_gh_headers(), timeout=20) as cli:
        r = await cli.get(f"/repos/{repo}/issues",
                          params={"state": state, "per_page": min(limit, 100)})
        r.raise_for_status()
        # /issues endpoint 把 PR 也算进来，过掉
        return [Issue.from_raw(x) for x in r.json() if "pull_request" not in x]


async def gh_get(repo: str, number: int) -> Issue:
    async with httpx.AsyncClient(base_url=GH_BASE, headers=_gh_headers(), timeout=20) as cli:
        r = await cli.get(f"/repos/{repo}/issues/{number}")
        r.raise_for_status()
        return Issue.from_raw(r.json())


async def gh_create(repo: str, title: str, body: str = "") -> Issue:
    if not GH_TOKEN:
        raise RuntimeError("需要 GITHUB_TOKEN 才能创建 issue")
    async with httpx.AsyncClient(base_url=GH_BASE, headers=_gh_headers(), timeout=20) as cli:
        r = await cli.post(f"/repos/{repo}/issues",
                           json={"title": title, "body": body})
        r.raise_for_status()
        return Issue.from_raw(r.json())


# ============================================================
# 2) MCP server（FastMCP）
# ============================================================

def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("github-issues")

    @mcp.tool()
    async def list_issues(repo: str, state: str = "open", limit: int = 10) -> list[dict]:
        """列出指定 GitHub repo 的 issue。

        Args:
            repo: "owner/name" 格式，例如 "pydantic/pydantic-ai"
            state: open / closed / all，默认 open
            limit: 返回条数 1-100，默认 10
        """
        log.info(f"list_issues repo={repo} state={state} limit={limit}")
        issues = await gh_list(repo, state=state, limit=limit)
        return [i.model_dump() for i in issues]

    @mcp.tool()
    async def get_issue(repo: str, number: int) -> dict:
        """查询单个 issue 的详细信息。

        Args:
            repo: "owner/name"
            number: issue 编号
        """
        log.info(f"get_issue repo={repo} number={number}")
        issue = await gh_get(repo, number)
        return issue.model_dump()

    @mcp.tool()
    async def create_issue(
        repo: str,
        title: str = Field(..., description="issue 标题"),
        body: str = "",
    ) -> dict:
        """在指定 repo 创建一条 issue。需要环境变量 GITHUB_TOKEN。

        Args:
            repo: "owner/name"
            title: issue 标题
            body: Markdown 正文
        """
        log.info(f"create_issue repo={repo} title={title}")
        issue = await gh_create(repo, title, body)
        return issue.model_dump()

    @mcp.resource("github://my-repos")
    async def my_repos() -> str:
        """当前 token 持有人的 repo 列表（前 10 个）。"""
        import json
        if not GH_TOKEN:
            return "未配置 GITHUB_TOKEN"
        async with httpx.AsyncClient(headers=_gh_headers(), timeout=20) as cli:
            r = await cli.get(f"{GH_BASE}/user/repos?per_page=10")
        if r.status_code != 200:
            return f"failed: {r.status_code}"
        return json.dumps(
            [{"name": x["full_name"], "stars": x["stargazers_count"]} for x in r.json()],
            indent=2,
        )

    @mcp.prompt()
    def triage_issue(issue_title: str, issue_body: str) -> str:
        """生成一个 issue triage 的 prompt。"""
        return (
            f"请帮我分析以下 GitHub issue：\n\n"
            f"标题：{issue_title}\n\n正文：\n{issue_body}\n\n"
            f"输出：\n"
            f"1. 类型（bug / feature / question / docs）\n"
            f"2. 优先级（P0/P1/P2/P3）\n"
            f"3. 给作者的回复 draft\n"
        )

    return mcp


# 让 `mcp dev` 也能找到 server 对象（顶层变量）
mcp = build_server()


# ============================================================
# 3) Pydantic AI 作 client
# ============================================================

async def run_client_stdio() -> None:
    """spawn 一个 stdio server 进程，让 Pydantic AI Agent 使用它的工具。"""
    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPServerStdio

    if not os.getenv("OPENAI_API_KEY"):
        print("❌ 请设置 OPENAI_API_KEY")
        return

    server = MCPServerStdio(
        command=sys.executable,
        args=[__file__, "--server", "stdio"],
        env={
            "GITHUB_TOKEN": GH_TOKEN or "",
            "PATH": os.environ.get("PATH", ""),
        },
    )
    agent = Agent("openai:gpt-4o-mini", toolsets=[server],
                  system_prompt="你是一个 GitHub 助手，遇到查询请求时一定要调工具。")

    async with agent:
        result = await agent.run(
            "帮我列一下 pydantic/pydantic-ai 最近 3 个 open issue 的标题。"
        )
    print("\n=== Agent 回复 ===")
    print(result.output)


async def run_client_http(url: str = "http://localhost:8765") -> None:
    """连接已经在运行的 HTTP server。"""
    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPServerStreamableHTTP

    if not os.getenv("OPENAI_API_KEY"):
        print("❌ 请设置 OPENAI_API_KEY")
        return

    server = MCPServerStreamableHTTP(url=url)
    agent = Agent("openai:gpt-4o-mini", toolsets=[server],
                  system_prompt="你是一个 GitHub 助手。")
    async with agent:
        result = await agent.run("列一下 pydantic/pydantic 最近 3 个 open issue")
    print("\n=== Agent 回复 ===")
    print(result.output)


# ============================================================
# 4) 入口
# ============================================================

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", choices=["stdio", "http"],
                    help="作为 MCP server 启动")
    ap.add_argument("--client", choices=["stdio", "http"],
                    help="作为 Pydantic AI client 测试")
    ap.add_argument("--port", type=int, default=8765, help="HTTP server 端口")
    ap.add_argument("--url", default="http://localhost:8765",
                    help="HTTP client 连接地址")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    if args.server == "stdio":
        log.info("启动 stdio MCP server")
        mcp.run()
    elif args.server == "http":
        log.info(f"启动 HTTP MCP server on 0.0.0.0:{args.port}")
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)
    elif args.client == "stdio":
        asyncio.run(run_client_stdio())
    elif args.client == "http":
        asyncio.run(run_client_http(args.url))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
