# CE 05：Context Budget——把上下文当预算来分配

> **一句话**：上下文窗口不是「随便用的免费空间」，而是一笔有上限的预算（比如 200K）。Context Budget 思维就是：明确给 system / 历史 / 检索 / 工具 / 输出各划一块额度，超了就按优先级裁——保指令 > 保近期 > 压远期 > 砍检索。

---

## 1. 为什么要做预算

前几篇说清楚了三件事：窗口有上限（[02](./02-context-window.md)）、塞太满会 rot（[03](./03-context-rot.md)）、每个 token 都要钱要时间（[04](./04-cost-latency.md)）。

把这三件事合起来，结论只有一个：**上下文是稀缺资源，必须主动分配，不能让它「自然增长到撑爆」**。

不做预算的典型下场：

```
第 1 轮：上下文 3K，一切正常
第 10 轮：历史累积到 40K，开始变慢变贵
第 30 轮：历史 + 检索 + 工具结果 = 195K，逼近上限
第 31 轮：超窗 → 报错，或者框架随便砍掉最早的消息（可能砍掉了关键约定）
```

主动做预算，就是提前规定好「谁占多少、超了先砍谁」。

---

## 2. 预算表思维

拿一个 200K 窗口举例，先给输出留够，再把剩下的输入额度分块。一张典型的预算表：

| 块 | 预算 | 占比 | 说明 |
|----|------|------|------|
| 输出（output） | 8K | — | 先扣掉，剩 192K 给输入 |
| System / 指令 | 4K | ~2% | 固定，最高优先级，绝不裁 |
| 工具定义 | 8K | ~4% | 按需加载，用不到的不放 |
| 检索（retrieved） | 60K | ~31% | 弹性最大，rot 重灾区，宁缺毋滥 |
| 对话历史 | 100K | ~52% | 近期原样、远期压缩 |
| 当前用户输入 | 20K | ~10% | 用户贴的大段内容 |
| **输入合计** | **192K** | 100% | ≤ 200K - 8K 输出 |

注意这不是「平均分」，而是**按价值和弹性分**：指令小但不可压，检索大但最该砍。

---

## 3. 超预算时的优先级

当输入要爆了，按这个顺序处理（从「绝不动」到「最先砍」）：

```
优先级（从高到低，越下面越先被裁/压）
1. 保指令      System / 核心约束 —— 绝不裁，裁了整个任务跑偏
2. 保近期      最近几轮对话 —— 当前任务的直接上下文
3. 压远期      早期历史 —— 摘要 / 压缩成几句，而非整段删
4. 砍检索      召回片段 —— 降 top-k、rerank 后只留最相关的
```

口诀：**先砍「可再生」的（检索能重新召回）、再压「可摘要」的（历史能浓缩），最后才动「不可替代」的（指令、近期）。**

```python
# ❌ 框架默认的「砍最早消息」——可能砍掉了 system 里的关键约定
def naive_trim(messages, limit):
    while count_tokens(messages) > limit:
        messages.pop(0)   # 盲目砍头，危险
    return messages

# ✅ 按优先级分块预算
def budgeted_trim(system, recent, old, retrieved, input_budget):
    # 1. 指令固定占用，最高优先级
    used = count_tokens(system)
    # 2. 近期历史尽量全留
    used += count_tokens(recent)
    # 3. 远期历史：超了就摘要
    if used + count_tokens(old) > input_budget * 0.7:
        old = [summarize(old)]            # 压缩成一段摘要
    used += count_tokens(old)
    # 4. 检索：用剩下的预算，rerank 后能放几条放几条
    remaining = input_budget - used
    retrieved = fit_by_rerank(retrieved, remaining)  # 砍到塞得下
    return system + old + recent + retrieved
```

---

## 4. 一段「算预算占用」的代码

把当前各块的 token 占用算出来、对照预算上限报警，是 CE 的日常工具：

```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-4o")

def n_tokens(text: str) -> int:
    return len(enc.encode(text))

def context_report(blocks: dict[str, str], window=128_000, output=8_000):
    input_budget = window - output
    usage = {name: n_tokens(text) for name, text in blocks.items()}
    total = sum(usage.values())

    print(f"输入预算上限: {input_budget:,} tokens")
    print(f"当前输入合计: {total:,} tokens "
          f"({total / input_budget:.0%})")
    print("-" * 40)
    for name, n in sorted(usage.items(), key=lambda x: -x[1]):
        print(f"  {name:<12} {n:>8,}  ({n / total:.0%})")
    if total > input_budget:
        print(f"\n⚠️  超预算 {total - input_budget:,} tokens，需裁剪！")
    return usage

context_report({
    "system":    SYSTEM_PROMPT,
    "tools":     TOOLS_SCHEMA,
    "retrieved": "\n".join(retrieved_chunks),
    "history":   format_history(messages),
    "user":      user_input,
})
```

输出示例：

```
输入预算上限: 120,000 tokens
当前输入合计: 73,400 tokens (61%)
----------------------------------------
  history       42,100  (57%)
  retrieved     21,800  (30%)
  tools          6,200  (8%)
  system         2,900  (4%)
  user             400  (1%)
```

一眼就能看出：history 和 retrieved 是大头，要省先从这两块下手。

---

## 5. 实战建议

| 场景 | 预算策略 |
|------|----------|
| 多轮对话 Agent | 近期原样 + 远期滚动摘要，给历史设硬上限 |
| RAG 问答 | 检索块设固定预算，rerank 后填满为止，不超额 |
| 多工具 Agent | 工具定义按需加载，当前不相关的工具别放进窗口 |
| 长文档处理 | 单窗放不下就 map-reduce，别硬塞 |
| 成本敏感 | 整体预算往小压，能用 8K 解决就别开 128K |

---

## 6. 常见坑

| 坑 | 真相 |
|----|------|
| 「窗口没满就不用管」 | 没满也在花钱和拖延迟，预算意识要常在 |
| 「框架自带截断，省心」 | 默认砍最早消息，可能砍掉关键约定，要自定义优先级 |
| 「检索和历史平均分」 | 按弹性分：检索最该砍，指令绝不动 |
| 「远期历史直接删」 | 删丢上下文连贯性，应摘要压缩 |
| 「预算一次定死」 | 不同任务窗口构成不同，预算要随场景调 |

---

## 7. 下一步

- 📖 预算的指导原则：最少必要上下文 → [06-minimal-context.md](./06-minimal-context.md)
- 📖 成本与延迟的账本（预算的经济学依据） → [04-cost-latency.md](./04-cost-latency.md)
- 📖 为什么塞满会变差（预算要留余地的原因） → [03-context-rot.md](./03-context-rot.md)
- 📖 历史压缩 / 摘要的具体做法，见本手册后续 → 内存与历史管理章节

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic 上下文窗口文档：https://docs.anthropic.com/en/docs/build-with-claude/context-windows
