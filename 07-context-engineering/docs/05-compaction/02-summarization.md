# CE 05-02：摘要式压缩（Summarization）

> **一句话**：摘要式压缩就是用一次额外的 LLM 调用，把一大段旧历史**总结**成一小段文字，再用这段摘要替换原文塞回上下文。它是信息密度最高的压缩手段——能把 100K 历史压成 1K 还保住语义。代价是要多跑一次 LLM（有延迟、有 token 成本、有丢信息风险），所以摘要 prompt 怎么写、保什么丢什么，就是这整套技术的胜负手。

---

## 1. 核心思路：原文换摘要

最朴素的摘要压缩，结构是这样的：

```
压缩前的上下文：
  [system]
  [第 1 轮 user/assistant]  ┐
  [第 2 轮 user/assistant]  │  旧历史，一大坨
  ...                       │
  [第 40 轮 user/assistant] ┘
  [第 41 轮 user]  ← 当前

压缩后的上下文：
  [system]
  [一条摘要消息：「以下是前 40 轮的总结：用户在做 X，已确定 Y，
   还差 Z 未完成，曾试过 W 失败...」]   ← LLM 生成，替换掉 40 轮原文
  [第 41 轮 user]  ← 当前
```

旧的几十轮原文被一条摘要消息顶替。模型看到的历史变短了，但「故事梗概」还在。

---

## 2. 摘要 prompt 怎么写——保决策、保事实、保未完成

摘要质量 = prompt 质量。一个垃圾摘要 prompt（「总结一下上面的对话」）会丢掉所有关键信息，留一堆「用户和助手友好地讨论了项目」的废话。

好的摘要 prompt 必须**明确指定保留维度**：

```python
SUMMARY_PROMPT = """\
你是一个对话压缩器。请把下面的历史对话压缩成一段结构化摘要，
供后续对话作为「记忆」使用。一个失忆的助手读了这段摘要后，
必须能无缝接着干活。

必须保留（按这几类组织）：
1. 任务目标：用户最终想达成什么
2. 已确认的关键事实：文件路径、配置、ID、用户偏好等具体信息
3. 已做的决策及理由：选了什么方案、为什么（含被否决的方案）
4. 已完成的工作：改了哪些文件 / 完成了哪些步骤
5. 未完成的任务 / TODO：还差什么
6. 踩过的坑：试过哪些方案失败了、为什么（防止重试）

必须丢弃：
- 寒暄、客套、确认性回复（「好的」「明白了」）
- 工具返回的原始大段输出（只留结论）
- 重复复述的内容

输出用简洁的要点，不要分析、不要展开。

=== 历史对话 ===
{history}
=== 历史对话结束 ===
"""
```

「踩过的坑」这一条经常被忽略，但它对 Agent 极其关键——没有它，模型会反复尝试已经失败过的方案。

---

## 3. 一段「历史超阈值就摘要」的完整代码

下面是可直接套用的实现：累计 token 超过阈值，就把除最近几轮外的历史摘要掉。

```python
import openai
import tiktoken

client = openai.OpenAI()
enc = tiktoken.encoding_for_model("gpt-4o")

THRESHOLD_TOKENS = 6000   # 历史超过这个就触发摘要
KEEP_RECENT = 4           # 保留最近几条原样（摘要可能丢近期细节）


def count_tokens(messages: list[dict]) -> int:
    return sum(len(enc.encode(m["content"])) for m in messages)


def summarize_history(messages: list[dict]) -> str:
    """把一批消息压成一段摘要文本。"""
    history_text = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",   # 摘要用便宜模型即可
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT.format(history=history_text)}
        ],
        temperature=0,         # 摘要要稳定、不发挥
    )
    return resp.choices[0].message.content


def compact(messages: list[dict]) -> list[dict]:
    """messages[0] 是 system；超阈值则摘要中间段，保留最近 KEEP_RECENT 条。"""
    system_msg, body = messages[0], messages[1:]

    if count_tokens(body) <= THRESHOLD_TOKENS:
        return messages  # 没超阈值，原样返回

    to_summarize = body[:-KEEP_RECENT]   # 要被摘要的旧历史
    recent = body[-KEEP_RECENT:]         # 保留的近期原文

    summary = summarize_history(to_summarize)
    summary_msg = {
        "role": "system",
        "content": f"[历史摘要] 以下是先前对话的压缩记忆：\n{summary}",
    }
    return [system_msg, summary_msg, *recent]


# 用法：每轮对话后调用，喂给下一次请求前先 compact
messages = compact(messages)
```

几个工程要点：
- **摘要用便宜模型**（`gpt-4o-mini` / Haiku），这是省钱关键，主对话用强模型、摘要用弱模型。
- **`temperature=0`**，摘要要的是稳定还原事实，不是创意。
- **保留最近 KEEP_RECENT 条原文**，因为近期细节（当前正在改的那个函数）摘要容易糊掉。

---

## 4. 递归摘要：summary of summaries

会话超级长时，单段摘要也会慢慢变长，最终又撑满。解法是**递归摘要**——把「旧摘要 + 新增历史」再摘要成一段新摘要：

```
轮 1-40：   原文 ──摘要──►  摘要A (1K)
轮 41-80：  摘要A + 原文41-80 ──摘要──► 摘要B (1K)   ← 摘要A 被合并进去了
轮 81-120： 摘要B + 原文81-120 ──摘要──► 摘要C (1K)
```

```python
def recursive_compact(prev_summary: str, new_messages: list[dict]) -> str:
    """把上一版摘要 + 新增历史，再压成一版新摘要（长度恒定）。"""
    new_text = "\n".join(f"[{m['role']}] {m['content']}" for m in new_messages)
    combined = f"=== 已有摘要 ===\n{prev_summary}\n\n=== 新增对话 ===\n{new_text}"
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": SUMMARY_PROMPT.format(history=combined)}],
        temperature=0,
    )
    return resp.choices[0].message.content
```

递归摘要让摘要长度**恒定**（不随会话增长），代价是越早的信息经过越多轮压缩，损失越大——这是个固有取舍。Claude Code 的 auto-compaction 本质就是这种递归思路：每次触发都基于上一份总结继续滚动压缩。

---

## 5. 摘要丢信息的风险与缓解

摘要是「有损压缩」，必然丢东西。典型翻车场景和对策：

| 风险 | 现象 | 缓解措施 |
|------|------|----------|
| 丢具体值 | 摘要写「配置了数据库」但丢了连接串 | prompt 明确要求保留「具体值：路径/ID/配置」 |
| 丢失败记录 | 模型重试已失败的方案 | prompt 强制保留「踩过的坑」 |
| 近期细节糊化 | 当前在改的代码细节被概括没了 | 保留最近 N 条原文不摘要 |
| 摘要本身幻觉 | LLM 摘要时编造没发生的事 | `temperature=0` + 要求「只总结，不推断」 |
| 一次性全压丢太多 | 100K 一把压成 500 字，信息密度过载 | 控制压缩比，别太激进（见下） |
| 不可逆 | 摘要后原文没了，发现丢了关键信息无法找回 | **原文外置存储**（落盘/DB），摘要只是窗口里的代理 |

最后一条最重要的工程实践：**摘要进窗口，原文进外部存储**。窗口里放摘要省 token，但原始历史另存一份（文件 / 向量库），需要时能检索回来。这样摘要的「有损」就有了兜底——这也是 [04-memory](../04-memory/01-short-vs-long.md) 章节「外置记忆」的思路衔接点。

---

## 6. 什么时候用摘要、什么时候别用

| 场景 | 适合摘要吗 |
|------|-----------|
| 长对话客服 / 助手，需要记住早期需求 | ✅ 很适合，能保住语义 |
| 长程 Agent，需要记住任务目标和进度 | ✅ 适合，但务必保留 TODO 和坑 |
| 只需要最近几轮的简单 QA | ❌ 杀鸡用牛刀，滑窗就够（见 [03](./03-sliding-window.md)） |
| 对延迟极敏感的实时场景 | ⚠️ 摘要要多跑一次 LLM，有延迟，考虑异步预压缩 |
| 历史里全是结构化大 JSON | ⚠️ 先做字段剪枝（见 [04](./04-pruning.md)）更精准 |

---

## 7. 下一步

- 📖 滑动窗口与截断：不跑 LLM 的最简压缩 → [03-sliding-window.md](./03-sliding-window.md)
- 📖 重要性评分与剪枝：按价值取舍而非一刀切 → [04-pruning.md](./04-pruning.md)
- 📖 何时触发摘要：阈值与时机权衡 → [05-when-to-compact.md](./05-when-to-compact.md)
- 📖 为什么要压缩（回看动机） → [01-why-compact.md](./01-why-compact.md)
- 📖 原文外置：把历史存进外部记忆 → [../04-memory/01-short-vs-long.md](../04-memory/01-short-vs-long.md)

## 参考资料

- LangChain, "Summarization" / `SummarizationNode`: https://langchain-ai.github.io/langgraph/how-tos/memory/add-summary-conversation-history/
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
