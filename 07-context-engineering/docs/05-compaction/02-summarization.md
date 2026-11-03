# CE 05-02：摘要式压缩（Summarization）

> 摘要式压缩是用一次额外的 LLM 调用，把一大段旧历史总结成一小段文字，再用这段摘要替换原文放回上下文。在各种压缩手段里，它的信息密度最高，能把历史压到原来的几十分之一，仍保住任务需要的信息；但一步压得太狠会丢细节。代价是多跑一次 LLM，有延迟、有 token 成本，也有丢信息的风险，所以摘要 prompt 怎么写、保留什么、丢掉什么，决定了这套做法的成败。

---

## 1. 思路：用摘要替换原文

最朴素的摘要压缩长这样：

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

几十轮旧原文被一条摘要消息顶替，模型看到的历史变短了，但来龙去脉还在。

---

## 2. 摘要 prompt 怎么写：保决策、保事实、保未完成

摘要好不好，基本取决于 prompt。像「总结一下上面的对话」这样的 prompt 会把关键信息全丢掉，只剩「用户和助手友好地讨论了项目」这类空话。

好的摘要 prompt 要**明确写出必须保留哪几类信息**：

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

「踩过的坑」这一条常被忽略，对 Agent 却特别重要。少了它，模型会一遍遍去试已经失败过的方案。

---

## 3. 一段「历史超阈值就摘要」的完整代码

下面的实现可以直接套用：累计 token 超过阈值，就把除最近几条之外的历史摘要掉。

```python
import openai
import tiktoken

client = openai.OpenAI()
enc = tiktoken.get_encoding("o200k_base")

THRESHOLD_TOKENS = 6000   # 历史超过这个就触发摘要
KEEP_RECENT = 4           # 保留最近几条原样（摘要可能丢近期细节）


def count_tokens(messages: list[dict]) -> int:
    return sum(len(enc.encode(m["content"])) for m in messages)


def summarize_history(messages: list[dict]) -> str:
    """把一批消息压成一段摘要文本。"""
    history_text = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
    resp = client.chat.completions.create(
        model="gpt-6-luna",   # 摘要用便宜模型即可
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

几个工程上的考虑。摘要用便宜模型（`gpt-6-luna` 或 Haiku），主对话用强模型，省钱主要靠这一点。示例里给 `gpt-6-luna` 设了 `temperature=0`，摘要要的是稳定地还原事实，不需要发挥（GPT-6 是否接受这个参数以模型页为准）。换成 Haiku 时去掉这个参数：Claude 5.x 不接受 `temperature`，传了会返回 400，想要稳定就靠摘要 prompt 写清楚「只总结、不推断」和固定的输出结构。最近 KEEP_RECENT 条保留原文，因为近期细节（比如当前正在改的那个函数）一摘要就容易变模糊。

---

## 4. 平台和框架的内置实现

上面的 `compact()` 是为了讲清原理，生产里优先用内置的。下面三个例子的语法取自官方文档，后两段需要对应平台的 API key 才能运行：

```python
# ① LangChain 1.x：SummarizationMiddleware（trigger/keep 的取值：tokens / messages / fraction）
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware

agent = create_agent(
    model="openai:gpt-6.1-sol",
    tools=[],
    middleware=[
        SummarizationMiddleware(
            model="openai:gpt-6-luna",           # 摘要用便宜模型
            trigger=("tokens", 6000),            # 超过 6000 token 触发
            keep=("messages", 20),               # 保留最近 20 条原文
        ),
    ],
)
```

```python
# ② Anthropic 服务端 compaction（beta）：服务端按阈值写摘要
response = client.beta.messages.create(
    betas=["compact-2026-01-12"],
    model="claude-opus-5-5",
    max_tokens=4096,
    messages=messages,
    context_management={"edits": [{"type": "compact_20260112",
                                   "trigger": {"type": "input_tokens", "value": 150000}}]},
)
messages.append({"role": "assistant", "content": response.content})  # 含 compaction 块，原样带回
```

```python
# ③ OpenAI Responses 服务端 compaction
response = client.responses.create(
    model="gpt-6.1-sol",
    input=conversation,
    store=False,
    context_management=[{"type": "compaction", "compact_threshold": 200000}],
)
```

有了这些，自己实现的理由只剩两个：要自定义摘要内容（② 里可以用 `instructions` 整体替换默认的摘要提示），或者要在不同厂商之间保持一致的行为。

---

## 5. 递归摘要：summary of summaries

会话特别长时，单段摘要也会慢慢变长，最后又把窗口撑满。办法是递归摘要，把「旧摘要 + 新增历史」再压成一段新摘要：

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
        model="gpt-6-luna",
        messages=[{"role": "system", "content": SUMMARY_PROMPT.format(history=combined)}],
        temperature=0,
    )
    return resp.choices[0].message.content
```

递归摘要能让摘要长度保持恒定，不随会话增长。代价是越早的信息经过的压缩轮次越多，损失越大，这个取舍绕不开。Claude Code 这类会多次 auto-compact 的工具，后一次压缩的输入里本来就带着上一次的总结，效果上也是这样一层层滚动的。

---

## 6. 摘要丢信息的风险与缓解

摘要是有损压缩，一定会丢东西。常见的出错情况和对策：

| 风险 | 现象 | 缓解措施 |
|------|------|----------|
| 丢具体值 | 摘要写「配置了数据库」但丢了连接串 | prompt 明确要求保留「具体值：路径/ID/配置」 |
| 丢失败记录 | 模型重试已失败的方案 | prompt 强制保留「踩过的坑」 |
| 近期细节变模糊 | 当前在改的代码细节被概括没了 | 保留最近 N 条原文不摘要 |
| 摘要本身幻觉 | LLM 摘要时编造没发生的事 | 要求「只总结，不推断」并固定输出结构；能设 temperature 的模型设为 0（Claude 5.x 不接受这个参数） |
| 一次性全压丢太多 | 一步压得太狠（比如 100K 直接压成 500 字），细节丢光 | 控制每一步的压缩比，或分段、递归地压 |
| 不可逆 | 摘要后原文没了，发现丢了关键信息无法找回 | 原文外置存储（落盘/DB），摘要只是窗口里的代理 |

最后一条是我认为最重要的工程实践：摘要进窗口，原文进外部存储。窗口里放摘要省 token，原始历史另存一份（文件或向量库），需要时可以检索回来，摘要丢了东西也有兜底。这和记忆一章（[短期记忆 vs 长期记忆](../04-memory/01-short-vs-long.md)）讲的外置记忆是同一个思路。

---

## 7. 什么时候用摘要、什么时候别用

| 场景 | 适合摘要吗 |
|------|-----------|
| 长对话客服 / 助手，需要记住早期需求 | 很适合，能保住语义 |
| 长程 Agent，需要记住任务目标和进度 | 适合，但一定要保留 TODO 和踩过的坑 |
| 只需要最近几轮的简单 QA | 不必，滑窗就够（见 [03](./03-sliding-window.md)） |
| 对延迟极敏感的实时场景 | 慎用：摘要要多跑一次 LLM，有延迟，考虑异步预压缩 |
| 历史里全是结构化大 JSON | 慎用：先做字段剪枝（见 [04](./04-pruning.md)）更精准 |

---

## 8. 下一步

- 不跑 LLM 的最简压缩：[滑动窗口与截断](./03-sliding-window.md)
- 按价值取舍而非一刀切：[重要性评分与剪枝](./04-pruning.md)
- 摘要什么时候触发：[何时触发压缩](./05-when-to-compact.md)
- 回看压缩的动机：[为什么要压缩上下文](./01-why-compact.md)
- 把历史原文存进外部记忆：[短期记忆 vs 长期记忆](../04-memory/01-short-vs-long.md)

## 参考资料

- LangGraph, "How to add summary of the conversation history": https://langchain-ai.github.io/langgraph/how-tos/memory/add-summary-conversation-history/
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
