# Pydantic AI 05-03：测试 Agent —— TestModel、FunctionModel 与 override

> **一句话**：Pydantic AI 的 `TestModel` / `FunctionModel` 配合 `agent.override(...)`，让你完全脱离真实 LLM 跑单测 —— 既快又便宜还可复现，CI 里跑 1000 个 case 也不烧一分钱。

---

## 1. 为什么 Agent 单测很难

LLM 应用第一次写单测的人，会发现传统单测套路全部失效：

- **不可复现**：同一个 prompt 跑两次结果不一样
- **慢**：单 case 几秒到几十秒
- **贵**：CI 每跑一次几块到几十块
- **依赖网络**：CI 在飞机上 / 内网时直接全挂
- **断言难写**：模型说"今天天气不错"和"今天天气挺好"算同一个意思吗？

直接 mock OpenAI SDK 也不优雅 —— 你要把 function calling、tool result、token usage 全部模拟一遍，case 多了维护成本爆炸。

Pydantic AI 的方案是**在 Model 这一层提供测试替身**，Agent 层完全不用改动：

```
真实使用：Agent + OpenAIModel  ←→  调真实 API
测试时：Agent + TestModel / FunctionModel  ←→  纯本地虚拟回复
```

切换只是一行 `with agent.override(model=...)`。

---

## 2. 三大测试工具速览

| 工具 | 作用 | 适合场景 |
|------|------|----------|
| **TestModel** | 自动按 schema 生成"看起来合理"的假数据 | 冒烟测试、跑通 happy path |
| **FunctionModel** | 你写一个函数模拟 LLM 行为 | 复杂场景、覆盖分支、断言工具调用 |
| **agent.override** | 临时替换 Agent 的 model / deps / system_prompt | 任何测试都先用它隔离副作用 |

辅助工具：

- `dirty-equals` —— 模糊断言（比如 `IsNow()` 校验时间字段）
- `inline-snapshot` —— 把第一次跑的结果 inline 写回测试代码作为期望值
- `logfire.no_auto_trace` —— 测试里禁用 Logfire 上报

---

## 3. TestModel：零代码假数据

最简单的姿势 —— 一行替换 model：

```python
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

agent = Agent("openai:gpt-4o-mini", system_prompt="你是助手。")

def test_agent_runs():
    with agent.override(model=TestModel()):
        result = agent.run_sync("你好")
    assert result.output  # 不为空就行
```

TestModel 会**根据 Agent 的 `output_type` 自动编造数据**：

```python
from pydantic import BaseModel

class Profile(BaseModel):
    name: str
    age: int

agent = Agent("openai:gpt-4o-mini", output_type=Profile)

def test_structured():
    with agent.override(model=TestModel()):
        r = agent.run_sync("...")
    assert isinstance(r.output, Profile)
    # name 会是 "a"，age 会是 0，这种"占位值"
```

### 3.1 控制 TestModel 输出

不想让 TestModel 完全随机？传 `custom_output_text` 或 `custom_output_args`：

```python
TestModel(custom_output_text="固定文本输出")
TestModel(custom_output_args={"name": "张三", "age": 30})
```

### 3.2 工具调用的默认行为

TestModel 默认会**把 Agent 的每个 tool 都调用一次**（用编造的参数），方便你测试"工具有没有被定义对"：

```python
@agent.tool
async def lookup(ctx, user_id: int) -> str: ...

with agent.override(model=TestModel()):
    r = agent.run_sync("...")
# lookup 会被调用一次，参数是 TestModel 编的
```

不想自动调？`TestModel(call_tools=[])`（空列表表示不调任何工具），或者 `call_tools=["lookup"]` 只调指定工具。

---

## 4. FunctionModel：完全自定义 LLM 行为

`TestModel` 适合冒烟，**真正复杂的单测要用 `FunctionModel`** —— 你写一个函数，每次模型被调用时这个函数就被执行：

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

这种"两轮回合"几乎覆盖所有 tool use 测试场景。

### 4.2 断言模型收到了什么

`FunctionModel` 的入参 `messages` 是 Agent 真实拼好的消息历史，可以直接断言：

```python
def llm(messages, info):
    assert any("管理员" in str(p.content) for m in messages for p in m.parts)
    return ModelResponse(parts=[TextPart(content="...")])
```

非常适合验证 "system prompt 有没有把 deps 里的用户角色填进去"。

---

## 5. agent.override：测试的总开关

`agent.override()` 是 context manager，能临时改三样东西：

```python
with agent.override(
    model=TestModel(),
    deps=MockDeps(db=fake_db, user_id=1),
    system_prompt="测试用 system prompt",
):
    agent.run_sync("...")
```

特点：

- **嵌套安全**：内层 override 离开后自动恢复到外层值
- **支持 async**：`async with agent.override(...): ...`
- **测试 fixture 友好**：可以放在 `pytest` autouse fixture 里

### 5.1 一个标准 fixture 模板

```python
import pytest
from pydantic_ai.models.test import TestModel

@pytest.fixture
def isolated_agent():
    with agent.override(model=TestModel(), deps=MockDeps()):
        yield agent
```

测试函数直接用 `isolated_agent` 就行，再也不用每个 case 写 `with override(...)`.

---

## 6. 工具调用的测试技巧

工具调用是 Agent 里最容易出 bug 的地方，重点测试它的输入输出契约。

### 6.1 Mock 整个工具

如果某个 tool 调用了外部 API，单测里直接换成 mock 实现：

```python
async def fake_lookup(ctx, user_id: int) -> str:
    return f"user-{user_id}"

# 临时换掉
original = agent._function_tools["lookup"]
agent._function_tools["lookup"] = original.replace(function=fake_lookup)
```

更优雅的姿势是从一开始就把外部依赖放进 `deps`，单测里换 deps 即可：

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

**架构纪律**：工具内部不要直接 `import requests / await db.connect()`，所有外部依赖通过 `ctx.deps`。这样测试时 0 改动就能 mock。

### 6.2 验证工具被调用的次数和参数

`FunctionModel` 里数 call_count 就行；如果想测"工具本身"是否被 Agent 正确调用：

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

Pydantic 模型字段错了会自动 raise `ValidationError`，**测试要专门覆盖错误路径**：

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

这种 case 验证了 **Pydantic AI 的 ModelRetry 机制**有没有正确触发。

---

## 8. 与 pytest 集成

`pytest-asyncio` 是必须的：

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

每次更新 prompt 都担心改坏？用 `inline-snapshot` 把上次跑通的输出 inline 写到测试里：

```python
from inline_snapshot import snapshot

def test_prompt_unchanged():
    with agent.override(model=FunctionModel(my_deterministic_llm)):
        r = agent.run_sync("你好")
    assert r.output == snapshot("你也好")
```

第一次跑加 `--inline-snapshot=create` 参数自动写入；之后改了 prompt 跑 `--inline-snapshot=fix` 一键更新。比手写 `assert ==` 字符串方便很多。

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

适合"字段格式我知道，但具体值我不在乎"的场景。

---

## 10. Logfire 在测试环境的处理

Pydantic AI 默认会上报 Logfire span，在测试里要关掉：

```python
import logfire
logfire.configure(send_to_logfire=False)
```

或者用环境变量 `LOGFIRE_SEND_TO_LOGFIRE=false`，CI 上一次设置全部测试生效。

如果你想**验证测试里产生了哪些 span**（白盒测试），用 `logfire.testing.CaptureLogfire`：

```python
from logfire.testing import CaptureLogfire

def test_spans(capfire: CaptureLogfire):
    agent.run_sync("...")
    assert any("agent.run" in s.name for s in capfire.exporter.exported_spans)
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
    "openai:gpt-4o-mini",
    deps_type=Deps,
    output_type=Out,
    system_prompt="问候用户。",
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

**实际项目比例**：单测里 90% TestModel + 10% FunctionModel，外加每周跑一次 5-10 个真实模型的"金标"用例。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `with agent.override(...)` 之外的代码还在用真实 model | override 是 context-scoped | 测试函数内完整覆盖，或用 fixture |
| TestModel 工具调用炸了 | TestModel 默认会调所有 tool，但工具内部依赖真实 deps | `TestModel(call_tools=[])` 或同时 override deps |
| FunctionModel 不知道返回什么类型 | 没看清 Agent 的 output_type | 看 `info.output_tools`，里面有 final_result 工具 schema |
| inline-snapshot 死活更新不了 | 没传 `--inline-snapshot=fix` | `pytest --inline-snapshot=fix` |
| 测试里 Logfire 报网络错误 | 默认开启上报，但 CI 没 token | `logfire.configure(send_to_logfire=False)` |
| `pytest-asyncio` 报错 fixture 不识别 | `asyncio_mode` 没配 | 设 `asyncio_mode = "auto"` |
| FunctionModel 拿不到 system prompt | system 已经被合并到 messages | 在 `messages[0]` 就能找到 |
| 真实模型测试偶尔 fail | LLM 本身不确定 | 用 `dirty-equals` 做语义级断言，或用低 temperature + snapshot |

---

## 14. 生产环境建议

1. **CI 必跑 TestModel 套件**：每个 PR 必过，秒级反馈
2. **金标用例独立 job**：真实模型跑的测试单独一个 nightly job，失败发告警但不阻塞 PR
3. **prompt 改动必加 snapshot**：用 inline-snapshot 把改动可见化，review 时一眼看出对话有没有变
4. **deps 全部走依赖注入**：tool 里不要直接 import 全局对象，否则没法 mock
5. **测错误路径**：单测要专门覆盖 ValidationError / ModelRetry / Timeout，这些是真实故障率最高的地方
6. **Logfire 在 CI 关闭**：环境变量 `LOGFIRE_SEND_TO_LOGFIRE=false` 全局生效

---

## 15. 本章 demo

完整可运行代码：[`demos/patterns/03_testing.py`](../../demos/patterns/03_testing.py)

跑：

```bash
pytest demos/patterns/03_testing.py -v
```

下一篇：[04-embeddings.md](04-embeddings.md) — Pydantic AI 怎么对接向量库做 RAG。
