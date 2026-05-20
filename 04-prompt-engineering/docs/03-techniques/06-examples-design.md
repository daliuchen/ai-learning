# PE Technique 06：Examples 设计 —— 好 Few-shot 长什么样

> **一句话**：Few-shot 的关键不是"数量"是"质量"——3 个精心挑的例子 > 10 个随便凑的。本篇给一套**示例设计 checklist**，让你的示例真正帮模型而不是干扰它。

---

## 1. 示例的 4 个作用

把 few-shot 想清楚：示例是给模型看的"how to"。它在做这些事：

| 作用 | 举例 |
|------|------|
| **格式 anchor** | "输出应该长这样" |
| **风格 anchor** | "语气这么写" |
| **边界示范** | "这种 edge case 你应该这样处理" |
| **类别 anchor** | "这种输入归这个类" |

---

## 2. 一个好示例的 5 条标准

### 2.1 代表性 > 覆盖性
示例分布应该接近真实数据分布。

```
真实分布: 70% bug, 15% feature, 10% complaint, 5% other
示例分布: 也应该接近这个比例

❌ 4 个示例每个类别 1 个（25/25/25/25）→ 模型以为各类等概率
✅ 7 个示例: 5 bug, 1 feature, 1 complaint  
```

### 2.2 涵盖关键 edge case
保留 1-2 个"最容易错"的 edge：

```
✅ 反讽分类（容易判错）
✅ 含 emoji（容易格式炸）
✅ 多类同时含（要选主类）
```

### 2.3 格式 100% 一致
所有示例输出格式严格一致。哪怕字段顺序、缩进、引号都要统一。

```
❌ 示例 1: {"name": "A", "age": 30}
   示例 2: {"age": 30, "name": "B"}  ← 顺序变
   示例 3: { "name" : "C" , "age" : 30 }  ← 空格变
   → 模型困惑
✅ 全部 {"name": "...", "age": ...}
```

### 2.4 风格匹配真实场景
示例的语气、长度、复杂度要和真实输入一致。

```
❌ 示例都是 5 字短句，真实是 100 字段落 → 模型在长输入上表现差
✅ 示例和真实分布一致
```

### 2.5 没有冗余信息
示例只给最关键内容；不要写解释 / 旁注。

```
❌ "示例 1: 用户写了 'App 闪退'，这是一个 bug 报告，因为它表达了软件问题。
   所以应该输出 'bug'。"
✅ 输入: App 闪退
   输出: bug
```

---

## 3. 示例的"反例"

故意给一个反例，让模型知道**不该**怎么做：

```
约束：JSON 输出严格符合 schema。

示例（正确）:
输入: Alice 30 岁
输出: {"name": "Alice", "age": 30}

反例（不要这样）:
输入: Alice 30 岁
输出: {"name": "Alice", "age": "30 岁"}   ← age 不应该是字符串
```

反例**慎用**——示例本身是强 anchor，反例可能反而让模型困惑。仅在反例和正例形成"显著对比"时用。

---

## 4. 示例放在哪里

### 在 system message
```
SYSTEM = """任务...
约束...

示例：
<example>...</example>
<example>...</example>
"""
```

适合：示例固定、不随每次输入变。

### 在 messages 数组（多轮形式）
```python
messages = [
    {"role": "system", "content": "任务..."},
    {"role": "user", "content": "示例 1 输入"},
    {"role": "assistant", "content": "示例 1 输出"},
    {"role": "user", "content": "示例 2 输入"},
    {"role": "assistant", "content": "示例 2 输出"},
    {"role": "user", "content": "真实输入"},
]
```

OpenAI 文档推荐这种——更"对话语义化"。

### 在 XML 容器（Claude 友好）
```
<examples>
<example>
<input>...</input>
<output>...</output>
</example>
</examples>
```

---

## 5. 动态 Few-shot（检索式）

示例库巨大时，根据输入检索最相关的 K 个：

```python
def get_relevant_examples(user_input: str, k: int = 3) -> list[dict]:
    # 用向量检索从示例库挑最像的 k 个
    embedding = embed(user_input)
    return example_store.search(embedding, k=k)
```

适合：
- 各业务线 / 各 customer 的示例不同
- 静态 few-shot 覆盖不够
- 有几百+条标注样本

注意：和 **prompt caching** 矛盾（每次示例变，cache miss）。要 trade-off。

---

## 6. 示例怎么从生产数据来

**别**手编示例——从真实数据来：

1. 找 100-500 条线上数据
2. 人工标注期望输出
3. 按代表性 + 关键 edge 挑 5-10 条
4. 加进 prompt

好处：

- 风格自动匹配真实输入
- 暴露真实分布问题
- 边界 case 来自真实而非想象

---

## 7. 示例迭代规则

每次改 prompt 触发的示例调整：

| 触发 | 调整 |
|------|------|
| 发现新失败模式 | 补 1 个该模式的正确示例 |
| 示例和真实分布偏差 | 重采样 |
| 模型过 anchor 到某风格 | 多样化示例 |
| 示例长度都偏短，真实长 | 用真实长样本替换 |
| 类别更新 / 业务规则改 | 用新规则下的示例 |

**铁律**：示例改了要重新跑 evalset。

---

## 8. 检测 "示例过拟合"

模型可能学了示例风格但失去通用性。检测：

```python
# evalset 里特意放一些"和示例风格差异大"的样本
def detect_example_overfitting(evalset, prompt_v):
    by_style = defaultdict(lambda: {"pass": 0, "total": 0})
    for sample in evalset:
        style = sample.get("style", "default")
        result = run(prompt_v, sample["input"])
        by_style[style]["total"] += 1
        if evaluate(result, sample):
            by_style[style]["pass"] += 1
    
    for style, stats in by_style.items():
        print(f"{style}: {stats['pass']}/{stats['total']}")
```

如果 "示例风格匹配的" 都 95%、"风格不匹配的" 都 50% → 过拟合，要多样化示例。

---

## 9. demo：示例的影响

```python
# demos/techniques/06_examples_impact.py
import anthropic
client = anthropic.Anthropic()

SYS_NO_EX = """把用户反馈分到 bug / feature / complaint / praise。只返回类别名。"""

SYS_WITH_EX = SYS_NO_EX + """

<examples>
<example>
<input>App 闪退</input>
<output>bug</output>
</example>
<example>
<input>请加深色模式</input>
<output>feature</output>
</example>
<example>
<input>用得真顺，再也不用了</input>
<output>complaint</output>
</example>
</examples>
"""

TESTS = [
    "我不会再用这个产品",            # complaint
    "希望能支持夜间模式",            # feature  
    "崩溃了",                       # bug
    "好用",                         # praise
    "客服真贴心，再也找不到这么差的", # complaint (反讽)
]

for sys, name in [(SYS_NO_EX, "no examples"), (SYS_WITH_EX, "with examples")]:
    right = 0
    print(f"\n=== {name} ===")
    expected = ["complaint", "feature", "bug", "praise", "complaint"]
    for text, exp in zip(TESTS, expected):
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            temperature=0,
            system=sys,
            messages=[{"role": "user", "content": text}],
        )
        actual = resp.content[0].text.strip().lower()
        ok = actual == exp
        right += ok
        print(f"  {'✅' if ok else '❌'} {exp:12s} → {actual:12s} | {text}")
    print(f"  通过 {right}/{len(TESTS)}")
```

预期：反讽 case 上加示例后明显改善。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **示例数量 > 10** | 通常拆任务或换 RAG |
| **示例格式不一致** | 严格统一 |
| **示例都来自训练数据集** | 模型可能"记得"，泛化差 |
| **示例 leak 测试集** | evalset 里的样本不能进 prompt |
| **手编 vs 真实数据** | 用真实数据 |
| **示例输出含解释 / 旁注** | 模型也会学，输出冗余 |
| **改示例顺便改任务** | 评测变量乱 |

---

## 11. 下一步

- 📖 边界 / 拒绝 → [07-boundaries-refusal.md](./07-boundaries-refusal.md)
- 📖 self-critique → [08-self-critique.md](./08-self-critique.md)
- 📖 dynamic few-shot（RAG-based） → [04-advanced/03-rag-prompting.md](../04-advanced/03-rag-prompting.md)

## 参考资料

- "Rethinking the Role of Demonstrations" (Min et al.): https://arxiv.org/abs/2202.12837
- Anthropic Multishot Prompting: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
