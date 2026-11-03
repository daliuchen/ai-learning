# Agent 的上下文累积问题

> Agent 每走一步，思考、工具调用和工具结果都会追加进同一条上下文，几十步之后，窗口就被它自己的轨迹塞满了。这是 Agent 比 chatbot 消耗上下文多得多的原因，也是它跑到后期变慢、变贵、变笨的根源。

---

## 1. Chatbot vs Agent：上下文增长的两种曲线

聊天机器人的上下文是人说一句、模型答一句，线性增长，人类每轮的输入通常也不长。Agent 不一样，它在一次任务内部自己循环很多步，每一步都往上下文里加东西。

```
Chatbot（每轮 ~1 次往返）          Agent（一次任务内 N 步循环）
turn1: user + assistant            step1: thought + tool_call + tool_result
turn2: user + assistant            step2: thought + tool_call + tool_result
...                                 ...
                                    stepN: thought + tool_call + tool_result
                                    final: answer
```

区别在于 Agent 的 N 步全部累积在同一条上下文里。一个查了 10 个网页、读了 5 个文件、跑了 8 次 grep 的编码 Agent，一次任务就能产生几十万 token 的轨迹，而用户只提了一个问题。

---

## 2. ReAct 轨迹是怎么膨胀的

ReAct（Reason + Act）循环每一步都把三段内容追加回上下文，下一步推理时，整段历史要重新发给模型：

```python
# ReAct 循环的本质：messages 只增不减
messages = [{"role": "user", "content": task}]

while not done:
    resp = client.messages.create(model="claude-sonnet-5-5", max_tokens=4096,
                                  messages=messages, tools=tools)
    messages.append({"role": "assistant", "content": resp.content})   # ① 思考 + tool_call

    for call in tool_calls(resp):
        result = run_tool(call)                                       # ② 执行
        messages.append({"role": "user", "content": tool_result(call, result)})  # ③ 结果回灌

    done = is_final(resp)
# 第 N 步时，messages 里塞着前 N-1 步的全部思考和全部工具结果
```

第 1 步的上下文也许只有 2K token，到第 20 步可能已经 80K。每一步都要把累积的全部历史重新发一遍，所以 input token 的总消耗接近 O(N²)：步数翻倍，总成本大约翻四倍。

---

## 3. 累积带来的三个代价

| 代价 | 机制 | 表现 |
|------|------|------|
| 变贵 | 每步重发全部历史，总 input ≈ Σ(每步窗口) ≈ O(N²) | 一个长任务的 token 账单几美元起，规模一上去就失控 |
| 变慢 | 输入越长，prefill 越慢，TTFT（首 token 延迟）越高 | 后期每步明显卡顿，用户能感觉到 Agent 越跑越慢 |
| 变笨 | 窗口被早期无关轨迹填满，触发 context rot / lost-in-the-middle | 忘掉最初目标、重复已做过的步骤、被旧工具结果带偏 |

三个代价里，变笨最隐蔽，后果也最严重。Anthropic 的文章也用 context rot（上下文腐烂）来称呼这种现象：窗口越满，模型分给任意单个 token 的注意力就越少。Agent 跑到第 30 步时，最初的任务目标早已埋在几万 token 的 grep 结果下面，它开始跑偏、绕圈，甚至忘了自己在干什么。

---

## 4. 为什么 Agent 比 chatbot 更吃上下文

有四个因素叠在一起放大了问题。工具结果体积大：一次网页抓取就是几千 token，一次 `ls -R` 或一个大 JSON 轻松上万，这些原始结果原样回灌进上下文（详见 [工具结果的上下文管理](./02-tool-results.md)）。步数不可控：复杂任务可能要 50 步以上，每步都在追加。思考链也占地方：开了 extended thinking 或 reasoning 之后，每步的思考 token 也进上下文。错误轨迹没人清理：失败的工具调用、报错、重试都留在历史里，既占空间，又可能误导后面的步骤（详见 [上下文相关的 Agent 失败模式](./06-failure-modes.md)）。

```
# ❌ 朴素 Agent：什么都累积，从不回收
messages.append(huge_grep_result)   # 50K token 的 grep 全文，永久占着窗口
messages.append(failed_attempt)     # 上一次试错的报错，留着继续误导
# 跑到第 40 步窗口爆了，要么报错，要么 context rot 把任务搞砸

# ✅ 思路：累积是默认行为，但必须主动管理（本章后续各篇逐一展开）
```

---

## 5. 应对累积的四条主线（本章地图）

累积本身消除不了，Agent 就是靠历史往前推进的。能做的是**主动管理这条不断变长的上下文**。本章后面几篇分别讲四种办法：

| 主线 | 做什么 | 对应篇 |
|------|--------|--------|
| 管工具结果 | 大结果落盘 / 摘要 / 截断，上下文只放句柄 | [工具结果的上下文管理](./02-tool-results.md) |
| 拆多 Agent | 子任务交给 sub-agent，主上下文不被细节淹没 | [多 Agent 间的上下文传递](./03-multi-agent-passing.md) |
| 隔离上下文 | 每个子任务独立窗口，互不污染 | [上下文隔离](./04-isolation.md) |
| State 外置 | 中间变量 / 待办放外部状态，不反复占窗口 | [State vs Context](./05-state-vs-context.md) |

上下文真涨到接近窗口上限时，还得靠压缩和裁剪把历史折叠成摘要，那是上一章的主题（见 [为什么要压缩上下文](../05-compaction/01-why-compact.md)）。这一章关心的是在涨到那一步之前，怎么从源头少累积一些，累积得更聪明一些。

---

## 下一步

- [工具结果的上下文管理](./02-tool-results.md)：工具结果太大怎么办，截断、摘要、落盘留句柄
- [State vs Context](./05-state-vs-context.md)：不是所有状态都该进上下文，State 外置
- 跨章：[为什么要压缩上下文](../05-compaction/01-why-compact.md)，窗口接近上限时的压缩与裁剪
- 跨章：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)，context rot 的机理和示意数据
