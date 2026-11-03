# Pydantic AI 04-04：Logfire 可观测性集成

> Logfire 是 Pydantic 团队做的可观测性平台，底层基于 OpenTelemetry。接入只要两行代码，之后 Agent 的每次 run、每次工具调用、每次模型请求都会进到 dashboard，prompt、response、token、时延和失败堆栈都能直接查到。

以 `pydantic-ai 2.64.0` + `logfire 5.4` 为准。V2 去掉了 Agent 的 `instrument=` 参数，改用 `Instrumentation` capability。

---

## 1. 为什么 LLM 应用特别需要 trace

传统服务监控看的通常是 QPS、Latency、Error Rate。LLM 应用还有一些别的问题：

- 慢：单次调用 1-10 秒很常见，按传统 APM 阈值设的告警会一直响
- 贵：按 token 计费，没有监控的话，费用被刷上去了也不知道
- 难复现：模型输出不确定，事后很难重现当时的情况
- 链路深：一条用户消息，后端可能调了 3 个 Agent、8 个工具
- prompt 就是逻辑：改一行 prompt 行为就变了，排查时得回头看当时实际发出去的 prompt

直接用 OpenAI SDK 的话，埋点、存 prompt、算 token、捕获异常都得自己做。在 Pydantic AI 里，调一次 `instrument_pydantic_ai()` 就全部自动记录了。

---

## 2. 三十秒接入

```python
import logfire
from pydantic_ai import Agent

logfire.configure()                   # 1) 初始化（读环境变量 LOGFIRE_TOKEN）
logfire.instrument_pydantic_ai()      # 2) 给所有 Agent 自动埋点

agent = Agent("openai-chat:qwen-flash", instructions="...")
result = agent.run_sync("hi")
```

运行之后，到 https://logfire.pydantic.dev 能看到一条 span，点开有这些内容：

- 完整 system prompt + user message
- 模型输出（text & tool_calls）
- 每轮 LLM 调用的 input/output tokens、cost
- 工具调用的入参 / 出参 / 耗时
- 如果失败：完整异常堆栈

---

## 3. 初始化 `logfire.configure()`

最常用参数：

```python
logfire.configure(
    service_name="my-agent",         # 服务名（多服务区分）
    service_version="0.3.1",         # 版本（关联 release）
    environment="prod",              # dev/staging/prod
    send_to_logfire=True,            # False 就只本地打，不上报
    console=False,                   # 关掉 stdout 的 trace 输出
    token="lf_xxx",                  # 一般通过 LOGFIRE_TOKEN 环境变量
)
```

`send_to_logfire=False` 用得很多：本地开发不想上报，或者要把数据导到自己的 OTel 后端时，都靠它。不传时默认是 `"if-token-present"`：配了 `LOGFIRE_TOKEN` 才上报，没配就只把 trace 树打印到 stdout，模型请求（如 `chat qwen-flash`）、工具调用（`running tool: ...`）和自定义 span 都会出现在树里。

### 3.1 获取 token

```bash
pip install logfire
logfire auth                # 浏览器登录
logfire projects new        # 在你的 organization 下新建项目
# 拿到 LOGFIRE_TOKEN 写进 .env
```

---

## 4. `instrument_pydantic_ai()` 都装了什么

这个函数在背后给三类对象加了埋点：

| 自动埋点对象 | span 内容 |
|------------|----------|
| `Agent.run()` / `run_sync()` / `run_stream()` | 完整 prompt / output / 元数据 |
| 模型每一次 HTTP 请求 | 模型名、tokens、latency、cost（按官方价目表估） |
| `@agent.tool` 工具执行 | 函数名、入参、返回值、异常 |

attribute 名遵循 OpenTelemetry GenAI semantic conventions（`gen_ai.system`、`gen_ai.usage.input_tokens`…），不和 Logfire 后端绑定。

### 4.0 三种打开方式

```python
# A. 全局：给所有 Agent 打开（最常用）
logfire.instrument_pydantic_ai()

# B. 单个 Agent：Instrumentation capability（V2）
from pydantic_ai.capabilities import Instrumentation
agent = Agent("openai:gpt-6.1-sol", capabilities=[Instrumentation()])

# C. 不用 logfire 库，纯 OTel：给全局 TracerProvider 之后
from pydantic_ai import Agent, InstrumentationSettings
Agent.instrument_all(InstrumentationSettings())
```

`Instrumentation(settings=InstrumentationSettings(...))` 可以定制 provider、内容采集和数据格式版本（`version=`，和 Pydantic AI 的包版本无关：默认 5，可选 6；2/3/4 是弃用的兼容格式。版本 3 起 span 名遵循 OpenTelemetry GenAI 语义约定，比如 agent 的 span 叫 `invoke_agent {agent 名}`、工具的叫 `execute_tool {工具名}`）。其它 capability 可以通过 `opentelemetry.trace.get_current_span().set_attribute(...)` 往这些 span 上加属性。`InstrumentationSettings` 直接从 `pydantic_ai` 导入；V2 的 Agent 没有 `instrument=` 构造参数，单个 Agent 开 trace 用上面的 B 写法。

### 4.1 控制敏感字段

工具入参和模型 prompt 里可能有 PII，可以这样关掉内容采集：

```python
from pydantic_ai import InstrumentationSettings

logfire.instrument_pydantic_ai(
    settings=InstrumentationSettings(
        include_content=False,   # 不上报 prompt/response 文本，只留 token/时延
    ),
)
```

设了 `include_content=False` 之后，你只能看到"调用了模型 X、花了 N token、耗时 M ms"，看不到具体内容（prompt、模型回复、工具参数和返回值都不记），这能满足不少内部合规要求。另有 `include_binary_content=False`（只去掉图片 / 音频等二进制内容）和排除模型请求参数的选项，见官方 *Advanced usage*。

---

## 5. 自定义 span：把业务字段也带上

Logfire 不限于 LLM，任何业务逻辑都能加 span：

```python
with logfire.span("process_order", order_id=order.id, user_id=u.id) as span:
    parsed = await agent.run(order.text)
    span.set_attribute("parsed_amount", parsed.output.amount)
    await db.save(parsed.output)
```

在 dashboard 里，这条 span 会包住下面的 Agent、model、tool 子 span，看到的就是按业务划分的火焰图。

### 5.1 字段 vs 子 span

| 场景 | 用法 |
|------|------|
| 想给当前 span 加一个 metric | `span.set_attribute(...)` |
| 想表示"一个新的逻辑阶段" | `with logfire.span(...)` 开子 span |
| 想打一行 log（不计入 span） | `logfire.info("msg", k=v)` |

---

## 6. 把每次 run 的业务元数据带进 span

两种办法：

```python
# A. 运行元数据：Agent(metadata=...) 或 run(metadata=...)，开着 instrumentation 时会写进 run span 的属性
result = await agent.run(msg, metadata={"tenant_id": "t-42", "user_id": ctx_user})
print(result.metadata)

# B. 在 hook / 工具里直接设当前 span 的属性
from pydantic_ai.capabilities import Hooks
hooks = Hooks()

@hooks.on.before_run
async def tag(ctx):
    logfire.span_current().set_attribute("user_id", ctx.deps.user_id)   # 或 opentelemetry 的 get_current_span()

agent = Agent("openai-chat:qwen-flash", deps_type=Deps, capabilities=[hooks])
```

（`@agent.run_step` / `@agent.tool_call` 这样的装饰器在 2.x 里不存在，对应能力是 04 篇的 `Hooks`。）dashboard 里搜 `user_id=12345` 就能拉到这个用户所有的 Agent 调用。

---

## 7. dashboard 主要视图

| 视图 | 看什么 |
|------|-------|
| Live | 实时 trace 流，开发时最常用 |
| Explore | 历史查询，按 attribute 过滤 |
| Dashboards | 自定义仪表板：avg latency、p95、token/天、按用户的成本 |
| Alerts | 阈值告警（错误率 / 时延 / token 突增） |

Logfire 用 SQL 查 trace，下面几条我觉得最常用：

```sql
-- 看哪个 user 输出 token 最多（user_id 是第 6 节写进 span 的属性）
select attributes->>'user_id' as user_id,
       sum((attributes->>'gen_ai.usage.output_tokens')::bigint) as tokens
from records where service_name = 'my-agent'
group by 1 order by tokens desc limit 10;

-- agent run 的 p95 时延（默认数据格式下 span 名是 invoke_agent <agent 名>）
select approx_percentile_cont(duration, 0.95)
from records where span_name like 'invoke_agent%';

-- 有异常的 trace
select trace_id, count(*) as n from records where is_exception group by trace_id;
```

---

## 8. 不用 Logfire 也能用：纯 OpenTelemetry

Pydantic AI 的 instrumentation 用的是 OTel SDK，所以可以把数据导到任何 OTel 后端：

```python
logfire.configure(send_to_logfire=False)
# 然后用 OTel 标准方式配置 exporter
import os
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
```

官方提到的兼容后端有：

- Langfuse
- W&B Weave
- Arize
- mlflow
- 自建 OTel Collector → Jaeger / Tempo / Honeycomb

所以 `instrument_pydantic_ai()` 做的是协议层的适配，用它并不会被 Logfire 锁定。

---

## 9. 也给 HTTPX / SQLAlchemy / FastAPI 一起埋

Agent 之外的 IO 也加上 trace，才能看到完整的业务链路：

```python
logfire.configure()
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()           # 只覆盖 httpx；openai 3.x 默认走 httpx2，见下方常见坑
logfire.instrument_fastapi(app)
logfire.instrument_sqlalchemy(engine=engine)
```

这样一条 trace 里就能看到：HTTP 请求 → FastAPI 路由 → Agent run → 模型 HTTP 调用 → 工具里的 DB 查询。排查"这个请求为什么花了 8 秒"时特别好用。

---

## 10. 实战：给客服 Agent 加 trace

完整代码见 [`demos/modules/04_logfire.py`](../../demos/modules/04_logfire.py)，主要结构如下：

```python
import logfire
from pydantic_ai import Agent

logfire.configure(service_name="customer-service", send_to_logfire=False)
logfire.instrument_pydantic_ai()

agent = Agent("openai-chat:qwen-flash", instructions="...")

@agent.tool_plain
def lookup_order(order_id: str) -> dict:
    with logfire.span("db_lookup", order_id=order_id):
        return {"order_id": order_id, "status": "shipped"}

async def handle(user_id: str, msg: str):
    with logfire.span("customer_request", user_id=user_id):
        r = await agent.run(msg)
        logfire.info("done", user_id=user_id, output_len=len(r.output))
        return r.output
```

`console=True` 时，跑完后 stdout 会直接打印 trace 树，结构大致如下（示意，具体的 span 名和属性随 instrumentation 数据格式版本而变）：

```
customer_request user_id=u_123
└─ invoke_agent agent
   ├─ chat qwen-flash
   ├─ running tool: lookup_order
   │  └─ db_lookup order_id=A001
   └─ chat qwen-flash
done user_id=u_123 output_len=87
```

---

## 11. 何时该上 / 不上

| 场景 | 上 Logfire | 用其他 |
|------|-----------|--------|
| 生产环境的 Agent 服务，不论规模 | 应该上 | — |
| 一次性脚本、本地写 demo | 可以，`console=True` 直接看 stdout | — |
| 公司有自己的 OTel 平台（Jaeger / Honeycomb） | 可以，`send_to_logfire=False` 导出去 | — |
| 数据合规严格、不想出公网 | 可以，自建 OTel collector | — |
| 想看 token / cost 趋势 | 可以，Logfire 内置这些维度 | — |

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| dashboard 看不到任何数据 | 没设 `LOGFIRE_TOKEN`，或 `send_to_logfire=False` | 检查 token / configure 参数 |
| 看到 trace 但 prompt 是空的 | `include_content=False` | 改回 True（或这就是你想要的） |
| trace 数据量暴涨 | 把全量 prompt 都记了进去 | 用 `include_content=False`，或采样 |
| trace 太多导致前端卡 | 没采样 | `logfire.configure(sampling=...)` |
| 看不到 HTTP 子 span | 没给 HTTP 客户端装 instrumentation；而且 `openai` 3.x 和 Pydantic AI 2.x 默认用的是 `httpx2`，不是 `httpx` | 按你实际用的客户端装对应的 instrumentation，装之前先确认 logfire 版本是否支持 httpx2 |
| FastAPI 路由 + Agent 没拼在一起 | trace context 没传 | 用 `instrument_fastapi` 让 OTel 接住 |
| 想关掉 stdout 输出 | 默认会打印到 console | `configure(console=False)` |
| 敏感字段意外上报 | 用 attribute 装 PII | 用 `attribute_scrubbing_patterns` 配 regex 脱敏 |
| 改了 prompt 想对比效果 | 没记 service_version | configure 时填 `service_version`，dashboard 按版本对比 |
| 第一次运行时打印一个 ASCII 图案横幅 | Pydantic AI 的启动横幅 | 设环境变量 `PYDANTIC_AI_NO_BANNER=1` |
| 想给 Graph 也加 trace | `instrument_pydantic_ai()` 已自动覆盖 Agent | Graph 里调 Agent 的部分自动出现，节点之间手动 `logfire.span` |

---

## 13. 成本控制：采样与脱敏

LLM 的 trace 数据量很容易膨胀，下面几个办法可以控制成本。

### 13.1 按比例采样

```python
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

logfire.configure(
    sampling=logfire.SamplingOptions(head=0.1),   # 头部采样 10%（也可以传 OTel 的 Sampler）
)
```

错误请求最好全部保留，所以实际中常用"全部 error + 10% 正常请求"的混合策略，Logfire 的尾部采样可以做到：先暂存 trace，出错或超时的才留下。采样 API 随 logfire 版本变化，动手前先读一下 Logfire 的 *Sampling* 文档，以你装的版本为准。

### 13.2 字段脱敏

Logfire 自带 scrubbing，会按 regex 把疑似 PII 的字段（password / token / api_key…）打码，也可以加上项目自己的字段：

```python
logfire.configure(
    scrubbing=logfire.ScrubbingOptions(
        extra_patterns=[r"phone", r"id_card"],   # 加项目特定字段
    ),
)
```

### 13.3 关掉模型内容只留指标

最彻底的做法是 `include_content=False`，只留下 token、latency、model_name。这样不会有 PII 泄露的风险，但事后也没法看到当时的 prompt：

```python
from pydantic_ai import InstrumentationSettings

logfire.instrument_pydantic_ai(
    settings=InstrumentationSettings(include_content=False),
)
```

适合金融、医疗等强合规场景。

---

## 14. 与 Pydantic Evals 联动

[Pydantic Evals 评测框架](02-evals.md)里提过，跑 `dataset.evaluate(...)` 时如果开着 Logfire，每条 case 都会生成一条独立的 trace，可以在 dashboard 里用 SQL 查：

```sql
-- 按 case 名看每次评测结果
select
  attributes->>'case_name' as case_name,
  attributes->>'output' as output,
  duration as seconds
from records
where span_name = 'case: {case_name}'
order by start_timestamp desc;
```

评测分数和 trace 这样就连起来了：准确率掉了，点开就能看到是哪条 case、模型具体说了什么。

---

## 15. 本章 demo

完整可运行代码：[`demos/modules/04_logfire.py`](../../demos/modules/04_logfire.py)

包含：

1. 用 `logfire.configure(console=True, send_to_logfire=False)` 本地跑，不需要 token
2. 给 Agent 装 instrumentation
3. 业务侧加 `logfire.span("customer_request", ...)`
4. 工具里再加子 span
5. 没 API Key 时用 `TestModel`

跑完在 stdout 就能看到 trace 树。

下一篇：[clai 命令行与 Harness 工具集](05-cli-harness.md)。
