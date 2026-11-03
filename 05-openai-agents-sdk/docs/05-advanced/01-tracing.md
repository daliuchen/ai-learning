# 内置 Tracing 与 OpenAI Platform Dashboard

> 装好 SDK 就自带 tracing：每次 `Runner.run` 都会自动上传到 https://platform.openai.com/traces，在上面能看到 LLM 调用、工具调用、handoff、token 用量和耗时。这篇讲怎么用、怎么关，以及数据隐私上要注意什么。

---

## 1. 默认开启

> 前提是你有 OpenAI API key，而且账号不是 Zero Data Retention（ZDR）组织，ZDR 组织下 tracing 不可用。用千问这类第三方模型、又没有 OpenAI key 时，默认的 trace 上传会返回 401，这时要 `set_tracing_disabled(True)`，或者换成自己的 tracing 处理器（下一篇讲）。下文列出的 span 结构，用自定义 `TracingProcessor` 也能收到同样的内容。

```python
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")
await Runner.run(agent, "你好")
# 自动产生 trace，上传到 OpenAI Platform
```

去 https://platform.openai.com/traces 能看到一条新 trace：

```
Trace "Agent workflow"（默认名；可用 workflow_name 改）
└─ Task（整个 Runner 调用，带汇总 usage）
   └─ Turn 1（一次模型回合，带本轮 usage）
      └─ Agent: A
         └─ Generation（一次 LLM 调用）
```

---

## 2. 关掉 Tracing

```python
from agents import RunConfig, set_tracing_disabled

set_tracing_disabled(True)                                   # 全局
await Runner.run(agent, "...", run_config=RunConfig(tracing_disabled=True))   # 只关这一次
```

或者用环境变量：

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1
```

本地开发不想弄脏 dashboard，或者生产环境用 LangSmith、Langfuse 等其他平台时，就关掉它。

---

## 3. trace 的层级结构

```
Trace                ← 一次 Runner.run 顶层（或你用 trace() 包起来的一组 run）
  └─ Task span       ← 每次 Runner 调用（0.24 里带汇总 usage）
       └─ Turn span  ← 每个模型回合（带本轮 usage）
            └─ Agent span      ← 一个 Agent 的执行段
                 ├─ Generation ← 一次 LLM 调用（记输入输出）
                 ├─ Function   ← 一个 function_tool 调用（记输入输出）
                 ├─ Handoff    ← handoff 事件
                 └─ Guardrail  ← guardrail 检查
音频：Transcription（STT）/ Speech（TTS）/ Speech group span
```

Span 会自动嵌套，dashboard 上显示成树形。Task 和 Turn 在 SDK 里是特殊的 `custom` span（`sdk_span_type` 为 `task` / `turn`），写自定义处理器时按这个字段识别。嫌层级太深，可以用 `RunConfig(tracing={"include_task_and_turn_spans": False})` 关掉它们，agent / generation / function / guardrail / handoff / custom span 照常记录。

---

## 4. 给 trace 起名

```python
from agents import trace

with trace("My Custom Workflow"):
    result = await Runner.run(agent, "...")

# 只改某一次 run 的名字/分组，不想包 with：
Runner.run(agent, "...", run_config=RunConfig(workflow_name="客服主流程", group_id="thread_42", trace_metadata={"env": "prod"}))
```

Dashboard 里这条 trace 的标题就是 "My Custom Workflow"。`group_id` 用来把同一个对话（比如聊天线程 ID）的多条 trace 串在一起；`trace_id` 必须是 `trace_<32位字母数字>` 格式。

---

## 5. 把多次 Runner.run 放进一个 trace

默认每次 `Runner.run` 是一个独立的 trace。想合并：

```python
from agents import trace


async def pipeline(question: str):
    with trace("Research Pipeline"):
        plan = await Runner.run(planner, question)
        research = await Runner.run(researcher, plan.final_output)
        report = await Runner.run(writer, research.final_output)
        return report.final_output
```

三次 run 合进一个 trace，整条 pipeline 的耗时一目了然。

---

## 6. 加自定义 metadata

```python
with trace(
    "Customer Service",
    metadata={
        "user_id": "u42",
        "session_id": "s99",
        "experiment": "v3",
    },
):
    result = await Runner.run(agent, msg)
```

Dashboard 里可以按 metadata 筛选。

---

## 7. 加自定义 Span

```python
from agents import custom_span


with trace("Pipeline"):
    with custom_span("Pre-process", data={"input_len": len(raw_input)}):     # 同步 with（不是 async with），数据通过 data= 传
        cleaned = clean_input(raw_input)

    result = await Runner.run(agent, cleaned)

    with custom_span("Post-process"):
        formatted = format_output(result.final_output)
```

自定义处理器收到的是 `type=custom`、`name=Pre-process`、`data={'input_len': ...}` 这样一条 span。trace 也可以不用 `with`，手动写 `t = trace(...); t.start(mark_as_current=True) ... t.finish(reset_current=True)`。当前的 trace 和 span 是靠 contextvar 追踪的，所以并发的协程互不干扰。

自己写的代码段也想在 trace 里看到时，就用这种方式。

---

## 8. 看 trace 时看什么

一条 trace 在 dashboard 上能看到：

- 总时长：分清是 LLM 慢还是 tool 慢
- Token 用量：哪一步花得最多
- Tool 的输入输出：模型给了 tool 什么参数，tool 返回了什么
- Handoff 链：从哪个 agent 转到了哪个
- 错误：哪一步抛了异常

---

## 9. 区分用户和环境

OpenAI Platform 没有内置 project / env 的概念，需要自己写进 trace metadata：

```python
with trace(
    "API request",
    metadata={
        "env": "production",   # production / staging / dev
        "service": "customer-bot",
        "version": "v3.2.1",
    },
):
    ...
```

然后在 dashboard 上按 metadata 过滤。

---

## 10. 跟 LangSmith / Langfuse 对比

| | OpenAI Traces | LangSmith | Langfuse |
|---|---|---|---|
| 开箱即用 | 是，默认开 | 需配置 key | 需配置 key |
| 多模型支持 | 第三方模型的 trace 也能上报（需要 OpenAI key，见第 11 节） | 全 | 全 |
| 评测 | 弱 | 强，Evals 是核心功能 | 有 Evals |
| 项目管理 | 无，只有 metadata | 内置 | 内置 |
| 自托管 | 不支持 | 支持 | 支持（开源） |
| 价格 | OpenAI 免费 | 按 trace 数 | freemium |

如果要用非 OpenAI 的模型、需要完整的评测工作流，或者有严格的数据合规要求（只能放内部环境），就该切到外部平台了，见 [自定义 Tracer：接 LangSmith / Langfuse / Logfire](./02-custom-tracer.md)。

---

## 11. 数据隐私：不上传 trace 内容

从轻到重有三档：

```python
# 1) 保留 trace 结构，但 LLM / 工具的输入输出内容不记录
await Runner.run(agent, "...", run_config=RunConfig(trace_include_sensitive_data=False))
# 或全局：export OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA=0   （默认 True！）
# 语音的音频 base64 另有开关：VoicePipelineConfig.trace_include_sensitive_audio_data

# 2) trace 单独用一个 OpenAI key（比如“只用来传 trace 的 key”）
from agents import set_tracing_export_api_key
set_tracing_export_api_key("sk-...")
# 只对单次 run：RunConfig(tracing={"api_key": "sk-tracing-123"})
# 模型流量和 trace 用不同 key：set_default_openai_client(client, use_for_tracing=False)

# 3) 完全关掉 / 换自己的处理器
set_tracing_disabled(True)
```

如果要求先脱敏再导出，别用 `add_trace_processor()` 先放一个脱敏处理器、再放导出器。这些处理器是互相独立的观察者，脱敏失败时其他处理器照样收到原始数据。官方建议用 `set_trace_processors()` 替换默认处理器，把脱敏和投递写在同一个 exporter 里：复制数据、脱敏、只把脱敏后的结果交给目的地，失败就丢弃这一批。

长驻 worker 里的 trace 默认每隔几秒批量导出，进程退出时也会 flush。Celery 或 FastAPI 后台任务如果想在任务结束时就确保送达，在 `trace()` 退出之后调用 `flush_traces()`。

非 OpenAI 模型的情况：有 OpenAI key 的话，`set_tracing_export_api_key(os.environ["OPENAI_API_KEY"])` 就能让第三方模型的 trace 也免费进入 OpenAI 的 Traces 看板；没有 key 就关掉 tracing，或者换处理器。

---

## 12. 完整 demo

```python
import asyncio
from agents import Agent, Runner, trace, function_tool


@function_tool
def search(query: str) -> str:
    return f"results for {query}"


writer = Agent(
    name="Writer",
    instructions="把素材写成报告",
    model="gpt-6-luna",
)


researcher = Agent(
    name="Researcher",
    instructions="先 search，再总结",
    tools=[search],
    model="gpt-6-luna",
)


async def pipeline(question: str, user_id: str):
    with trace(
        "Research Pipeline",
        metadata={"user_id": user_id, "env": "demo"},
    ):
        research_result = await Runner.run(researcher, question)
        final_result = await Runner.run(
            writer,
            f"基于研究材料写 200 字报告:\n{research_result.final_output}",
        )
        return final_result.final_output


async def main():
    answer = await pipeline("LLM 推理速度怎么提升", user_id="u1")
    print(answer)
    print("\n去 https://platform.openai.com/traces 看 trace")


asyncio.run(main())
```

---

## 13. 下一步

- 接 LangSmith / Langfuse → [自定义 Tracer：接 LangSmith / Langfuse / Logfire](./02-custom-tracer.md)
- 用 Lifecycle Hooks 做监控 → [Lifecycle Hooks：生命周期钩子](./03-lifecycle-hooks.md)
- 把 trace 数据导出来做评测 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)
