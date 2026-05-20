# PE Production 04：A/B 与可观测

> **一句话**：线上跑 prompt 不能"信任不验证"——必须有 trace（每次调用的完整记录）、A/B（新旧版本对比）、漂移告警（性能突然下降）。本篇讲三种工具栈 + 监控指标。

---

## 1. 关键指标

线上 prompt 系统的核心指标：

| 类别 | 指标 |
|------|------|
| **质量** | 通过率（如果有 ground truth）、用户满意度、负面 feedback 率 |
| **性能** | p50 / p95 / p99 延迟、token 使用量 |
| **成本** | 每次调用 cost、每月总 cost、cache 命中率 |
| **可靠性** | 错误率、超时率、retry 率 |
| **业务** | 转化率、用户停留、retention（如适用） |

每个上线的 prompt 都该监控这五类。

---

## 2. Trace（每次调用的完整记录）

存到日志 / DB / 可观测平台：

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

工具：
- **LangSmith** 自动 trace（LangChain 生态）
- **Langfuse** 开源
- **Logfire** Pydantic 出品
- **自建** ELK / Loki / BigQuery

---

## 3. A/B 测试

把 5-10% 流量分给新版本：

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

分析时按 version 分组对比：

```sql
SELECT 
  prompt_version,
  AVG(latency_ms),
  COUNT(*) FILTER (WHERE user_satisfied=true) * 1.0 / COUNT(*) AS satisfaction_rate
FROM traces
WHERE ts > NOW() - INTERVAL '7 days'
GROUP BY prompt_version;
```

---

## 4. 漂移告警

线上 prompt 性能可能"安静地变差"——上游数据分布漂、模型 deprecate、外部依赖变化。建告警：

```python
# 实时检测
if metric.error_rate_5min > metric.error_rate_baseline * 2:
    alert("Error rate spike!")

if metric.p95_latency > 5000:
    alert("Latency spike!")

if metric.daily_cost > budget_daily * 1.3:
    alert("Cost overshoot!")
```

工具：Datadog / Grafana / 自建。

---

## 5. 持续评测：线上抽样标注

每周抽 50-100 条线上调用做人工标注 → 加进 evalset → 跑回归：

```python
def weekly_eval_sample():
    samples = db.fetch_random_traces(n=100, days=7)
    # 输出供人工标注
    export_to_label_studio(samples)
```

label 完后比较"线上模型决定 vs 人工 ground truth"。

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

LangSmith UI：

- 看每次 trace
- A/B 对比
- 标注 → 加 evalset
- 自动 evaluator 跑

---

## 7. Langfuse（开源）

```python
from langfuse.decorators import observe

@observe()
def classify(user_input):
    # ... 你的逻辑
    return result

# Langfuse 自动 trace
```

Self-host 或 cloud 都可以。

---

## 8. Logfire（Pydantic AI 友好）

```python
import logfire
logfire.configure()

with logfire.span("classify", input=user_input):
    result = call_llm(...)
    logfire.info("classification done", category=result["category"])
```

UI 里看完整 trace tree。

---

## 9. 实战 demo：分桶 A/B

```python
# demos/production/04_ab_test.py
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
        model="claude-haiku-4-5-20251001",
        max_tokens=30,
        system=prompt,
        messages=[{"role": "user", "content": user_input}],
    )
    latency = (time.time() - t0) * 1000
    return {
        "category": resp.content[0].text.strip(),
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

## 10. 监控仪表盘要素

| 仪表盘项 | 显示 |
|---------|------|
| **请求总量** | 每分钟 QPS，按 version 分组 |
| **延迟分布** | p50 / p95 / p99 over time |
| **成本** | 每天 cost trend |
| **错误率** | 总错误 / parse 错误 / API 错误 |
| **Cache 命中率** | 实时 |
| **Token usage** | input / output tokens / day |
| **失败模式** | top error reasons |

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **不 trace 上线** | 出问题黑盒 |
| **没 user_id stable** | 用户在 A/B 里来回切换 |
| **A/B 流量太少** | < 1000 样本统计不显著 |
| **A/B 太快下结论** | 至少跑一周看周期性 |
| **没漂移告警** | 安静变差 |
| **trace 保留时间太短** | 7-30 天起 |
| **trace 含敏感数据** | 脱敏 / 加密 |

---

## 12. 下一步

- 📖 团队协作 → [05-team-collab.md](./05-team-collab.md)
- 📖 评测先于 prompt（基础） → [01-foundations/05-eval-first.md](../01-foundations/05-eval-first.md)
- 📖 跨手册：LangSmith → ../../../01-langchain/docs/02-langsmith/

## 参考资料

- LangSmith: https://docs.smith.langchain.com
- Langfuse: https://langfuse.com
- Logfire: https://logfire.pydantic.dev
