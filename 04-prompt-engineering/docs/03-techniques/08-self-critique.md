# PE Technique 08：Self-Critique（自我反思）

> **一句话**：让模型先给出答案、再"审查自己的答案"、必要时修正——能在主观质量任务（写作、code review、决策）上稳定涨 5-15%。但**不能修正模型不会的能力**，且 cost / latency 翻倍。

---

## 1. Self-critique 三种模式

### 1.1 同 prompt 内 critique
让模型在一个 prompt 内"答完再看一遍"：

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

下游只解析 `<final>`，其他用来 debug。

### 1.2 二阶段：Generate → Critique
两个独立 prompt：

```
[Prompt A] 生成初稿
[Prompt B] 看初稿，指出问题
[Prompt C (可选)] 根据 B 的反馈改

```

代价：3x cost / latency。

### 1.3 二阶段 + judge 角色
让 critique 由**不同 role / 不同模型**做——避免"自己评自己"偏：

```
[Generator: GPT-4o-mini] 写初稿
[Critic: Claude Sonnet] "你是严格 editor，找出问题"
[Revise: GPT-4o-mini] 根据 critic 反馈改
```

跨模型 + 跨角色 → 反馈更"刺"。

---

## 2. Critique 提示的设计

critique 提示要**明确维度**，不能笼统说"找问题":

```
请按以下维度审查：
1. 事实准确性：是否存在错误信息？引用源是否可靠？
2. 完整性：是否漏关键论点？
3. 简洁度：能否更精简？哪些段落啰嗦？
4. 风格：是否符合<role>风格？

按维度列具体问题，每条不超过 30 字。
```

维度多了不行（5+ 维度，模型 prioritize 不当）；少了反馈太空（"提高质量"无用）。**3-4 个维度**最稳。

---

## 3. 何时用 / 不用

### 用
- 输出长 / 主观（写作、规划、code review、设计）
- 错误 cost 高（关键报告、法律文档）
- 可以接受 cost / latency 翻倍

### 不用
- 输出短（单 label / 数字）
- 已用 CoT（CoT 本质上是"边想边答"，再 critique 是冗余）
- 实时交互（用户等不了）
- 简单事实问答

---

## 4. Self-Refine：多轮修正

研究里有个模式叫 **Self-Refine**：

```
[Draft] → [Critique] → [Refine] → [Critique] → [Refine] → ...
```

通常 2-3 轮后趋于稳定，再修反而变差（"过度修正"）。

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

## 5. Critique 的常见失败模式

### 5.1 "礼貌型 critique"
模型不愿真指出问题：

```
critique:
"很好的初稿！可以考虑稍微调整开头。整体结构清晰。"
```

对策：prompt 里强制"必须指出 3 个具体问题"："至少 3 个，每个含具体 line 引用"。

### 5.2 "同义反复"
critique 说"应该更清晰" → refine 没改实质，只换了同义词。

对策：critique 必须**给具体改写方案**，不只是"应该这样"。

### 5.3 "找不存在的问题"
模型为完成"必须找 3 个问题"任务，硬编一些问题。

对策：允许 critique 输出 "no significant issues"；强制找 N 个反而更糟。

---

## 6. demo：邮件起草 + critique + refine

```python
# demos/techniques/08_self_critique.py
"""三阶段：起草 → 批评 → 修正"""
import anthropic
client = anthropic.Anthropic()

REQUEST = """写一封邮件给老板，告诉他这个季度 KPI 完成 80%，主要因为团队 2 人请长假。
要诚实但不卑微，给出补救计划。"""


def draft() -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system="你是写邮件的助手。简洁，无废话。",
        messages=[{"role": "user", "content": REQUEST}],
    )
    return resp.content[0].text


def critique(text: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",  # 用更强模型做 critic
        max_tokens=500,
        system="""你是严格的写作 editor。从下面 3 个维度审查邮件：
1. 诚实但不卑微：是否过度道歉或推卸责任？
2. 补救计划：是否具体可执行？
3. 简洁度：哪些句子可以删？

每个维度给 1-2 条具体建议，引用原文片段。""",
        messages=[{"role": "user", "content": text}],
    )
    return resp.content[0].text


def refine(text: str, critique_text: str) -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system="你是邮件助手。根据 editor 反馈修改邮件，保持原意。",
        messages=[{
            "role": "user",
            "content": f"原邮件:\n{text}\n\nEditor 反馈:\n{critique_text}\n\n请给修改后的邮件，不要解释。",
        }],
    )
    return resp.content[0].text


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

## 7. Self-critique vs Self-consistency

两个容易混：

| 维度 | Self-critique | Self-consistency |
|------|---------------|------------------|
| 思路 | 答 → 审 → 改 | 多次答 → 投票 |
| 适合 | 主观质量、长输出 | 客观推理、单值答案 |
| 步数 | 2-3 步 | N 次 |
| 模型 | 一个或多个 | 同一个 |
| Cost | 2-3x | N x |

实战：复杂任务上**两个都用**——self-consistency 投票主答案，self-critique 优化最终表述。

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| **critique 用同模型同温度** | 容易"礼貌评"自己；换强 critic / 跨家 |
| **critique 不强制 specific** | 反馈空泛，refine 改不动 |
| **无限循环 refine** | 设 max_iter；early exit "no changes needed" |
| **简单任务也加 critique** | 浪费 |
| **critique 暴露给用户** | UI 只展示 final |

---

## 9. 下一步

- 📖 self-consistency → [09-self-consistency.md](./09-self-consistency.md)
- 📖 delimiter 选择 → [10-delimiters.md](./10-delimiters.md)
- 📖 LLM-as-judge（critic 的工程化）→ [05-by-task/05-judge.md](../05-by-task/05-judge.md)

## 参考资料

- "Self-Refine" (Madaan et al. 2023): https://arxiv.org/abs/2303.17651
- "Reflexion" (Shinn et al. 2023): https://arxiv.org/abs/2303.11366
