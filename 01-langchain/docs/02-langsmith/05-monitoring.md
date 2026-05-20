# LangSmith 05：Monitoring 监控 / Dashboards / Alerts / Online Eval

> **一句话**：Tracing 让你看见每一条请求；Monitoring 让你看见**所有请求的聚合趋势**——延迟、错误率、token 消耗、用户反馈、自动 Eval 得分。

---

## 1. 内置 Dashboard

每个 Project 自带几个图：

- **Volume**：请求数随时间
- **Latency P50/P95/P99**：耗时
- **Error rate**：错误率
- **Token / Cost**：用量与费用估算
- **Feedback**：人工评分分布
- **Run status**：success / error / pending

可以按 `tags` / `metadata` / `run_name` 过滤，例如只看 `metadata.model = gpt-4o`。

---

## 2. 自定义 Dashboard

UI → Dashboards → New Dashboard，可以拖各种 widget：

- 时间序列
- 表格
- Heatmap
- TopN（最贵的 prompt / 最慢的 user）

代码方式：

```python
from langsmith import Client
client = Client()
client.create_dashboard(
    name="ops-dashboard",
    widgets=[...],   # 详见文档 schema
)
```

---

## 3. Alerts 告警

UI → Alerts → New：

- 触发条件：`p99 latency > 5s` / `error_rate > 5%` / `cost_per_hour > $10`
- 通知方式：Email / Slack / Webhook / PagerDuty

定时窗口可设 1m/5m/1h。Webhook payload 是 JSON，里面带触发 trace id 列表，便于直接跳转排查。

---

## 4. Online Evaluation

Tracing 章节末尾提过：可以让 evaluator 在**生产 trace** 上自动跑。

UI → Project → Rules → New Rule：

- **触发**：
  - 按 metadata 过滤（如 `feature=rag`）
  - 按抽样比例（如 10%）
  - 按 trace 状态（error 才评）
- **动作**：
  - Run evaluator（用某个保存的 evaluator）
  - Create feedback
  - Send to dataset（自动收集 bad case）
  - Webhook

例：抽样 10% RAG trace 用 LLM-as-Judge 跑 faithfulness，分数 < 0.5 自动入 `bad-cases` 数据集。

---

## 5. User Feedback API

生产应用一定要把"用户反馈"接进来：

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

前端按钮带上 `run_id`（chain.invoke 返回时拿到，或用 `config={"run_id": uuid4()}` 自己生成）。

LangSmith 把 feedback 关联到 trace，Dashboard 可以画出按用户/模型/版本的"好评率"曲线。

---

## 6. 集成第三方监控

LangSmith 不替代 Datadog / Sentry，互补：

- LangSmith：LLM 维度的 trace、prompt、Eval
- Datadog / Prometheus：系统级 metrics、infra alert
- Sentry：异常堆栈

集成方式：在 callback 里同时报到多个系统。

---

## 7. 数据导出 / Webhook

Webhook：每次 run 完成实时推送：

```
POST your-webhook
{
  "event": "run.completed",
  "run": {...完整 run...},
}
```

API：

```python
runs = client.list_runs(start_time=..., end_time=...)
df = pd.DataFrame([r.dict() for r in runs])
df.to_parquet("export.parquet")
```

可以把 LangSmith 数据导到内部数仓做长期分析。

---

## 8. 控制成本与采样

LangSmith 也是有量额度的。如果生产请求量非常大：

```bash
# 全局采样率
LANGSMITH_SAMPLE_RATE=0.1   # 只上传 10%
```

或代码：

```python
chain.invoke(x, config={"callbacks": [LangChainTracer(sample_rate=0.1)]})
```

按 metadata 选择性上传：

```python
import random

class SamplingTracer(BaseCallbackHandler):
    def __init__(self, p):
        self.p = p
    def __call__(self, run):
        return random.random() < self.p
```

更精细：错误必传，正常按采样。

---

## 9. demo：发反馈 + 拉聚合统计

```python
# demos/langsmith/05_monitoring.py
import os, uuid
from dotenv import load_dotenv
from langsmith import Client
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()
client = Client()

chain = (
    ChatPromptTemplate.from_template("简短回答：{q}")
    | ChatOpenAI(model="gpt-4o-mini")
    | StrOutputParser()
)

run_id = str(uuid.uuid4())
ans = chain.invoke({"q": "LCEL 是什么？"}, config={"run_id": run_id})
print("answer:", ans)

# 模拟用户点了 👍
client.create_feedback(run_id=run_id, key="user_thumbs", score=1, comment="useful")
print("feedback uploaded for run", run_id)

# 拉最近一些 run 看统计
from datetime import datetime, timedelta
runs = list(client.list_runs(
    project_name=os.getenv("LANGSMITH_PROJECT") or "default",
    start_time=datetime.utcnow() - timedelta(hours=1),
    is_root=True,
    limit=20,
))
print(f"近 1h 根 run 数: {len(runs)}")
if runs:
    tot_in = sum(r.prompt_tokens or 0 for r in runs)
    tot_out = sum(r.completion_tokens or 0 for r in runs)
    print(f"输入 token={tot_in}, 输出 token={tot_out}")
```

---

## 10. 工程清单（生产部署）

- [ ] 服务 boot 时强校验 `LANGSMITH_API_KEY` 是否可用
- [ ] 给所有 trace 打 `service=...`, `version=...`, `user_id=...`
- [ ] 配置 `LANGSMITH_SAMPLE_RATE` 控制流量
- [ ] 重要错误一定 `error=true` 上传
- [ ] 用户 👍👎 反馈接 LangSmith
- [ ] CI 跑 evaluation 卡分数
- [ ] Dashboard + Alert：P99 延迟 + 错误率 + 日成本
- [ ] 每周抽样人工 review 一批 bad case → 入 dataset

---

## 11. 本章 demo

[`demos/langsmith/05_monitoring.py`](../../demos/langsmith/05_monitoring.py)

至此 LangSmith 五篇完成。接下来：

- [LangGraph 01](../03-langgraph/01-introduction.md)
