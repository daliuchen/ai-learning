# CE 05-04：重要性评分与剪枝（Pruning）

> **一句话**：滑动窗口按「时间」一刀切，剪枝按「价值」精挑细选——给每条消息 / 每个内容块打一个重要性分，然后在 token 预算内贪心保留高分的、丢弃低分的。它的核心是评分函数：相关性、时近性、是否被后文引用、是否含决策。剪枝最实用的一招是**工具结果剪枝**——把工具返回的大 JSON / 长日志只留关键字段，这往往是 Agent 上下文里最大的可压缩空间。

---

## 1. 为什么需要比滑窗更聪明

滑窗的假设是「越新越重要」，但这个假设经常错：

```
[轮1]  用户：「整个项目必须兼容 Python 3.8」   ← 极重要，但最老
[轮2]  助手：「好的」                          ← 废话
...
[轮38] 工具返回：8000 行 npm install 日志       ← 巨大、最新、但没用
[轮39] 助手：「依赖装好了」                     ← 废话
```

滑窗会保住轮 38 的 8000 行垃圾日志（因为新），却把轮 1 的关键约束砍掉（因为老）。**完全砍反了**。剪枝就是要纠正这种「唯时间论」——按内容价值排序，而不是按位置。

---

## 2. 评分维度：什么样的内容该留

给每条消息打一个 0~1 的重要性分，几个核心维度：

| 维度 | 含义 | 高分信号 | 低分信号 |
|------|------|----------|----------|
| **相关性** | 和当前任务 / 当前问题的语义相关度 | 与当前 query embedding 相似 | 跑题、闲聊 |
| **时近性** | 离现在多近 | 最近几轮 | 很久以前 |
| **被引用** | 后文是否提到 / 依赖它 | 「按之前说的方案…」 | 没人再提 |
| **含决策/事实** | 是否是决策、约束、具体值 | 「确定用 X」「key 在第 3 行」 | 「好的」「明白」 |
| **类型权重** | 消息类型本身的价值 | system / 任务定义 | 寒暄、确认 |

实际评分是这些维度的加权和。下面给一个可解释的启发式打分器（不依赖额外模型，纯规则 + embedding 相似度）。

---

## 3. 打分 + 剪枝完整代码

```python
import re
import numpy as np
import openai
import tiktoken

client = openai.OpenAI()
enc = tiktoken.encoding_for_model("gpt-4o")

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

要点：
- **system / 第 0 条强制加权**，确保任务约束不被剪掉（解决第 1 节的「砍反了」问题）。
- 剪枝**只删不重排**：按分数挑选，但输出仍按原始时间顺序，否则会破坏对话连贯性和 tool 对配对。
- embedding 用便宜的 `text-embedding-3-small`，评分这种事不值得用贵模型。

---

## 4. 工具结果剪枝：Agent 场景的最大金矿

Agent 上下文里最肥的可压缩空间是**工具返回结果**。一次 API 调用返回的 JSON 可能有几千 token，但模型真正要用的往往就那几个字段。与其整段留着或整段删掉，不如**结构化剪枝**——只留关键字段：

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
# ✅ 压成 [{"id":1,"name":"A"},...] 几十 token，模型要的信息一个不少
```

剪枝工具结果的几条经验：

| 工具输出类型 | 剪枝策略 |
|--------------|----------|
| 大 JSON 数组 | 只留关键字段 + 截前 N 项 + 附「共 X 项」 |
| 长日志 / stdout | 保头保尾，中间标记省略 |
| 文件内容 | 只留相关函数 / 行号范围，别整文件塞 |
| 网页抓取 | 抽正文，去导航/广告/脚本 |
| SQL 结果 | 限 `LIMIT`，或只回聚合结果 |

**最佳实践是在工具返回时就剪**（源头剪枝），而不是等塞进上下文后再剪——越早剪，下游每一轮都受益。

---

## 5. 剪枝 vs 滑窗 vs 摘要

三者不是竞品，是不同精度的工具：

| 维度 | 滑动窗口 | 剪枝 | 摘要 |
|------|----------|------|------|
| 取舍依据 | 时间 | 重要性分 | 语义压缩 |
| 额外开销 | 0 | 评分计算（embedding） | LLM 调用 |
| 信息损失 | 远期全丢 | 低分项丢 | 全程有损 |
| 保留形式 | 原文 | 原文（选择性） | 改写后的摘要 |
| 最擅长 | 无远期依赖的短任务 | 取舍散落各处的关键信息 / 剪工具结果 | 把长历史压成梗概 |

组合拳：**工具结果在源头剪枝 → 旧历史摘要 → 近期滑窗保原文**，三层叠加是生产级 Agent 的常见配置。

---

## 6. 常见误区

| 误区 | 真相 |
|------|------|
| 「剪枝就是按时间删旧的」 | 那是滑窗；剪枝按价值，老但重要的要留 |
| 「评分必须用 LLM」 | 规则 + embedding 相似度的启发式打分通常够用且便宜 |
| 「剪完可以重排消息」 | 不行，会破坏连贯性和 tool 对；只删不重排 |
| 「工具结果整段保留才安全」 | 大 JSON 90% 是噪声，源头剪字段省巨量 token |
| 「剪掉了就找不回」 | 剪枝前可落盘原文，需要时检索回来 |

---

## 7. 下一步

- 📖 何时触发剪枝 / 压缩：阈值与时机 → [05-when-to-compact.md](./05-when-to-compact.md)
- 📖 摘要式压缩：剪不动的旧历史就摘要 → [02-summarization.md](./02-summarization.md)
- 📖 滑动窗口：剪枝的「唯时间」简化版 → [03-sliding-window.md](./03-sliding-window.md)
- 📖 相关性评分背后的 embedding 检索 → [../03-retrieval/01-rag-as-context.md](../03-retrieval/01-rag-as-context.md)
- 📖 Agent 工具结果如何累积撑爆窗口 → [../06-agent-context/01-accumulation.md](../06-agent-context/01-accumulation.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents"（工具结果裁剪建议）: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OpenAI Embeddings 文档（`text-embedding-3-small`）: https://platform.openai.com/docs/guides/embeddings
