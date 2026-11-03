# 对话历史：上下文里增长最快的部分

> 在上下文的几个组成部分里，只有历史是一直在增长的。每多一轮，user、assistant、tool 三类消息都会往里堆，工具调用的记录尤其占 token。全部保留最简单，但最先撑爆窗口，所以截断和摘要迟早要做。这篇先看历史为什么会膨胀、涨得有多快，再比较几种处理办法。

---

## 1. 历史为什么会膨胀

system prompt 不变，检索结果每轮按需替换，只有历史在不停往上累加：

```text
第 1 轮上下文 = system + [u1, a1]
第 2 轮上下文 = system + [u1, a1, u2, a2]
第 N 轮上下文 = system + [u1, a1, ..., uN]   ← 越来越长
```

模型本身是无状态的。它能「记得上文」，全靠你每一轮把历史完整重发一遍，不重发它就忘了。所以历史长度随轮数线性增长；把每一轮重发的输入加起来，整个会话累计付费的输入 token 大致按轮数的平方增长。

| 部分 | 增长方式 | 趋势 |
|------|---------|------|
| system prompt | 不变 | 平 |
| 检索内容 | 每轮替换 | 平（有上限） |
| 对话历史 | 每轮追加 | 单调上升 |
| 当前 user 输入 | 只在当轮 | 波动 |

---

## 2. 三种消息角色都在占预算

历史不只是你问我答的文本，它由三类消息组成，每类都要占 token：

| 角色 | 内容 | token 体感 |
|------|------|-----------|
| `user` | 用户输入 | 通常最短 |
| `assistant` | 模型回复，加上工具调用请求 | 中等，带工具调用时变大 |
| `tool` | 回灌的工具执行结果 | 经常最大，一次 API 返回就是几千 token |

```python
# 一轮带工具调用的历史，实际是 4 条消息
messages = [
    {"role": "user", "content": "北京天气怎么样？"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "get_weather",
                                  "arguments": '{"city":"北京"}'}}]},
    {"role": "tool", "tool_call_id": "call_1",
     "content": '{"temp":28,"humidity":45,"wind":"...大段 JSON..."}'},  # ← 占大头
    {"role": "assistant", "content": "北京今天 28°C，湿度 45%。"},
]
```

很多人以为历史就是那些聊天气泡，其实在 agent 场景里，tool 消息经常是历史的大头（按下一节的估算，每轮 1460 token 里工具结果占 1200，约 82%）。一次搜索、SQL 查询或网页抓取的原始结果灌回来就是几千 token，几轮下来，历史里堆的大多是工具留下的结果。

---

## 3. 估一下增长有多快

```python
import tiktoken

enc = tiktoken.get_encoding("o200k_base")

def count(messages):
    return sum(len(enc.encode(str(m.get("content") or ""))) +
               len(enc.encode(str(m.get("tool_calls") or ""))) + 4
               for m in messages)

# 假设每轮：user 30 + assistant 150 + 一次工具调用 80 + tool 结果 1200 ≈ 1460 token
per_turn = 1460
for n in (1, 5, 10, 20):
    print(f"{n:>2} 轮 ≈ {n*per_turn:>6} token "
          f"({n*per_turn/128000:.0%} of 128K)")
# 20 轮 ≈ 29200 token，已是 128K 窗口的 23%，且只增不减
```

带工具的 agent 跑几十轮就会逼近窗口上限，主要原因是工具结果在不断沉积，对话本身占不了多少。

---

## 4. 全部保留、截断、摘要

处理历史的基本路线就三条，各有代价：

| 策略 | 做法 | 优点 | 代价 |
|------|------|------|------|
| 全部保留 | 每轮重发完整历史 | 信息无损，实现最简单 | 迟早超窗，成本随轮数线性上涨，还有 context rot |
| 截断 / 滑窗 | 只留最近 N 条或最近 K 个 token | 简单，预算可控 | 丢掉早期信息，比如最初的需求 |
| 摘要 / 压缩 | 把旧历史压成一段 summary | 保住要点，省 token | 多一次 LLM 调用，摘要可能丢细节或失真 |

```python
# ❌ 全保留，一直发到爆
messages = full_history + [{"role": "user", "content": q}]

# ✅ 截断：保 system + 摘要 + 最近 6 条
def build(system, summary, history, q, keep=6):
    msgs = [{"role": "system", "content": system}]
    if summary:
        msgs.append({"role": "system",
                     "content": f"[早期对话摘要]\n{summary}"})
    msgs += history[-keep:]   # 实际要按轮切，别把 tool_call 和它的结果拆开（见第 5 节）
    msgs.append({"role": "user", "content": q})
    return msgs
```

实际项目里一般是几种手段一起用：system 和关键事实固定保留，旧历史做成摘要，近期历史原样保留，工具结果做裁剪或过期淘汰。

> 截断、摘要、压缩的具体算法、触发时机、滑窗参数，第 05 章会专门讲，见 [为什么要压缩上下文（Why Compact）](../05-compaction/01-why-compact.md)。这一篇只需要记住：历史是增长最快的部分，不主动管理，它会最先撑爆预算。

---

## 5. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 把完整的工具原始结果原样塞回历史 | 几轮就超窗 | 先裁剪、提取再回灌（见 [工具上下文：定义与结果的双重膨胀](04-tools-context.md)） |
| 历史里残留早已被否决的方案或废弃数据 | 模型被旧信息带偏 | 摘要时明确标注「已废弃」 |
| 做了摘要，原始历史也留着 | 等于没省 | 用摘要替换原文，不要并存 |
| 每轮都重算摘要 | 浪费调用，还破坏缓存 | 超过阈值才做增量摘要 |
| 截断时把工具调用对（assistant tool_call + tool result）拆开 | API 报错或语义断裂 | 以轮为单位整段截断，保证配对完整 |

---

## 下一步

- 工具结果怎么裁剪后再回灌：[工具上下文：定义与结果的双重膨胀](04-tools-context.md)
- system 为什么要保持稳定（和历史正好相反）：[系统指令层：上下文里最稳定的一块](01-system-instructions.md)
- 历史压缩的完整策略：[为什么要压缩上下文（Why Compact）](../05-compaction/01-why-compact.md)
- 长期记忆和短期历史的区别：[短期记忆 vs 长期记忆](../04-memory/01-short-vs-long.md)
