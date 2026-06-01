# CE 04-02：会话记忆管理

> **一句话**：单次会话内，对话历史会无限长大，而窗口和钱包都有限。会话记忆管理就是在 token 预算内决定「保留哪几轮原文、把哪些轮压成摘要、丢弃哪些」。经典三招——全量 buffer、滑动 window、summary buffer（窗口 + 摘要）——本质是「保真度」和「token 成本」之间的取舍。

---

## 1. 问题：历史只增不减，窗口会爆

短期记忆活在窗口里（见 [01-short-vs-long.md](./01-short-vs-long.md)）。最朴素的做法是把每一轮 user/assistant 原样追加，一路拼下去：

```python
# ❌ 全量 buffer：聊得越久越贵，最终撞窗
messages.append({"role": "user", "content": user_input})
# ... 调用模型 ...
messages.append({"role": "assistant", "content": reply})
# messages 永远只增不减
```

后果：

1. **token 线性涨**：第 100 轮要把前 99 轮全发一遍，输入 token 越来越多，每次都重复付费、响应越来越慢。
2. **撞窗截断**：累计超过窗口上限，要么报错，要么粗暴砍头，连贯性瞬间断裂。

会话记忆管理就是回答：**在固定的 token 预算里，留什么、压什么、丢什么。**

---

## 2. 三种经典策略

借鉴 LangChain memory 的几个经典概念（虽然新版 LangGraph 已重构 API，但这几种「思路」是通用的，跨任何框架都成立）：

| 策略 | 怎么做 | 保真度 | token 成本 | 适用 |
|------|--------|--------|-----------|------|
| Buffer（全量） | 全部历史原样保留 | 最高（一字不漏） | 最高（线性涨） | 短会话、调试 |
| Window（滑窗） | 只留最近 N 轮，旧的直接丢 | 中（丢早期细节） | 恒定（封顶） | 客服、问答 |
| Summary Buffer（摘要+窗口） | 近 N 轮原文 + 更早的压成摘要 | 较高（要点不丢） | 较低且可控 | 长会话、Agent |
| Token Buffer | 按 token 数而非轮数截断 | 中 | 精确封顶 | 严格预算场景 |

核心规律：**越靠近现在的轮次越值钱**，保留原文；越久远的越该被压缩或丢弃——因为当前对话的指代、上下文几乎都落在最近几轮。

---

## 3. 滑动窗口（Window）：最简单有效

只保留最近 `k` 轮，旧的丢弃。成本恒定，实现一行：

```python
# ✅ 滑动窗口：永远只发最近 k 轮
def windowed(history: list[dict], k_turns: int = 6) -> list[dict]:
    # 一轮 = user + assistant 两条，所以乘 2
    return history[-k_turns * 2:]
```

缺点很明显：**窗口外的信息彻底蒸发**。用户第 1 轮说「我叫张三」，聊到第 20 轮你已经不记得了。所以纯滑窗适合「不需要记早期细节」的场景（如一问一答的检索问答），需要长记忆的场景得叠加摘要或长期记忆。

---

## 4. Summary Buffer：窗口 + 摘要的混合

这是工程上最常用的折中：**最近 N 轮保原文（保连贯），更早的滚动压成一段摘要（保要点、省 token）**。

```
┌─────────────────────────────────────────────┐
│ [摘要] 用户张三，做电商，问过退货和物流；       │  ← 早期 N-∞ 轮压成一段
│        已确认要 API 集成方案                    │
├─────────────────────────────────────────────┤
│ 第 N-2 轮 user / assistant  （原文）           │  ← 最近 N 轮保真
│ 第 N-1 轮 user / assistant  （原文）           │
│ 第 N   轮 user              （当前输入）        │
└─────────────────────────────────────────────┘
```

当原文轮数超过阈值，就把「溢出的最旧几轮」喂给模型压成摘要，合并进既有摘要里：

```python
import anthropic

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-5"

def summarize(old_summary: str, overflow: list[dict]) -> str:
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in overflow)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=(
            "你在维护一段对话摘要。把【已有摘要】和【新增对话】合并成一段更新后的摘要。"
            "保留：用户身份、偏好、已达成的结论、未完成的任务。丢弃寒暄与冗余。用第三人称，简洁。"
        ),
        messages=[{
            "role": "user",
            "content": f"【已有摘要】\n{old_summary or '（空）'}\n\n【新增对话】\n{convo}",
        }],
    )
    return resp.content[0].text
```

完整的 summary buffer 记忆类：

```python
class SummaryBufferMemory:
    def __init__(self, keep_recent: int = 4):
        self.summary = ""              # 早期对话的滚动摘要
        self.recent: list[dict] = []   # 最近原文轮次
        self.keep_recent = keep_recent # 保留多少条原文（user+assistant 计数）

    def add(self, role: str, content: str) -> None:
        self.recent.append({"role": role, "content": content})
        # 溢出的最旧轮次滚动进摘要
        if len(self.recent) > self.keep_recent:
            overflow = self.recent[: len(self.recent) - self.keep_recent]
            self.recent = self.recent[-self.keep_recent :]
            self.summary = summarize(self.summary, overflow)

    def to_messages(self) -> list[dict]:
        msgs = []
        if self.summary:
            msgs.append({"role": "user", "content": f"[对话摘要] {self.summary}"})
        msgs.extend(self.recent)
        return msgs


# 用法
mem = SummaryBufferMemory(keep_recent=4)
mem.add("user", "我叫张三，做跨境电商")
mem.add("assistant", "你好张三，需要什么帮助？")
# ... 聊很多轮后 ...
mem.add("user", "刚才说的退货方案细化一下")
# to_messages() 里：早期被压成摘要（仍记得"张三、跨境电商"），近几轮保原文
context = mem.to_messages()
```

这样无论聊多久，进窗口的 token 都被压在「一段摘要 + 最近 N 轮」的恒定量级，且早期关键信息不会凭空蒸发。

---

## 5. Token 预算下保留哪些轮次

不是「轮数」而是「token」才是真正的硬约束。给会话历史划一个 token 预算（比如总窗口的 30%，见 [01-foundations/05-context-budget.md](../01-foundations/05-context-budget.md)），从最新往回填，填满为止：

```python
def fit_budget(history: list[dict], max_tokens: int, count_tokens) -> list[dict]:
    kept, used = [], 0
    for msg in reversed(history):  # 从最新往旧填
        t = count_tokens(msg["content"])
        if used + t > max_tokens:
            break
        kept.append(msg)
        used += t
    return list(reversed(kept))  # 恢复时间序
```

保留优先级的经验法则：

| 优先保留 | 优先压缩/丢弃 |
|----------|---------------|
| 最近几轮（当前指代所在） | 久远的寒暄、客套 |
| 用户明确陈述的事实/偏好 | 模型自己的长篇解释（可压成结论） |
| 未完成任务的状态 | 已被后续推翻的旧信息 |
| 用户的纠正（"不对，应该是…"） | 重复出现的相同内容 |

---

## 6. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 摘要把关键事实压没了 | 模型"忘记"用户身份/偏好 | 摘要 prompt 明确指定必留字段 |
| 摘要太频繁 | 每次溢出都调一次模型，慢且贵 | 攒够一批再压（批量摘要） |
| 滑窗砍掉了 tool_call 配对 | 出现孤立的 tool_result 报错 | 按"完整轮"裁剪，别拦腰切 |
| 摘要本身越滚越长 | 又变回 token 爆炸 | 给摘要本身设上限，超了再压一次 |
| 把长期记忆塞进会话 buffer | 会话一结束就丢 | 持久信息走长期记忆，别混进短期 buffer |

---

## 7. 下一步

- 📖 这只解决「单次会话」，跨会话怎么持久化与召回 → [03-storage-recall.md](./03-storage-recall.md)
- 📖 摘要其实是压缩的一种，系统讲压缩看下一章 → [05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md)
- 📖 短期 vs 长期记忆的分层观 → [01-short-vs-long.md](./01-short-vs-long.md)
- 📖 会话历史的 token 预算怎么分 → [01-foundations/05-context-budget.md](../01-foundations/05-context-budget.md)

## 参考资料

- LangChain，"How to add memory to chatbots"：https://python.langchain.com/docs/how_to/chatbots_memory/
- LangGraph 持久化与短期记忆：https://langchain-ai.github.io/langgraph/concepts/memory/
