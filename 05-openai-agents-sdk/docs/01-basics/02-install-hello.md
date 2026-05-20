# 安装与第一个 Agent

> **一句话**：装 `openai-agents`、设 `OPENAI_API_KEY`、写一个 Agent 调 `Runner.run` —— 完事。

---

## 1. 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install openai-agents
```

依赖：

- Python 3.9+
- `openai>=1.50`（自动作为依赖装）

可选：

```bash
pip install python-dotenv      # 读 .env
pip install litellm            # 接非 OpenAI 模型
pip install langsmith langfuse # 外部观测
```

---

## 2. 环境变量

```bash
# .env
OPENAI_API_KEY=sk-...
```

```python
from dotenv import load_dotenv
load_dotenv()
```

---

## 3. 最小 Agent

```python
# demos/basics/01_hello.py
import asyncio
from agents import Agent, Runner


agent = Agent(
    name="Greeter",
    instructions="你是友好的助手，用一句话回答。",
    model="gpt-4o-mini",
)


async def main():
    result = await Runner.run(agent, "你好啊")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
```

跑：

```bash
python demos/basics/01_hello.py
# 你好！很高兴见到你 :)
```

---

## 4. 同步版（脚本场景）

不想用 asyncio：

```python
from agents import Agent, Runner

agent = Agent(name="G", instructions="...", model="gpt-4o-mini")
result = Runner.run_sync(agent, "你好")
print(result.final_output)
```

`run_sync` 内部 `asyncio.run`，单次脚本调用最方便。生产并发场景必须用 `await Runner.run`。

---

## 5. 加个工具

```python
# demos/basics/02_with_tool.py
import asyncio
from agents import Agent, Runner, function_tool


@function_tool
def get_weather(city: str) -> str:
    """查城市天气"""
    fake = {"北京": "23°C 晴", "上海": "26°C 多云"}
    return fake.get(city, f"{city}: 暂无数据")


agent = Agent(
    name="WeatherBot",
    instructions="用 get_weather 查询天气，回答简短。",
    tools=[get_weather],
    model="gpt-4o-mini",
)


async def main():
    result = await Runner.run(agent, "北京天气怎么样")
    print(result.final_output)


asyncio.run(main())
```

跑：

```
北京天气晴，23°C。
```

---

## 6. 结构化输出

```python
# demos/basics/03_structured.py
import asyncio
from pydantic import BaseModel
from agents import Agent, Runner


class Recipe(BaseModel):
    name: str
    ingredients: list[str]
    steps: list[str]


agent = Agent(
    name="Chef",
    instructions="生成一道菜的食谱",
    output_type=Recipe,
    model="gpt-4o-mini",
)


async def main():
    result = await Runner.run(agent, "西红柿炒鸡蛋")
    recipe: Recipe = result.final_output
    print(recipe.name)
    print("配料:", recipe.ingredients)
    print("步骤:")
    for i, s in enumerate(recipe.steps, 1):
        print(f"  {i}. {s}")


asyncio.run(main())
```

`output_type` 设了 Pydantic 模型后，`final_output` 就是 `Recipe` 实例。底层走 OpenAI 的 structured outputs。

---

## 7. 三件套：Agent / Tools / output_type

最常用的 Agent 构造范式：

```python
Agent(
    name=...,
    instructions=...,
    model="gpt-4o-mini",      # 或 "gpt-4o"
    tools=[...],
    output_type=SomeModel,
)
```

加 `handoffs=[...]` 就有多 Agent 协作；加 `input_guardrails=[...]` 就有守卫。下面几篇分别讲。

---

## 8. 错误处理 sanity check

跑不通常见原因：

| 报错 | 原因 | 解 |
|------|------|----|
| `openai.AuthenticationError` | `OPENAI_API_KEY` 没设 | 加到 .env 或 shell export |
| `ModuleNotFoundError: agents` | 包没装 | `pip install openai-agents` |
| `Model 'gpt-4o-mini' not found` | API key 没访问权限 | 升级账号 / 换 gpt-3.5-turbo |
| Tool 不被调用 | instructions 没明确指引 | 在 instructions 里描述 tool 用法 |

---

## 9. trace 上传（默认开）

跑完去 https://platform.openai.com/traces 能看到这次的：

- LLM call 内容
- Tool call 输入输出
- 总耗时 / token

关掉 tracing：

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1
```

或代码里：

```python
from agents import set_tracing_disabled
set_tracing_disabled(True)
```

---

## 10. 下一步

- 📖 Agent 完整配置参数 → [03-agent-config.md](./03-agent-config.md)
- 📖 Runner 的 run / run_sync / run_streamed 三种 → [04-runner.md](./04-runner.md)
- 📖 拿到结果后能取啥 → [05-run-result.md](./05-run-result.md)
- 📖 加会话状态 → [06-sessions.md](./06-sessions.md)

## 参考资料

- 官方 quickstart：https://openai.github.io/openai-agents-python/quickstart/
