# Lifecycle Hooks：生命周期钩子

> 实现 `AgentHooks` 或 `RunHooks`，就能在 agent 开始、调用工具、handoff、结束这些节点上执行自己的代码，记日志、打 metric、做审计、上报进度都靠它。

---

## 1. 两类 hooks

| | AgentHooks | RunHooks |
|---|---|---|
| 绑定到 | 单个 Agent | 整个 Runner.run |
| 用途 | 这个 Agent 自己的事 | 所有 Agent 都触发 |

---

## 2. AgentHooks 接口

```python
from agents import AgentHooks, RunContextWrapper, Agent, Tool     # agents.lifecycle 里也有，顶层直接 import 更省事


class MyHooks(AgentHooks):
    async def on_start(
        self,
        context: RunContextWrapper,
        agent: Agent,
    ) -> None:
        """Agent 开始跑"""
        pass

    async def on_end(
        self,
        context: RunContextWrapper,
        agent: Agent,
        output,
    ) -> None:
        """Agent 给出 final_output"""
        pass

    async def on_handoff(
        self,
        context: RunContextWrapper,
        agent: Agent,
        source: Agent,
    ) -> None:
        """被 handoff 到本 agent"""
        pass

    async def on_tool_start(
        self,
        context: RunContextWrapper,
        agent: Agent,
        tool: Tool,
    ) -> None:
        """这个 agent 准备调一个 tool"""
        pass

    async def on_tool_end(
        self,
        context: RunContextWrapper,
        agent: Agent,
        tool: Tool,
        result: str,
    ) -> None:
        """tool 返回结果（函数工具是字符串；MCP 工具是 {'type': 'text', 'text': ...} 这样的 dict）"""
        pass

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        """每次调模型前（可以在这里记 prompt、算 token 预估）"""

    async def on_llm_end(self, context, agent, response) -> None:
        """每次模型返回后；response.usage 是这一次调用的用量"""


agent = Agent(name="A", instructions="...", hooks=MyHooks())
```

---

## 3. RunHooks 接口

```python
from agents import RunHooks


class MyRunHooks(RunHooks):
    async def on_agent_start(self, context, agent): ...
    async def on_agent_end(self, context, agent, output): ...
    async def on_handoff(self, context, from_agent, to_agent): ...
    async def on_tool_start(self, context, agent, tool): ...
    async def on_tool_end(self, context, agent, tool, result): ...
    async def on_llm_start(self, context, agent, system_prompt, input_items): ...
    async def on_llm_end(self, context, agent, response): ...


# 在 Runner.run 时传
result = await Runner.run(agent, "...", hooks=MyRunHooks())
# Agent.as_tool 的子 Agent 那次 run 也可以单独传 hooks：translator.as_tool(..., hooks=...)
```

`RunHooks` 会在所有 Agent 上触发，包括 handoff 之后的，适合放日志、监控这类横切逻辑。

---

## 4. 用例 1：日志

```python
import logging

log = logging.getLogger("agents")


class LoggingHooks(RunHooks):
    async def on_agent_start(self, ctx, agent):
        log.info(f"[Agent start] {agent.name}")

    async def on_tool_start(self, ctx, agent, tool):
        log.info(f"  [Tool] {agent.name} → {tool.name}")

    async def on_tool_end(self, ctx, agent, tool, result):
        log.info(f"  [Tool result] {tool.name} → {str(result)[:80]}")

    async def on_handoff(self, ctx, from_agent, to_agent):
        log.info(f"  [Handoff] {from_agent.name} → {to_agent.name}")

    async def on_agent_end(self, ctx, agent, output):
        log.info(f"[Agent end] {agent.name} → {str(output)[:80]}")


await Runner.run(triage, "...", hooks=LoggingHooks())
```

---

## 5. 用例 2：Metrics

```python
from prometheus_client import Counter, Histogram
import time


tool_calls = Counter("agent_tool_calls", "Total tool calls", ["agent", "tool"])
tool_duration = Histogram("agent_tool_duration_seconds", "Tool duration", ["tool"])


class MetricsHooks(RunHooks):
    def __init__(self):
        self._tool_start = {}

    async def on_tool_start(self, ctx, agent, tool):
        self._tool_start[id(tool)] = time.time()
        tool_calls.labels(agent=agent.name, tool=tool.name).inc()

    async def on_tool_end(self, ctx, agent, tool, result):
        start = self._tool_start.pop(id(tool), None)
        if start:
            tool_duration.labels(tool=tool.name).observe(time.time() - start)
```

---

## 6. 用例 3：审计和合规

```python
class AuditHooks(RunHooks):
    async def on_handoff(self, ctx, from_agent, to_agent):
        # 客服转交都要 log
        await audit_log.write({
            "user_id": ctx.context.user_id,
            "from": from_agent.name,
            "to": to_agent.name,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def on_tool_start(self, ctx, agent, tool):
        # 写库 / 删数据等敏感操作
        if tool.name in {"delete_user", "issue_refund", "wire_transfer"}:
            await audit_log.write({
                "user_id": ctx.context.user_id,
                "agent": agent.name,
                "tool": tool.name,
                "timestamp": datetime.utcnow().isoformat(),
            })
```

---

## 7. 用例 4：缓存 tool 调用

```python
class CacheHooks(RunHooks):
    def __init__(self):
        self.cache = {}

    async def on_tool_start(self, ctx, agent, tool):
        # 这只是个 hook，不能改 tool 行为 - 缓存逻辑放 tool 内部
        pass
```

上面这种写法其实做不到缓存：hook 只能观察，不能替换或修改操作本身。缓存要写在 tool 内部：

```python
_cache = {}

@function_tool
def cached_search(query: str) -> str:
    if query in _cache:
        return _cache[query]
    result = real_search(query)
    _cache[query] = result
    return result
```

---

## 8. 用例 5：流式上报进度

```python
class ProgressHooks(RunHooks):
    def __init__(self, websocket):
        self.ws = websocket

    async def on_agent_start(self, ctx, agent):
        await self.ws.send_json({"type": "agent_start", "name": agent.name})

    async def on_tool_start(self, ctx, agent, tool):
        await self.ws.send_json({"type": "tool_start", "tool": tool.name})

    async def on_tool_end(self, ctx, agent, tool, result):
        await self.ws.send_json({"type": "tool_end", "tool": tool.name})


# WebSocket handler
@app.websocket("/chat")
async def chat(ws):
    await ws.accept()
    while True:
        msg = await ws.receive_text()
        await Runner.run(agent, msg, hooks=ProgressHooks(ws))
```

---

## 9. AgentHooks 和 RunHooks 怎么选

只关心某一个 Agent，就用 AgentHooks，逻辑跟着 Agent 走；要监控整个 run，包括 handoff 之后的 agent，就用 RunHooks。两者也可以混用，都会触发。

---

## 10. Hook 里抛异常会怎样

整个 run 会失败。比如在工具 hook 里 `raise RuntimeError("oops")`，`Runner.run` 抛出的是 `UserError: Error running tool ping: oops`：

```python
class BadHooks(RunHooks):
    async def on_tool_start(self, ctx, agent, tool):
        raise RuntimeError("oops")  # 整个 Runner.run 抛
```

所以生产环境里的 hook 要写得稳，我一般这样包一层：

```python
async def on_tool_start(self, ctx, agent, tool):
    try:
        await self._do_something()
    except Exception as e:
        log.error("hook failed", error=e)
        # 别抛
```

---

## 11. 完整 demo

```python
import asyncio
import time
from agents import Agent, Runner, function_tool
from agents import RunHooks


@function_tool
def slow_op(x: int) -> int:
    time.sleep(0.5)
    return x * 2


class TimingHooks(RunHooks):
    def __init__(self):
        self.t0 = None
        self.tool_t0 = {}

    async def on_agent_start(self, ctx, agent):
        if not self.t0:
            self.t0 = time.time()
        print(f"[{time.time() - self.t0:.2f}s] Agent start: {agent.name}")

    async def on_tool_start(self, ctx, agent, tool):
        self.tool_t0[id(tool)] = time.time()
        print(f"[{time.time() - self.t0:.2f}s] Tool start: {tool.name}")

    async def on_tool_end(self, ctx, agent, tool, result):
        dt = time.time() - self.tool_t0.pop(id(tool))
        print(f"[{time.time() - self.t0:.2f}s] Tool end: {tool.name} ({dt:.2f}s)")

    async def on_agent_end(self, ctx, agent, output):
        print(f"[{time.time() - self.t0:.2f}s] Agent end: {agent.name}")


agent = Agent(
    name="A",
    instructions="用 slow_op 计算",
    tools=[slow_op],
    model="gpt-6-luna",
)


async def main():
    result = await Runner.run(agent, "计算 5 的二倍", hooks=TimingHooks())
    print("\nFinal:", result.final_output)


asyncio.run(main())
```

输出：

```
[0.00s] Agent start: A
[0.85s] Tool start: slow_op
[1.36s] Tool end: slow_op (0.50s)
[1.78s] Agent end: A

Final: 10
```

---

## 12. 下一步

- 接千问、Claude 等非 OpenAI 模型 → [Multi-provider：接千问 / Claude / Gemini / 本地模型](./04-multi-provider.md)
- [Realtime Agent：实时语音 API](./05-realtime.md)
- 实战：用 hooks 做 SSE 进度上报 → [FastAPI / Lambda 部署](../06-integration/03-fastapi-deploy.md)
