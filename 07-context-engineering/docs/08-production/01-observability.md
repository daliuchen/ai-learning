# CE 08-01：上下文可观测性——看到模型「真正收到了什么」

> **一句话**：生产中最大的盲区不是「模型输出了什么」，而是「模型到底收到了什么」。你的代码里散落着 system prompt、检索片段、工具定义、压缩后的历史、当前问题——它们被框架拼成一个最终序列喂给 API，而这个**渲染后的完整上下文**，99% 的团队从来没打印出来看过。可观测性的第一性目标，就是让这个序列从黑盒变成可审计的对象。

---

## 1. 为什么「最终上下文」是头号盲区

你写的是模板和拼装逻辑，模型吃的是渲染结果。两者之间隔着一层框架（LangChain / Pydantic AI / 自研 orchestrator），中间发生了太多你没看到的事：

- system prompt 被框架悄悄追加了一段「You are a helpful assistant...」
- 检索召回了 8 条，但有 3 条因为超 token 预算被静默截断
- 历史压缩把第 3 轮的关键约束摘掉了
- 工具定义的 JSON schema 比你以为的长 5 倍，吃掉了 2K token

这些都不会报错。模型「答非所问」时，你盯着 prompt 模板看半天，根因其实在那个你从没打印过的最终序列里。**第一条铁律：排查任何上下文问题前，先 dump 出模型实际收到的完整 messages。**

```python
# ❌ 只看自己写的模板，以为那就是模型收到的
prompt = template.format(question=q, docs=retrieved)

# ✅ 拿到框架真正发出去的 payload，逐字看
import json
print(json.dumps(messages_sent_to_api, ensure_ascii=False, indent=2))
```

---

## 2. 可观测性要采集哪四类信号

| 信号 | 回答什么问题 | 怎么用 |
|------|--------------|--------|
| 渲染后的完整上下文 | 模型逐字收到了什么 | 排查答非所问 / 幻觉的第一现场 |
| 各部分 token 占比 | 预算花在哪了 | 找成本与稀释的最大头（见 [02-cost-optimization.md](./02-cost-optimization.md)） |
| 缓存命中率 | prefix 缓存有没有生效 | 命中率低 = 钱白花、延迟高 |
| 延迟分解（检索 / 拼装 / 推理） | 慢在哪一段 | 定位瓶颈，别盲目换模型 |

只看「输入 token 总数」是不够的——你需要知道这 18K token 里，工具定义占了 6K、历史占了 8K、真正的检索内容只剩 2K。**没有占比拆解，优化就是瞎猜。**

---

## 3. 打印「上下文构成 + token 占比」

这是本篇的核心代码：把发给 API 的 messages 按角色/来源分桶，用真实 tokenizer 计数，输出一张占比表。

```python
import json
import tiktoken
from dataclasses import dataclass, field

# 用与目标模型匹配的 tokenizer；Claude 可用 anthropic 的 count_tokens API
ENC = tiktoken.get_encoding("o200k_base")  # GPT-4o / 4.1 系列

def ntok(text: str) -> int:
    return len(ENC.encode(text or ""))

@dataclass
class ContextBreakdown:
    buckets: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, text: str) -> None:
        self.buckets[name] = self.buckets.get(name, 0) + ntok(text)

    def report(self) -> str:
        total = sum(self.buckets.values()) or 1
        rows = sorted(self.buckets.items(), key=lambda kv: -kv[1])
        lines = [f"{'部分':<14}{'tokens':>8}{'占比':>8}"]
        for name, n in rows:
            lines.append(f"{name:<14}{n:>8}{n / total:>7.1%}")
        lines.append(f"{'合计':<14}{total:>8}{1.0:>7.1%}")
        return "\n".join(lines)


def inspect_context(messages: list[dict], tools: list[dict] | None = None) -> ContextBreakdown:
    """传入即将发给 API 的 messages，拆解 token 占比。"""
    b = ContextBreakdown()
    for m in messages:
        role = m["role"]
        content = m["content"]
        if isinstance(content, list):  # 多模态 / 工具结果块
            content = "".join(part.get("text", "") for part in content)
        bucket = {"system": "system", "user": "user", "assistant": "history"}.get(role, role)
        b.add(bucket, content)
    if tools:
        # 工具定义经常是隐形大头，单独算一桶
        b.add("tools", json.dumps(tools, ensure_ascii=False))
    return b


# 用法：在调用前一行插桩
bd = inspect_context(messages, tools=my_tools)
print(bd.report())
```

典型输出（一个 Agent 第 4 轮调用）：

```
部分            tokens     占比
history          8120    44.8%
tools            5980    33.0%
system           2010    11.1%
retrieved        1450     8.0%
user              570     3.1%
合计            18130   100.0%
```

一眼就看出问题：**真正干活的检索内容只占 8%，历史和工具定义吃掉了 78%**。这时候该做的不是换模型，是压历史、裁工具（见 [02-cost-optimization.md](./02-cost-optimization.md)）。

---

## 4. 缓存命中率：别让 prompt caching 白配

2025-2026 各家都支持 prefix caching（Anthropic 的 `cache_control`、OpenAI 的自动前缀缓存、Gemini 的 context caching）。命中的前提是**前缀逐字节稳定**。一个常见翻车：把时间戳、随机 ID 放在 system prompt 开头，导致每次前缀都变，缓存永远 miss。

可观测性要把命中情况打出来。Anthropic 的 usage 直接回传缓存字段：

```python
resp = client.messages.create(model="claude-sonnet-4-5", messages=messages, max_tokens=1024)
u = resp.usage
hit_rate = u.cache_read_input_tokens / max(
    u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens, 1
)
print(f"cache_read={u.cache_read_input_tokens} "
      f"cache_write={u.cache_creation_input_tokens} "
      f"fresh_input={u.input_tokens} hit_rate={hit_rate:.1%}")
```

命中率长期偏低，去查：稳定前缀（system + 工具定义）是不是被动态内容污染了？`cache_control` 断点有没有打在变动边界之前？

---

## 5. 工具：LangSmith / Langfuse / Logfire / OTel

手搓 print 适合排查单次，生产要上 trace 平台。2025-2026 的主流选择：

| 工具 | 定位 | 适合 |
|------|------|------|
| LangSmith | LangChain 官方，trace + eval + 数据集 | LangChain / LangGraph 栈 |
| Langfuse | 开源、可自托管，框架无关 | 想自托管 / 多框架混用 |
| Logfire | Pydantic 出品，基于 OTel，Python 友好 | Pydantic AI 栈、已有 OTel 体系 |
| OpenTelemetry（GenAI 语义约定） | 厂商中立标准，2025 起 LLM span 约定成熟 | 想接入现有 APM、不被锁死 |

趋势很明确：**往 OpenTelemetry GenAI 语义约定收敛**——span 上记录 `gen_ai.request.model`、input/output token、缓存命中等标准属性，再导出到任意后端。这样你不被单一厂商绑定。无论用哪个，确保它记录的是**渲染后的最终 messages**，而不只是你的模板变量。

```python
# Logfire 一行接入（自动给 Pydantic AI / OpenAI 调用插桩）
import logfire
logfire.configure()
logfire.instrument_openai()   # 之后每次调用自动产生带完整 messages 的 span
```

---

## 6. 落地清单

- ✅ 每次 API 调用前，能 dump 出**渲染后的完整 messages**（不是模板）
- ✅ 有一张 token 占比表，知道预算花在 history / tools / retrieved / system 各多少
- ✅ 监控缓存命中率，发现稳定前缀被污染立刻报警
- ✅ 延迟拆成「检索 / 拼装 / 推理」三段，别把锅都甩给模型
- ✅ trace 平台记录的是真实输入，采样保留「异常输出」对应的完整上下文供事后复盘
- ❌ 别只盯输出做评估——输出错了，根因 90% 在你没看的那段输入里

---

## 下一步

- 📖 拿占比表去砍最大头、做降本 → [02-cost-optimization.md](./02-cost-optimization.md)
- 📖 dump 出上下文后怎么按症状定位故障 → [03-debugging.md](./03-debugging.md)
- 📖 把可观测信号沉淀成评测指标 → [04-evaluation.md](./04-evaluation.md)
- 📖 回看成本与延迟的基础模型 → [../01-foundations/04-cost-latency.md](../01-foundations/04-cost-latency.md)
- 📖 实战：客服 Agent 的记忆与上下文 → [../09-practice/01-memory-customer-agent.md](../09-practice/01-memory-customer-agent.md)

## 参考资料

- OpenTelemetry GenAI 语义约定：https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Langfuse 文档：https://langfuse.com/docs
- Pydantic Logfire：https://logfire.pydantic.dev/docs/
- LangSmith Observability：https://docs.smith.langchain.com/
