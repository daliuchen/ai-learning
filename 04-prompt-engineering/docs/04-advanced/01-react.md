# PE Advanced 01：ReAct（Reasoning + Acting）

> ReAct 让模型在思考和行动（调用工具）之间循环：Thought、Action、Observation，再回到 Thought，直到任务完成。LangGraph、Pydantic AI、Claude Code、Cursor 这些 Agent 框架和工具，底层大多是这个模式。

---

## 1. 概念

```
[问题: "查一下旧金山今天天气，然后写一首关于它的诗"]
   ↓
[Thought 1] 我需要先查天气。
[Action 1]  调用 get_weather(city="San Francisco")
[Observation 1] {"temp": 18, "conditions": "rainy"}
   ↓
[Thought 2] 现在我可以写一首关于雨天的诗。
[Action 2]  生成诗（不需要工具）
   ↓
[最终输出] "雾都细雨..."
```

模型按 Thought、Action、Observation 三段循环，直到能给出最终答案。

---

## 2. 现在的写法

早期的 ReAct 论文是用 prompt 字符串模拟的，让模型输出 "Thought:..., Action:..."。现在直接用 tool calling API 就行，更稳定，也省掉了大量 prompt 工程。

```python
import anthropic

client = anthropic.Anthropic()
TOOLS = [
    {
        "name": "get_weather",
        "description": "查询城市天气",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
]

def react_loop(user_message: str):
    messages = [{"role": "user", "content": user_message}]
    for step in range(10):    # 防死循环
        resp = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=1000,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            # 终态：返回最终文本
            return next((b.text for b in resp.content if b.type == "text"), "")

        # 执行所有 tool_use
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError("step limit exceeded")


def run_tool(name, input):
    if name == "get_weather":
        return {"temp": 18, "conditions": "rainy"}
    raise ValueError(f"unknown tool: {name}")
```

什么时候调工具、什么时候给最终答案，由模型自己决定。

---

## 3. ReAct 的 prompt 怎么写

即使用了 tool calling API，prompt 也得认真写。

### 3.1 在 system 里说明工作方式

```
你是研究助手。你会：
1. 接到任务后，**先思考**需要什么信息
2. 调用工具获取信息
3. 根据工具结果继续推理或调更多工具
4. 信息够了再给最终答案

行为约束：
- 一步最多调 1-2 个工具
- 如果不确定就调工具而不是猜
- 调够 5 步还没结论，告诉用户"信息不足，需要 ..."
```

### 3.2 工具描述写给模型看

工具描述的读者是模型，要写得清楚完整，带上使用提示：

```python
{
    "name": "search_web",
    "description": """搜索互联网获取最新信息。
    
    用途：
    - 查询当前事件、新闻、价格
    - 验证事实
    
    不要用于：
    - 数学计算（用 calculator）
    - 内部数据（用 database_query）
    """,
    "input_schema": {...}
}
```

### 3.3 防止死循环

模型可能反复调用同一个工具，要给它停下来的条件：

```
约束：
- 同一个工具同样参数不要调超过 2 次
- 信息明显不在该工具范围内时，换工具或告诉用户
```

---

## 4. ReAct 的失败模式

### 4.1 不调工具，直接编
模型懒得调工具，凭训练数据里的知识作答，结果答错。可以在 prompt 里写死：

```
重要：
- 任何时间敏感 / 数字 / 事实查询，必须调工具
- 不要根据训练数据回答"今天" / "最新" / "现在" 类问题
```

### 4.2 什么都要调工具
连「1+1 等于几」都去调工具。对策是在工具描述里写清楚「不要用于」哪些情况。

### 4.3 步数太多
20 步还没结论，token 和成本一起涨。用 max_steps 兜底，并要求模型 5 步之内给出方向。

### 4.4 误读工具结果
模型把工具返回的数据理解错了。让工具返回结构化的 JSON 而不是自然语言，必要时在返回里附上说明，告诉模型字段怎么读。

---

## 5. ReAct、CoT 和普通 Tool Call

| 模式 | 思考 | 工具 |
|------|------|------|
| 普通 (zero-shot) | 无 | 无 |
| CoT | 显式 | 无 |
| Tool call (单轮) | 隐式 | 1 次 |
| ReAct | 显式 | 多次循环 |

可以把 ReAct 理解成 CoT 加上工具调用，再循环起来。

---

## 6. 框架对照

| 框架 | ReAct 实现 |
|------|-----------|
| LangChain 1.x | `create_agent(model, tools)`（返回 LangGraph 图，取代已弃用的 `langgraph.prebuilt.create_react_agent`），见 [LangChain 08：Agents](../../../01-langchain/docs/01-langchain/08-agents.md) |
| Pydantic AI | Agent + tools 默认就是 ReAct |
| Claude Code | 内置 ReAct 循环，写好 MCP tool 就能用 |
| 自己写 | 上面的 `react_loop` 函数 |

---

## 7. ReAct prompt 检查清单

```
□ system 说明 "你是 agent，会循环调用工具"
□ system 明确什么时候必须调工具
□ system 明确什么时候不该调工具
□ 工具描述每个写完整：用途 / 不要用于 / 输入 schema
□ 防 loop：max_steps + 同工具重复检测
□ 失败兜底：信息不足时告诉用户
□ 输出最终答案时引用工具结果（透明性）
```

---

## 8. demo

完整 demo 见 [Research Agent 的 5 版迭代史](../08-practice/02-research-agent.md)，是一个带搜索、读网页、Python 三个工具的 research agent。

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| system 里没说明工作方式 | 模型不知道要循环调工具 |
| 没限制 max_steps | 死循环，token 耗尽 |
| 工具描述太短 | 模型选错工具 |
| 不透明 | 用户不知道调了哪些工具 |
| prompt 里还写 "Thought: ... Action: ..." | 这是老式写法，现在用 tool calling API |

---

## 10. 下一步

- [Tool Use Prompting](./02-tool-use.md)
- [RAG Prompting 与引用约束](./03-rag-prompting.md)
- [Research Agent 的 5 版迭代史](../08-practice/02-research-agent.md)

## 参考资料

- "ReAct" (Yao et al. 2022): https://arxiv.org/abs/2210.03629
- LangChain Agents（create_agent）: https://docs.langchain.com/oss/python/langchain/agents
- Anthropic Tool Use Guide: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
