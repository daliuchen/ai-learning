# EKB 50：可观测性

> RAG 系统出了问题，「答得不好」只是表面现象。要定位原因，得看清链路上每一步：检索到了什么，rerank 怎么排的，prompt 长什么样，模型又是怎么答的。这篇讲该记录哪些信号、怎么做链路追踪，把系统从黑盒变成看得见的。

---

## 1. RAG 为什么特别需要可观测

```
用户报告：「问 X 答得不对」
没有可观测：你只能干瞪眼，复现都难
有可观测：调出这次问答的完整链路——
  → 哦，检索根本没召回对的文档（检索问题）
  → 或者召回对了但 rerank 排到第 8（排序问题）
  → 或者片段对了但模型没用上（生成问题）
```

RAG 的链路长，每一步都可能出错，没有可观测就没法定位。从 demo 走向生产，这是最要紧的基础设施之一。

---

## 2. 每次问答记录什么

沿着链路逐步记录，存进 `query_logs` 或专门的 trace：

```python
trace = {
    "query_id": ...,
    "question": q,
    "user_role": role,
    # 检索层
    "rewritten_queries": [...],          # 改写结果
    "retrieved": [                        # 召回了哪些，带分数
        {"chunk_id": .., "doc_id": .., "vector_dist": .., "bm25": .., "rrf": ..},
    ],
    "reranked": [{"chunk_id": .., "rerank_score": ..}],   # rerank 后
    # 生成层
    "prompt_tokens": .., "completion_tokens": ..,
    "found": True, "cited_doc_ids": [..],
    # 性能
    "latency": {"rewrite": .., "retrieve": .., "rerank": .., "generate": ..},
    # 反馈（事后回填）
    "feedback": None,
}
```

有了这份 trace，任何一次问答都能完整回放。反馈分诊（[第 49 篇](./03-feedback-loop.md)）和故障排查（下一篇）都以它为基础。

---

## 3. 盯哪些指标

把单次 trace 聚合成监控面板，重点看这几项：

| 指标 | 看什么 | 异常信号 |
|------|--------|----------|
| found=false 率 | 多少问题答不出 | 突然升高，可能是检索退化或出现知识盲区 |
| 平均 rerank top1 分 | 检索质量趋势 | 下降说明检索变差 |
| TTFT / 总延迟 P95 | 性能 | 超出预算（[第 48 篇](./02-cost-latency.md)） |
| token 用量 | 成本 | 突增通常是片段塞多了 |
| 「没用」反馈率 | 用户满意度 | 上升就要排查 |
| 越权召回数 | 安全红线 | 任何大于 0 的值立即告警 |

越权召回数是红线监控，生产环境里要一直盯着，不是上线前测一次就完事。

---

## 4. 用现成的 LLM 可观测工具

这套东西不必全部自己造。LLM 应用的可观测已经有成熟工具，能自动记录链路、token 和延迟：

- LangSmith（见 [01 手册的 LangSmith 总览](/docs/01-langchain/02-langsmith/01-overview)）：trace 和评估一体
- Langfuse：开源，适合自托管
- Pydantic Logfire：和 Pydantic AI 出自同一团队，集成最顺

它们能把上面的 trace 自动可视化，省得自己写日志面板。Pydantic AI 项目接 Logfire 几乎不用改代码：

```python
import logfire
logfire.configure()
logfire.instrument_pydantic_ai()    # 自动 trace 每次 agent 调用（V2 仍可用）
```

不想用 Logfire 也可以：`Agent.instrument_all()`（或 V2 的 `Instrumentation` capability）按 OpenTelemetry 标准发出 trace，能接 Langfuse 等任何 OTel 后端。要留意的是，trace 默认包含提示词、回复和工具参数，受限文档的片段也会跟着进去。想把内容挡在 span 之外，设置 `InstrumentationSettings(include_content=False)`，隐私问题在下一节展开。

自己造还是用现成工具，看团队情况。但「能回放每一次问答」这个能力必须有，工具只是实现手段。

---

## 5. 日志里的隐私与权限

可观测会记录问题和文档内容，这本身就涉及合规：

- trace 里会有受限文档的片段，trace 存储同样要控制访问权限
- 用户的问题可能含个人信息，要按合规要求脱敏，并管理保留期限
- 运维人员查 trace 时，受限文档的内容是否需要脱敏，要提前想清楚

可观测不能变成绕过权限的后门。检索层花大力气做的权限过滤，别在日志这里漏出去。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只记问题和答案 | 中间环节是黑盒，无法定位 | 记录全链路 trace |
| 出了问题无法复现 | 只能干瞪眼 | trace 支持回放 |
| 越权召回没有在线监控 | 漏洞上线后才发现 | 红线指标持续告警 |
| 日志全部自己造 | 重复造轮子 | 用 LangSmith、Logfire 等工具 |
| 日志泄漏受限内容 | 成了权限后门 | trace 也要控制权限、做脱敏 |

---

## 下一步

有了可观测，下一篇讲怎么用它系统地排查常见故障：[常见故障排查](./05-troubleshooting.md)
