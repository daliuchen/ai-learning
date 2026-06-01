# CE 06：核心原则——最少必要上下文

> **一句话**：上下文工程的第一性原则是「最少必要上下文」（minimal necessary context）——**塞进窗口的，应该是恰好够完成任务的最少信息，不是越多越好**。多余的上下文有三宗罪：涨成本、增延迟、稀释注意力降准确率。这条原则贯穿整本手册。

---

## 1. 反直觉，但是对的

新手的本能是：「我多给点上下文，模型信息越全，答得越好吧？」

错。前几篇已经分别证明了反面：

- [03 Context Rot](./03-context-rot.md)：塞太多，关键信息被稀释、被干扰，召回反而变差。
- [04 成本与延迟](./04-cost-latency.md)：每个多余 token 都要付费、都拖慢 TTFT。
- [05 预算](./05-context-budget.md)：窗口是有限资源，多塞一块就挤掉另一块。

合起来就是这条原则：**目标不是「信息最全」，而是「恰好够用」**。多出来的每一段，都是在花钱、拖时间、还降准确率——三输。

> 把它当成 CE 版的奥卡姆剃刀：**如无必要，勿增上下文。**

---

## 2. 多余上下文的三宗罪

| 罪 | 机制 | 后果 |
|----|------|------|
| 涨成本 | input token 线性计费 | 月账单翻几倍（见 [04](./04-cost-latency.md) 的估算表） |
| 增延迟 | prefill 随输入长度增长 | TTFT 变慢，体验崩 |
| 稀释注意力 → 降准确率 | attention 被摊薄 + 引入噪声/干扰 | lost in the middle，召回错段 |

注意第三宗罪最反直觉：**多给信息可能让模型答得更差**。一个干净的 5 段检索，往往打得过混了噪声的 50 段。

---

## 3. 一个对比

同样问「我们的退款周期是几天」，两种喂法：

```python
# ❌ 「以防万一全给」——塞了整个知识库
context = load_entire_knowledge_base()   # 80K tokens，啥都有
prompt = f"{context}\n\n问题：退款周期几天？"
# 成本高、慢、且答案埋在 80K 里可能召回不到 → 翻车

# ✅ 「恰好够用」——只给相关那一段
chunk = retrieve_top1("退款周期")        # 200 tokens，就是政策那段
prompt = f"{chunk}\n\n问题：退款周期几天？"
# 便宜、快、信噪比 100%，稳答对
```

省了 ~99% 的 token，准确率还更高。这就是「最少必要」的威力。

---

## 4. 怎么判断哪些能砍

逐块审视，问自己「这块对完成**当前这个任务**是必需的吗」：

| 上下文块 | 判断标准 | 能砍的信号 |
|----------|----------|-----------|
| System 指令 | 是否影响行为/边界 | 重复啰嗦的说明、用不到的规则 |
| 工具定义 | 当前任务会不会用到 | 本轮明显用不到的工具 schema |
| 检索片段 | 与问题相关度 | rerank 分数低、内容重复、与答案无关 |
| 对话历史 | 当前轮是否依赖 | 远期、已解决、与当前话题无关的轮次 |
| 用户贴的内容 | 是否真的要全文 | 大段日志/代码里只有几行相关 |

一个实用启发式：**如果删掉某块，任务仍能正确完成，那它就是多余的**。拿不准就做 A/B——带与不带各跑评测集，看准确率有没有掉。

```python
# 用「删了还对不对」来验证某块是否必要
def is_necessary(block_name, eval_set):
    with_block = run_eval(eval_set, include=block_name)
    without    = run_eval(eval_set, exclude=block_name)
    if without.accuracy >= with_block.accuracy - 0.01:
        print(f"✅ '{block_name}' 可砍：删了准确率没掉，省 token")
    else:
        print(f"⚠️ '{block_name}' 要留：删了掉 "
              f"{with_block.accuracy - without.accuracy:.1%}")
```

---

## 5. 「最少」不等于「不够」

别矫枉过正。最少必要 = **最少 + 必要**，两个词都要：

- 砍到任务做不成了，那是「过度删减」，不是 CE。
- 真正必需的约束、关键检索、近期上下文，该留必须留。

判断的锚点永远是**任务能否正确完成**，不是单纯追求 token 数最小。所以这条原则离不开评测——没有评测集，你根本不知道砍到哪一步开始伤准确率。

---

## 6. 这条原则怎么贯穿全手册

后面每一章本质上都是「最少必要上下文」的具体落地：

| 章节主题 | 怎么体现「最少必要」 |
|----------|----------------------|
| 上下文解剖 | 每块只放必需的，去冗余 |
| RAG / 检索 | rerank + top-k 控制，召回质量优先 |
| 记忆与历史管理 | 远期摘要压缩，只留必要轮次 |
| Agent 上下文 | 工具按需加载，工具结果用完即裁 |
| 压缩与摘要 | 用更少 token 表达同样信息 |
| Prompt caching | 在「必要」前提下省固定前缀的成本 |

记住一句话就够了：**Context Engineering 的全部努力，都是在「任务做得对」的前提下，把上下文压到最少。**

---

## 7. 常见坑

| 坑 | 真相 |
|----|------|
| 「多给点总没坏处」 | 三宗罪：涨成本、增延迟、降准确率 |
| 「最少 = 越短越好」 | 是「最少且必要」，砍掉必需信息会翻车 |
| 「凭感觉判断该砍啥」 | 用评测集验证：删了准确率不掉才算多余 |
| 「一次砍到位」 | 迭代式裁剪，边砍边测 |

---

## 8. 下一步

- 📖 落地第一站：上下文各块的解剖 → [02-anatomy/01-system-instructions.md](../02-anatomy/01-system-instructions.md)
- 📖 回看预算分配（最少必要的量化版） → [05-context-budget.md](./05-context-budget.md)
- 📖 回看成本账本（多余 token 的代价） → [04-cost-latency.md](./04-cost-latency.md)
- 📖 回到起点：什么是上下文工程 → [01-what-is-context-engineering.md](./01-what-is-context-engineering.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- 跨手册关联（最少必要与「评测先于 prompt」一脉相承）：[04-prompt-engineering/01-foundations/05-eval-first.md](../../../04-prompt-engineering/docs/01-foundations/05-eval-first.md)
