# Agent as Tool：把 Agent 当作工具调用

> `agent.as_tool(...)` 能把一个完整的 Agent 包装成工具，主 Agent 像调函数一样调用它，调用完控制权还在主 Agent 手里。这和 handoff 是两种不同的多 Agent 组织方式，这篇先讲区别，再讲用法。

---

## 1. 和 Handoffs 的区别

| 维度 | Agent as Tool | Handoff |
|------|--------------|---------|
| 控制权 | 主 Agent 拿回控制 | 切换主 Agent |
| 上下文 | 只给子 Agent 你想给的 | 整段对话历史 |
| 用法 | "我需要翻译一下" | "这事我搞不定，你来" |
| 类比 | 函数调用 | 转接电话 |

主 Agent 掌控流程、子 Agent 只负责翻译、总结、计算这类独立的小活，而且你不想把整段历史交给子 Agent 时，用 Agent as Tool。

如果是分流，比如账单问题从此交给 billing 处理，或者子 Agent 必须看到完整对话，就用 handoff，详见 [Handoffs 概念：与 Tool、Sub-Agent 的区别](../03-handoffs/01-handoffs-concept.md)。

---

## 2. 最简示例

```python
from agents import Agent, Runner

# 子 Agent
translator = Agent(
    name="Translator",
    instructions="把任何语言翻成英语，只输出翻译。",
    model="gpt-6-luna",
)

# 主 Agent，把 translator 当 tool
main_agent = Agent(
    name="Main",
    instructions="用户输入可能是中文，先用 translate 翻成英语再处理。",
    tools=[
        translator.as_tool(
            tool_name="translate",
            tool_description="翻译任何语言到英语",
        ),
    ],
    model="gpt-6-luna",
)

result = await Runner.run(main_agent, "我想买杯咖啡")
print(result.final_output)
```

执行过程是：主 Agent 调用 `translate(input="我想买杯咖啡")`，SDK 在内部跑一次 `translator`，拿到英文结果后交回主 Agent，主 Agent 接着往下走。

---

## 3. 多个专家子 Agent

```python
translator = Agent(name="Translator", instructions="翻译到英语")
summarizer = Agent(name="Summarizer", instructions="100 字总结")
sentiment = Agent(
    name="Sentiment",
    instructions="判断情感（positive / negative / neutral）",
)


main_agent = Agent(
    name="Coordinator",
    instructions="""按用户需求选合适的专家子工具。
- 翻译：translate
- 总结：summarize
- 情感：analyze_sentiment
""",
    tools=[
        translator.as_tool("translate", "翻译到英语"),
        summarizer.as_tool("summarize", "100 字总结"),
        sentiment.as_tool("analyze_sentiment", "情感分析"),
    ],
)
```

主 Agent 按需调用其中几个，把结果组合起来。

---

## 4. as_tool 完整参数

```python
agent.as_tool(
    tool_name="my_tool",                 # tool 名
    tool_description="干什么的",          # 描述
    custom_output_extractor=None,        # 自定义提取子 agent 的输出（必须是 async 函数）
    is_enabled=True,                     # 布尔 / (ctx, agent) -> bool：按条件对模型隐藏
    needs_approval=False,                # 调这个子 Agent 前要不要人工批准（同 function_tool）
    parameters=None,                     # Pydantic/dataclass：给子 Agent 传结构化输入，而不只是一个字符串
    include_input_schema=False, input_builder=None,
    on_stream=None,                      # 监听子 Agent 的流式事件
    max_turns=None, run_config=None, hooks=None,                  # 子 Agent 那次 run 的配置
    session=None, previous_response_id=None, conversation_id=None,  # 子 run 的状态策略（不会自动继承父 run 的）
    failure_error_function=...,          # 子 Agent 崩了怎么回给主 Agent
)
```

默认的输入是 `{"input": "..."}`，只有一个字符串字段。需要结构化输入时，传 `parameters`：

```python
class TranslationInput(BaseModel):
    text: str = Field(description="要翻译的文本")
    target: Literal["en", "fr"]

translator.as_tool("translate", "翻译", parameters=TranslationInput)
# 模型按 schema 填 {"text": ..., "target": "en"}，子 Agent 在 context.tool_input 里能读到解析后的对象
```

`custom_output_extractor` 用来改写交回主 Agent 的内容，默认取 `final_output`。它的签名是 `Callable[[RunResult], Awaitable[str]]`，必须写成 `async def`。传普通函数或 lambda 会在调用时失败，主 Agent 收到的是一条通用的工具出错提示，而不是你想要的输出：

```python
async def my_extract(result):
    # result 是子 agent 的 RunResult
    return str(result.final_output).upper()

translator.as_tool("translate", "...", custom_output_extractor=my_extract)   # 输出 HELLO, WORLD
```

也可以监听子 Agent 的流式事件：

```python
from agents import AgentToolStreamEvent

async def on_stream(ev: AgentToolStreamEvent) -> None:
    print(ev["agent"].name, ev["event"].type)   # raw_response_event / run_item_stream_event / agent_updated_stream_event

translator.as_tool("translate", "...", on_stream=on_stream)
```

给了 `on_stream` 就会以流式方式跑子 Agent，并在流结束后把最终输出返回给主 Agent。

如果子 Agent 里的工具需要审批，中断会一路冒泡到最外层 run 的 `result.interruptions`，在外层 `RunState` 上批准或拒绝后恢复即可，详见 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](./04-tool-choice.md)。

---

## 5. 子 Agent 也能有自己的 tools

```python
@function_tool
def lookup_dict(word: str) -> str:
    return "..."


translator = Agent(
    name="Translator",
    instructions="先查字典再翻",
    tools=[lookup_dict],
)

main_agent = Agent(
    name="Main",
    tools=[translator.as_tool("translate", "...")],
)
```

子 Agent 可以带自己的 tools 和 handoffs，对主 Agent 来说，这一整套能力就是一个可调用的工具。

---

## 6. 子 Agent + output_type

```python
from pydantic import BaseModel

class Sentiment(BaseModel):
    label: str
    score: float


sentiment_agent = Agent(
    name="Sentiment",
    instructions="判断情感",
    output_type=Sentiment,
)


# as_tool 自动序列化 Sentiment 给主 Agent
main_agent = Agent(
    name="Main",
    tools=[sentiment_agent.as_tool("get_sentiment", "情感分析")],
)
```

子 Agent 返回 Sentiment 实例，SDK 把它序列化成 JSON 字符串交给主 Agent。需要别的格式，就用上面的 `custom_output_extractor`。

---

## 7. 多层嵌套

```python
deep = Agent(name="Deep", instructions="...")
mid = Agent(name="Mid", instructions="...", tools=[deep.as_tool("deep_tool", "...")])
top = Agent(name="Top", instructions="...", tools=[mid.as_tool("mid_tool", "...")])
```

嵌套层数没有限制，但超过两层以后 trace 会很难读，我一般不这么做。另外，子 Agent 那次 run 不继承父 run 的会话状态，想共享历史，要显式给两边传同一个 `session`。

---

## 8. 选 as_tool 还是 handoffs

```
任务能否独立 / 可重入？
  ├─ 能 → as_tool
  └─ 不能（需对话上下文）→ handoff

主 Agent 需要"调完拿回"控制吗？
  ├─ 需要 → as_tool
  └─ 转交后不管 → handoff

子 Agent 看到什么上下文？
  ├─ 你显式给 → as_tool
  └─ 整段对话 → handoff
```

---

## 9. 实战：Research Agent 的子 Agent 化

```python
# Researcher 一类问题搜一类
researcher = Agent(
    name="Researcher",
    instructions="对一个 sub-question 做 web 搜索，给摘要 + URL",
    tools=[WebSearchTool()],
    model="gpt-6-luna",
)

# 主 Agent: 规划 + 综合
main = Agent(
    name="ResearchCoordinator",
    instructions="""把用户问题拆 3-5 个 sub-questions，
每个 sub-question 调 research(sub_question) 子工具。
拿到全部结果后综合 500-1000 字报告。
""",
    tools=[researcher.as_tool("research", "对一个 sub-question 做研究")],
    model="gpt-6.1-sol",
)


result = await Runner.run(main, "AI Agent 框架的现状")
```

主 Agent 用 `gpt-6.1-sol` 做规划和综合，子 Agent 用 `gpt-6-luna` 跑搜索，贵的模型只花在需要推理的地方。

---

## 10. 其他框架里的对应写法

| 框架 | Agent as Tool 等价物 |
|------|---------------------|
| OpenAI Agents | `agent.as_tool(...)` |
| Pydantic AI | 自己写：`@parent_agent.tool` 里 `await child_agent.run(...)` |
| LangChain | RunnableSequence / `chain.as_tool()` |
| LangGraph | sub-graph as node |

几家里 OpenAI Agents 的写法最直接，一个方法就够了。

---

## 11. 完整示例

```python
import asyncio
from agents import Agent, Runner


translator = Agent(
    name="Translator",
    instructions="把任何语言翻成简洁英语，只输出翻译。",
    model="gpt-6-luna",
)


reviewer = Agent(
    name="Reviewer",
    instructions="审查英文文本，找语法和措辞问题，列出修改建议。",
    model="gpt-6-luna",
)


editor = Agent(
    name="Editor",
    instructions="""你是文本编辑助手。
工作流：
1. 用 translate 把用户中文翻成英语
2. 用 review 审查
3. 给最终改进后的英语
""",
    tools=[
        translator.as_tool("translate", "翻译到英语"),
        reviewer.as_tool("review", "审查英文"),
    ],
    model="gpt-6.1-sol",
)


async def main():
    result = await Runner.run(editor, "请把以下翻好并润色：我对你公司的产品非常感兴趣，希望讨论合作。")
    print(result.final_output)


asyncio.run(main())
```

---

## 12. 下一步

- [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](./04-tool-choice.md)
- [动态工具集与上下文感知工具](./05-dynamic-tools.md)
- [Handoffs 概念：与 Tool、Sub-Agent 的区别](../03-handoffs/01-handoffs-concept.md)
