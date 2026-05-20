# Hosted Tools：OpenAI 独门武器

> **一句话**：OpenAI 把 **web_search / file_search / code_interpreter / computer_use** 这些重型工具直接在云端跑——你只要在 `tools=[...]` 里加，模型就能用。这是 OpenAI Agents SDK 相对其它框架最大的优势。

---

## 1. 这些工具是啥

| Tool | 干啥 | 模型 |
|------|------|------|
| `WebSearchTool` | 实时搜 Web | GPT-4o / 4o-mini |
| `FileSearchTool` | 在 Vector Store 里检索 | GPT-4o / 4o-mini |
| `CodeInterpreterTool` | 运行 Python | GPT-4o |
| `ComputerTool` | 模型直接操作浏览器 / 桌面 | computer-use 模型 |
| `ImageGenerationTool` | 生成图片 | GPT-4o（image gen） |

**为啥叫 hosted**：工具的执行不在你这边，在 OpenAI 服务器跑。你只是声明"我想用 web_search"，模型决定调，OpenAI 跑完结果给模型。

---

## 2. WebSearchTool

```python
from agents import Agent, Runner, WebSearchTool


agent = Agent(
    name="Researcher",
    instructions="回答问题前用 web_search 找最新资料，引用源。",
    tools=[WebSearchTool()],
    model="gpt-4o-mini",
)

result = await Runner.run(agent, "OpenAI 最新的模型是什么？")
print(result.final_output)
```

特性：

- 实时（不是 cached snapshot）
- 自动引用源（带 URL）
- 计费：按调用计费（看 OpenAI pricing）

配置：

```python
WebSearchTool(
    user_location={"country": "CN", "city": "Beijing"},  # 地理偏好
    search_context_size="medium",  # "low" / "medium" / "high"
)
```

`search_context_size` 越大召回越多但烧 token。

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
    tools=[FileSearchTool(vector_store_ids=[vs.id], max_num_results=5)],
    model="gpt-4o-mini",
)

result = await Runner.run(agent, "公司年假怎么休")
print(result.final_output)
```

特性：

- 内置 chunk / embedding / 检索 / rerank
- 比自己搭 RAG 省心，但灵活度低
- 适合：标准化文档 / 不需要自己控 chunk 策略

跟 LangChain RAG 对比详见 [06-integration/04-vs-others.md](../06-integration/04-vs-others.md)。

---

## 4. CodeInterpreterTool

```python
from agents import Agent, Runner, CodeInterpreterTool

agent = Agent(
    name="DataAnalyst",
    instructions="用 code_interpreter 分析数据，给图表和结论。",
    tools=[CodeInterpreterTool(container={"type": "auto"})],
    model="gpt-4o",
)

result = await Runner.run(
    agent,
    "斐波那契前 20 项是什么，画个折线图",
)
```

特性：

- 在 OpenAI 沙箱跑 Python（有 numpy / pandas / matplotlib）
- 能产文件（图表 / CSV / Excel）
- 适合：临时数据分析、画图、计算

⚠️ **不能 pip install**——只有预装库。

---

## 5. ComputerTool（操作浏览器）

```python
from agents import Agent, Runner, ComputerTool
from agents.computer import LocalPlaywrightComputer


# 1. 创建 Computer 实例（这里用本地 Playwright）
computer = LocalPlaywrightComputer()
await computer.__aenter__()

# 2. 喂给 Agent
agent = Agent(
    name="WebAgent",
    instructions="按用户要求操作浏览器。",
    tools=[ComputerTool(computer=computer)],
    model="computer-use-preview",  # 必须用专门模型
)

result = await Runner.run(agent, "打开 hacker news，告诉我第一条标题")
print(result.final_output)

await computer.__aexit__(None, None, None)
```

特性：

- 模型看截图、决定点击 / 输入 / 滚动
- 适合做"AI 操作浏览器" agent
- ⚠️ 必须用 `computer-use-preview` 等专门模型

详见 [08-practice/04-computer-use.md](../08-practice/04-computer-use.md)。

---

## 6. ImageGenerationTool

```python
from agents import Agent, Runner, ImageGenerationTool


agent = Agent(
    name="Designer",
    instructions="按用户描述生成图片",
    tools=[ImageGenerationTool()],
    model="gpt-4o",
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
        CodeInterpreterTool(container={"type": "auto"}),
    ],
    model="gpt-4o",
)
```

模型按指令选 tool。

---

## 8. 跟 function_tool 混用

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

## 9. 计费 / Cost

| Tool | 费用 |
|------|------|
| WebSearchTool | $0.025 / 调用（看 openai pricing 实时） |
| FileSearchTool | 检索调用 + Vector Store 存储 |
| CodeInterpreterTool | $0.03 / session |
| ComputerTool | 按 input/output token，无额外 tool 费 |
| ImageGenerationTool | 按生成图片数 |

**实战注意**：

- 控制 `WebSearchTool` 滥用：在 instructions 里加 "只在用户问'最新'相关时用"
- 缓存：相同 query 走 prompt cache 也能省

---

## 10. Hosted Tool vs LangChain Tool vs MCP

| 维度 | OpenAI Hosted | LangChain Tools | MCP Server |
|------|---------------|-----------------|------------|
| 谁跑 | OpenAI | 你的进程 | 你的 MCP Server |
| 配置 | `tools=[WebSearchTool()]` | 自接 SerpAPI 等 | 启 MCP Server 进程 |
| 多 Agent 共享 | 重复 declare | 共享 Python obj | 多个 Agent 连同一 Server |
| 模型支持 | 仅 OpenAI 模型 | 任意 | 任意（取决于客户端） |
| 灵活度 | 低（OpenAI 实现） | 高 | 高 |

---

## 11. 完整 demo

```python
# demos/tools/02_hosted_tools.py
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
    model="gpt-4o-mini",
)


async def main():
    result = await Runner.run(agent, "今天 AI 圈有什么大新闻")
    print(result.final_output)
    print(f"\n费用估算: {result.usage.total_tokens} tokens")


asyncio.run(main())
```

---

## 12. 下一步

- 📖 把 Agent 当工具 → [03-agent-as-tool.md](./03-agent-as-tool.md)
- 📖 Tool 控制 / 错误 → [04-tool-choice.md](./04-tool-choice.md)
- 📖 实战：Computer Use Agent → [08-practice/04-computer-use.md](../08-practice/04-computer-use.md)
