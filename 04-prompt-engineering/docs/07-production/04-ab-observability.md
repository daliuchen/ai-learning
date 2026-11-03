# PE Production 04：A/B 与可观测

> prompt 上线以后，不能只靠"应该没问题"。至少要有三样东西：trace，记录每次调用的完整输入输出；A/B，让新版本和线上版本在真实流量上比较；漂移告警，指标突然变差时能第一时间知道。这篇介绍三套常用工具和需要盯的指标。

---

## 1. 要盯的指标

| 类别 | 指标 |
|------|------|
| 质量 | 通过率（有 ground truth 的话）、用户满意度、差评率 |
| 性能 | p50 / p95 / p99 延迟、token 用量 |
| 成本 | 单次调用成本、月度总成本、缓存命中率 |
| 可靠性 | 错误率、超时率、重试率 |
| 业务 | 转化率、停留时长、留存（视业务而定） |

每个上线的 prompt，这五类指标都应该有监控。

---

## 2. Trace：每次调用的完整记录

存到日志、数据库或可观测平台：

```python
def log_trace(call_id, prompt_version, model, input, output, **meta):
    trace_record = {
        "id": call_id,
        "ts": time.time(),
        "prompt_version": prompt_version,
        "model": model,
        "input_text": input,
        "output_text": output,
        "input_tokens": meta.get("input_tokens"),
        "output_tokens": meta.get("output_tokens"),
        "cached_tokens": meta.get("cached_tokens"),
        "latency_ms": meta.get("latency_ms"),
        "user_id": meta.get("user_id"),
        "metadata": meta,
    }
    db.insert(trace_record)
```

可选的工具：

- LangSmith：LangChain 生态，自动 trace
- Langfuse：开源，基于 OTel，SDK 5.x，同样带 prompt 管理和评测
- Logfire：Pydantic 出品，`logfire.instrument_openai()` / `instrument_anthropic()` / `instrument_google_genai()` / `instrument_pydantic_ai()` 一行接入
- 自建：ELK / Loki / BigQuery

---

## 3. A/B 测试

先把 5-10% 的流量分给新版本：

```python
def get_prompt_version(user_id: str) -> str:
    # 用 hash 稳定分流
    h = hashlib.md5(user_id.encode()).hexdigest()
    bucket = int(h, 16) % 100
    if bucket < 5:    # 5% 走新版本
        return "v2.0.0"
    return "v1.5.0"


def classify(user_input: str, user_id: str):
    version = get_prompt_version(user_id)
    prompt = load_prompt(version)
    result = run(prompt, user_input)
    log_trace(... version=version ...)
    return result
```

分析时按版本分组对比：

```sql
SELECT 
  prompt_version,
  AVG(latency_ms),
  COUNT(*) FILTER (WHERE user_satisfied=true) * 1.0 / COUNT(*) AS satisfaction_rate
FROM traces
WHERE to_timestamp(ts) > NOW() - INTERVAL '7 days'   -- ts 存的是 time.time() 的秒数
GROUP BY prompt_version;
```

---

## 4. 漂移告警

线上 prompt 的效果可能在没人改动的情况下慢慢变差，原因可能是上游数据分布变了、模型被下线、外部依赖有变化。所以要配告警：

```python
# 实时检测
if metric.error_rate_5min > metric.error_rate_baseline * 2:
    alert("Error rate spike!")

if metric.p95_latency > 5000:
    alert("Latency spike!")

if metric.daily_cost > budget_daily * 1.3:
    alert("Cost overshoot!")
```

工具可以用 Datadog、Grafana，或者自建。

---

## 5. 持续评测：线上抽样标注

每周从线上调用里抽 50-100 条做人工标注，加进评测集，再跑一轮回归：

```python
def weekly_eval_sample():
    samples = db.fetch_random_traces(n=100, days=7)
    # 输出供人工标注
    export_to_label_studio(samples)
```

标完以后，对比线上模型的判断和人工标注的结果。

---

## 6. LangSmith 完整流程

```python
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_PROJECT"] = "my-classifier"

# 调用自动 trace（LangChain 生态）
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([("system", "..."), ("user", "{input}")])
chain = prompt | ChatOpenAI()

result = chain.invoke({"input": "..."})
# 自动 trace 进 LangSmith UI
```

在 LangSmith 界面里可以逐条看 trace、做 A/B 对比、把标注过的样本加进评测集、自动跑 evaluator。

---

## 7. Langfuse（开源）

```python
from langfuse import observe, get_client   # SDK 3+：旧的 langfuse.decorators 已移除

@observe()                                  # 函数入参/返回值自动成为 trace
def classify(user_input):
    # ... 你的逻辑
    return result

# 需要环境变量 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST
classify("...")
get_client().flush()    # 短脚本退出前 flush
```

OpenAI 的调用可以改成 `from langfuse.openai import OpenAI`，自动记录。自部署和云服务都支持。Langfuse 也有 Prompt Management（`get_client().get_prompt("name", label="production")`，label 就是上线别名）。

---

## 8. Logfire（和 Pydantic AI 配合好）

```python
import logfire
logfire.configure()
logfire.instrument_openai()        # 自动记录 OpenAI 调用（Anthropic / Gemini 同理有 instrument_*）

with logfire.span("classify", input=user_input):
    result = call_llm(...)
    logfire.info("classification done", category=result["category"])
```

界面上能展开完整的 trace 树。

---

## 9. 实战 demo：分桶 A/B

```python
import hashlib
import time
import anthropic

client = anthropic.Anthropic()


PROMPTS = {
    "v1.0.0": "你是简洁的分类器。返回 bug/feature/complaint/praise/other。",
    "v1.1.0": """你是分类器。
- bug: 软件错误
- feature: 功能请求  
- complaint: 抱怨
- praise: 好评
- other: 其他

只返回类别名。
""",
}


def get_version(user_id: str) -> str:
    h = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
    return "v1.1.0" if h % 100 < 10 else "v1.0.0"


def classify(user_input: str, user_id: str):
    version = get_version(user_id)
    prompt = PROMPTS[version]
    t0 = time.time()
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": user_input}],
    )
    latency = (time.time() - t0) * 1000
    return {
        "category": resp.content[-1].text.strip(),
        "version": version,
        "latency_ms": latency,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }


if __name__ == "__main__":
    USERS = [f"user_{i}" for i in range(20)]
    TEXTS = ["App 闪退", "希望加深色模式", "客服真差"]
    
    by_version = {}
    for u in USERS:
        for t in TEXTS:
            result = classify(t, u)
            by_version.setdefault(result["version"], []).append(result)
    
    for v, results in by_version.items():
        avg_lat = sum(r["latency_ms"] for r in results) / len(results)
        avg_tok = sum(r["input_tokens"] for r in results) / len(results)
        print(f"{v}: count={len(results)}, avg_latency={avg_lat:.0f}ms, avg_input_tok={avg_tok:.0f}")
```

---

## 10. 监控面板放什么

| 面板项 | 显示 |
|---------|------|
| 请求量 | 每分钟 QPS，按版本分组 |
| 延迟分布 | p50 / p95 / p99 随时间变化 |
| 成本 | 每日成本趋势 |
| 错误率 | 总错误 / 解析错误 / API 错误 |
| 缓存命中率 | 实时 |
| token 用量 | 每日 input / output tokens |
| 失败模式 | 最常见的错误原因 |

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 上线了却不记 trace | 出了问题只能对着黑盒猜 |
| 分流用的 user_id 不稳定 | 同一个用户在 A/B 两组之间来回切 |
| A/B 流量太少 | 样本不够，差异在统计上不显著；需要多少样本取决于想检测的差距大小，上线前先估算 |
| A/B 太早下结论 | 至少跑一周，把周期性因素包进去 |
| 没有漂移告警 | 效果变差了也没人发现 |
| trace 保留时间太短 | 至少 7-30 天 |
| trace 里有敏感数据 | 先脱敏或加密 |

---

## 12. 下一步

- [Prompt 的团队协作与 Review 流程](./05-team-collab.md)
- 基础回顾：[先建评测，再写 Prompt](../01-foundations/05-eval-first.md)
- LangSmith 的完整用法见另一本手册：`01-langchain/docs/02-langsmith/`

## 参考资料

- LangSmith: https://docs.smith.langchain.com
- Langfuse: https://langfuse.com
- Logfire: https://logfire.pydantic.dev
