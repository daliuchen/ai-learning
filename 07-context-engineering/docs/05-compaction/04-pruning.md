# CE 05-04：重要性评分与剪枝（Pruning）

> 滑动窗口按时间一刀切，剪枝则按价值挑选：给每条消息或每个内容块打一个重要性分，在 token 预算内贪心地保留高分的、丢掉低分的。做好剪枝要靠评分函数，常用的维度有相关性、时近性、是否被后文引用、是否包含决策。最实用的一招是工具结果剪枝，把工具返回的大 JSON 或长日志只留关键字段，这通常是 Agent 上下文里最大的可压缩空间。

---

## 1. 为什么需要比滑窗更聪明

滑窗默认越新越重要，这个假设经常不成立：

```
[轮1]  用户：「整个项目必须兼容 Python 3.8」   ← 极重要，但最老
[轮2]  助手：「好的」                          ← 废话
...
[轮38] 工具返回：8000 行 npm install 日志       ← 巨大、最新、但没用
[轮39] 助手：「依赖装好了」                     ← 废话
```

滑窗会留下轮 38 那 8000 行没用的日志（因为新），砍掉轮 1 的关键约束（因为老），正好弄反了。剪枝要纠正的就是这种只看时间的取舍，按内容的价值排序，而不是按位置。

---

## 2. 评分维度：什么样的内容该留

给每条消息打一个 0~1 的重要性分，主要看这几个维度：

| 维度 | 含义 | 高分信号 | 低分信号 |
|------|------|----------|----------|
| 相关性 | 和当前任务 / 当前问题的语义相关度 | 与当前 query embedding 相似 | 跑题、闲聊 |
| 时近性 | 离现在多近 | 最近几轮 | 很久以前 |
| 被引用 | 后文是否提到 / 依赖它 | 「按之前说的方案…」 | 没人再提 |
| 含决策/事实 | 是否是决策、约束、具体值 | 「确定用 X」「key 在第 3 行」 | 「好的」「明白」 |
| 类型权重 | 消息类型本身的价值 | system / 任务定义 | 寒暄、确认 |

实际的分数是这些维度的加权和。下面是一个可解释的启发式打分器，不依赖额外模型，只用规则加 embedding 相似度。

---

## 3. 打分 + 剪枝完整代码

```python
import re
import numpy as np
import openai
import tiktoken

client = openai.OpenAI()
enc = tiktoken.get_encoding("o200k_base")

# 含「决策 / 约束 / 事实」信号的关键词
DECISION_PAT = re.compile(
    r"(决定|确定|选择|必须|不要|因为|失败|错误|TODO|待办|路径|配置|因此|方案)"
)
FILLER_PAT = re.compile(r"^(好的|明白|收到|了解|谢谢|嗯+|ok)[。!,.\s]*$", re.I)


def embed(texts: list[str]) -> np.ndarray:
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([d.embedding for d in resp.data])


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def score_messages(messages: list[dict], query: str) -> list[float]:
    """对每条消息打 0~1 重要性分。messages[0] 假定是 system。"""
    n = len(messages)
    q_vec = embed([query])[0]
    msg_vecs = embed([m.get("content") or " " for m in messages])

    scores = []
    for i, m in enumerate(messages):
        content = m.get("content") or ""

        relevance = max(0.0, cosine(msg_vecs[i], q_vec))      # 相关性
        recency = i / max(1, n - 1)                            # 时近性 0~1
        has_decision = 1.0 if DECISION_PAT.search(content) else 0.0
        is_filler = 1.0 if FILLER_PAT.match(content.strip()) else 0.0
        is_system = 1.0 if m.get("role") == "system" or i == 0 else 0.0

        score = (
            0.35 * relevance
            + 0.25 * recency
            + 0.25 * has_decision
            + 0.30 * is_system    # system / 任务定义强制高分
            - 0.40 * is_filler    # 寒暄强力扣分
        )
        scores.append(max(0.0, min(1.0, score)))
    return scores


def prune(messages: list[dict], query: str, max_tokens: int) -> list[dict]:
    """按重要性分贪心保留，预算内留高分的，保持原始顺序。"""
    scores = score_messages(messages, query)
    indexed = sorted(range(len(messages)), key=lambda i: scores[i], reverse=True)

    kept_idx, budget = set(), max_tokens
    for i in indexed:
        cost = len(enc.encode(messages[i].get("content") or ""))
        if cost <= budget:
            kept_idx.add(i)
            budget -= cost

    # 恢复时间顺序输出（剪枝只删不重排）
    return [messages[i] for i in sorted(kept_idx)]


# 用法
messages = prune(messages, query="当前用户问题", max_tokens=8000)
```

几点说明：

- system 和第 0 条强制加权，任务约束就不会被剪掉，第 1 节那种弄反的情况也就避免了。
- 剪枝只删不重排：按分数挑选，输出仍按原始时间顺序，否则会打乱对话的连贯性。
- 示例为了简洁按单条消息打分，逐条删可能留下孤立的 tool 结果（API 会报错）。实际要把 assistant 的 tool_call 和它对应的结果当成一个单元，一起打分、一起留或一起删。
- embedding 用便宜的 `text-embedding-3-small`，打分这种事不值得用贵模型。

---

## 4. 工具结果剪枝：Agent 场景里收益最大的一招

Agent 上下文里可压缩空间最大的是工具返回结果。一次 API 调用返回的 JSON 可能有几千 token，模型要用的往往就那几个字段。整段留着太浪费，整段删掉又会丢信息，更合适的是做结构化剪枝，只留关键字段：

```python
import json


def prune_tool_result(raw: str, keep_keys: list[str], max_items: int = 5) -> str:
    """把工具返回的大 JSON 剪成只含关键字段的精简版。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # 非 JSON（如日志），截断保头尾
        if len(raw) > 2000:
            return raw[:1000] + "\n...[已剪枝中间内容]...\n" + raw[-500:]
        return raw

    def pick(obj):
        if isinstance(obj, dict):
            return {k: obj[k] for k in keep_keys if k in obj}
        if isinstance(obj, list):
            return [pick(x) for x in obj[:max_items]]  # 长列表只留前几项
        return obj

    pruned = pick(data)
    return json.dumps(pruned, ensure_ascii=False)


# 例：一个返回 200 个用户、每个 20 字段的 API 结果
raw = '[{"id":1,"name":"A","email":"...","内部字段...":"一堆"}, ...]'  # 8000 token
slim = prune_tool_result(raw, keep_keys=["id", "name"], max_items=5)
# ✅ 压成前 5 项的 [{"id":1,"name":"A"},...]，几十 token；后面 195 项没了，
#    任务需要全量时别截前 N 项，至少附上「共 200 项，已截断」
```

不同类型的工具输出，剪法不一样：

| 工具输出类型 | 剪枝策略 |
|--------------|----------|
| 大 JSON 数组 | 只留关键字段 + 截前 N 项 + 附「共 X 项」 |
| 长日志 / stdout | 保头保尾，中间标记省略 |
| 文件内容 | 只留相关函数 / 行号范围，别整文件塞 |
| 网页抓取 | 抽正文，去导航/广告/脚本 |
| SQL 结果 | 限 `LIMIT`，或只回聚合结果 |

**最好在工具返回时就剪**，也就是在源头剪，不要等塞进上下文之后再剪。剪得越早，后面每一轮都跟着受益。

---

## 5. 平台和框架内置的工具结果清理

清掉旧工具结果、留一个占位符，这件事现在已经有现成实现，语义和上面手写的基本一致：

- Anthropic context editing：`context_management={"edits": [{"type": "clear_tool_uses_20250919", "trigger": {"type": "input_tokens", "value": 30000}, "keep": {"type": "tool_uses", "value": 3}, "clear_at_least": {"type": "input_tokens", "value": 5000}, "exclude_tools": ["web_search"]}]}`，需要 beta header `context-management-2025-06-27`。清理在服务端做，客户端仍保留完整历史；响应里的 `context_management.applied_edits` 会告诉你清了多少。默认 `trigger` 是 100,000 input token，保留最近 3 组。`clear_at_least` 用来权衡清掉这点内容值不值得丢一次 prompt cache。（语法按官方文档整理，运行需要 Anthropic API key）
- LangChain 1.x：`ContextEditingMiddleware(edits=[ClearToolUsesEdit(trigger=50000, keep=5, exclude_tools=[...], placeholder="[old result removed]")])`。

什么时候还要自己写：想按重要性而不是时间顺序清理（本篇 §2、§3），或者要给不同工具设不同的保留策略。

---

## 6. 剪枝 vs 滑窗 vs 摘要

三者精度不同，常常配合使用：

| 维度 | 滑动窗口 | 剪枝 | 摘要 |
|------|----------|------|------|
| 取舍依据 | 时间 | 重要性分 | 语义压缩 |
| 额外开销 | 0 | 评分计算（embedding） | LLM 调用 |
| 信息损失 | 远期全丢 | 低分项丢 | 全程有损 |
| 保留形式 | 原文 | 原文（选择性） | 改写后的摘要 |
| 最擅长 | 无远期依赖的短任务 | 取舍散落各处的关键信息 / 剪工具结果 | 把长历史压成梗概 |

生产级 Agent 的常见配置是三层叠加：工具结果在源头剪枝，旧历史做摘要，近期用滑窗保留原文。

---

## 7. 常见误区

| 误区 | 实际情况 |
|------|------|
| 「剪枝就是按时间删旧的」 | 那是滑窗；剪枝按价值，老但重要的要留 |
| 「评分必须用 LLM」 | 规则 + embedding 相似度的启发式打分通常够用且便宜 |
| 「剪完可以重排消息」 | 不行，会破坏连贯性和 tool 对；只删不重排 |
| 「工具结果整段保留才安全」 | 大 JSON 里大部分是噪声，在源头剪字段能省下大量 token |
| 「剪掉了就找不回」 | 剪枝前可以把原文落盘，需要时检索回来 |

---

## 8. 下一步

- 剪枝和压缩什么时候触发：[何时触发压缩](./05-when-to-compact.md)
- 剪不动的旧历史就做摘要：[摘要式压缩](./02-summarization.md)
- 只看时间的简化版：[滑动窗口与截断](./03-sliding-window.md)
- 相关性评分背后的 embedding 检索：[RAG 作为上下文来源](../03-retrieval/01-rag-as-context.md)
- Agent 的工具结果如何累积撑爆窗口：[Agent 的上下文累积问题](../06-agent-context/01-accumulation.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents"（工具结果裁剪建议）: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OpenAI Embeddings 文档（`text-embedding-3-small`）: https://developers.openai.com/api/docs/guides/embeddings
