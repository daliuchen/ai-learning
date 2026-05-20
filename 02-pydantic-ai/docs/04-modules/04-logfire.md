# Pydantic AI 04-04：Logfire 可观测性集成

> **一句话**：Logfire 是 Pydantic 团队出品的可观测性平台（基于 OpenTelemetry），两行代码就能把 Agent 的每一次 run、每一次工具调用、每一次模型请求都打进 dashboard，看 prompt / response / token / 时延 / 失败堆栈一目了然。

---

## 1. 为什么 LLM 应用特别需要 trace

传统服务监控通常关注 QPS / Latency / Error Rate 三件套。LLM 应用多了一堆"非典型问题"：

- **慢**：单次调用 1-10 秒是常态，传统 APM 阈值告警全在响
- **贵**：每次调用按 token 算钱，没监控就被无脑刷爆
- **黑盒**：模型输出非确定，事后没办法"复现"
- **多跳**：一次 user message 后端可能调 3 个 Agent / 8 个工具，链路深
- **改 prompt = 改逻辑**：改了一行 prompt 行为变了你需要回过头看实际 prompt

裸用 OpenAI SDK 你要自己埋点、自己存 prompt、自己算 token、自己捕错。Logfire 在 Pydantic AI 里**一行 `instrument_pydantic_ai()` 就把这一切自动捕获**。

---

## 2. 三十秒接入

```python
import logfire
from pydantic_ai import Agent

logfire.configure()                   # 1) 初始化（读环境变量 LOGFIRE_TOKEN）
logfire.instrument_pydantic_ai()      # 2) 给所有 Agent 自动埋点

agent = Agent("openai:gpt-4o-mini", system_prompt="...")
result = agent.run_sync("hi")
```

跑一次后到 https://logfire.pydantic.dev 就能看到一条 span，点开里面有：

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

**`send_to_logfire=False`** 很关键：本地开发不想上报、或要把数据导到自家 OTel 后端时用。

### 3.1 获取 token

```bash
pip install logfire
logfire auth                # 浏览器登录
logfire projects new        # 在你的 organization 下新建项目
# 拿到 LOGFIRE_TOKEN 写进 .env
```

---

## 4. `instrument_pydantic_ai()` 都装了什么

一行函数实际上在背后干了三件事：

| 自动埋点对象 | span 内容 |
|------------|----------|
| `Agent.run()` / `run_sync()` / `run_stream()` | 完整 prompt / output / 元数据 |
| 模型每一次 HTTP 请求 | 模型名、tokens、latency、cost（按官方价目表估） |
| `@agent.tool` 工具执行 | 函数名、入参、返回值、异常 |

输出是符合 **OpenTelemetry GenAI semantic conventions** 的 attribute 名（`gen_ai.system`、`gen_ai.usage.input_tokens`…），不绑死 Logfire 后端。

### 4.1 控制敏感字段

工具入参 / 模型 prompt 可能含 PII，可以：

```python
from pydantic_ai.agent import InstrumentationSettings

logfire.instrument_pydantic_ai(
    settings=InstrumentationSettings(
        include_content=False,   # 不上报 prompt/response 文本，只留 token/时延
    ),
)
```

`include_content=False` 后你只看得到"调用了模型 X、花了 N token、耗时 M ms"，看不到具体内容，符合很多内部合规要求。

---

## 5. 自定义 span：把业务字段也带上

Logfire 不只是给 LLM 用的——任何业务逻辑都能加 span：

```python
with logfire.span("process_order", order_id=order.id, user_id=u.id) as span:
    parsed = await agent.run(order.text)
    span.set_attribute("parsed_amount", parsed.output.amount)
    await db.save(parsed.output)
```

在 dashboard 里这条 span 会包住底下的 Agent / model / tool 子 span，形成"业务视角"的火焰图。

### 5.1 字段 vs 子 span

| 场景 | 用法 |
|------|------|
| 想给当前 span 加一个 metric | `span.set_attribute(...)` |
| 想表示"一个新的逻辑阶段" | `with logfire.span(...)` 开子 span |
| 想打一行 log（不计入 span） | `logfire.info("msg", k=v)` |

---

## 6. 配合 Hook 把每次 run 的元数据捞出来

Pydantic AI 允许你在 Agent 上挂 hook（`@agent.run_step`、`@agent.tool_call` 等），结合 Logfire 你可以把"用户 id / 业务字段"塞进 span：

```python
@agent.run_step
async def add_user_attr(ctx, step):
    logfire.span_current().set_attribute("user_id", ctx.deps.user_id)
```

dashboard 里搜 `user_id=12345` 就能拉到这个用户所有的 Agent 调用。

---

## 7. dashboard 主要视图

| 视图 | 看什么 |
|------|-------|
| **Live** | 实时 trace 流，开发时狂用 |
| **Explore** | 历史查询，按 attribute 过滤 |
| **Dashboards** | 自定义仪表板：avg latency、p95、token/天、按用户的成本 |
| **Alerts** | 阈值告警（错误率 / 时延 / token spike） |

最有用的几条 SQL（Logfire 用 SQL 查 trace）：

```sql
-- 看哪个 user 最烧钱（top spender）
select user_id, sum(gen_ai.usage.output_tokens) tokens
from records where service_name = 'my-agent'
group by user_id order by tokens desc limit 10;

-- p95 latency
select percentile_cont(0.95) within group (order by duration)
from records where span_name = 'agent run';

-- 失败 trace
select * from records where is_exception group by trace_id;
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

兼容的后端清单（官方提到）：

- Langfuse
- W&B Weave
- Arize
- mlflow
- 自建 OTel Collector → Jaeger / Tempo / Honeycomb

也就是说 **`instrument_pydantic_ai()` 是协议适配**，不是 Logfire 厂商锁定。

---

## 9. 也给 HTTPX / SQLAlchemy / FastAPI 一起埋

Agent 之外的 IO 也要上 trace，才能看清楚"业务全链路"：

```python
logfire.configure()
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()           # OpenAI SDK 走 httpx
logfire.instrument_fastapi(app)
logfire.instrument_sqlalchemy(engine=engine)
```

一条 trace 里就能看到：HTTP 请求 → FastAPI 路由 → Agent run → 模型 HTTP 调用 → 工具里的 DB 查询。这是排查"为什么这次 user 请求 8 秒"的神器。

---

## 10. 实战：给客服 Agent 加 trace

完整代码见 [`demos/modules/04_logfire.py`](../../demos/modules/04_logfire.py)。核心结构：

```python
import logfire
from pydantic_ai import Agent

logfire.configure(service_name="customer-service", send_to_logfire=False)
logfire.instrument_pydantic_ai()

agent = Agent("openai:gpt-4o-mini", system_prompt="...")

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

跑完之后 `console=True` 时 stdout 直接打印 trace 树：

```
customer_request user_id=u_123
└─ agent run prompt="客服 agent"
   ├─ chat completion model=gpt-4o-mini tokens_in=120 tokens_out=18
   ├─ tool: lookup_order order_id=A001
   │  └─ db_lookup order_id=A001
   └─ chat completion model=gpt-4o-mini tokens_in=180 tokens_out=42
done user_id=u_123 output_len=87
```

---

## 11. 何时该上 / 不上

| 场景 | 上 Logfire | 用其他 |
|------|-----------|--------|
| Prod 任何规模的 Agent 服务 | ✅ 必须 | — |
| 一次性脚本、本地写 demo | ✅ `console=True` 直接看 stdout | — |
| 公司有自家 OTel 平台（Jaeger / Honeycomb） | ✅ `send_to_logfire=False` 导出去 | — |
| 数据合规严格、不想出公网 | ✅ self-host OTel collector | — |
| 想看 token / cost 趋势 | ✅ Logfire 内置维度 | — |

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| dashboard 看不到任何数据 | 没设 `LOGFIRE_TOKEN`，或 `send_to_logfire=False` | 检查 token / configure 参数 |
| 看到 trace 但 prompt 是空的 | `include_content=False` | 改回 True（或这就是你想要的） |
| token 飙到天文数字 | 把全量 prompt 都打了进去 | 用 `include_content=False`，或采样 |
| trace 太多导致前端卡 | 没采样 | `logfire.configure(sampling=...)` |
| 看不到 HTTPX 子 span | 没调 `instrument_httpx` | 单独调一下，需要 OpenAI SDK 1.x+ |
| FastAPI 路由 + Agent 没拼在一起 | trace context 没传 | 用 `instrument_fastapi` 让 OTel 接住 |
| 想关掉 stdout 输出 | `console=False` | 别忘 `configure(console=False)` |
| 敏感字段意外上报 | 用 attribute 装 PII | 用 `attribute_scrubbing_patterns` 配 regex 脱敏 |
| 改了 prompt 想对比效果 | 没记 service_version | configure 时填 `service_version`，dashboard 按版本对比 |
| 想给 Graph 也加 trace | `instrument_pydantic_ai()` 已自动覆盖 Agent | Graph 里调 Agent 的部分自动出现，节点之间手动 `logfire.span` |

---

## 13. 成本控制：采样与脱敏

LLM trace 体量很容易爆，简单几招控成本：

### 13.1 按比例采样

```python
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

logfire.configure(
    sampling=TraceIdRatioBased(rate=0.1),   # 只采 10%
)
```

注意：错误一定要全采，所以**实际中常用"全部 error + 10% 正常请求"**的混合策略：

```python
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

logfire.configure(
    sampling=ParentBased(root=TraceIdRatioBased(0.1)),
)
```

### 13.2 字段脱敏

Logfire 内置 scrubbing，能自动按 regex 把疑似 PII 字段（password / token / api_key…）打码：

```python
logfire.configure(
    scrubbing=logfire.ScrubbingOptions(
        extra_patterns=[r"phone", r"id_card"],   # 加项目特定字段
    ),
)
```

### 13.3 关掉模型内容只留指标

最激进的做法——`include_content=False` 后只剩 token / latency / model_name，**zero PII 风险**，但事后没法复现 prompt：

```python
from pydantic_ai.agent import InstrumentationSettings

logfire.instrument_pydantic_ai(
    settings=InstrumentationSettings(include_content=False),
)
```

适合金融、医疗等强合规场景。

---

## 14. 与 Pydantic Evals 联动

之前 [02-evals.md](02-evals.md) 提过，跑 `dataset.evaluate(...)` 时如果开了 Logfire，每条 case 都会生成一条独立 trace。在 dashboard 用 SQL 查：

```sql
-- 按 case 名看每次评测结果
select
  attributes->>'case_name' as case_name,
  attributes->>'output' as output,
  duration / 1e9 as seconds
from records
where span_name = 'evaluation_case'
order by start_timestamp desc;
```

这就把"评测分数 + 详细 trace"打通了——准确率掉了，点开就能看到具体哪条 case 模型说了什么。

---

## 15. 本章 demo

完整可运行代码：[`demos/modules/04_logfire.py`](../../demos/modules/04_logfire.py)

包含：

1. 用 `logfire.configure(console=True, send_to_logfire=False)` 本地跑，不需要 token
2. 给 Agent 装 instrumentation
3. 业务侧加 `logfire.span("customer_request", ...)`
4. 工具里再加子 span
5. 没 API Key 时用 `TestModel`

跑完直接在 stdout 看到漂亮的 trace 树。

下一篇：[05-cli-harness.md](05-cli-harness.md) —— `clai` 命令行工具与 Pydantic AI Harness。
