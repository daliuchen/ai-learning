# Agent 的上下文累积问题

> **一句话**：Agent 每走一步（思考 + 工具调用 + 工具结果）都把内容**追加**进同一条上下文，几十步后窗口就被自己的轨迹塞满——这就是为什么 Agent 比 chatbot 吃上下文吃得凶得多，也是后期变慢、变贵、变笨的根因。

---

## 1. Chatbot vs Agent：上下文增长的两种曲线

聊天机器人的上下文是「人说一句、模型答一句」线性增长，且每轮人类输入通常不长。Agent 不一样：它在**一次任务内部**自己循环很多步，每步都往上下文里塞东西。

```
Chatbot（每轮 ~1 次往返）          Agent（一次任务内 N 步循环）
turn1: user + assistant            step1: thought + tool_call + tool_result
turn2: user + assistant            step2: thought + tool_call + tool_result
...                                 ...
                                    stepN: thought + tool_call + tool_result
                                    final: answer
```

关键差异：**Agent 的 N 步全在同一条上下文里累积**。一个查 10 个网页、读 5 个文件、跑 8 次 grep 的编码 Agent，单次任务就能产生几十万 token 的轨迹——而用户只问了一句话。

---

## 2. ReAct 轨迹是怎么膨胀的

经典 ReAct（Reason + Act）循环每一步都把三段内容追加回上下文，下一步推理时整段历史都得重新喂进去：

```python
# ReAct 循环的本质：messages 只增不减
messages = [{"role": "user", "content": task}]

while not done:
    resp = client.messages.create(model="claude-sonnet-4-6", messages=messages, tools=tools)
    messages.append({"role": "assistant", "content": resp.content})   # ① 思考 + tool_call

    for call in tool_calls(resp):
        result = run_tool(call)                                       # ② 执行
        messages.append({"role": "user", "content": tool_result(call, result)})  # ③ 结果回灌

    done = is_final(resp)
# 第 N 步时，messages 里塞着前 N-1 步的全部思考和全部工具结果
```

第 1 步上下文也许 2K token，第 20 步可能已经 80K。而**每一步都要把累积的全部历史重新发给模型**，于是 input token 的消耗不是线性，而是接近 O(N²)——步数翻倍，总成本翻四倍。

---

## 3. 累积带来的三个具体代价

| 代价 | 机制 | 表现 |
|------|------|------|
| **变贵** | 每步重发全部历史，总 input ≈ Σ(每步窗口) ≈ O(N²) | 一个长任务的 token 账单几美元起，规模化即失控 |
| **变慢** | 输入越长，prefill 越慢，TTFT（首 token 延迟）越高 | 后期每步明显卡顿，用户感知到 Agent 越跑越慢 |
| **变笨** | 窗口被早期无关轨迹填满，触发 context rot / lost-in-the-middle | 忘掉最初目标、重复已做过的步骤、被旧工具结果带偏 |

「变笨」最隐蔽也最致命。Anthropic 把它叫 **context rot（上下文腐烂）**：窗口越满，模型对任意单个 token 的注意力越摊薄。一个 Agent 跑到第 30 步，最初的任务目标早被埋在几万 token 的 grep 结果下面，于是它开始跑偏、绕圈、甚至忘了自己在干嘛。

---

## 4. 为什么 Agent 比 chatbot「更吃上下文」

四个叠加的放大器：

- **工具结果体积大**：一次网页抓取就是几千 token，一次 `ls -R` 或大 JSON 返回轻松上万——这些原始结果原样回灌进上下文（详见 [02-tool-results.md](./02-tool-results.md)）。
- **步数不可控**：复杂任务可能 50+ 步，每步都追加。
- **思考链占地**：开了 extended thinking / reasoning 后，每步的思考 token 也进上下文。
- **错误轨迹不清理**：失败的工具调用、报错、重试全留在历史里，既占地又可能误导后续（详见 [06-failure-modes.md](./06-failure-modes.md)）。

```
# ❌ 朴素 Agent：什么都累积，从不回收
messages.append(huge_grep_result)   # 50K token 的 grep 全文，永久占着窗口
messages.append(failed_attempt)     # 上一次试错的报错，留着继续误导
# 跑到第 40 步窗口爆了，要么报错，要么 context rot 把任务搞砸

# ✅ 思路：累积是默认行为，但必须主动管理（本章后续各篇逐一展开）
```

---

## 5. 应对累积的四条主线（本章地图）

累积本身无法消除——Agent 就是要靠历史推进。能做的是**主动管理这条不断增长的上下文**。本章给四把刀，对应后续各篇：

| 主线 | 做什么 | 对应篇 |
|------|--------|--------|
| **管工具结果** | 大结果落盘 / 摘要 / 截断，上下文只放句柄 | [02-tool-results.md](./02-tool-results.md) |
| **拆多 Agent** | 子任务交给 sub-agent，主上下文不被细节淹没 | [03-multi-agent-passing.md](./03-multi-agent-passing.md) |
| **隔离上下文** | 每个子任务独立窗口，互不污染 | [04-isolation.md](./04-isolation.md) |
| **State 外置** | 中间变量 / 待办放外部状态，不反复占窗口 | [05-state-vs-context.md](./05-state-vs-context.md) |

此外，当上下文确实涨到逼近窗口上限时，还要靠**压缩 / 裁剪**把历史折叠成摘要——那是上一章的主题（见 [../05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md)）。本章关注的是「在涨到那一步之前，怎么从源头少累积、累积得更聪明」。

---

## 下一步

- [02-tool-results.md](./02-tool-results.md)：工具结果太大怎么办——截断、摘要、落盘留句柄
- [05-state-vs-context.md](./05-state-vs-context.md)：不是所有状态都该进上下文，State 外置
- 跨章：[../05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md) 窗口逼近上限时的压缩裁剪
- 跨章：[../07-long-context/01-long-context-models.md](../07-long-context/01-long-context-models.md) 长窗口模型与 context rot 实测
