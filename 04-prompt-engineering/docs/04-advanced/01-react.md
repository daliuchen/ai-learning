# PE Advanced 01：ReAct —— Reasoning + Acting

> **一句话**：ReAct = 让模型在"思考"和"行动（调工具）"间循环——Thought → Action → Observation → Thought → ... 直到完成任务。这是绝大多数 Agent 框架（LangGraph、Pydantic AI、Claude Code、Cursor）的底层范式。

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

模型按"Thought / Action / Observation" 三段式循环，直到能给出最终答案。

---

## 2. ReAct 的"现代写法"

早期 ReAct 论文用 prompt 字符串模拟："Thought:..., Action:..."。**现代写法直接用 tool calling API**——更稳、更省 prompt 工程：

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
            model="claude-sonnet-4-6",
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

模型自己决定"何时调工具、何时给最终答案"。

---

## 3. ReAct prompt 设计要点

哪怕用了 tool calling API，prompt 仍然要写好：

### 3.1 系统消息说明 "agentic" 风格

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

### 3.2 工具描述要 LLM-friendly

工具描述给 LLM 看——清晰、完整、含使用提示：

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

### 3.3 防 loop

模型可能陷入"反复调同一个工具"——给个 stop condition：

```
约束：
- 同一个工具同样参数不要调超过 2 次
- 信息明显不在该工具范围内时，换工具或告诉用户
```

---

## 4. ReAct 失败模式

### 4.1 不调工具直接编
模型不愿调工具，直接用训练知识答 → 答错。

对策：

```
重要：
- 任何时间敏感 / 数字 / 事实查询，必须调工具
- 不要根据训练数据回答"今天" / "最新" / "现在" 类问题
```

### 4.2 工具调用 over-eagerly
什么都调工具，包括"1+1=?"。

对策：工具描述明确"不要用于"清单。

### 4.3 串太多步
20 步还没结论，token 爆 + cost 高。

对策：max_steps 限制 + 强制 "5 步内给方向"。

### 4.4 工具结果误解
模型把工具返回的数据理解错。

对策：
- 工具返回结构化 JSON 而非自然语言
- 返回里加 metadata 提示模型怎么读

---

## 5. ReAct vs CoT vs 普通 Tool Call

| 模式 | 思考 | 工具 |
|------|------|------|
| 普通 (zero-shot) | 无 | 无 |
| CoT | 显式 | 无 |
| Tool call (单轮) | 隐式 | 1 次 |
| **ReAct** | 显式 | 多次循环 |

ReAct 是 CoT + Tool 的循环版。

---

## 6. 框架对照

| 框架 | ReAct 实现 |
|------|-----------|
| LangGraph | `create_react_agent(model, tools)` |
| Pydantic AI | Agent + tools 默认就是 ReAct |
| Claude Code | 内置 ReAct loop（你写 MCP tool 即用） |
| 自己写 | 上面的 `react_loop` 函数 |

---

## 7. ReAct 的 prompt 关键点 checklist

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

完整 demo 见 [08-practice/02-research-agent.md](../08-practice/02-research-agent.md)——一个 4 工具的 research agent。

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **不写 system 风格说明** | 模型不知道该 agentic |
| **max_steps 没限** | 死循环爆 token |
| **工具描述太短** | LLM 选错工具 |
| **没透明性** | 用户不知道工具调了什么 |
| **prompt 写"Thought: ... Action: ..."** | 老 ReAct 风格，现在用 tool calling API |

---

## 10. 下一步

- 📖 工具调用 prompt 优化 → [02-tool-use.md](./02-tool-use.md)
- 📖 RAG → [03-rag-prompting.md](./03-rag-prompting.md)
- 🛠️ Research agent 实战 → [08-practice/02-research-agent.md](../08-practice/02-research-agent.md)

## 参考资料

- "ReAct" (Yao et al. 2022): https://arxiv.org/abs/2210.03629
- LangGraph create_react_agent: https://langchain-ai.github.io/langgraph/reference/prebuilt/
- Anthropic Tool Use Guide: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
