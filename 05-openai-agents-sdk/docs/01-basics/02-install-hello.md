# 安装与第一个 Agent

> 跑通第一个 Agent 只要三步：装 `openai-agents`，设好 `OPENAI_API_KEY`，写一个 Agent 交给 `Runner.run`。这篇顺带讲没有 OpenAI key 时怎么用千问代替，后面各篇的示例都可以照这个办法换模型。

---

## 1. 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install openai-agents
```

依赖：

- Python 3.10+（0.9 起不再支持 3.9；`any-llm` 扩展要 3.11+）
- `openai>=3,<4`（0.21 起要求 openai v3，自动作为依赖装）

可选：

```bash
pip install python-dotenv      # 读 .env
pip install "openai-agents[any-llm]"   # 接非 OpenAI 模型的适配器（LiteLLM 见 05-advanced/04）
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

### 没有 OpenAI key？接第三方（以千问为例）

SDK 默认走 Responses API，并且会把 trace 上传到 OpenAI。千问、DeepSeek 这类 OpenAI 兼容端点一般只支持 Chat Completions；没有 OpenAI key 时上传 trace 也会报 401。所以要改两处：模型换成 Chat Completions 模型，再关掉 tracing（或者换成自己的 tracing 处理器）：

```python
# qwen.py —— 接 OpenAI 兼容端点（千问）的公共小模块
import os
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

set_tracing_disabled(True)
client = AsyncOpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],   # https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key=os.environ["OPENAI_API_KEY"],
)
MODEL = OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client)
```

之后把文中所有 `model="gpt-6-luna"` 换成 `model=MODEL` 即可（`from qwen import MODEL`）。懒得每个 Agent 都传的话，也可以全局切换：

```python
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")   # 配合环境变量 OPENAI_API_KEY / OPENAI_BASE_URL
set_tracing_disabled(True)
# Agent(model="qwen-flash") 这样直接写模型名就行
```

> 如果想保留 tracing、只发给自己的处理器（见 [自定义 Tracer：接 LangSmith / Langfuse / Logfire](../05-advanced/02-custom-tracer.md)），不要 `set_tracing_disabled(True)`（它会让所有处理器都收不到 span），改用 `set_trace_processors([...])` 替换掉默认的 OpenAI 导出器。
>
> 走兼容端点时，function tool、handoffs、guardrails、sessions、MCP 都能正常用。hosted tools、Realtime 和 Voice 的 STT/TTS 是 OpenAI 自家的服务，第三方端点用不了。

---

## 3. 最小 Agent

想换成千问，把 `model=` 换成 `from qwen import MODEL` 导入的 `MODEL`。

```python
import asyncio
from agents import Agent, Runner


agent = Agent(
    name="Greeter",
    instructions="你是友好的助手，用一句话回答。",
    model="gpt-6-luna",
)


async def main():
    result = await Runner.run(agent, "你好啊")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
```

把上面的代码保存为 `hello.py` 后运行：

```bash
python hello.py
# 你好！很高兴见到你 :)
```

---

## 4. 同步版（脚本场景）

不想写 asyncio 的话：

```python
from agents import Agent, Runner

agent = Agent(name="G", instructions="...", model="gpt-6-luna")
result = Runner.run_sync(agent, "你好")
print(result.final_output)
```

`run_sync` 内部自己开事件循环，写一次性脚本最方便。但它不能在已有的事件循环里调用，放在 FastAPI handler 或 Jupyter cell 里会报 `RuntimeError: cannot be called when an event loop is already running`。线上要并发处理请求，就用 `await Runner.run`。

---

## 5. 加个工具

```python
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
    model="gpt-6-luna",
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
    model="gpt-6-luna",
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

`output_type` 设成 Pydantic 模型后，`final_output` 就是 `Recipe` 实例，底层用的是 OpenAI 的 structured outputs（JSON schema）。有些第三方端点不支持 `json_schema`，会报 `response_format.type ... not one of ['text','json_object']`；千问 `qwen-flash` 支持。

---

## 7. Agent、Tools、output_type

大部分 Agent 都长这样：

```python
Agent(
    name=...,
    instructions=...,
    model="gpt-6-luna",      # 或 "gpt-6.1-sol"
    tools=[...],
    output_type=SomeModel,
)
```

再加 `handoffs=[...]` 可以做多 Agent 协作，加 `input_guardrails=[...]` 可以挂输入守卫，后面几篇分别讲。

---

## 8. 跑不通时先查这几项

| 报错 | 原因 | 解 |
|------|------|----|
| `openai.AuthenticationError` | `OPENAI_API_KEY` 没设 | 加到 .env 或 shell export |
| `ModuleNotFoundError: agents` | 包没装 | `pip install openai-agents` |
| `Model '...' not found` | 账号没有该模型权限 / 模型名过期 | 换账号可用的模型，或设 `OPENAI_DEFAULT_MODEL` |
| 第三方端点报 404 / 401（tracing） | 默认走 Responses API、默认上传 trace | `set_default_openai_api("chat_completions")` + `set_tracing_disabled(True)` |
| Tool 不被调用 | instructions 没明确指引 | 在 instructions 里描述 tool 用法 |

---

## 9. trace 上传（默认开）

有 OpenAI key 的话，跑完去 https://platform.openai.com/traces 能看到这次运行的 LLM 调用内容、工具调用的输入输出、总耗时和 token 数。用第三方模型又没有 OpenAI key 时要把它关掉，否则会报 tracing 401。

关掉 tracing：

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1
```

或代码里：

```python
from agents import set_tracing_disabled
set_tracing_disabled(True)          # 全局
# 或只关某一次：Runner.run(agent, "...", run_config=RunConfig(tracing_disabled=True))
```

不写 `model` 时，SDK 默认用 `gpt-5.6-luna`（0.20 起，更早的版本默认模型不同），可以用环境变量 `OPENAI_DEFAULT_MODEL` 或 `RunConfig(model=...)` 统一改。

---

## 10. 下一步

- [Agent 配置详解](./03-agent-config.md)
- [Runner 的三种运行方式：run / run_sync / run_streamed](./04-runner.md)
- [RunResult 与 Usage：结果、中间步骤与 token 用量](./05-run-result.md)
- [Sessions：会话状态自动管理](./06-sessions.md)

## 参考资料

- 官方 quickstart：https://openai.github.io/openai-agents-python/quickstart/
