# CE 03：Context Rot 与 Lost in the Middle

> **一句话**：上下文不是越长越好。塞得越满，模型对「中间那段」的召回越差（lost in the middle），无关内容还会稀释注意力、引入干扰——这种「长上下文质量退化」就叫 Context Rot。结论很反直觉但很实用：**与其塞 50 段碰运气，不如精挑 5 段放对位置**。

---

## 1. Lost in the Middle 现象

2023 年斯坦福 / 伯克利的论文 *"Lost in the Middle: How Language Models Use Long Contexts"* 做了个经典实验：

把一条「正确答案所在的文档」放进一堆文档里，改变它的位置，看模型答对率。

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

结论：**关键信息放在上下文中间时，召回率明显低于放在头尾**。呈一条 U 型曲线。

到 2025-2026，模型长上下文能力大幅进步，但**这个效应并没有完全消失**——尤其在上下文很长（几十 K 以上）、干扰项很多时，中间内容仍然吃亏。别因为模型号称「1M 上下文」就以为这事不存在了。

---

## 2. Needle in a Haystack 测试

业界评估长上下文召回能力的标准方法：**大海捞针（needle in a haystack）**。

做法：

1. 准备一大段无关文本（haystack，比如 100K token 的小说 / 文档）。
2. 在某个位置插入一句明确事实（needle，比如「2026 年的幸运数字是 73」）。
3. 在末尾提问：「2026 年的幸运数字是几？」
4. 改变 needle 的**插入深度**（0%、25%、50%、75%、100%）和**总长度**，画出召回热力图。

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
        model="claude-opus-4-20250514",
        max_tokens=20,
        messages=[{"role": "user",
                   "content": f"{ctx}\n\n{QUESTION}"}],
    )
    return resp.content[0].text

# 跑多个深度，看哪个位置会丢
for d in (0.0, 0.25, 0.5, 0.75, 1.0):
    print(d, "->", probe(big_doc, d))
```

典型结果：深度 0% 和 100% 召回稳，50% 附近偶尔丢，且**上下文越长、需要同时记的 needle 越多，中间越容易翻车**。

---

## 3. 越长不一定越好的三个原因

| 原因 | 机制 | 后果 |
|------|------|------|
| 噪声 | 无关内容混进来 | 模型可能引用错段，张冠李戴 |
| 干扰（distractor） | 有「看起来像答案但不是」的片段 | 直接答错 |
| 稀释 | n 越大，注意力被摊薄 | 关键信息「声音」变小，召回下降 |

特别注意「干扰」：研究表明，**加入和正确答案相似但错误的片段，比单纯加无关内容更伤**。RAG 召回 top-50 里混进几条「相关但错」的，比召回 top-5 干净结果还糟。这就是为什么召回**质量 > 数量**。

---

## 4. 实战影响

哪些场景最容易被 Context Rot 咬到：

- **RAG 问答**：盲目召回 top-k=50，关键片段被埋中间 → 答非所问。
- **长文档总结**：文档中段的要点被漏掉。
- **长对话 Agent**：早期约定（「用户偏好中文」）在第 30 轮被忘掉。
- **多工具 Agent**：一堆工具返回结果堆在中段，模型抓不到关键那条。

---

## 5. 缓解手段

| 手段 | 怎么做 | 对应原则 |
|------|--------|----------|
| 重要信息放头尾 | system 关键约束 + 把最相关检索片段放最前/最后 | 位置即信号 |
| 减少无关内容 | 召回 top-5 而非 top-50，先 rerank 再喂 | 最少必要上下文 |
| 去重 / 压缩 | 合并重复片段、摘要远期历史 | 提高信噪比 |
| 重述关键指令 | 长上下文末尾再贴一遍核心问题/约束 | 抵消中间衰减 |
| 分块处理 | 太长就 map-reduce / 多次调用，别硬塞一窗 | 绕开 rot |

代码上一个最常见的「放对位置」技巧——把最相关片段放在末尾（紧挨问题）：

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

| 坑 | 真相 |
|----|------|
| 「1M 上下文 = 随便塞」 | rot 依然存在，长 ≠ 准 |
| 「召回越多越保险」 | 噪声 / 干扰会拉低准确率，质量优先 |
| 「关键约束写在 system 开头就够」 | 超长上下文里开头的约束也会被衰减，末尾再贴一遍更稳 |
| 「测了 needle 能召回就没问题」 | 单 needle 简单，多 needle / 需要综合多处信息时才见真章 |

---

## 7. 下一步

- 📖 长上下文除了召回变差，还很贵很慢 → [04-cost-latency.md](./04-cost-latency.md)
- 📖 那到底该塞多少：把上下文当预算分配 → [05-context-budget.md](./05-context-budget.md)
- 📖 终极原则：最少必要上下文 → [06-minimal-context.md](./06-minimal-context.md)
- 📖 回看窗口与 attention 机制 → [02-context-window.md](./02-context-window.md)

## 参考资料

- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts" (2023): https://arxiv.org/abs/2307.03172
- Greg Kamradt, "Needle In A Haystack" 测试：https://github.com/gkamradt/LLMTest_NeedleInAHaystack
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
