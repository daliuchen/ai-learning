# Hosted Tools：OpenAI 托管的内置工具

> web_search、file_search、code_interpreter、hosted MCP、image_generation 这些比较重的工具，OpenAI 直接在自己的云端运行，你只要把它们放进 `tools=[...]`，模型就能调用。这是 OpenAI Agents SDK 和其他框架拉开差距最明显的地方，前提是用 OpenAI 的 Responses 模型。
>
> 本篇代码以 0.24 为准，真正调用需要 OpenAI API key 和 Responses 模型。工具对象不连服务也能构造，但挂到 Chat Completions 模型（比如千问）上会直接报 `UserError: Hosted tools are not supported with the ChatCompletions API`。

---

## 1. 有哪些工具

| Tool | 用途 | 备注 |
|------|------|------|
| `WebSearchTool` | 实时搜 Web | Responses 模型 |
| `FileSearchTool` | 在 Vector Store 里检索 | Responses 模型 |
| `CodeInterpreterTool` | 在 OpenAI 沙箱里运行 Python | Responses 模型 |
| `HostedMCPTool` | 让 OpenAI 代你连一个公网可达的 MCP 服务 | 见 [MCP 集成：让 Agent 消费 MCP Server](../06-integration/01-mcp.md) |
| `ImageGenerationTool` | 生成图片 | Responses 模型 |
| `ToolSearchTool` | 让模型按需加载延迟工具 / 命名空间 / hosted MCP | 见 [动态工具集与上下文感知工具](./05-dynamic-tools.md) |
| `ProgrammaticToolCallingTool` | 模型写一段 JavaScript，在托管 V8 里批量调你允许的工具（0.19 起） | 见第 8 节 |

另有一类工具同样由模型决定何时调用，但在你自己的环境里执行：

| Tool | 用途 |
|------|------|
| `ComputerTool` | 模型看截图，发出点击、输入、滚动指令；你来实现 `Computer` / `AsyncComputer`，见 [实战 4：Computer Use Agent（操作浏览器）](../08-practice/04-computer-use.md) |
| `ShellTool` | 最新的 shell 工具：本地执行（`executor=`）或 OpenAI 托管容器（`environment={"type": "container_auto", ...}`，可挂 skills） |
| `ApplyPatchTool` | 模型给 diff，你实现 `ApplyPatchEditor` 落盘 |
| `LocalShellTool` | 旧的本地 shell，被 `ShellTool` 取代 |

叫 hosted，是因为工具在 OpenAI 的服务器上执行。你只声明要用 web_search，模型决定什么时候调，OpenAI 执行完把结果交回给模型，中间不经过你的代码。

> 0.14 起还有 beta 的 Sandbox Agents（`SandboxAgent` + `Manifest`，带本地 / Docker / E2B / Modal / Vercel 等后端），用来让 agent 在隔离工作区里读写文件、跑命令；本手册不展开，见官方 Sandbox 指南。

---

## 2. WebSearchTool

```python
from agents import Agent, Runner, WebSearchTool


agent = Agent(
    name="Researcher",
    instructions="回答问题前用 web_search 找最新资料，引用源。",
    tools=[WebSearchTool()],
    model="gpt-6-luna",
)

result = await Runner.run(agent, "OpenAI 最新的模型是什么？")
print(result.final_output)
```

搜索是实时的，不是缓存快照，回答会自动附上来源 URL。按调用次数计费，价格以 OpenAI pricing 页面为准。

可以这样配置：

```python
WebSearchTool(
    user_location={"type": "approximate", "country": "CN", "city": "Beijing"},  # 地理偏好
    search_context_size="medium",       # "low" / "medium" / "high"
    filters=...,                        # 限制域名等
    external_web_access=True,           # 是否真的联网
    search_content_types=["text", "image"],       # 想要图片结果时加 "image"
    image_settings={"max_results": 3, "caption": True},
)
```

`search_context_size` 越大，召回的内容越多，消耗的 token 也越多。选了 `"image"` 时 SDK 会自动请求 `web_search_call.results`，原始结果在 `new_items` 里。

---

## 3. FileSearchTool

先把文件上传到 OpenAI Vector Store，再让 Agent 检索。

### 3.1 创建 Vector Store + 上传

```python
from openai import OpenAI
client = OpenAI()

# 1. 创建 Vector Store
vs = client.vector_stores.create(name="company-docs")

# 2. 上传文件
with open("policy.pdf", "rb") as f:
    file_batch = client.vector_stores.file_batches.upload_and_poll(
        vector_store_id=vs.id,
        files=[f],
    )
```

### 3.2 在 Agent 里用

```python
from agents import Agent, Runner, FileSearchTool

agent = Agent(
    name="DocBot",
    instructions="基于公司文档回答员工问题。",
    tools=[FileSearchTool(vector_store_ids=[vs.id], max_num_results=5)],   # max_num_results 取 1~50
    model="gpt-6-luna",
)

result = await Runner.run(agent, "公司年假怎么休")
print(result.final_output)
```

还支持 `filters`（按文件属性过滤）、`ranking_options`（打分阈值、ranker）和 `include_search_results=True`（把检索到的原始片段带回 `new_items`，方便核对引用）。

切分、embedding、检索、rerank 都由 OpenAI 包办，比自己搭 RAG 省事，代价是能调的地方少。文档比较规整、不需要自己定制切分策略时，用它很合适。和 LangChain RAG 的对比见 [与 Pydantic AI / LangChain 互操作](../06-integration/04-vs-others.md)。

---

## 4. CodeInterpreterTool

```python
from agents import Agent, Runner, CodeInterpreterTool

agent = Agent(
    name="DataAnalyst",
    instructions="用 code_interpreter 分析数据，给图表和结论。",
    tools=[CodeInterpreterTool(tool_config={"type": "code_interpreter", "container": {"type": "auto"}})],
    model="gpt-6.1-sol",
)

result = await Runner.run(
    agent,
    "斐波那契前 20 项是什么，画个折线图",
)
```

代码在 OpenAI 的沙箱里运行，预装了 numpy、pandas、matplotlib，可以产出图表、CSV、Excel 文件，适合临时的数据分析、画图和计算。沙箱里不能 pip install，只能用预装的库。

0.24 的 `CodeInterpreterTool` 不再接受 `container=` 参数，这样写会报 `TypeError: unexpected keyword argument 'container'`。参数要放进 `tool_config`，内容就是 REST API 里 tool 的那段 JSON。`ImageGenerationTool(tool_config={"type": "image_generation", ...})` 同理。

---

## 5. ComputerTool（操作浏览器）

`ComputerTool` 在本地执行：你提供一个 `Computer` / `AsyncComputer` 实现（截图、点击、输入、滚动、按键…），SDK 把它映射到 Responses API 的 computer 工具。

```python
from agents import Agent, ComputerTool
from agents.computer import AsyncComputer   # 只有接口；SDK 包里没有现成的 Playwright 实现


class MyComputer(AsyncComputer):            # 你自己实现（官方 examples/tools/computer_use.py 里有 Playwright 版）
    environment = "browser"
    dimensions = (1280, 800)

    async def screenshot(self): ...         # 返回 base64 PNG
    async def click(self, x, y, button): ...
    async def double_click(self, x, y): ...
    async def scroll(self, x, y, scroll_x, scroll_y): ...
    async def type(self, text): ...
    async def wait(self): ...
    async def move(self, x, y): ...
    async def keypress(self, keys): ...
    async def drag(self, path): ...


agent = Agent(
    name="WebAgent",
    instructions="按用户要求操作浏览器。",
    tools=[ComputerTool(computer=MyComputer(), on_safety_check=lambda data: False)],  # 默认放行；这里保守地拒绝所有被标记的调用
    model="gpt-5.6",   # GA 内置 computer 工具；旧的 computer-use-preview 走旧 payload
)
```

要点：

- 模型迁移：`computer-use-preview` 换成 GA 的 `gpt-5.6-*`；工具选择符从 `computer_use_preview` 改为 `computer`；一个 `computer_call` 从单个动作变成一批 `actions[]`（SDK 按顺序执行）；GA 路径不再要求 `truncation="auto"`。SDK 会根据实际请求的模型自动选用对应的 payload。
- SDK 只按模型名判断用哪种 payload：`computer-use-preview` 开头的走旧格式，其他模型（包括不写 `model` 时的默认 `gpt-5.6-luna`）都按 GA 的 `computer` 工具发请求。
- `on_safety_check`：Responses API 可能在 `computer_call` 里带 `pending_safety_checks`；你的回调返回 True 才放行，返回 False 抛 `UserError`。不写回调的话会直接执行，那条 check 就一直处于未确认状态，SDK 不会替你弹人工确认。
- SDK 里没有 `agents.computer.LocalPlaywrightComputer`，也没有 `openai-agents[browser]` 这个 extra，浏览器那一端要自己实现。

详见 [实战 4：Computer Use Agent（操作浏览器）](../08-practice/04-computer-use.md)。运行这段需要 OpenAI 的 computer use 模型。

---

## 6. ImageGenerationTool

```python
from agents import Agent, Runner, ImageGenerationTool


agent = Agent(
    name="Designer",
    instructions="按用户描述生成图片",
    tools=[ImageGenerationTool(tool_config={"type": "image_generation", "quality": "low"})],
    model="gpt-6.1-sol",
)

result = await Runner.run(agent, "画一只穿宇航服的猫")
# result 里能拿到图片（base64 或 URL）
```

---

## 7. 组合 hosted tools

```python
agent = Agent(
    name="SuperResearcher",
    instructions="""你是研究助手。
- 时事 / 最新数据 → web_search
- 公司内部 → file_search
- 计算 / 数据分析 → code_interpreter
""",
    tools=[
        WebSearchTool(),
        FileSearchTool(vector_store_ids=["..."]),
        CodeInterpreterTool(tool_config={"type": "code_interpreter", "container": {"type": "auto"}}),
    ],
    model="gpt-6.1-sol",
)
```

模型根据指令自己选用哪个工具。

---

## 8. Programmatic Tool Calling（0.19 新增）

这个工具让模型写一段 JavaScript，在 OpenAI 托管的 V8 里运行（没有 Node API、文件和网络），用循环、分支、并行去调用你明确允许的工具，再把汇总结果交回模型。原本要 N 次模型往返的事，一次就做完。

```python
from pydantic import BaseModel
from agents import Agent, ModelSettings, ProgrammaticToolCallingTool
from agents.decorators import tool


class InventoryOutput(BaseModel):
    sku: str
    available_units: int


@tool(allowed_callers=["programmatic"])      # 只允许“程序”调用；["direct","programmatic"] 两者皆可
def get_inventory(sku: str) -> InventoryOutput:
    return InventoryOutput(sku=sku, available_units=42)


agent = Agent(
    name="Inventory planner",
    model="gpt-5.6",
    model_settings=ModelSettings(tool_choice="programmatic_tool_calling"),
    tools=[get_inventory, ProgrammaticToolCallingTool()],
)
```

要点：只有 Responses 模型可用；一个 agent 最多一个 `ProgrammaticToolCallingTool()`；结构化返回值（Pydantic / TypedDict / dataclass）会自动变成严格的输出 schema；工具的 guardrails、hooks、超时、审批、session、`RunState` 照常生效；需要人工逐个审查的高风险工具，建议还是让模型直接调用。

---

## 9. 跟 function_tool 混用

```python
@function_tool
def get_internal_data(table: str) -> str:
    """从公司内部 DB 查"""
    return "..."


agent = Agent(
    name="Hybrid",
    instructions="...",
    tools=[
        WebSearchTool(),               # hosted
        FileSearchTool(...),           # hosted
        get_internal_data,             # 自定义
    ],
)
```

---

## 10. 计费 / Cost

| Tool | 费用 |
|------|------|
| WebSearchTool | 按调用计费（价格随时变，以 OpenAI pricing 为准） |
| FileSearchTool | 检索调用 + Vector Store 存储 |
| CodeInterpreterTool | 按容器 session 计费 |
| ComputerTool | 按 input/output token，无额外 tool 费（截图会占用大量 input token） |
| ImageGenerationTool | 按生成图片数 |

防止 `WebSearchTool` 被滥用，可以在 instructions 里写明"只在用户问'最新'相关时用"。

---

## 11. Hosted Tool vs LangChain Tool vs MCP

| 维度 | OpenAI Hosted | LangChain Tools | MCP Server |
|------|---------------|-----------------|------------|
| 谁跑 | OpenAI | 你的进程 | 你的 MCP Server |
| 配置 | `tools=[WebSearchTool()]` | 自接 SerpAPI 等 | 启 MCP Server 进程 |
| 多 Agent 共享 | 重复 declare | 共享 Python obj | 多个 Agent 连同一 Server |
| 模型支持 | 仅 OpenAI Responses 模型 | 任意 | 任意（取决于客户端） |
| 灵活度 | 低（OpenAI 实现） | 高 | 高 |

---

## 12. 完整示例

运行这段需要 OpenAI API key。

```python
import asyncio
from agents import Agent, Runner, WebSearchTool


agent = Agent(
    name="NewsResearcher",
    instructions="""你是新闻助手。
- 用 web_search 查最新资讯
- 每条 claim 标注 [Source: URL]
- 输出 200 字内
""",
    tools=[WebSearchTool(search_context_size="medium")],
    model="gpt-6-luna",
)


async def main():
    result = await Runner.run(agent, "今天 AI 圈有什么大新闻")
    print(result.final_output)
    print(f"\n费用估算: {result.context_wrapper.usage.total_tokens} tokens")


asyncio.run(main())
```

---

## 13. 下一步

- [Agent as Tool：把 Agent 当作工具调用](./03-agent-as-tool.md)
- [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](./04-tool-choice.md)
- [实战 4：Computer Use Agent（操作浏览器）](../08-practice/04-computer-use.md)
