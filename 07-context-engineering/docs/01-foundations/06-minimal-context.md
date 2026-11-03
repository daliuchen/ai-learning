# CE 06：最少必要上下文原则

> 上下文工程最基本的原则是最少必要上下文（minimal necessary context）：放进窗口的，应该是恰好够完成任务的最少信息，而不是越多越好。多余的上下文会推高成本、拉长延迟，还会稀释注意力、降低准确率。后面整本手册的做法，大多可以看成这条原则在不同环节的落地。

---

## 1. 反直觉，但是对的

很多人的第一反应是：多给点上下文，模型掌握的信息越全，答得应该越好。

前几篇已经从三个方向说明了情况正好相反：

- [Context Rot 与 Lost in the Middle](./03-context-rot.md)：塞得太多，关键信息被稀释、被干扰，召回反而变差。
- [上下文的成本与延迟模型](./04-cost-latency.md)：每个多余的 token 都要付钱，都会拖慢 TTFT。
- [按预算分配上下文（Context Budget）](./05-context-budget.md)：窗口是有限资源，多塞一块就要挤掉另一块。

所以目标是恰好够用，而不是信息最全。多出来的每一段，既花钱又拖时间，还可能拉低准确率。

---

## 2. 多余上下文的三个代价

| 代价 | 机制 | 后果 |
|----|------|------|
| 成本上涨 | input token 线性计费 | 月账单翻几倍（见 [上下文的成本与延迟模型](./04-cost-latency.md) 里的估算表） |
| 延迟增加 | prefill 随输入长度增长 | TTFT 变慢，体验变差 |
| 注意力被稀释，准确率下降 | attention 被摊薄，还引入了噪声和干扰 | lost in the middle，召回到错的段落 |

第三条最反直觉：多给信息，模型可能答得更差。一份干净的 5 段检索结果，往往比混着噪声的 50 段效果好。

---

## 3. 一个对比

同样问「我们的退款周期是几天」，两种喂法：

```python
# ❌ 「以防万一全给」——塞了整个知识库
context = load_entire_knowledge_base()   # 80K tokens，什么都有
prompt = f"{context}\n\n问题：退款周期几天？"
# 成本高、慢、且答案埋在 80K 里可能召回不到 → 翻车

# ✅ 「恰好够用」——只给相关那一段
chunk = retrieve_top1("退款周期")        # 200 tokens，就是政策那段
prompt = f"{chunk}\n\n问题：退款周期几天？"
# 便宜、快、没有干扰内容，答对的把握大得多
```

token 省了约 99%，准确率反而更高。

---

## 4. 怎么判断哪些能砍

逐块过一遍，问自己：这块对完成当前这个任务是必需的吗？

| 上下文块 | 判断标准 | 能砍的信号 |
|----------|----------|-----------|
| System 指令 | 是否影响行为或边界 | 重复啰嗦的说明、用不到的规则 |
| 工具定义 | 当前任务会不会用到 | 这一轮明显用不到的工具 schema |
| 检索片段 | 和问题的相关度 | rerank 分数低、内容重复、和答案无关 |
| 对话历史 | 当前这一轮是否依赖它 | 远期的、已经解决的、和当前话题无关的轮次 |
| 用户贴的内容 | 是否真需要全文 | 大段日志或代码里只有几行相关 |

一个好用的判断方法：删掉某一块之后任务仍然能正确完成，它就是多余的。拿不准就做 A/B，带和不带各跑一遍评测集，看准确率有没有掉。

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

## 5. 最少不等于不够

也别砍过头。最少必要包括两个词，最少和必要缺一不可。砍到任务做不成，那是删过头了；真正必需的约束、关键的检索结果、近期的上下文，都得留着。

判断的标准始终是任务能不能正确完成，而不是 token 数越少越好。所以这条原则离不开评测：没有评测集，就不知道砍到哪一步开始伤准确率。

---

## 6. 这条原则在后面各章的体现

| 章节主题 | 怎么体现最少必要 |
|----------|----------------------|
| 上下文解剖 | 每块只放必需的，去掉冗余 |
| RAG / 检索 | 用 rerank 和 top-k 控制，召回质量优先 |
| 记忆与历史管理 | 远期摘要压缩，只留必要的轮次 |
| Agent 上下文 | 工具按需加载，工具结果用完即裁 |
| 压缩与摘要 | 用更少的 token 表达同样的信息 |
| Prompt caching | 在必要的前提下，省下固定前缀的成本 |

后面各章做的事，都是在任务做对的前提下，把上下文压到最少。

---

## 7. 常见坑

| 坑 | 实际情况 |
|----|------|
| 「多给点总没坏处」 | 成本涨、延迟增、准确率降 |
| 「最少就是越短越好」 | 要最少且必要，砍掉必需信息会出错 |
| 「凭感觉判断该砍什么」 | 用评测集验证，删了准确率不掉才算多余 |
| 「一次砍到位」 | 迭代着裁，边砍边测 |

---

## 8. 下一步

- 落地的第一站，拆开上下文的各块：[系统指令层：上下文里最稳定的一块](../02-anatomy/01-system-instructions.md)
- 回看预算分配，最少必要的量化版：[按预算分配上下文（Context Budget）](./05-context-budget.md)
- 回看成本账本，多余 token 的代价：[上下文的成本与延迟模型](./04-cost-latency.md)
- 回到起点：[什么是上下文工程（Context Engineering）](./01-what-is-context-engineering.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- 跨手册关联，最少必要和「评测先于 prompt」是一个思路：[先建评测，再写 Prompt](../../../04-prompt-engineering/docs/01-foundations/05-eval-first.md)
