# Agent as Tool：把 Agent 当工具调

> **一句话**：用 `agent.as_tool(...)` 把整个 Agent 包成一个 tool，主 Agent 像调函数一样调它——适合"专家子 agent"模式，跟 handoffs 是两种思路。

---

## 1. 跟 Handoffs 的区别

| 维度 | Agent as Tool | Handoff |
|------|--------------|---------|
| 控制权 | 主 Agent 拿回控制 | 切换主 Agent |
| 上下文 | 只给子 Agent 你想给的 | 整段对话历史 |
| 用法 | "我需要翻译一下" | "这事我搞不定，你来" |
| 类比 | 函数调用 | 转接电话 |

**何时用 Agent as Tool**：

- 主 Agent 主导流程，子 Agent 是工具人（翻译、总结、计算）
- 子 Agent 的工作独立可重入
- 不想给子 Agent 整段历史

**何时用 Handoff**：

- 真·分流（这事 billing 处理，从此交给 billing）
- 子 Agent 需要看完整对话
- 详见 [03-handoffs/01-handoffs-concept.md](../03-handoffs/01-handoffs-concept.md)

---

## 2. 最简示例

```python
from agents import Agent, Runner

# 子 Agent
translator = Agent(
    name="Translator",
    instructions="把任何语言翻成英语，只输出翻译。",
    model="gpt-4o-mini",
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
    model="gpt-4o-mini",
)

result = await Runner.run(main_agent, "我想买杯咖啡")
print(result.final_output)
```

底层：主 Agent 调 `translate(input="我想买杯咖啡")` → 内部跑一次 `translator` → 拿到英语结果 → 主 Agent 用结果继续。

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

主 Agent 像调子函数一样组合它们。

---

## 4. as_tool 完整参数

```python
agent.as_tool(
    tool_name="my_tool",                 # tool 名
    tool_description="干啥的",            # 描述
    custom_output_extractor=None,        # 自定义提取子 agent 的输出
)
```

`custom_output_extractor`：默认 `lambda r: r.final_output`，可以改：

```python
def my_extract(result):
    # result 是子 agent 的 RunResult
    return result.final_output.upper()

translator.as_tool("translate", "...", custom_output_extractor=my_extract)
```

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

子 Agent 可以自带 tools / handoffs，把整套能力封装成一个 callable。

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

子 Agent 返回 Sentiment 实例 → SDK dump 成 JSON 字符串给主 Agent。

---

## 7. 嵌套（孙子 Agent）

```python
deep = Agent(name="Deep", instructions="...")
mid = Agent(name="Mid", instructions="...", tools=[deep.as_tool("deep_tool", "...")])
top = Agent(name="Top", instructions="...", tools=[mid.as_tool("mid_tool", "...")])
```

理论上可以嵌套但**别玩出花**——超过 2 层 trace 难看。

---

## 8. as_tool vs handoffs 抉择树

```
任务能否独立 / 可重入？
  ├─ 能 → as_tool
  └─ 不能（需对话上下文）→ handoff

主 Agent 需要"调完拿回"控制吗？
  ├─ 需要 → as_tool
  └─ 转交后不管 → handoff

子 Agent 看到啥上下文？
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
    model="gpt-4o-mini",
)

# 主 Agent: 规划 + 综合
main = Agent(
    name="ResearchCoordinator",
    instructions="""把用户问题拆 3-5 个 sub-questions，
每个 sub-question 调 research(sub_question) 子工具。
拿到全部结果后综合 500-1000 字报告。
""",
    tools=[researcher.as_tool("research", "对一个 sub-question 做研究")],
    model="gpt-4o",
)


result = await Runner.run(main, "AI Agent 框架的现状")
```

主 Agent（Sonnet）规划 + 综合，子 Agent（mini）跑 search——cost / quality 双优。

---

## 10. 跟 Pydantic AI / LangChain 视角

| 框架 | Agent as Tool 等价物 |
|------|---------------------|
| OpenAI Agents | `agent.as_tool(...)` |
| Pydantic AI | 自己写：`@parent_agent.tool` 里 `await child_agent.run(...)` |
| LangChain | RunnableSequence / `chain.as_tool()` |
| LangGraph | sub-graph as node |

OpenAI Agents 的 API 最直接。

---

## 11. 完整 demo

```python
# demos/tools/03_agent_as_tool.py
import asyncio
from agents import Agent, Runner


translator = Agent(
    name="Translator",
    instructions="把任何语言翻成简洁英语，只输出翻译。",
    model="gpt-4o-mini",
)


reviewer = Agent(
    name="Reviewer",
    instructions="审查英文文本，找语法和措辞问题，列出修改建议。",
    model="gpt-4o-mini",
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
    model="gpt-4o",
)


async def main():
    result = await Runner.run(editor, "请把以下翻好并润色：我对你公司的产品非常感兴趣，希望讨论合作。")
    print(result.final_output)


asyncio.run(main())
```

---

## 12. 下一步

- 📖 Tool 控制 / 错误 → [04-tool-choice.md](./04-tool-choice.md)
- 📖 动态工具集 → [05-dynamic-tools.md](./05-dynamic-tools.md)
- 📖 跟 Handoffs 对比 → [03-handoffs/01-handoffs-concept.md](../03-handoffs/01-handoffs-concept.md)
