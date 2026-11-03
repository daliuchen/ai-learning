# PE By-Task 04：总结（Summarizer）

> 总结看起来是最简单的任务，但"给谁看、多长、留什么、丢什么"这几件事不写清楚，每次输出的长度和风格都会不一样。只写"请总结"，结果飘忽不定；写成"给 CEO 总结成 1 段、不超过 100 字、带数字"，输出就稳了。

---

## 1. 任务特征

- 输入：长文本（500 字 - 50,000 字）
- 输出：短文本（10-500 字）
- 评测：人工，加上用 LLM-judge 打 faithfulness 和 relevance
- 难点：长文档注意力衰减、漏掉关键信息、风格漂移

---

## 2. 先说清给谁看、为什么看

模糊：
```
"总结这篇文章"
```

精确：
```
"为 CEO 总结这份财报。CEO 关心：营收、增长、风险。
长度: 1 段，不超过 100 字。必须含 3 个具体数字。"
```

动笔前把下面几个维度定下来：

| 维度 | 例 |
|------|-----|
| 受众 | CEO / 工程师 / 5 岁小朋友 |
| 目的 | 决策 / 学习 / 入门 / FYI |
| 长度 | 1 句 / 1 段 / 500 字 / 1000 字 |
| 格式 | 段落 / bullets / 结构化 JSON |
| 必须含 | 数字 / 引用 / 时间线 |
| 可以省 | 细节 / 引文 / 历史 |

---

## 3. 常见模板

### 3.1 一段话总结

```
你是<受众>的助手。

任务：把下面的文章总结成 1 段，不超过 100 字。

必须含：
- 主题 / 立场
- 1-2 个关键数据
- 一句话结论

避免：
- 引用原文
- 无 substance 的形容词
- "本文讨论了..." 等元描述
```

### 3.2 bullet 总结

```
任务：列出文章的 3-5 个 key points。

每个 bullet：
- 一句话陈述（不要疑问 / 段落）
- 含具体数据（如适用）
- 不超过 25 字
```

### 3.3 结构化 JSON 总结

```python
class ArticleSummary(BaseModel):
    headline: str = Field(max_length=30)
    key_points: list[str] = Field(min_length=3, max_length=5)
    main_takeaway: str = Field(max_length=80)
    sentiment: Literal["positive", "neutral", "negative"]
```

---

## 4. 长文档：Map-Reduce

文档超过 10k token 时整篇直接喂，效果会因为注意力衰减而变差。可以用 map-reduce：

```python
def summarize_long(text: str, chunk_size: int = 3000) -> str:
    # Map: 分段 + 各自总结
    chunks = split_chunks(text, chunk_size)
    chunk_summaries = [summarize_short(c) for c in chunks]
    
    # Reduce: 把分段总结合起来再总结
    combined = "\n\n".join(chunk_summaries)
    return summarize_short(combined)
```

也可以迭代 refine：

```python
def iter_refine(text: str, chunk_size: int = 3000) -> str:
    chunks = split_chunks(text, chunk_size)
    summary = summarize_short(chunks[0])
    for chunk in chunks[1:]:
        prompt = f"""现有总结:
{summary}

新增内容:
{chunk}

更新总结，融入新内容，保持长度。"""
        summary = call_llm(prompt)
    return summary
```

map-reduce 可以并行，速度快，但各段总结彼此看不到；iter-refine 每一步都带着前文的总结，前后更连贯，代价是只能串行跑。

---

## 5. 多文档总结

```
任务：基于以下 N 篇文章，总结公司本季度业务表现。

<articles>
<article id="1">{...}</article>
<article id="2">{...}</article>
...
</articles>

要求：
- 综合所有文章
- 矛盾的地方标注 "(来源 1 vs 来源 3)"
- 每个 claim 标 [Article N] 引用
```

---

## 6. 防止幻觉

总结时模型会顺手"补全"原文没有的东西：

```
原文: "营收 100 万"
总结: "营收 100 万，同比增长 20%"  ← "20%" 是编的
```

prompt 里要明确禁止：

```
重要：
- 只总结**原文中明确存在**的信息
- 不要推断 / 比较 / 增添
- 不知道的不要写
- 数字必须从原文复述
```

还可以再加一道 faithfulness 检查，做法见 [RAG Prompting 与引用约束](../04-advanced/03-rag-prompting.md)。

---

## 7. 风格一致性

总结的风格很容易漂。能用的手段有三种：给 2-3 个 few-shot 示例、写一份 voice guide、用 structured output 卡住长度和字段。

---

## 8. 精确控制长度

字数不好控，因为模型是按 token 计数的，不是按字。可以这样写：

```
长度: 80-120 字（中文）

如果不确定，宁短勿长。
```

或者在后处理里兜底：

```python
result = summarize(...)
if len(result) > 120:
    result = summarize(result, target_length="100 字")  # 再 condense
```

---

## 9. 完整 demo

```python
from pydantic import BaseModel, Field
from typing import Literal
from openai import OpenAI

client = OpenAI()


class ArticleSummary(BaseModel):
    headline: str = Field(max_length=30)
    key_points: list[str] = Field(min_length=3, max_length=5)
    main_takeaway: str = Field(max_length=80)
    sentiment: Literal["positive", "neutral", "negative"]


SYSTEM = """你是公司 CEO 的执行助理，擅长长文档 → 行动洞察。

任务：把输入文章压缩成结构化总结。

要求：
- headline: 一句话主旨（< 30 字）
- key_points: 3-5 条最关键事实（每条 < 25 字、含具体数字如适用）
- main_takeaway: 给 CEO 的"一句话决策摘要"（< 80 字）
- sentiment: 整体倾向

约束：
- 只用原文事实，不要推测
- 数字精确复述
- 不要"本文讨论..."的元描述
"""


def summarize_article(text: str) -> ArticleSummary:
    resp = client.chat.completions.parse(
        model="gpt-6-luna",
        response_format=ArticleSummary,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
    )
    return resp.choices[0].message.parsed


if __name__ == "__main__":
    ARTICLE = """
    XX 公司 2026 Q1 财报：营收 12.3 亿，同比增长 18%。
    主营业务 SaaS 占比 65%，新业务（AI agent）首次贡献 8% 营收。
    用户数突破 200 万。北美市场增长最快（+25%）。
    Q2 计划：加大 AI 投入，目标年底用户 350 万。
    潜在风险: 同行竞争激烈，毛利率从 72% 降至 68%。
    """
    print(summarize_article(ARTICLE))
```

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| 只写"请总结" | 说清受众、长度、关注点 |
| 长文档整篇直接喂 | 超过 1 万 token 左右考虑 map-reduce |
| 没要求"只用原文" | 模型会补出原文没有的内容 |
| 没有字数控制 | 长度漂移 |
| 总结里出现"本文讨论了" | prompt 里加"避免元描述" |
| 数字精度丢失 | 强调"数字照原文复述" |
| 多次输出风格差异大 | 加 few-shot；能调采样参数的模型把 temperature 调低（Claude 5.x 不接受 temperature，靠 few-shot 和固定输出结构） |

---

## 11. 下一步

- [LLM-as-Judge](./05-judge.md)
- 长文档处理（map-reduce）：[Decomposition 任务拆解](../03-techniques/04-decomposition.md)
- 防幻觉：[RAG Prompting 与引用约束](../04-advanced/03-rag-prompting.md)
