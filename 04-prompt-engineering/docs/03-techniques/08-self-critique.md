# PE Technique 08：Self-Critique（自我反思）

> 让模型先给出答案，再回头审查自己的答案，必要时修改。在写作、code review、决策这类主观质量任务上，这样做通常有看得见的提升（Self-Refine 论文在多种生成任务上都报告了明显改善），具体多少要用自己的评测集量。它的局限也很明显：模型本来不会的东西，审一遍也补不上，而且成本和延迟都会翻倍。

---

## 1. Self-critique 的三种模式

### 1.1 在同一个 prompt 里自审
让模型在一次调用里写完再看一遍：

```
任务：写一段中文产品介绍。

请按以下步骤：
1. 先在 <draft> 标签里写初稿
2. 在 <critique> 标签里指出初稿的 2-3 个改进点
3. 在 <final> 标签里给最终版本

输入：{product}
```

输出：

```
<draft>...</draft>
<critique>
- 开头太长，可以删掉前两句
- 没强调价格优势
- 结尾不够 call-to-action
</critique>
<final>...</final>
```

下游只解析 `<final>`，其余部分留着调试用。

### 1.2 两阶段：先生成，再审查
拆成几个独立的 prompt：

```
[Prompt A] 生成初稿
[Prompt B] 看初稿，指出问题
[Prompt C (可选)] 根据 B 的反馈改

```

代价是成本和延迟变成 2 到 3 倍。

### 1.3 两阶段，审查换人
让另一个角色、甚至另一家模型来审，避免自己评自己的偏差：

```
[Generator: GPT-6 Luna] 写初稿
[Critic: Claude Sonnet] "你是严格 editor，找出问题"
[Revise: GPT-6 Luna] 根据 critic 反馈改
```

换模型又换角色之后，反馈往往更不客气，也更有用。

---

## 2. 审查提示怎么写

审查提示要写明从哪些维度看，不能只说「找找问题」：

```
请按以下维度审查：
1. 事实准确性：是否存在错误信息？引用源是否可靠？
2. 完整性：是否漏关键论点？
3. 简洁度：能否更精简？哪些段落啰嗦？
4. 风格：是否符合<role>风格？

按维度列具体问题，每条不超过 30 字。
```

维度太多（5 个以上）模型分不清轻重；太少则反馈空洞，「提高质量」这种话没有用。我的经验是 3 到 4 个维度最稳。

---

## 3. 什么时候用

适合用的场景：输出长且主观（写作、规划、code review、设计）；出错代价高（关键报告、法律文档）；能接受成本和延迟翻倍。

不适合的场景：

- 输出很短，只是一个标签或数字
- 已经用了 CoT。CoT 是边想边答，再加一轮审查多半是重复劳动
- 实时交互，用户等不起
- 简单的事实问答

---

## 4. Self-Refine：多轮修正

研究里有个模式叫 Self-Refine，起草、审查、修改反复进行：

```
[Draft] → [Critique] → [Refine] → [Critique] → [Refine] → ...
```

一般 2 到 3 轮之后就稳定了，再改下去反而会变差，也就是修正过度。

```python
def self_refine(initial_input: str, max_iter: int = 3) -> str:
    draft = generate(initial_input)
    for _ in range(max_iter):
        critique_text = critique(draft)
        if "no changes needed" in critique_text.lower():
            break
        new_draft = refine(draft, critique_text)
        if new_draft == draft:
            break
        draft = new_draft
    return draft
```

---

## 5. 审查常见的失败方式

### 5.1 客套式审查
模型不愿意认真挑毛病：

```
critique:
"很好的初稿！可以考虑稍微调整开头。整体结构清晰。"
```

可以在 prompt 里要求「至少指出 3 个具体问题，每个都引用原文」。

### 5.2 换汤不换药
审查说「应该更清晰」，修改时只是换了几个同义词，实质没变。对策是要求审查必须给出具体的改写方案，而不只是方向。

### 5.3 无中生有
为了凑够「必须找 3 个问题」，模型会硬编一些并不存在的问题。所以要允许审查输出 "no significant issues"。强制凑数比不审还糟。

---

## 6. demo：邮件起草、审查、修改

```python
"""三阶段：起草 → 批评 → 修正"""
import anthropic
client = anthropic.Anthropic()

REQUEST = """写一封邮件给老板，告诉他这个季度 KPI 完成 80%，主要因为团队 2 人请长假。
要诚实但不卑微，给出补救计划。"""


def draft() -> str:
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system="你是写邮件的助手。简洁，无废话。",
        messages=[{"role": "user", "content": REQUEST}],
    )
    return resp.content[-1].text


def critique(text: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-5-5",  # 用更强模型做 critic
        max_tokens=1024,
        system="""你是严格的写作 editor。从下面 3 个维度审查邮件：
1. 诚实但不卑微：是否过度道歉或推卸责任？
2. 补救计划：是否具体可执行？
3. 简洁度：哪些句子可以删？

每个维度给 1-2 条具体建议，引用原文片段。""",
        messages=[{"role": "user", "content": text}],
    )
    return resp.content[-1].text


def refine(text: str, critique_text: str) -> str:
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system="你是邮件助手。根据 editor 反馈修改邮件，保持原意。",
        messages=[{
            "role": "user",
            "content": f"原邮件:\n{text}\n\nEditor 反馈:\n{critique_text}\n\n请给修改后的邮件，不要解释。",
        }],
    )
    return resp.content[-1].text


if __name__ == "__main__":
    d = draft()
    print("=== Draft ===")
    print(d)
    c = critique(d)
    print("\n=== Critique ===")
    print(c)
    r = refine(d, c)
    print("\n=== Refined ===")
    print(r)
```

---

## 7. Self-critique 和 Self-consistency 的区别

这两个名字容易混：

| 维度 | Self-critique | Self-consistency |
|------|---------------|------------------|
| 思路 | 答 → 审 → 改 | 多次答 → 投票 |
| 适合 | 主观质量、长输出 | 客观推理、单值答案 |
| 步数 | 2-3 步 | N 次 |
| 模型 | 一个或多个 | 同一个 |
| Cost | 2-3x | N x |

复杂任务上可以两个一起用：用 self-consistency 投票定下主答案，再用 self-critique 打磨最终表述。

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| 审查用同一个模型、同样的温度 | 容易对自己手下留情，换更强的模型或别家模型来审 |
| 不要求审查具体到原文 | 反馈空泛，修改时无从下手 |
| 修改无限循环 | 设 max_iter，遇到 "no changes needed" 提前退出 |
| 简单任务也加审查 | 白白浪费 |
| 审查内容暴露给用户 | UI 只展示 final |

---

## 9. 下一步

- [Self-Consistency 采样投票](./09-self-consistency.md)
- [分隔符策略（XML / JSON / Markdown / 三引号）](./10-delimiters.md)
- [LLM-as-Judge](../05-by-task/05-judge.md)：把审查角色工程化

## 参考资料

- "Self-Refine" (Madaan et al. 2023): https://arxiv.org/abs/2303.17651
- "Reflexion" (Shinn et al. 2023): https://arxiv.org/abs/2303.11366
