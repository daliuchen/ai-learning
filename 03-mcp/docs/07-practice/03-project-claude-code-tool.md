# MCP Practice 03：给 Claude Code 写一个 PR 评审助手 MCP

> **一句话**：本项目从零写一个能被 Claude Code 当工具用的 MCP Server——拉 GitHub PR 元信息、跑 diff 检查、提交评论。让你"在 Claude Code 里说一句话就 review 完一个 PR"。

---

## 1. 项目目标

写一个 `pr-helper` MCP Server，包含：

- Tool: `get_pr(repo, number)` — 获取 PR 详情（标题 / 描述 / 状态 / 文件清单）
- Tool: `get_pr_diff(repo, number)` — 拿 diff 内容
- Tool: `post_pr_comment(repo, number, body)` — 在 PR 上留评论（写操作 + 二次确认）
- Resource: `pr://recent/{repo}` — 列最近 PR
- Prompt: `review-pr` — 引导 review 一条 PR 的标准流程

工作流：

```
[user]: 帮我 review 一下 PR #42
↓
Claude Code 调 get_pr → 拿元信息
        调 get_pr_diff → 拿 diff
        分析 → 找问题
        问用户："要发评论吗？"
        调 post_pr_comment（用户确认后）
```

---

## 2. 设计决策

| 问题 | 决定 |
|------|------|
| 用什么 SDK | `httpx` 直接调 GitHub REST API（轻量） |
| Auth | GitHub Personal Access Token（环境变量） |
| 写操作 | 用 annotations 标 destructive + 让 Claude Code 弹审批 |
| 错误处理 | ToolError 让 LLM 看见友好错 |
| Prompt | 一个 review-pr 模板把"步骤"写清 |

---

## 3. 目录结构

```
demos/practice/pr_helper/
├── server.py
└── README.md
```

---

## 4. 完整代码

```python
# demos/practice/pr_helper/server.py
"""PR 评审助手 MCP Server"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError


@dataclass
class AppCtx:
    http: httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("环境变量 GITHUB_TOKEN 未设置")

    http = httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mcp-pr-helper/1.0",
        },
        timeout=30,
    )
    try:
        yield AppCtx(http=http)
    finally:
        await http.aclose()


mcp = FastMCP("pr-helper", lifespan=app_lifespan)


def _app(ctx: Context) -> AppCtx:
    return ctx.request_context.lifespan_context


# ============= Tools =============

@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True}
)
async def get_pr(repo: str, number: int, ctx: Context) -> dict:
    """获取 PR 的元信息（标题、描述、状态、文件清单）。

    Args:
        repo: 仓库 owner/name，例如 'anthropic/anthropic-sdk-python'
        number: PR 编号
    """
    app = _app(ctx)
    await ctx.info(f"GET /repos/{repo}/pulls/{number}")
    r = await app.http.get(f"/repos/{repo}/pulls/{number}")
    if r.status_code == 404:
        raise ToolError(f"PR 不存在: {repo}#{number}")
    if r.status_code == 401:
        raise ToolError("GITHUB_TOKEN 无效或权限不足")
    r.raise_for_status()
    pr = r.json()

    # 拉文件清单
    files_resp = await app.http.get(f"/repos/{repo}/pulls/{number}/files")
    files_resp.raise_for_status()
    files = [
        {"path": f["filename"], "status": f["status"],
         "additions": f["additions"], "deletions": f["deletions"]}
        for f in files_resp.json()
    ]

    return {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "draft": pr["draft"],
        "author": pr["user"]["login"],
        "body": pr["body"] or "",
        "head": pr["head"]["ref"],
        "base": pr["base"]["ref"],
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": pr["changed_files"],
        "files": files[:50],
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True}
)
async def get_pr_diff(
    repo: str,
    number: int,
    max_chars: int = 50000,
    ctx: Context = None,
) -> str:
    """拿 PR 的完整 diff 文本。

    Args:
        repo: owner/name
        number: PR 编号
        max_chars: 最大字符数（防爆上下文），默认 50000

    返回 diff 文本（截断到 max_chars）。
    """
    app = _app(ctx)
    await ctx.info(f"拉 diff: {repo}#{number}")
    r = await app.http.get(
        f"/repos/{repo}/pulls/{number}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    if r.status_code == 404:
        raise ToolError(f"PR 不存在: {repo}#{number}")
    r.raise_for_status()
    diff = r.text
    if len(diff) > max_chars:
        diff = diff[:max_chars] + f"\n\n... (已截断，原 diff 共 {len(r.text)} 字符)"
    return diff


@mcp.tool(
    annotations={
        "destructiveHint": True,    # ← 让 Claude Code 弹审批
        "readOnlyHint": False,
        "openWorldHint": True,
    }
)
async def post_pr_comment(
    repo: str,
    number: int,
    body: str,
    ctx: Context,
) -> dict:
    """⚠️ 在 PR 上发评论（写操作，会通知所有 reviewer）。

    Args:
        repo: owner/name
        number: PR 编号
        body: 评论内容（支持 markdown）
    """
    if not body.strip():
        raise ToolError("评论内容不能为空")

    app = _app(ctx)
    await ctx.warning(f"⚠️ 即将在 {repo}#{number} 发评论")
    r = await app.http.post(
        f"/repos/{repo}/issues/{number}/comments",
        json={"body": body},
    )
    if r.status_code == 404:
        raise ToolError(f"PR 不存在或无权限: {repo}#{number}")
    if r.status_code in (401, 403):
        raise ToolError(f"鉴权失败: {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    return {
        "id": data["id"],
        "url": data["html_url"],
        "created_at": data["created_at"],
    }


# ============= Resources =============

@mcp.resource("pr://recent/{repo}", mime_type="application/json")
async def recent_prs(repo: str) -> list[dict]:
    """列最近的 30 个 PR（包含 open / closed）"""
    token = os.environ["GITHUB_TOKEN"]
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    ) as http:
        r = await http.get(
            f"/repos/{repo}/pulls",
            params={"state": "all", "per_page": 30, "sort": "updated"},
        )
        r.raise_for_status()
        return [
            {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "author": pr["user"]["login"],
                "updated_at": pr["updated_at"],
            }
            for pr in r.json()
        ]


# ============= Prompts =============

@mcp.prompt(name="review-pr")
def review_pr(repo: str, number: int) -> str:
    """启动 PR review 工作流"""
    return (
        f"请帮我 review {repo} 的 PR #{number}。请按以下流程：\n"
        f"\n"
        f"1. 用 get_pr 拿 PR 元信息，了解整体改动量和目的\n"
        f"2. 用 get_pr_diff 拿完整 diff\n"
        f"3. 按严重度分类列出问题：\n"
        f"   - 🔴 阻塞性（安全漏洞 / 核心 bug / 破坏向后兼容）\n"
        f"   - 🟡 改进建议（性能、可读性、测试覆盖）\n"
        f"   - 🟢 nitpick（命名、注释）\n"
        f"4. 给一个整体评价：approve / request changes / comment\n"
        f"5. 问我要不要用 post_pr_comment 把上述总结发到 PR 上"
    )


if __name__ == "__main__":
    mcp.run()
```

---

## 5. 跑起来

### 5.1 准备 token

```bash
# 1. 去 https://github.com/settings/tokens 生成 fine-grained PAT
# 2. 至少给 "Pull requests: read & write" 权限
export GITHUB_TOKEN=github_pat_xxx
```

### 5.2 Inspector 试

```bash
GITHUB_TOKEN=$GITHUB_TOKEN npx @modelcontextprotocol/inspector python demos/practice/pr_helper/server.py
```

试：

- `get_pr(repo="anthropic/anthropic-sdk-python", number=1)` → 元信息
- `get_pr_diff(repo="anthropic/anthropic-sdk-python", number=1)` → diff
- `pr://recent/anthropic/anthropic-sdk-python` → 最近 PR 列表

⚠️ `post_pr_comment` 慎用——会真发评论。建议先在自己 fork 仓库测试。

---

## 6. 接到 Claude Code

```json
{
  "mcpServers": {
    "pr-helper": {
      "command": "python",
      "args": ["/abs/path/pr_helper/server.py"],
      "env": {
        "GITHUB_TOKEN": "${env:GITHUB_TOKEN}"
      }
    }
  }
}
```

注意用 `${env:...}` 引用 shell 环境，**别**把 token 写到 git 仓库里的 mcp.json。

### 6.1 用法

```
[user]: /pr-helper:review-pr repo=myorg/myrepo number=42
[claude]: 拉取 PR 信息中...
         调用 get_pr → 标题: ..., 改动 +120 -30
         调用 get_pr_diff → 共 50 个 hunk
         分析中...
         
         发现以下问题：
         🔴 阻塞性：
           - src/auth.py:42 用了 md5 处理密码（应该用 argon2id）
         🟡 改进：
           - tests/test_x.py 缺少 edge case 测试
         🟢 nit:
           - 第 88 行变量名 'x' 不明确
         
         整体评价：request changes
         
         需要我把这份 review 发到 PR 上吗？
[user]: 发
[claude]: 调用 post_pr_comment → Claude Code 弹审批气泡显示 body
         （用户点确认）
         ✅ 已发评论：https://github.com/myorg/myrepo/pull/42#issuecomment-xxx
```

注意：`post_pr_comment` 因为标了 `destructiveHint: true`，Claude Code 默认会要求用户确认。

---

## 7. 接到 LangChain / Pydantic AI

同样的 Server 也能给 Agent 用，参考 04-integration/03 和 04。

---

## 8. 扩展方向

| 想加 | 怎么做 |
|------|--------|
| 自动 review（不发评论，存到本地） | 加 `save_review(repo, number, body)` 写本地文件 |
| 跨 PR 跨仓库统计 | Resource `pr://stats/{owner}` 返回月度数据 |
| 集成 CI 状态 | Tool `get_pr_checks(repo, number)` |
| 自动 cherry-pick 提案 | Tool `suggest_backport(repo, number, to_branch)` |
| 给 review 加 inline comment | GitHub `/pulls/X/comments` API（line-level） |
| 改用 GraphQL | 单次 API 拉 PR + 文件 + 评论，省请求数 |

---

## 9. 用 Sampling 增强：让 Server 自己跑 LLM

如果 Server 想"自己分析 diff 找问题"而不依赖 Host 端模型：

```python
from mcp.types import SamplingMessage, TextContent


@mcp.tool()
async def auto_review(repo: str, number: int, ctx: Context) -> str:
    """让 Server 端自动 review（用 Host 的 LLM）"""
    pr_info = await get_pr(repo, number, ctx)
    diff = await get_pr_diff(repo, number, ctx=ctx)

    review_result = await ctx.sample(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        f"请 review 以下 PR 改动，按严重度列问题：\n\n"
                        f"标题：{pr_info['title']}\n"
                        f"描述：{pr_info['body'][:1000]}\n\n"
                        f"Diff:\n```diff\n{diff[:30000]}\n```"
                    ),
                ),
            )
        ],
        system_prompt="你是严格的高级工程师 reviewer。",
        max_tokens=2000,
        model_preferences={
            "intelligencePriority": 0.9,
            "speedPriority": 0.3,
        },
    )

    return review_result.content.text
```

这样在 Claude Code 里 `auto_review` → Server 自动用 Claude（Host 模型）跑出 review，不用 LLM 自己分析。

---

## 10. 部署成远程 Server

如果想让团队共用同一个 PR Helper：

1. 把 GITHUB_TOKEN 换成**每用户 OAuth**（用 GitHub OAuth Apps）
2. 在 lifespan 里按用户 token 创建 http client
3. 跑 `mcp.run(transport="streamable-http")` 部署
4. 加 OAuth 2.1 middleware

参考 05-production 全部 5 篇。

---

## 11. 安全清单

- [x] Token 走环境变量，不写到代码
- [x] post_pr_comment 标 destructive，弹审批
- [x] 限制 diff 字符数（max_chars）防爆上下文
- [x] error message 不泄漏 token 片段
- [ ] 速率限制（生产环境，防 LLM 死循环调用）
- [ ] 审计日志（每次 post_pr_comment 记录到本地）
- [ ] 用 fine-grained PAT 而非 classic（最小权限）

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **403 forbidden** | Token 范围不够；fine-grained PAT 要明确给 Pull request 权限 |
| **diff 太大爆上下文** | max_chars 默认 50000，进一步降；或用 file-level diff |
| **rate limit** | GitHub 5000/小时；批量场景要 backoff |
| **写错仓库 owner** | 工具描述里强调"owner/name"格式 |
| **token 泄漏** | 错误信息不要 print token；用 `{r.status_code}` 而不是 `{r}` |

---

## 13. 一个完整的对话样例

```
[user]: 帮我看一下 PR #42
[claude]: 调用 pr-helper__get_pr(repo='myorg/myrepo', number=42)
         → 标题: "feat: 新增 search API"
         → 状态: open, +210 -45, 3 个文件改动
         调用 pr-helper__get_pr_diff
         → diff 已拿到

[claude]: 主要改动：
         1. src/api/search.py 新增 200 行
         2. tests/test_search.py 加了 1 个 happy path 测试
         3. README.md 加了文档

         问题：
         🔴 阻塞：
           - SQL 直接拼字符串 (search.py:88)，有注入风险
         🟡 改进：
           - 测试只覆盖 happy path，缺：空查询 / 超长查询 / 权限不足
           - 错误处理粗糙，所有异常都返 500

         建议：request changes，要求修 SQL 拼接 + 加测试

         要我把这份 review 发到 PR 上吗？

[user]: 发

[claude]: 调用 pr-helper__post_pr_comment（Claude Code 弹审批）
         [user 在 UI 点确认]
         → ✅ 评论已发：https://github.com/myorg/myrepo/pull/42#issuecomment-xxx
```

---

## 14. 全本手册大功告成

恭喜你看到最后！整本 MCP 手册：

| 章 | 篇数 |
|---|------|
| 01-basics | 6 |
| 02-server | 8 |
| 03-client | 5 |
| 04-integration | 5 |
| 05-production | 5 |
| 06-advanced | 3 |
| 07-practice | 3 |
| **合计** | **35** |

接下来你可以：

- 把本项目（PR Helper）改造成你公司的内部工具
- 写新的 MCP Server 贡献到 [官方 servers 仓库](https://github.com/modelcontextprotocol/servers)
- 发布到 [MCP Registry](https://registry.modelcontextprotocol.io)
- 在 Pydantic AI 或 LangChain Agent 里集成自己的 Server

## 参考资料

- GitHub REST API：https://docs.github.com/en/rest
- Claude Code MCP docs：https://docs.claude.com/en/docs/claude-code/mcp
- 官方 GitHub MCP Server：https://github.com/modelcontextprotocol/servers/tree/main/src/github
- 本手册总入口：[../../README.md](../../README.md)
