# 监控：召回率 / Latency / Cost

> **一句话**：生产 RAG 必须监控 **检索质量（召回率 / hit rate）+ 延迟（P50/P95/P99）+ 成本（每天 cost）+ 异常（错误率 / 限流）**——出问题 5 分钟告警比客户投诉早。

---

## 1. 关键指标矩阵

| 维度 | 指标 | 告警阈值（示例） |
|------|------|----------------|
| **质量** | 用户 thumbs up rate | < 70% |
| **质量** | LLM-as-judge faithfulness（抽样） | < 0.85 |
| **召回** | 离线 evalset Recall@5 | < 0.85 |
| **延迟** | embedding service P95 | > 100ms |
| **延迟** | 端到端 RAG P95 | > 3s |
| **成本** | 日 embedding cost | > 预算 1.5x |
| **错误** | embed API 错误率 | > 1% |
| **限流** | rate limit hit | > 0 |
| **缓存** | hit rate | < 50% |

---

## 2. 用 Prometheus + Grafana

### 代码层 instrument

```python
from prometheus_client import Counter, Histogram, Gauge


# 计数
embed_calls = Counter("embed_calls_total", "Embed API calls", ["provider", "model"])
embed_errors = Counter("embed_errors_total", "Embed errors", ["provider", "type"])

# 延迟
embed_latency = Histogram("embed_latency_seconds", "Embed call latency",
                           ["provider"], buckets=[0.01, 0.05, 0.1, 0.5, 1, 5])

retrieval_latency = Histogram("retrieval_latency_seconds", "Vector search latency",
                               buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5])

# 缓存
cache_hits = Counter("cache_hits_total", "Cache hits", ["layer"])
cache_misses = Counter("cache_misses_total", "Cache misses", ["layer"])

# 实时质量
quality_score = Gauge("rag_quality_score", "Recent quality score")


# 包一层
async def embed_instrumented(text, provider="openai"):
    embed_calls.labels(provider=provider, model="3-small").inc()
    try:
        with embed_latency.labels(provider=provider).time():
            return await embed_actual(text)
    except Exception as e:
        embed_errors.labels(provider=provider, type=type(e).__name__).inc()
        raise
```

### 暴露 /metrics

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

---

## 3. 用 Logfire（更简单）

```python
import logfire


logfire.configure()


@logfire.instrument("rag_search")
async def rag_search(query):
    with logfire.span("embed"):
        q_vec = await embed(query)
    
    with logfire.span("retrieve"):
        hits = await vector_db.search(q_vec)
    
    with logfire.span("rerank"):
        ranked = await rerank(query, hits)
    
    with logfire.span("llm_generate"):
        answer = await llm(query, ranked)
    
    return answer
```

Logfire 自动建 dashboard，看 trace、延迟、错误。

---

## 4. 日志结构化

每次 RAG 调用记一条结构化日志：

```python
import structlog


log = structlog.get_logger()


async def rag_search(query, user_id):
    t_start = time.time()
    
    q_vec = await embed(query)
    hits = await retrieve(q_vec)
    answer = await llm(query, hits)
    
    log.info(
        "rag_search",
        user_id=user_id,
        query=query,
        retrieved_count=len(hits),
        retrieved_doc_ids=[h.id for h in hits],
        latency_ms=int((time.time() - t_start) * 1000),
        cost_usd=estimate_cost(query, hits),
    )
    
    return answer
```

落到 ELK / Datadog / ClickHouse。

---

## 5. Dashboard 必备图

1. **QPS** 时间序列
2. **延迟 P50/P95/P99** 时间序列
3. **错误率** 时间序列
4. **每日 cost** 累积图
5. **缓存 hit rate** 时间序列
6. **召回率 Recall@5** （定期跑 evalset 后上报）
7. **Top 慢查询** 表
8. **Top 高成本 user / tenant** 表

---

## 6. 在线"健康检查"

定期跑一组小 evalset 看系统活着：

```python
HEALTH_CHECK_QUERIES = [
    ("如何取消订阅", "kb_cancel"),
    ("退款政策", "kb_refund"),
    # 10-20 条
]


async def health_check():
    """定时跑：5 分钟一次"""
    hits = 0
    for query, expected_doc in HEALTH_CHECK_QUERIES:
        results = await retrieve(query, top_k=5)
        if expected_doc in [r.id for r in results]:
            hits += 1
    
    recall = hits / len(HEALTH_CHECK_QUERIES)
    quality_score.set(recall)
    
    if recall < 0.7:
        await alert(f"RAG health degraded: recall {recall:.2%}")
```

---

## 7. 异常告警

```python
# 1. embed API 限流
if embed_errors.labels(provider="openai", type="RateLimitError")._value > 10:
    alert("OpenAI embed 频繁限流")


# 2. 延迟突然变高
# Grafana alert: P95 latency > threshold for 5 min


# 3. 成本异常
# 当日 cost > 平日 2x → 检查是否被攻击 / bug


# 4. 召回率掉
# health_check 输出 < 阈值 → alert
```

---

## 8. 用户感知质量

```python
@app.post("/feedback")
async def feedback(req):
    await db.insert("rag_feedback", {
        "trace_id": req.trace_id,
        "rating": req.rating,    # 1 / -1
        "comment": req.comment,
        "user_id": req.user_id,
        "created_at": now(),
    })
    
    # 实时算 thumbs up rate
    recent = await db.query("SELECT rating FROM rag_feedback WHERE created_at > NOW() - INTERVAL '1 hour'")
    rate = sum(1 for r in recent if r == 1) / len(recent) if recent else 0
    
    user_satisfaction.set(rate)
    
    if rate < 0.5:
        await alert(f"User satisfaction dropped: {rate:.0%}")
```

---

## 9. 成本监控细化

```python
# 拆开看
COST_RATES = {
    "openai_embed_3small": 0.02 / 1_000_000,    # per token
    "openai_embed_3large": 0.13 / 1_000_000,
    "cohere_embed_v3": 0.10 / 1_000_000,
    "cohere_rerank_v3": 2 / 1000,                # per call
    "openai_gpt4o_mini_in": 0.15 / 1_000_000,
    "openai_gpt4o_mini_out": 0.6 / 1_000_000,
}


def log_cost(operation, model, tokens):
    cost = COST_RATES.get(f"{model}_{operation}", 0) * tokens
    daily_cost.labels(model=model, operation=operation).inc(cost)
```

Grafana 看：

- 按 model 拆的成本
- 按操作（embed / rerank / generate）的成本
- 按 tenant / user 的成本（找烧钱大户）

---

## 10. 完整 demo: 监控装备

```python
# demos/production/05_monitoring.py
import time
import asyncio
from prometheus_client import Counter, Histogram, Gauge, start_http_server


# Metrics
embed_calls = Counter("embed_calls_total", "", ["provider"])
embed_latency = Histogram("embed_latency_seconds", "", ["provider"])
recall_gauge = Gauge("rag_recall_at_5", "")


async def embed(text, provider="openai"):
    embed_calls.labels(provider=provider).inc()
    with embed_latency.labels(provider=provider).time():
        await asyncio.sleep(0.1)  # 模拟
        return [0.1] * 1536


async def health_check_loop():
    """定期跑 health check"""
    while True:
        # 简化版：模拟 recall
        recall = 0.85
        recall_gauge.set(recall)
        if recall < 0.7:
            print(f"⚠️  Recall dropped: {recall}")
        await asyncio.sleep(60 * 5)


async def main():
    # 起 /metrics endpoint
    start_http_server(9090)
    
    # health check 后台跑
    asyncio.create_task(health_check_loop())
    
    # 模拟流量
    for i in range(100):
        await embed(f"query {i}")
    
    print("Visit http://localhost:9090/metrics")
    await asyncio.sleep(60)


asyncio.run(main())
```

---

## 11. 警报渠道

- **Slack / 飞书 / 钉钉**：日常 alert
- **PagerDuty / Opsgenie**：on-call 紧急
- **Email**：日报 / 周报

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| 只看 avg 不看 P95 | P99 才反映真实坏 case |
| 没记 trace_id | debug 时找不到具体一次 call |
| 没拆 tenant 看 | 一个 tenant 烧钱拖垮全站 |
| Health check 太轻 | 多覆盖几种 query 类型 |

---

## 13. 07-production 章节小结

完整生产化清单：

- [x] 增量索引
- [x] 批量 embed + cost 优化
- [x] 3 层缓存
- [x] 部署形态（TEI / 独立服务）
- [x] 监控（质量 / 延迟 / 成本）

---

## 14. 下一步

- 📖 完整 RAG 项目 → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
- 📖 多模态搜索 → [08-applications/03-multimodal.md](../08-applications/03-multimodal.md)
- 📖 推荐系统 → [08-applications/04-recommendation.md](../08-applications/04-recommendation.md)
