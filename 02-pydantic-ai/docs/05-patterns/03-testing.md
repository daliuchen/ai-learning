# Pydantic AI 05-03：用 TestModel、FunctionModel 与 override 测试 Agent

> Pydantic AI 的 `TestModel` / `FunctionModel` 配合 `agent.override(...)`，可以完全不调用真实 LLM 来跑单元测试。这样测试快、结果可复现，CI 里跑 1000 个 case 也不产生 API 费用。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. 为什么 Agent 单测很难

第一次给 LLM 应用写单元测试，会发现传统的那套做法都不太好用：

- 不可复现：同一个 prompt 跑两次，结果不一样
- 慢：单个 case 要几秒到几十秒
- 贵：CI 每跑一次就是几块到几十块钱
- 依赖网络：在飞机上或者内网里，CI 直接全挂
- 断言难写："今天天气不错"和"今天天气挺好"算不算同一个意思？

直接 mock OpenAI SDK 也麻烦，function calling、tool result、token usage 都得模拟一遍，case 一多就很难维护。

Pydantic AI 的做法是在 Model 这一层提供测试替身，Agent 层完全不用改：

```
真实使用：Agent + OpenAIModel  ←→  调真实 API
测试时：Agent + TestModel / FunctionModel  ←→  纯本地虚拟回复
```

切换只需要一行 `with agent.override(model=...)`。

分工也要先划清：单元测试测的是你自己的代码（工具、依赖、输出校验、失败处理），模型回答得好不好交给 evals（见 [Pydantic Evals 评测框架](../04-modules/02-evals.md)）。官方文档把这一页叫 *Unit testing*，也是这个意思。

---

## 2. 三个测试工具速览

| 工具 | 作用 | 适合场景 |
|------|------|----------|
| TestModel | 按 schema 自动生成格式正确的假数据 | 冒烟测试、跑通 happy path |
| FunctionModel | 你写一个函数模拟 LLM 行为 | 复杂场景、覆盖分支、断言工具调用 |
| agent.override | 临时替换 Agent 的 model / deps / instructions | 所有测试都先用它隔离副作用 |

辅助工具：

- `dirty-equals`：模糊断言（比如用 `IsNow()` 校验时间字段）
- `inline-snapshot`：把第一次跑出的结果直接写回测试代码，作为期望值
- `logfire.configure(send_to_logfire=False)`：测试里不上报 Logfire
- `models.ALLOW_MODEL_REQUESTS = False`（或在 conftest 里全局设置）：万一漏了 override，真实模型请求会直接报错，不会悄悄产生费用
- `TestImageGenerationModel`：图片生成模型的测试替身

`TestModel` 和 `FunctionModel` 在内存里实现了批处理 API，用到 `model_request_batch` 的代码也能离线测。

---

## 3. TestModel：零代码假数据

最简单的用法是一行替换 model：

```python
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

agent = Agent("openai-chat:qwen-flash", instructions="你是助手。")

def test_agent_runs():
    with agent.override(model=TestModel()):
        result = agent.run_sync("你好")
    assert result.output  # 不为空就行
```

TestModel 会根据 Agent 的 `output_type` 自动编造数据：

```python
from pydantic import BaseModel

class Profile(BaseModel):
    name: str
    age: int

agent = Agent("openai-chat:qwen-flash", output_type=Profile)

def test_structured():
    with agent.override(model=TestModel()):
        r = agent.run_sync("...")
    assert isinstance(r.output, Profile)
    # name 会是 "a"，age 会是 0，这种"占位值"
```

### 3.1 控制 TestModel 输出

想让 TestModel 返回指定内容，就传 `custom_output_text` 或 `custom_output_args`：

```python
TestModel(custom_output_text="固定文本输出")
TestModel(custom_output_args={"name": "张三", "age": 30})
```

### 3.2 工具调用的默认行为

TestModel 默认会用编造的参数把 Agent 的每个 tool 都调用一次，方便检查工具有没有定义对：

```python
@agent.tool
async def lookup(ctx, user_id: int) -> str: ...

with agent.override(model=TestModel()):
    r = agent.run_sync("...")
# lookup 会被调用一次，参数是 TestModel 编的
```

不想让它自动调用，可以用 `TestModel(call_tools=[])`（空列表表示不调任何工具），或者 `call_tools=["lookup"]` 只调指定的工具。

编造的字符串参数是占位值 `'a'`。如果工具里校验参数格式（比如必须是订单号），校验会一直失败，直到 `ModelRetry` 预算耗尽。这时要么让校验放过占位值，要么改用按脚本返回固定调用的 `FunctionModel`（见下一节）。

---

## 4. FunctionModel：完全自定义 LLM 行为

`TestModel` 适合冒烟测试，复杂一些的单元测试要用 `FunctionModel`：你写一个函数，每次模型被调用时就执行这个函数：

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

def my_llm(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    # 看最后一条用户消息
    last = messages[-1].parts[-1].content
    if "你好" in last:
        return ModelResponse(parts=[TextPart(content="你也好")])
    return ModelResponse(parts=[TextPart(content="不懂")])

def test_function_model():
    with agent.override(model=FunctionModel(my_llm)):
        assert agent.run_sync("你好").output == "你也好"
        assert agent.run_sync("xxx").output == "不懂"
```

### 4.1 模拟工具调用

`FunctionModel` 可以返回 `ToolCallPart`，让 Agent 走工具调用分支：

```python
from pydantic_ai.messages import ToolCallPart, ToolReturnPart

call_count = 0

def llm(messages, info):
    global call_count
    call_count += 1
    if call_count == 1:
        # 第一轮：模拟模型决定调用工具
        return ModelResponse(parts=[
            ToolCallPart(tool_name="lookup", args={"user_id": 42}, tool_call_id="c1"),
        ])
    # 第二轮：拿到 tool 结果后给最终答案
    return ModelResponse(parts=[TextPart(content="结果是 ok")])
```

先调工具、再给答案，这两轮的结构能覆盖绝大多数 tool use 测试场景。

### 4.2 断言模型收到了什么

`FunctionModel` 的入参 `messages` 是 Agent 真实拼好的消息历史，可以直接断言：

```python
def llm(messages, info):
    assert any("管理员" in str(p.content) for m in messages for p in m.parts)
    return ModelResponse(parts=[TextPart(content="...")])
```

用来验证 system prompt 有没有把 deps 里的用户角色填进去，很方便。

### 4.3 流式请求要传 `stream_function`

只传普通函数的 `FunctionModel` 不支持流式请求。被测代码用了 `run_stream`，或者挂了 `Coder` 这类内部走流式请求的 capability，就会报 `FunctionModel must receive a stream_function to support streamed requests`。这种情况要额外传 `stream_function`，让它按块产出文本或工具调用。

---

## 5. agent.override：测试的总开关

`agent.override()` 是一个 context manager，最常用的是临时替换下面三样（另外还能换 `tools` / `toolsets` / `model_settings` / `retries` 等）：

```python
with agent.override(
    model=TestModel(),
    deps=MockDeps(db=fake_db, user_id=1),
    instructions="测试用 system prompt",
):
    agent.run_sync("...")
```

override 可以嵌套，内层退出后自动恢复成外层的值；支持 `async with agent.override(...): ...`；也可以放进 `pytest` 的 autouse fixture 里。

### 5.1 一个标准 fixture 模板

```python
import pytest
from pydantic_ai.models.test import TestModel

@pytest.fixture
def isolated_agent():
    with agent.override(model=TestModel(), deps=MockDeps()):
        yield agent
```

测试函数直接用 `isolated_agent`，不用每个 case 都写一遍 `with override(...)`。

---

## 6. 工具调用的测试技巧

工具调用是 Agent 里最容易出 bug 的地方，测试重点放在它的输入输出约定上。

### 6.1 Mock 整个工具

如果某个 tool 调用了外部 API，单测里直接换成 mock 实现：

```python
from pydantic_ai import Tool

async def fake_lookup(ctx, user_id: int) -> str:
    return f"user-{user_id}"

# 临时换掉 agent 的函数工具（override 的 tools 会替换 agent 自己注册的那组）
with agent.override(tools=[Tool(fake_lookup, name="lookup")]):
    ...
```

别去改 `agent` 上的私有属性，V2 里内部结构已经变过，私有字段随时可能再变。

更好的做法是一开始就把外部依赖放进 `deps`，单元测试里换掉 deps 就行：

```python
@dataclass
class Deps:
    user_repo: UserRepo

@agent.tool
async def lookup(ctx: RunContext[Deps], user_id: int) -> str:
    return await ctx.deps.user_repo.fetch(user_id)

# 测试
with agent.override(deps=Deps(user_repo=FakeUserRepo())):
    ...
```

工具内部不要直接 `import requests / await db.connect()`，所有外部依赖都通过 `ctx.deps` 拿。这样测试时不用改业务代码就能 mock。

### 6.2 验证工具被调用的次数和参数

在 `FunctionModel` 里数 call_count 就行。如果想确认工具本身被 Agent 正确调用了：

```python
from unittest.mock import AsyncMock

mock_fn = AsyncMock(return_value="ok")

@agent.tool
async def lookup(ctx, user_id: int) -> str:
    return await mock_fn(user_id)

with agent.override(model=TestModel()):
    agent.run_sync("...")

mock_fn.assert_called()
```

---

## 7. 输出校验的测试

Pydantic 模型字段不合法时会抛 `ValidationError`，测试要专门覆盖这条错误路径：

```python
class Out(BaseModel):
    age: int = Field(ge=0, le=150)

agent = Agent(..., output_type=Out)

def test_invalid_output_recovers():
    def llm(messages, info):
        # 第一次故意返回非法值
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="final_result", args={"age": -1}, tool_call_id="x")
            ])
        return ModelResponse(parts=[
            ToolCallPart(tool_name="final_result", args={"age": 30}, tool_call_id="y")
        ])
    calls = 0
    with agent.override(model=FunctionModel(llm)):
        r = agent.run_sync("...")
    assert r.output.age == 30
    assert calls == 2  # Pydantic AI 自动 ModelRetry
```

这个 case 验证的是 Pydantic AI 的 ModelRetry 机制有没有正确触发。

---

## 8. 与 pytest 集成

需要装 `pytest-asyncio`：

```ini
# pyproject.toml or pytest.ini
[tool.pytest.ini_options]
asyncio_mode = "auto"  # 所有 async def test 自动当协程跑
```

异步测试写法：

```python
async def test_agent_async():
    with agent.override(model=TestModel()):
        r = await agent.run("hi")
    assert r.output
```

---

## 9. Snapshot 测试：用 inline-snapshot

担心改 prompt 时改坏东西，可以用 `inline-snapshot` 把上次跑通的输出直接写进测试代码：

```python
from inline_snapshot import snapshot

def test_prompt_unchanged():
    with agent.override(model=FunctionModel(my_deterministic_llm)):
        r = agent.run_sync("你好")
    assert r.output == snapshot("你也好")
```

第一次运行时加 `--inline-snapshot=create` 参数自动写入；之后改了 prompt，跑一下 `--inline-snapshot=fix` 就能更新。比手写 `assert ==` 字符串方便。

### 9.1 用 dirty-equals 做模糊断言

```python
from dirty_equals import IsStr, IsNow
from datetime import datetime, timezone

def test_with_dirty_equals():
    r = ...
    assert r.output == {
        "id": IsStr(regex=r"\d+"),
        "created_at": IsNow(tz=timezone.utc, delta=5),
    }
```

适合只关心字段格式、不关心具体值的场景。

---

## 10. Logfire 在测试环境的处理

开了 Logfire 之后 span 会被上报，测试里要关掉：

```python
import logfire
logfire.configure(send_to_logfire=False)
```

也可以用环境变量 `LOGFIRE_SEND_TO_LOGFIRE=false`，在 CI 上设一次，所有测试都生效。

如果想检查测试里产生了哪些 span（白盒测试），用 `logfire.testing.CaptureLogfire`：

```python
from logfire.testing import CaptureLogfire

def test_spans(capfire: CaptureLogfire):
    agent.run_sync("...")
    assert any("invoke_agent" in s.name for s in capfire.exporter.exported_spans)   # 默认数据格式下 agent span 的名字
```

---

## 11. 实战：完整 pytest 套件

```python
# tests/test_agent.py
import pytest
from dataclasses import dataclass
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

@dataclass
class Deps:
    name: str

class Out(BaseModel):
    greeting: str

agent = Agent(
    "openai-chat:qwen-flash",
    deps_type=Deps,
    output_type=Out,
    instructions="问候用户。",
)

@agent.tool
async def fancy_name(ctx: RunContext[Deps]) -> str:
    return f"大佬 {ctx.deps.name}"

# ---------- 用 TestModel 做冒烟 ----------
def test_smoke():
    with agent.override(model=TestModel(), deps=Deps(name="alice")):
        r = agent.run_sync("hi")
    assert isinstance(r.output, Out)

# ---------- 用 FunctionModel 测分支 ----------
def test_uses_tool():
    calls = []

    def llm(messages, info):
        calls.append(messages[-1].parts[-1].content)
        if len(calls) == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="fancy_name", args={}, tool_call_id="t1"),
            ])
        return ModelResponse(parts=[
            ToolCallPart(
                tool_name="final_result",
                args={"greeting": f"hello, {messages[-1].parts[0].content}"},
                tool_call_id="t2",
            ),
        ])

    with agent.override(model=FunctionModel(llm), deps=Deps(name="alice")):
        r = agent.run_sync("hi")
    assert "大佬 alice" in r.output.greeting
```

完整版（含 fixture / 参数化 / snapshot）见 [`demos/patterns/03_testing.py`](../../demos/patterns/03_testing.py)。

---

## 12. 选型决策树

```
要写 Agent 单测吗？
├─ 只想跑通别报错（冒烟）→ TestModel
├─ 要覆盖具体分支 / 工具调用 → FunctionModel + 自己写 llm 函数
├─ 验证字段约束 / Pydantic 校验 → FunctionModel 返回非法值，断言会重试
├─ 防止 prompt 改坏 → FunctionModel + inline-snapshot
└─ 集成测试（真实模型 + 真实 API）→ 不 override，跑少量"金标"用例
```

实际项目里通常是：冒烟类用例大多用 TestModel，需要覆盖分支的少数用例用 FunctionModel，另外定期用真实模型跑一小批"金标"用例。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `with agent.override(...)` 之外的代码还在用真实 model | override 是 context-scoped | 测试函数内完整覆盖，或用 fixture |
| TestModel 调工具时报错 | TestModel 默认会调所有 tool，但工具内部依赖真实 deps | `TestModel(call_tools=[])` 或同时 override deps |
| FunctionModel 不知道返回什么类型 | 没看清 Agent 的 output_type | 看 `info.output_tools`，里面有 final_result 工具 schema |
| inline-snapshot 怎么都更新不了 | 没传 `--inline-snapshot=fix` | `pytest --inline-snapshot=fix` |
| 测试里 Logfire 报网络错误 | 默认开启上报，但 CI 没 token | `logfire.configure(send_to_logfire=False)` |
| `pytest-asyncio` 报错 fixture 不识别 | `asyncio_mode` 没配 | 设 `asyncio_mode = "auto"` |
| FunctionModel 在 parts 里找不到 instructions | `instructions` 不是 part，挂在 `ModelRequest.instructions` 上 | 读 `info.instructions`，或 `messages[-1].instructions`；`system_prompt=` 产生的 `SystemPromptPart` 才在 `messages[0].parts` 里 |
| 真实模型测试偶尔 fail | LLM 本身不确定 | 用 `dirty-equals` 做语义级断言，或用低 temperature + snapshot |

---

## 14. 生产环境建议

1. CI 每个 PR 都跑 TestModel 套件，几秒内就有结果
2. 用真实模型的金标用例放在单独的 nightly job 里，失败时告警，但不阻塞 PR
3. 改 prompt 时配合 inline-snapshot，让输出变化在 review 时直接可见
4. 外部依赖全部走 deps 注入，tool 里不直接 import 全局对象，否则没法 mock
5. 专门覆盖错误路径：ValidationError、ModelRetry、Timeout，线上出问题最多的就是这些地方
6. CI 里关掉 Logfire，设环境变量 `LOGFIRE_SEND_TO_LOGFIRE=false` 即可

---

## 15. 本章 demo

完整可运行代码：[`demos/patterns/03_testing.py`](../../demos/patterns/03_testing.py)

跑：

```bash
pytest demos/patterns/03_testing.py -v
```

这 9 个用例都不需要 API key。

下一篇：[Embeddings 与 RAG 集成](04-embeddings.md)，讲 Pydantic AI 怎么对接向量库做 RAG。
