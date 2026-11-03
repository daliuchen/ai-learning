# LangSmith 05：Monitoring 监控 / Dashboards / Alerts / Online Eval

> Tracing 让你看清每一条请求，Monitoring 看的是所有请求汇总起来的趋势：延迟、错误率、token 消耗、用户反馈、自动评估的得分。这一篇讲 Dashboard、告警、在线评估和用户反馈怎么配。

版本：以 `langsmith 0.14.x` 为准。Dashboards、Alerts、Rules 在控制台配置，SDK 里没有对应的创建方法；`create_feedback` / `list_runs` 需要 LangSmith 账号，demo 在没有 key 时会跳过这两步。

---

## 1. 内置 Dashboard

每个 Project 自带这几张图：

- Volume：请求数随时间的变化
- Latency P50/P95/P99：耗时
- Error rate：错误率
- Token / Cost：用量和费用估算
- Feedback：人工评分分布
- Run status：success / error / pending

可以按 `tags`、`metadata`、`run_name` 过滤，比如只看 `metadata.model = gpt-6.1-sol` 的请求。

---

## 2. 自定义 Dashboard

在界面里进入 Dashboards → New Dashboard，可以拖入各种 widget：

- 时间序列
- 表格
- Heatmap
- TopN（比如最贵的 prompt、最慢的用户）

自定义 Dashboard 在控制台里配置，如果要用程序创建，得看官方 REST API 文档。

---

## 3. Alerts 告警

在界面里进入 Alerts → New：

- 触发条件：错误率、延迟（如 P99）、成本、反馈分数等指标超过阈值（具体可选指标以控制台为准）
- 通知方式：Webhook / PagerDuty 等（以控制台为准）

可选的统计窗口和 Webhook payload 的字段以控制台文档为准，配好后先发一条测试告警，确认接收端能解析。

---

## 4. Online Evaluation

Tracing 那篇末尾提到过，evaluator 可以在生产 trace 上自动运行。配置入口是 Project → Rules → New Rule：

- 触发条件：
  - 按 metadata 过滤（如 `feature=rag`）
  - 按抽样比例（如 10%）
  - 按 trace 状态（error 才评）
- 动作：
  - Run evaluator（用某个保存的 evaluator）
  - Create feedback
  - Send to dataset（自动收集 bad case）
  - Webhook

举个例子：从 RAG 的 trace 里抽 10%，用 LLM-as-Judge 评 faithfulness，分数低于 0.5 的自动加进 `bad-cases` 数据集。

---

## 5. User Feedback API

生产应用最好把用户反馈接进来：

```python
# 后端，处理用户点的 👍/👎
@app.post("/feedback")
def feedback(run_id: str, score: int, comment: str = None):
    client.create_feedback(
        run_id=run_id,
        key="user_thumbs",
        score=score,         # 0 / 1
        comment=comment,
        feedback_source_type="api",
    )
```

前端的按钮要带上 `run_id`，最省事的办法是自己用 `config={"run_id": uuid4()}` 生成。传给 `config` 的 `run_id` 要是 `UUID` 对象（`create_feedback` 的 `run_id` 字符串或 UUID 都行）；另外 LangSmith 是异步上传 trace 的，写反馈之前最好先 `client.flush()` 一下。

feedback 会关联到对应的 trace 上，Dashboard 就能按用户、模型、版本画出好评率曲线。

---

## 6. 集成第三方监控

LangSmith 替代不了 Datadog 或 Sentry，它们各管一摊：LangSmith 看 LLM 层面的 trace、prompt 和评估，Datadog / Prometheus 管系统指标和基础设施告警，Sentry 收异常堆栈。要同时用，就在 callback 里把事件分别报给这几个系统。

---

## 7. 数据导出 / Webhook

批量导出可以用 API：

```python
import pandas as pd
runs = client.list_runs(project_name="my-project", start_time=..., end_time=..., is_root=True)
df = pd.DataFrame([r.model_dump() for r in runs])
df.to_parquet("export.parquet")
```

这样可以把 LangSmith 的数据导进内部数仓做长期分析。Webhook 则可以挂在 Rules / Alerts 的动作里，payload 格式以控制台文档为准。

---

## 8. 控制成本与采样

LangSmith 按 trace 量计费，也有额度限制。生产请求量很大时，可以只上传一部分：

```bash
# 全局采样率（0~1）
export LANGSMITH_TRACING_SAMPLING_RATE=0.1   # 只上传 10%
```

或在代码里：

```python
from langsmith import Client
client = Client(tracing_sampling_rate=0.1)
```

SDK 里没有 `LANGSMITH_SAMPLE_RATE` 环境变量，也没有 `LangChainTracer(sample_rate=...)` 参数。如果想要更细的策略，比如出错的请求全部上传、正常请求按比例采样，可以按条件用 `tracing_context(enabled=...)` 包住调用；另外也可以在 Rules 里按条件筛选哪些 trace 触发评估。

---

## 9. demo：发反馈 + 拉聚合统计

完整代码：[`demos/langsmith/05_monitoring.py`](../../demos/langsmith/05_monitoring.py)

```python
# demos/langsmith/05_monitoring.py（节选）
import os, uuid
from datetime import datetime, timedelta, timezone
from langsmith import Client

client = Client()
run_id = uuid.uuid4()
ans = chain.invoke({"q": "LCEL 是什么？"}, config={"run_id": run_id})

# 模拟用户点了 👍
client.create_feedback(run_id=run_id, key="user_thumbs", score=1, comment="useful")

# 拉最近一些 run 看统计
runs = list(client.list_runs(
    project_name=os.getenv("LANGSMITH_PROJECT") or "default",
    start_time=datetime.now(timezone.utc) - timedelta(hours=1),   # 不要用已弃用的 utcnow()
    is_root=True,
    limit=20,
))
print(f"近 1h 根 run 数: {len(runs)}")
print("输入 token =", sum(r.prompt_tokens or 0 for r in runs), "输出 token =", sum(r.completion_tokens or 0 for r in runs))
```

---

## 10. 上线前的检查清单

- [ ] 服务启动时检查 `LANGSMITH_API_KEY` 是否可用
- [ ] 给所有 trace 打 `service=...`, `version=...`, `user_id=...`
- [ ] 配置 `LANGSMITH_TRACING_SAMPLING_RATE` 控制流量
- [ ] 重要错误都以 `error=true` 上传
- [ ] 用户的点赞 / 点踩反馈接入 LangSmith
- [ ] CI 跑 evaluation，分数不达标就拦下
- [ ] Dashboard + Alert：P99 延迟 + 错误率 + 日成本
- [ ] 每周人工抽查一批 bad case，加进 dataset

---

LangSmith 部分到这里结束，接下来进入 LangGraph：

- [LangGraph 01：介绍、定位与其他框架的对比](../03-langgraph/01-introduction.md)
