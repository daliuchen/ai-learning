# MCP Server 03：用 Prompts 封装可复用的对话模板

> MCP 里的 Prompt 更接近一段封装好的对话开局，而不只是一个模板字符串。它可以包含多轮 user、assistant 消息（Prompt 消息没有 system 角色），可以把 Resource 当附件带上，并且由用户显式触发，最常见的形式是斜杠命令。

---

## 1. Prompt 的定位

| 维度 | Tool | Resource | Prompt |
|------|------|----------|------------|
| 谁触发 | 模型自动 | 应用自动 | 用户显式 |
| 控制方 | model | application | user |
| 常见 UI | tool_use 气泡 | resource picker | 斜杠命令 / 命令面板 |

Prompt 的用处是让不懂技术的用户也能准确地启动一段 Agent 工作流。他不需要记住复杂的 prompt，选个模板、填几个参数就行。

---

## 2. 最小可用代码

```python
from mcp.server import MCPServer

mcp = MCPServer("github-helper")

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

返回 `str` 时，MCPServer 会自动把它包成一条 user message。

---

## 3. 返回多轮对话开局

场景复杂一些时，你可能想给几个示例（few-shot），或者预设 assistant 的行为：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import AssistantMessage, Message, UserMessage

mcp = MCPServer("translator")

@mcp.prompt(name="translate-zh-en")
def translate(text: str, style: str = "正式") -> list[Message]:
    """中译英"""
    return [
        UserMessage("把以下中文翻译为英文，风格：正式。"),
        AssistantMessage("好的，请提供原文。"),
        UserMessage(f"风格：{style}\n原文：{text}"),
    ]
```

可用的 Message 类型都在 `mcp.server.mcpserver` 下（旧路径 `mcp.server.fastmcp.prompts.base` 已经不存在），`Message` 是它们的公共基类，用作返回值注解：

- `UserMessage(text)`、`AssistantMessage(text)`：传入 `str` 会自动包成 `TextContent`，角色由类名决定
- 内容也可以是 `TextContent` / `ImageContent` / `EmbeddedResource`，或者 `Image(...)` / `Audio(...)` 辅助类

---

## 4. 引用 Resource，把上下文一起带上

Prompt 可以直接引用 resource，由 Host 加载附件，这是我觉得最有用的写法：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Message, UserMessage
from mcp.types import EmbeddedResource, TextResourceContents

mcp = MCPServer("code-reviewer")

@mcp.prompt(name="review-file")
def review_file(path: str) -> list[Message]:
    """对指定文件做 code review。Prompt 会把文件内容作为附件一并送给模型。"""
    with open(path) as f:
        code = f.read()           # 实际内容由 Server 在生成 prompt 时读出来
    return [
        UserMessage(EmbeddedResource(
            type="resource",
            resource=TextResourceContents(          # 注意：用 TextResourceContents，不是抽象基类 ResourceContents
                uri=f"file://{path}",
                mime_type="text/plain",             # v2：snake_case
                text=code,
            ),
        )),
        UserMessage("请对上面的代码做 code review，按 severity 排序，关注：安全、性能、可读性、测试。"),
    ]
```

模型收到 prompt 的同时就看到了文件内容，不需要再调一次 Tool 去读。

---

## 5. 参数定义与补全

`prompts/list` 返回的 prompt 描述里带有 arguments：

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

MCPServer 会根据函数签名自动生成 arguments。

> 参数说明不会从 docstring 的 `Args:` 段解析，docstring 只作为整个 Prompt 的 description；想给参数写说明，用 `Annotated[str, Field(description=...)]`。

用户在 `repo=` 后面开始输入时，Client 可以请求参数补全。Server 声明了 `completions` 能力的话，就可以注册补全 handler（详见[参数补全（Completion）与分页（Pagination）](05-completion-pagination.md)）。

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

线上传输的参数全都是字符串，即使 Python 函数签名里写的是 `int`（上例中 `days` 就是 `"7"`）。MCPServer 会按类型注解自动转换，但转换失败的情况需要你自己处理。

从 2026-07-28 起，`prompts/get` 也可以像 `tools/call` 一样返回 `InputRequiredResult`（多轮请求），向用户追问。所以 SDK 里的 `MCPServer.get_prompt()` 有可能返回 `InputRequiredResult`。

---

## 7. 在 Host 里怎么用

不同 Host 呈现 Prompt 的方式：

| Host | 触发方式 |
|------|---------|
| Claude Code | 斜杠命令，菜单里显示为 `/server-name:prompt-name (MCP)`，直接输入写 `/mcp__server-name__prompt-name` |
| Cursor | 命令面板 |
| Continue | `/` 斜杠命令 |
| VS Code Copilot Chat | `/mcp.server-name.prompt-name` 斜杠命令 |

参数的输入方式也因 Host 而异，有的弹出小表单，有的让你接着打字。

---

## 8. 综合 demo：travel-server

```python
# demos/server/03_prompts_travel.py（节选，完整版含 --selftest）
from mcp.server import MCPServer
from mcp.server.mcpserver import Message, UserMessage, AssistantMessage
from mcp.types import EmbeddedResource, TextResourceContents

mcp = MCPServer("travel-server")

PREFERENCES = "偏好：靠窗、晨班、最多 1 次中转。"


@mcp.resource("travel://preferences", mime_type="text/plain")
def preferences() -> str:
    return PREFERENCES


@mcp.prompt(name="plan-vacation", title="规划假期")
def plan_vacation(destination: str, days: int = 7, budget: int = 3000) -> str:
    """规划一次假期"""
    return (
        f"请帮我规划去 {destination} 的 {days} 天行程，预算 ${budget}。\n"
        f"流程：\n1. 查航班并解释为什么选某班\n2. 给酒店建议并按 budget 过滤\n3. 排个粗略 day-by-day 行程"
    )


@mcp.prompt(name="post-trip-report")
def post_trip_report(destination: str) -> list[Message]:
    """旅行结束后让模型帮你写游记（多轮开局）"""
    return [
        UserMessage(f"我刚结束 {destination} 的旅行，请帮我起标题、写开头、列 3 个值得纪念的瞬间。"),
        AssistantMessage("好的，请先告诉我整体感受关键词（比如：放松、惊喜、累、感动）。"),
    ]


@mcp.prompt(name="plan-with-preferences")
def plan_with_preferences(destination: str) -> list[Message]:
    """把偏好作为附件（EmbeddedResource）一起送给模型，模型不用再读一次资源"""
    return [
        UserMessage(
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(uri="travel://preferences", mime_type="text/plain", text=PREFERENCES),
            )
        ),
        UserMessage(f"按上面的偏好，帮我规划去 {destination} 的行程。"),
    ]
```

在 Claude Code 里的效果：

```
/mcp__travel-server__plan-vacation Barcelona 7 3500
↓
模型按 prompt 里的流程开始规划：查航班、给酒店建议、排行程（查航班需要 Host 里另有对应的工具）
```

---

## 9. Prompt 和 System Prompt 的区别

常有人问：MCP Prompt 和直接设一个 system prompt 有什么不同？

| 维度 | System Prompt | MCP Prompt |
|------|---------------|------------|
| 谁定义 | Host 或用户在会话开头设置 | Server 预先定义 |
| 何时生效 | 每次对话都生效 | 用户显式调用时才生效 |
| 参数 | 不带参数 | 带参数，可补全 |
| 能否分发 | 不能 | 在不同 Host 里表现一致 |

可以把 MCP Prompt 理解成一个能分发、能带参数的对话启动器。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| `@mcp.prompt()` 没写 name，结果名字带下划线 | 用 `name="kebab-case"` 显式指定 |
| 返回 list 却忘了 import `Message` | `from mcp.server.mcpserver import Message, UserMessage, AssistantMessage`（v1 的 `prompts.base` 路径已经变了） |
| 在 `EmbeddedResource` 里写 `ResourceContents(...)` | 那是抽象基类，要用 `TextResourceContents` / `BlobResourceContents` |
| 参数传不进去 | MCPServer 按类型注解转换，dict、BaseModel 这类复杂类型转不了，参数最好都用 `str` / `int` / `bool` |
| Prompt 引用的 Resource 太大，撑爆上下文 | 用 `resource_link` 让 Host 决定是否加载，不要直接 embed |
| 想让模型自动调用 Prompt | 做不到，Prompt 只能由用户主动触发；需要模型自动调用就用 Tool |

---

## 11. 下一步

- 生命周期与上下文注入：[用 Lifespan 和 Context 向工具注入共享资源](./04-lifespan-context.md)
- 参数补全，让 prompt 参数能联想：[参数补全（Completion）与分页（Pagination）](./05-completion-pagination.md)
- 在实际 Host 里怎么调用 prompt：[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)

## 参考资料

- Prompts spec：https://modelcontextprotocol.io/specification/2026-07-28/server/prompts
- Python SDK Prompts：https://py.sdk.modelcontextprotocol.io/servers/prompts/
- MCPServer prompts 源码：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/mcpserver/prompts
