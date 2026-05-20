# MCP Server 03：Prompts —— 把对话开局封装成可调用模板

> **一句话**：Prompt 不是"模板字符串"，而是「**封装好的对话开局**」——可以包含 system / user / assistant 多轮、可以引用 Resource 作为附件、由**用户**显式触发（典型形式是 slash command）。

---

## 1. Prompt 的核心定位

| 维度 | Tool | Resource | **Prompt** |
|------|------|----------|------------|
| 谁触发 | 模型自动 | 应用自动 | **用户显式** |
| 控制方 | model | application | **user** |
| 典型 UI | tool_use 气泡 | resource picker | **slash 命令 / 命令面板** |

Prompt 的价值是**让非技术用户也能精确启动一段 Agent 工作流**——他不用记复杂的 prompt，挑个模板填几个参数就行。

---

## 2. 最小可用代码

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("github-helper")

@mcp.prompt()
def summarize_prs(repo: str, days: int = 7) -> str:
    """总结最近 N 天的 PR"""
    return (
        f"请帮我总结 {repo} 最近 {days} 天的 PR：\n"
        f"1. 列出每个 PR 的标题和状态\n"
        f"2. 找出最重要的 3 个\n"
        f"3. 给每个一句话评价"
    )
```

返回 `str` → FastMCP 自动包成单条 user message。

---

## 3. 返回多轮对话开局

复杂场景下你想给一段示例（few-shot）或预设 assistant 行为：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

mcp = FastMCP("translator")

@mcp.prompt(name="translate-zh-en")
def translate(text: str, style: str = "正式") -> list[base.Message]:
    """中译英"""
    return [
        base.UserMessage("把以下中文翻译为英文，风格：正式。"),
        base.AssistantMessage("好的，请提供原文。"),
        base.UserMessage(f"风格：{style}\n原文：{text}"),
    ]
```

支持的 Message 类型：

- `base.UserMessage(text)` 或 `base.UserMessage(content=...)`
- `base.AssistantMessage(...)`
- 内容支持 `TextContent` / `ImageContent` / `EmbeddedResource`

---

## 4. 引用 Resource：让 Prompt 把上下文一并带上

最强大的用法是 prompt 自己引用 resource，让 Host 加载附件：

```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base
from mcp.types import EmbeddedResource, ResourceContents

mcp = FastMCP("code-reviewer")

@mcp.prompt(name="review-file")
def review_file(path: str) -> list[base.Message]:
    """对指定文件做 code review。Prompt 会把文件内容作为附件一并送给模型。"""
    return [
        base.UserMessage(
            f"请对下面的代码做 code review：\n\n"
            f"文件: {path}\n\n"
            f"请按 severity 排序，关注：安全、性能、可读性、测试。"
        ),
        base.UserMessage(content=EmbeddedResource(
            type="resource",
            resource=ResourceContents(
                uri=f"file://{path}",
                # 实际内容由 Server 在生成 prompt 时读出来
                text=open(path).read(),
                mimeType="text/plain",
            ),
        )),
    ]
```

这样模型一收到 prompt 就已经"看见"了文件内容，不需要再额外调一次 Tool 去读。

---

## 5. 参数定义与补全

`prompts/list` 返回的 prompt 描述里带 arguments：

```json
{
  "name": "summarize-prs",
  "title": "总结 PR",
  "description": "...",
  "arguments": [
    {"name": "repo", "description": "owner/name", "required": true},
    {"name": "days", "description": "天数", "required": false}
  ]
}
```

FastMCP 自动从函数签名生成 arguments。

**参数补全**：当用户在 `repo=` 后开始输入时，Client 可以请求补全。如果 Server 声明 `completions` 能力，可以注册补全 handler（详见 05-completion-pagination）。

---

## 6. 协议消息

```json
// 列 prompts
{"method":"prompts/list"}
// 响应
{"prompts":[{"name":"summarize-prs",...}]}

// 拿 prompt（带参数）
{
  "method":"prompts/get",
  "params":{"name":"summarize-prs","arguments":{"repo":"a/b","days":"7"}}
}
// 响应
{
  "description":"总结最近 N 天的 PR",
  "messages":[{"role":"user","content":{"type":"text","text":"..."}}]
}
```

注意：参数全是字符串（即使你 Python 函数签名写了 `int`）。FastMCP 会按类型注解自动转，但你要自己处理"无法转"的情况。

---

## 7. Host 端用户体验

不同 Host 暴露 Prompt 的方式：

| Host | 触发方式 |
|------|---------|
| Claude Code | `/server-name:prompt-name` 斜杠命令 |
| Cursor | 命令面板 |
| Continue | `@prompt name` |
| VS Code Copilot Chat | 命令面板 / `/` |

参数输入方式因 Host 而异——有的弹小表单，有的让你接着打字。

---

## 8. 综合 demo：travel-server

```python
# demos/server/03_prompts_travel.py
"""旅行助手 Prompt 演示：单参数、多参数 + Resource 引用"""
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

mcp = FastMCP("travel-server")

# 顺便定义一个 Resource 让下面的 Prompt 引用
@mcp.resource("travel://preferences")
def preferences() -> str:
    return "偏好：靠窗、晨班、最多 1 次中转。"

@mcp.prompt(name="plan-vacation")
def plan_vacation(destination: str, days: int = 7, budget: int = 3000) -> str:
    """规划一次假期"""
    return (
        f"请帮我规划去 {destination} 的 {days} 天行程，预算 ${budget}。\n"
        f"先读 travel://preferences 了解我的偏好。\n"
        f"流程：\n"
        f"1. 查航班并解释为什么选某班\n"
        f"2. 给酒店建议并按 budget 过滤\n"
        f"3. 排个粗略 day-by-day 行程"
    )

@mcp.prompt(name="post-trip-report")
def post_trip_report(destination: str) -> list[base.Message]:
    """旅行结束后让模型帮你写游记"""
    return [
        base.UserMessage(
            f"我刚结束 {destination} 的旅行，请帮我：\n"
            f"1. 起一个 80 字以内的游记标题\n"
            f"2. 写 500 字游记开头\n"
            f"3. 列 3 个值得纪念的瞬间问我细节"
        ),
        base.AssistantMessage(
            "好的，我会用游记 + 提问的方式收集信息。"
            "请先告诉我整体感受关键词（比如：放松、惊喜、累、感动）。"
        ),
    ]

if __name__ == "__main__":
    mcp.run()
```

在 Claude Code 里效果：

```
/travel-server:plan-vacation destination=Barcelona days=7 budget=3500
↓
模型立刻进入"规划行程"模式，自动去 read 偏好资源、调 flight 工具……
```

---

## 9. Prompt vs System Prompt

很多人会问：那 prompt 跟我直接设个 system prompt 有啥区别？

| 维度 | System Prompt | MCP Prompt |
|------|---------------|------------|
| 谁定义 | Host / 用户在某次会话开头 | Server 提前定义 |
| 谁触发 | 每次对话固定生效 | 用户**显式调**时才生效 |
| 参数 | 不参数化 | 参数化、可补全 |
| 可分发 | 不行 | 跨 Host 一致 |

Prompt 本质是"**可分发的、可参数化的对话启动器**"。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **`@mcp.prompt()` 没 name → 函数名带下划线** | 用 `name="kebab-case"` 显式指定 |
| **返回 list 但忘了 import `base.Message`** | `from mcp.server.fastmcp.prompts import base` |
| **参数填不进去** | FastMCP 用类型注解转，复杂类型（dict / BaseModel）转不了，建议参数都是 `str` / `int` / `bool` |
| **Prompt 引用大 Resource 占爆上下文** | 用 `resource_link` 让 Host 决定是否加载，而不是直接 embed |
| **想让模型自动调 Prompt** | 不会的，Prompt 是用户主动触发；要"模型自动"用 Tool |

---

## 11. 下一步

- 📖 生命周期 + 上下文注入 → [04-lifespan-context.md](./04-lifespan-context.md)
- 📖 参数补全（让 prompt 参数智能联想） → [05-completion-pagination.md](./05-completion-pagination.md)
- 🔍 实际 Host 里怎么调 prompt → 04-integration/01-claude-code

## 参考资料

- Prompts spec：https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- FastMCP prompts 源码：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/fastmcp/prompts
