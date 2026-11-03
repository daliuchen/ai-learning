# CE 03：Context Rot 与 Lost in the Middle

> 上下文不是越长越好。塞得越满，模型对中间那段内容的召回越差（lost in the middle），无关内容还会稀释注意力、带进干扰，这种长上下文下的质量退化叫 Context Rot。这篇讲它的来龙去脉和应对办法。结论有点反直觉：精挑 5 段放对位置，常常比塞 50 段效果好。

---

## 1. Lost in the Middle 现象

2023 年斯坦福和伯克利的论文 *"Lost in the Middle: How Language Models Use Long Contexts"* 做过一个很有名的实验：

把一篇包含正确答案的文档放进一堆文档里，改变它的位置，看模型的答对率怎么变。

```
答对率（经验性示意，U 型曲线）
高 │■                            ■
   │ ■                        ■
   │   ■                   ■
   │     ■■            ■■
低 │        ■■■■■■■■■■
   └────────────────────────────
    第1段   ...  中间  ...   最后一段
   答案在开头→高   答案在中间→明显掉   答案在结尾→高
```

结果是一条 U 型曲线：关键信息放在上下文中间时，召回率明显低于放在头尾。

到 2026 年，模型的长上下文能力进步很大，但这个效应没有完全消失。上下文很长（几十 K 以上）、干扰项又多的时候，中间的内容照样吃亏，别因为模型号称支持 1M 上下文就以为不用管了。官方文档也是这么说的：Anthropic 的 Context windows 文档提到，token 越多，准确率和召回越会下降（context rot），所以挑选什么放进窗口和窗口大小同样重要；Gemini 的 Long context 文档则说，单针检索能做到约 99%，但多针检索的准确率会下降，而且随上下文不同差异很大。

---

## 2. Needle in a Haystack 测试

业界评估长上下文召回的标准做法叫大海捞针（needle in a haystack）：

1. 准备一大段无关文本作为 haystack，比如 100K token 的小说或文档。
2. 在某个位置插入一句明确的事实作为 needle，比如「2026 年的幸运数字是 73」。
3. 在末尾提问：「2026 年的幸运数字是几？」
4. 改变 needle 的插入深度（0%、25%、50%、75%、100%）和总长度，画出召回热力图。

```python
# 一个最小化的 needle-in-haystack 测试骨架
import anthropic

client = anthropic.Anthropic()
NEEDLE = "记住：2026 年的幸运数字是 73。"
QUESTION = "2026 年的幸运数字是几？只回答数字。"

def build_context(haystack: str, depth: float) -> str:
    """把 needle 插到 haystack 的 depth 百分比位置"""
    cut = int(len(haystack) * depth)
    return haystack[:cut] + "\n" + NEEDLE + "\n" + haystack[cut:]

def probe(haystack: str, depth: float) -> str:
    ctx = build_context(haystack, depth)
    resp = client.messages.create(
        model="claude-opus-5-5",
        max_tokens=2048,   # Claude 5.x 默认自适应思考，思考 token 也算在 max_tokens 里，别设太小
        messages=[{"role": "user",
                   "content": f"{ctx}\n\n{QUESTION}"}],
    )
    # Claude 5.x 默认开 adaptive thinking，第一个块可能是 thinking，要取 text 块
    return next(b.text for b in resp.content if b.type == "text")

# 跑多个深度，看哪个位置会丢
big_doc = open("haystack.txt", encoding="utf-8").read()  # 事先准备的长文本
for d in (0.0, 0.25, 0.5, 0.75, 1.0):
    print(d, "->", probe(big_doc, d))
```

通常的结果是：深度 0% 和 100% 召回稳定，50% 附近偶尔会丢；上下文越长、要同时记住的 needle 越多，中间越容易出错。

---

## 3. 越长不一定越好的三个原因

| 原因 | 机制 | 后果 |
|------|------|------|
| 噪声 | 无关内容混进来 | 模型可能引用错的段落，张冠李戴 |
| 干扰（distractor） | 有看起来像答案、其实不是的片段 | 直接答错 |
| 稀释 | n 越大，注意力被摊得越薄 | 关键信息的分量变小，召回下降 |

干扰这一项最该留意。Cuconasu 等人在 RAG 场景下的实验（The Power of Noise, 2024）发现，和问题相关但不含答案的片段比随机的无关片段伤害更大；Chroma 的 Context Rot 报告也观察到，干扰项会让长输入下的退化更明显。RAG 召回的 top-50 里混进几条相关但错误的片段，效果可能还不如召回 5 条干净的结果。所以召回的质量比数量重要。

---

## 4. 哪些场景最容易受影响

- RAG 问答：不加筛选地召回 top-k=50，关键片段被埋在中间，结果答非所问。
- 长文档总结：文档中段的要点被漏掉。
- 长对话 Agent：早期的约定（比如「用户偏好中文」）到第 30 轮被忘了。
- 多工具 Agent：一堆工具返回结果堆在中段，模型抓不到最关键的那条。

---

## 5. 缓解手段

| 手段 | 怎么做 | 对应原则 |
|------|--------|----------|
| 重要信息放头尾 | 关键约束放 system，最相关的检索片段放最前或最后 | 位置本身就是信号 |
| 减少无关内容 | 召回 top-5 而不是 top-50，先 rerank 再喂 | 最少必要上下文 |
| 去重、压缩 | 合并重复片段，摘要远期历史 | 提高信噪比 |
| 重述关键指令 | 在长上下文末尾再贴一遍核心问题和约束 | 抵消中间的衰减 |
| 分块处理 | 太长就 map-reduce 或分多次调用，别硬塞进一个窗口 | 绕开 rot |

代码里最常用的放位置技巧，是把最相关的片段放在末尾，紧挨着问题：

```python
# ❌ 相关性最高的片段被埋在中间
context = "\n\n".join(retrieved_chunks)  # 顺序随便

# ✅ rerank 后，最相关的放最后（紧挨问题，召回最稳）
ranked = rerank(query, retrieved_chunks)        # 按相关度排序
top = ranked[:5]                                # 只取 top-5，别贪多
context = "\n\n".join(reversed(top))            # 最相关放最后
prompt = f"{context}\n\n问题：{query}"
```

---

## 6. 常见坑

| 坑 | 实际情况 |
|----|------|
| 「1M 上下文 = 随便塞」 | rot 依然存在，长不等于准 |
| 「召回越多越保险」 | 噪声和干扰会拉低准确率，质量优先 |
| 「关键约束写在 system 开头就够了」 | 超长上下文里，开头的约束也会衰减，末尾再贴一遍更稳 |
| 「needle 测试能召回就没问题」 | 单个 needle 很简单，多个 needle 或要综合多处信息时才见分晓 |

---

## 7. 下一步

- 长上下文除了召回变差，还又贵又慢：[上下文的成本与延迟模型](./04-cost-latency.md)
- 到底该塞多少，把上下文当预算分配：[按预算分配上下文（Context Budget）](./05-context-budget.md)
- 最根本的原则：[最少必要上下文原则](./06-minimal-context.md)
- 回看窗口和 attention 机制：[上下文窗口到底是什么](./02-context-window.md)

## 参考资料

- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts" (2023): https://arxiv.org/abs/2307.03172
- Cuconasu et al., "The Power of Noise: Redefining Retrieval for RAG Systems" (2024): https://arxiv.org/abs/2401.14887
- Chroma, "Context Rot: How Increasing Input Tokens Impacts LLM Performance" (2025): https://www.trychroma.com/research/context-rot
- Greg Kamradt, "Needle In A Haystack" 测试：https://github.com/gkamradt/LLMTest_NeedleInAHaystack
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
