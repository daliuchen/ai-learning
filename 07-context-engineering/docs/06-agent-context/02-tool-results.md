# 工具结果的上下文管理

> **一句话**：工具返回的原始内容（网页全文、大 JSON、文件内容）往往是 Agent 上下文里最大的占地户，而模型真正需要的常常只是其中一小段——**把大结果落到外部存储、上下文里只放摘要 + 句柄**，是 2025-2026 长程 Agent 的标配。

---

## 1. 工具结果为什么是头号占地户

Agent 的工具调用本身很短（一个函数名 + 几个参数），但**返回值可能巨大**：

| 工具 | 典型返回体积 | 模型实际需要的 |
|------|--------------|----------------|
| 网页抓取 | 5K~50K token 的 HTML/正文 | 某一段事实，几百 token |
| 数据库 / API 查询 | 几千行 JSON | 几个字段 |
| 读文件 / `cat` 大文件 | 整个文件 | 某个函数、某段配置 |
| `ls -R` / `find` | 上千行路径 | 一两个目标文件 |
| 日志查询 | 几 MB | 报错那几行 |

这些原始结果一旦原样 append 进 messages，就**永久**占着窗口（除非后续主动清理），直接喂大了上一篇说的累积问题。

```python
# ❌ 反模式：工具结果原样回灌
result = fetch_url(url)                                  # 返回 40K token 的网页全文
messages.append({"role": "user", "content": tool_result(call, result)})
# 这 40K token 接下来每一步都跟着重发，模型其实只想要其中一句话
```

---

## 2. 四种压缩策略

按「损失多少信息」从轻到重排列，工程上常组合使用：

| 策略 | 做法 | 适用 | 风险 |
|------|------|------|------|
| **截断（truncate）** | 只留前 N 个 token / 行，附「已截断」标记 | 结果开头就有答案、日志 tail | 答案在被砍掉的部分就丢了 |
| **结构化抽取** | 用规则 / 小模型抽出关键字段，丢弃其余 | JSON、HTML、表格 | 抽取逻辑漏字段 |
| **摘要（summarize）** | 调一次便宜模型把长结果压成要点 | 网页正文、长文档 | 多一次调用 + 摘要可能失真 |
| **引用 ID / 落盘** | 全文存外部，上下文只放摘要 + 句柄，需要时再取回 | 大文件、可能反复引用的内容 | 实现复杂，需要配套取回工具 |

核心判断：**这段结果模型现在需要看全文吗？** 大多数时候不需要——它只需要知道「这里有什么、要用时去哪取」。

---

## 3. 推荐范式：大结果落盘 + 上下文放摘要 + 句柄

最稳的做法是把工具结果存到外部（文件系统 / KV / 对象存储），上下文里只留一段轻量摘要和一个可寻址的句柄。需要细节时，模型再调一个「取回」工具把全文或某段拉回来——这正是 just-in-time / 按需检索的思路。

```python
import json, uuid, pathlib
from anthropic import Anthropic

client = Anthropic()
STORE = pathlib.Path("/tmp/agent_artifacts")
STORE.mkdir(exist_ok=True)

def store_large_result(content: str, kind: str) -> dict:
    """把大工具结果落盘，上下文只回一个摘要 + 句柄。"""
    handle = f"{kind}-{uuid.uuid4().hex[:8]}"
    (STORE / f"{handle}.txt").write_text(content)

    # 用便宜模型生成一句话摘要（也可换成规则抽取）
    summary = client.messages.create(
        model="claude-haiku-4-6",
        max_tokens=256,
        messages=[{"role": "user",
                   "content": f"用 2~3 句话概括以下内容的要点，便于后续判断是否需要取回全文：\n\n{content[:8000]}"}],
    ).content[0].text

    return {
        "handle": handle,
        "size_tokens": len(content) // 4,   # 粗估
        "summary": summary,
    }

def fetch_artifact(handle: str, grep: str | None = None) -> str:
    """按需取回：可选 grep 只拉相关行，进一步省 token。"""
    text = (STORE / f"{handle}.txt").read_text()
    if grep:
        return "\n".join(l for l in text.splitlines() if grep.lower() in l.lower())[:6000]
    return text[:12000]
```

工具调用环节这样接：

```python
# ✅ 工具返回大内容时，落盘 + 只把摘要句柄放进上下文
raw = fetch_url(url)                       # 40K token 网页全文
meta = store_large_result(raw, kind="webpage")
tool_payload = (
    f"[已落盘 handle={meta['handle']} ~{meta['size_tokens']}token]\n"
    f"摘要：{meta['summary']}\n"
    f"（需要原文细节请调用 fetch_artifact(handle, grep=...)）"
)
messages.append({"role": "user", "content": tool_result(call, tool_payload)})
# 上下文从 40K 降到 ~300 token；模型按需再取回
```

`fetch_artifact` 本身注册成一个工具，让模型自己决定要不要拉全文、拉哪几行。

---

## 4. 文件系统就是 Agent 的外部上下文

2025 年起的主流编码 Agent（Claude Code、各类 coding agent）几乎都把**文件系统当外部上下文存储**：工具读出的内容不全塞进窗口，而是落到文件，上下文里只留路径和摘要。这有两个额外好处：

- **跨步持久**：句柄在整个任务生命周期有效，第 30 步还能取回第 3 步存的东西，不靠把它一直挂在窗口里。
- **可被多个 sub-agent 共享**：句柄传给子 Agent，子 Agent 自己决定要不要拉全文（详见 [03-multi-agent-passing.md](./03-multi-agent-passing.md)）。

```
工具结果 ──→ 外部存储（文件 / KV / 对象存储）
                │
                ├─ 上下文里只放：摘要 + handle + size
                │
                └─ 模型需要细节 ──→ fetch_artifact(handle, grep) ──→ 拉回相关片段
```

---

## 5. 落地清单

- **给每个工具设返回上限**：超过阈值（比如 2K token）自动走「落盘 + 摘要」分支，别让大结果裸奔进窗口。
- **截断要带标记**：`...[已截断，共 1200 行，仅显示前 100 行，用 fetch_artifact 取全文]`，模型才知道有更多内容、去哪取。
- **结构化优先于自由文本**：API 返回先抽字段再进上下文，比让模型在原始 JSON 里捞字段省得多也准得多。
- **摘要用便宜模型**：Haiku 级别模型做压缩，成本几乎可忽略，省下的是主模型每步重发的钱。
- **保留来源**：摘要里带上 url / 文件路径 / 句柄，调试和引用归因时找得回原文。

---

## 下一步

- [03-multi-agent-passing.md](./03-multi-agent-passing.md)：句柄和摘要怎么在多个 Agent 间传递
- [05-state-vs-context.md](./05-state-vs-context.md)：句柄本质是一种外部 state，按需才注入
- 跨章：[../05-compaction/02-summarization.md](../05-compaction/02-summarization.md) 历史摘要与本篇结果摘要的异同
- 跨章：[../03-retrieval/03-just-in-time.md](../03-retrieval/03-just-in-time.md) 把取回做成工具的 just-in-time 检索
