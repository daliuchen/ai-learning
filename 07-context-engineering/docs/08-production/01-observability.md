# CE 08-01：上下文可观测性

> 生产环境里最大的盲区，往往是模型到底收到了什么。system prompt、检索片段、工具定义、压缩后的历史、当前问题散落在代码各处，由框架拼成最终的序列发给 API。这个渲染后的完整上下文，很多团队从来没打印出来看过。可观测性要做的第一件事，就是把它从黑盒变成能审计的对象。

---

## 1. 为什么「最终上下文」是头号盲区

你写的是模板和拼装逻辑，模型收到的是渲染结果。两者之间隔着一层框架（LangChain、Pydantic AI 或自研的 orchestrator），中间发生的很多事你都看不到：

- 框架在 system prompt 后面悄悄追加了一段「You are a helpful assistant...」
- 检索召回了 8 条，其中 3 条因为超出 token 预算被静默截断
- 历史压缩把第 3 轮的关键约束摘掉了
- 工具定义的 JSON schema 比你以为的长 5 倍，占掉了 2K token

这些都不会报错。模型答非所问时，你对着 prompt 模板看半天，问题却出在那个从没打印过的最终序列里。所以排查任何上下文问题之前，先把模型实际收到的完整 messages dump 出来。

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
| 渲染后的完整上下文 | 模型逐字收到了什么 | 排查答非所问、幻觉时第一个要看的地方 |
| 各部分 token 占比 | 预算花在哪了 | 找出成本和稀释的最大来源（见[上下文成本优化实战](./02-cost-optimization.md)） |
| 缓存命中率 | prefix 缓存有没有生效 | 命中率低意味着钱白花、延迟高 |
| 延迟分解（检索 / 拼装 / 推理） | 慢在哪一段 | 定位瓶颈，别急着换模型 |

只看输入 token 总数不够。你需要知道这 18K token 里，工具定义占了 6K，历史占了 8K，检索内容只有 1.5K（下一节的示例输出就是这样）。没有按部分拆开的占比，优化就只能靠猜。

---

## 3. 打印「上下文构成 + token 占比」

这一篇最主要的代码在这里：把要发给 API 的 messages 按角色和来源分桶，用真实的 tokenizer 计数，输出一张占比表。

```python
import json
import tiktoken
from dataclasses import dataclass, field

# 用与目标模型匹配的 tokenizer；Claude 可用 anthropic 的 count_tokens API
ENC = tiktoken.get_encoding("o200k_base")  # OpenAI 新近模型（GPT-4o 之后）用 o200k 系；新模型以官方为准

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
        # 拼装时可以给消息打个 _bucket 标签（如检索内容标 "retrieved"），发给 API 前去掉
        bucket = m.get("_bucket") or {"system": "system", "user": "user", "assistant": "history"}.get(role, role)
        b.add(bucket, content)
    if tools:
        # 工具定义经常是隐形大头，单独算一桶
        b.add("tools", json.dumps(tools, ensure_ascii=False))
    return b


# 用法：在调用前一行插桩
bd = inspect_context(messages, tools=my_tools)
print(bd.report())
```

下面是一个 Agent 第 4 轮调用时的输出示例：

```
部分            tokens     占比
history          8120    44.8%
tools            5980    33.0%
system           2010    11.1%
retrieved        1450     8.0%
user              570     3.1%
合计            18130   100.0%
```

问题很明显：干活用的检索内容只占 8%，历史和工具定义占了 78%。这时候该做的是压缩历史、裁剪工具，换模型解决不了（见[上下文成本优化实战](./02-cost-optimization.md)）。

---

## 4. 缓存命中率：别让 prompt caching 白配

2026 年各家都支持 prefix caching：Anthropic 的 `cache_control`、OpenAI 的自动前缀缓存、Gemini 的 context caching、千问的显式和隐式上下文缓存。命中的前提是前缀逐字节稳定。常见的失误是把时间戳、随机 ID 放在 system prompt 开头，每次前缀都不一样，缓存永远 miss。

可观测性要把命中情况打出来。Anthropic 的 usage 里直接带缓存字段：

```python
resp = client.messages.create(model="claude-sonnet-5-5", messages=messages, max_tokens=1024)
u = resp.usage
hit_rate = u.cache_read_input_tokens / max(
    u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens, 1
)
print(f"cache_read={u.cache_read_input_tokens} "
      f"cache_write={u.cache_creation_input_tokens} "
      f"fresh_input={u.input_tokens} hit_rate={hit_rate:.1%}")
```

命中率长期偏低时，先查两件事：稳定前缀（system 加工具定义）里有没有混进动态内容？`cache_control` 断点是不是打在了变动边界之前？

---

## 5. 工具：LangSmith / Langfuse / Logfire / OTel

手写 print 适合排查单次请求，生产环境要上 trace 平台。2025 到 2026 年的主流选择：

| 工具 | 定位 | 适合 |
|------|------|------|
| LangSmith | LangChain 官方，trace + eval + 数据集 | LangChain / LangGraph 栈 |
| Langfuse | 开源、可自托管，框架无关 | 想自托管或多框架混用 |
| Logfire | Pydantic 出品，基于 OTel，对 Python 友好；SDK 开源，托管平台闭源，自托管属企业版 | Pydantic AI 栈、已有 OTel 体系 |
| OpenTelemetry（GenAI 语义约定） | 厂商中立标准，约定本身仍在演进（规范状态为 Development），但主流 SDK 已普遍采用 | 想接入现有 APM、不被锁死 |

方向已经很清楚，大家都在往 OpenTelemetry GenAI 语义约定靠拢：span 上记录 `gen_ai.request.model`、输入输出 token、缓存命中等标准属性，再导出到任意后端，不被单一厂商绑定。不管用哪个，都要确认它记录的是渲染后的最终 messages，不只是模板变量。

```python
# Logfire 一行接入（自动给 Pydantic AI / OpenAI 调用插桩）
import logfire
logfire.configure()
logfire.instrument_openai()   # 之后每次调用自动产生带完整 messages 的 span
```

---

## 6. 落地清单

- 每次 API 调用前，都能 dump 出渲染后的完整 messages，而不是模板
- 有一张 token 占比表，知道预算在 history、tools、retrieved、system 上各花了多少
- 监控缓存命中率，稳定前缀一被污染就报警
- 延迟拆成检索、拼装、推理三段，别把问题都归到模型头上
- trace 平台记录真实输入，对异常输出采样保留完整上下文，方便事后复盘
- 别只盯着输出做评估。输出错了，原因多半在你没看过的那段输入里

---

## 下一步

- 拿占比表去砍最大的那块、做降本：[上下文成本优化实战](./02-cost-optimization.md)
- dump 出上下文后怎么按症状定位故障：[上下文相关故障排查](./03-debugging.md)
- 把可观测信号沉淀成评测指标：[如何评测上下文质量](./04-evaluation.md)
- 回看成本与延迟的基础模型：[上下文的成本与延迟模型](../01-foundations/04-cost-latency.md)
- 实战：[带长期记忆的客服 Agent](../09-practice/01-memory-customer-agent.md)

## 参考资料

- OpenTelemetry GenAI 语义约定：https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Langfuse 文档：https://langfuse.com/docs
- Pydantic Logfire：https://logfire.pydantic.dev/docs/
- LangSmith Observability：https://docs.smith.langchain.com/
