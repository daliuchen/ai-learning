# 对话历史：上下文里增长最快的部分

> **一句话**：历史是上下文里**唯一会单调增长**的部分——每多一轮，user+assistant+tool 三类消息都往里堆，工具调用记录尤其能吃 token；保留全部最简单但最早爆窗口，截断/摘要是必然的取舍。

---

## 1. 历史为什么会膨胀

system prompt 是常量，检索结果是按需替换，**只有历史在不停往上累加**：

```text
第 1 轮上下文 = system + [u1, a1]
第 2 轮上下文 = system + [u1, a1, u2, a2]
第 N 轮上下文 = system + [u1, a1, ..., uN]   ← 越来越长
```

关键认知：**模型本身是无状态的**。所谓"记得上文"，全靠你每一轮把历史完整重发一遍。不重发，模型就失忆。于是历史长度 ≈ 累计对话量，线性甚至超线性增长。

| 部分 | 增长方式 | 趋势 |
|------|---------|------|
| system prompt | 不变 | 平 |
| 检索内容 | 每轮替换 | 平（有上限） |
| **对话历史** | **每轮追加** | **单调上升** ⬆️ |
| 当前 user 输入 | 当轮 | 波动 |

---

## 2. 三种消息角色，都在占预算

历史不只是"你问我答"的文本，它由三类消息构成，每类都吃 token：

| 角色 | 内容 | token 体感 |
|------|------|-----------|
| `user` | 用户输入 | 通常最短 |
| `assistant` | 模型回复 **+ 工具调用请求** | 中等，含工具调用时变大 |
| `tool` | 工具执行结果回灌 | **常常最大**（一次 API 返回几千 token） |

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

新手常以为"历史就是聊天气泡"，实际上 **agent 场景里 tool 消息往往占历史 70%+**。一次搜索/SQL/网页抓取的原始结果灌回来就是几千 token，几轮下来历史里全是工具的"残渣"。

---

## 3. 估一下增长有多快

```python
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")

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

带工具的 agent，**几十轮就能逼近窗口上限**——不是因为对话内容多，而是工具结果不断沉淀。

---

## 4. 保留全部 vs 截断 vs 摘要

历史管理只有三条基本路线，全是取舍：

| 策略 | 做法 | 优点 | 代价 |
|------|------|------|------|
| **全保留** | 每轮重发完整历史 | 信息无损、实现最简单 | 必爆窗口、成本随轮数线性涨、context rot |
| **截断 / 滑窗** | 只留最近 N 条或最近 K token | 简单、可控预算 | 丢早期信息（如最初的需求） |
| **摘要 / 压缩** | 把旧历史压成一段 summary | 保留要点、省 token | 多一次 LLM 调用、摘要可能丢细节/失真 |

```python
# ❌ 全保留，一直发到爆
messages = full_history + [{"role": "user", "content": q}]

# ✅ 截断：保 system + 摘要 + 最近 6 条
def build(system, summary, history, q, keep=6):
    msgs = [{"role": "system", "content": system}]
    if summary:
        msgs.append({"role": "system",
                     "content": f"[早期对话摘要]\n{summary}"})
    msgs += history[-keep:]
    msgs.append({"role": "user", "content": q})
    return msgs
```

实战里通常是**组合拳**：固定保留 system + 关键事实，旧历史摘要化，近期历史原样保留，工具结果做裁剪/过期淘汰。

> 截断/摘要/压缩的**具体算法、触发时机、滑窗参数**是一整章的内容，见 [05-compaction](../05-compaction/01-why-compact.md)。本篇只需立住一个认知：**历史是增长最快的部分，必须主动管理，否则它会先撑爆预算**。

---

## 5. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 把完整工具原始结果原样塞回历史 | 几轮就爆窗口 | 工具结果先裁剪/提取再回灌（见 [04-tools-context.md](04-tools-context.md)） |
| 历史里残留早被否决的方案/废弃数据 | 模型被旧信息带偏 | 摘要时显式标注"已废弃" |
| 摘要后把原始历史也留着 | 等于没省 | 摘要替换原文，不要并存 |
| 每轮重算摘要 | 浪费调用、破坏缓存 | 只在超阈值时增量摘要 |
| 截断时连工具调用对（assistant tool_call + tool result）拆散 | API 报错/语义断裂 | 按"轮"为单位整段截断，保持配对完整 |

---

## 6. 小结

- 历史是上下文里唯一**单调增长**的部分，是预算压力的主要来源。
- 它由 user / assistant / tool 三类消息构成，agent 场景下 **tool 结果常占大头**。
- 模型无状态，记忆全靠重发历史——所以历史越长，每轮成本越高。
- 三条路线：全保留（会爆）、截断（丢信息）、摘要（失真+多调用），实战靠组合。
- 主动管理历史是上下文工程的日常工作，不是可选项。

---

## 下一步

- 工具结果如何裁剪后再回灌：[04-tools-context.md](04-tools-context.md)
- system 为什么要保持稳定（与历史相反）：[01-system-instructions.md](01-system-instructions.md)
- 历史压缩的完整策略：[../05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md)
- 长期记忆 vs 短期历史：[../04-memory/01-short-vs-long.md](../04-memory/01-short-vs-long.md)
