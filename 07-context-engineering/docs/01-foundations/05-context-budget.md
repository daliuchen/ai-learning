# CE 05：按预算分配上下文（Context Budget）

> 上下文窗口是一笔有上限的预算，比如给自己定 200K。做预算的意思是：给 system、历史、检索、工具、输出各划一块额度，超了就按优先级裁剪，顺序是保指令、保近期、压远期、砍检索。这篇讲怎么列这张预算表，以及超预算时先动谁。

---

## 1. 为什么要做预算

前面三篇讲了三件事：窗口有上限（[上下文窗口到底是什么](./02-context-window.md)），塞太满会 rot（[Context Rot 与 Lost in the Middle](./03-context-rot.md)），每个 token 都要花钱、花时间（[上下文的成本与延迟模型](./04-cost-latency.md)）。

三件事合起来，结论是：上下文是稀缺资源，必须主动分配，不能任它自然增长到撑爆。

不做预算，通常会这样收场：

```
第 1 轮：上下文 3K，一切正常
第 10 轮：历史累积到 40K，开始变慢变贵
第 30 轮：历史 + 检索 + 工具结果 = 195K，逼近上限
第 31 轮：超窗 → 报错，或者框架随便砍掉最早的消息（可能砍掉了关键约定）
```

做预算，就是提前定好每一块占多少、超了先砍谁。

---

## 2. 列一张预算表

以 200K 的预算为例（现在主力模型的窗口已经到 1M，但出于成本和质量，很多场景还是会给自己定一个更小的预算）。先给输出留够，再把剩下的输入额度分块：

| 块 | 预算 | 占比 | 说明 |
|----|------|------|------|
| 输出（output） | 8K | — | 先扣掉，剩 192K 给输入 |
| System / 指令 | 4K | ~2% | 固定，优先级最高，不裁 |
| 工具定义 | 8K | ~4% | 按需加载，用不到的不放 |
| 检索（retrieved） | 60K | ~31% | 弹性最大，也最容易 rot，宁缺毋滥 |
| 对话历史 | 100K | ~52% | 近期原样保留，远期压缩 |
| 当前用户输入 | 20K | ~10% | 用户贴进来的大段内容 |
| 输入合计 | 192K | 100% | ≤ 200K - 8K 输出 |

这张表不是平均分的，是按价值和弹性分的：指令占得少但压不得，检索占得多但最该砍。

---

## 3. 超预算时的优先级

输入快要超了，就按下面的顺序处理，越往下越先被裁或压：

```
优先级（从高到低，越下面越先被裁/压）
1. 保指令      System / 核心约束 —— 绝不裁，裁了整个任务跑偏
2. 保近期      最近几轮对话 —— 当前任务的直接上下文
3. 压远期      早期历史 —— 摘要 / 压缩成几句，而非整段删
4. 砍检索      召回片段 —— 降 top-k、rerank 后只留最相关的
```

背后的道理是：先砍能再生的（检索可以重新召回），再压能摘要的（历史可以浓缩），最后才碰不可替代的（指令和近期对话）。

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

## 4. 一段统计预算占用的代码

把各块当前的 token 占用算出来，和预算上限比一比，超了就报警。这个小工具我在调上下文时经常用：

```python
import tiktoken
enc = tiktoken.get_encoding("o200k_base")

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

history 和 retrieved 占了大头，要省就先从这两块下手。

---

## 5. 不同场景怎么定预算

| 场景 | 预算策略 |
|------|----------|
| 多轮对话 Agent | 近期原样，远期滚动摘要，给历史设硬上限 |
| RAG 问答 | 检索块设固定预算，rerank 后填满为止，不超额 |
| 多工具 Agent | 工具定义按需加载，当前用不上的工具别放进窗口 |
| 长文档处理 | 一个窗口放不下就 map-reduce，别硬塞 |
| 成本敏感 | 整体预算往小压，8K 能解决就别开 128K |

---

## 6. 常见坑

| 坑 | 实际情况 |
|----|------|
| 「窗口没满就不用管」 | 没满也在花钱、拖延迟，预算要一直盯着 |
| 「框架自带截断，省心」 | 默认砍最早的消息，可能把关键约定砍掉，要自己定优先级 |
| 「检索和历史平均分」 | 按弹性分：检索最该砍，指令不能动 |
| 「远期历史直接删掉」 | 会丢掉上下文的连贯性，应该摘要压缩 |
| 「预算一次定死」 | 不同任务的窗口构成不同，预算要跟着场景调 |

---

## 7. 下一步

- 做预算时的指导原则：[最少必要上下文原则](./06-minimal-context.md)
- 成本和延迟的账本，也就是做预算的经济依据：[上下文的成本与延迟模型](./04-cost-latency.md)
- 塞满为什么会变差，也是预算要留余地的原因：[Context Rot 与 Lost in the Middle](./03-context-rot.md)
- 历史压缩和摘要的具体做法：[摘要式压缩](../05-compaction/02-summarization.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic 上下文窗口文档：https://platform.claude.com/docs/en/build-with-claude/context-windows
