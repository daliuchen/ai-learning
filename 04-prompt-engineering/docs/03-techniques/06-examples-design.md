# PE Technique 06：Few-shot 示例怎么设计

> Few-shot 效果好不好，看的是示例质量，不是数量：3 个精挑的例子胜过 10 个随手凑的。这篇整理了一份示例设计清单，帮你确认示例是在帮模型，而不是在干扰它。

---

## 1. 示例起什么作用

示例是写给模型看的操作说明，主要起四种作用：

| 作用 | 举例 |
|------|------|
| 定格式 | "输出应该长这样" |
| 定风格 | "语气这么写" |
| 示范边界 | "这种 edge case 你应该这样处理" |
| 定类别 | "这种输入归这个类" |

---

## 2. 好示例的五条标准

### 2.1 代表性优先于覆盖性
示例的分布应该接近真实数据。

```
真实分布: 70% bug, 15% feature, 10% complaint, 5% other
示例分布: 也应该接近这个比例

❌ 4 个示例每个类别 1 个（25/25/25/25）→ 模型以为各类等概率
✅ 7 个示例: 5 bug, 1 feature, 1 complaint  
```

### 2.2 包含关键的边界情况
留 1 到 2 个最容易出错的样例：

```
✅ 反讽分类（容易判错）
✅ 含 emoji（容易格式炸）
✅ 多类同时含（要选主类）
```

### 2.3 格式完全一致
所有示例的输出格式必须严格一致，字段顺序、缩进、引号都要统一。

```
❌ 示例 1: {"name": "A", "age": 30}
   示例 2: {"age": 30, "name": "B"}  ← 顺序变
   示例 3: { "name" : "C" , "age" : 30 }  ← 空格变
   → 模型困惑
✅ 全部 {"name": "...", "age": ...}
```

### 2.4 风格贴近真实场景
示例的语气、长度、复杂度要和真实输入一致。

```
❌ 示例都是 5 字短句，真实是 100 字段落 → 模型在长输入上表现差
✅ 示例和真实分布一致
```

### 2.5 不带多余信息
示例只放最关键的内容，不要写解释或旁注。

```
❌ "示例 1: 用户写了 'App 闪退'，这是一个 bug 报告，因为它表达了软件问题。
   所以应该输出 'bug'。"
✅ 输入: App 闪退
   输出: bug
```

---

## 3. 反例要不要给

可以故意给一个反例，告诉模型不该怎么做：

```
约束：JSON 输出严格符合 schema。

示例（正确）:
输入: Alice 30 岁
输出: {"name": "Alice", "age": 30}

反例（不要这样）:
输入: Alice 30 岁
输出: {"name": "Alice", "age": "30 岁"}   ← age 不应该是字符串
```

不过**反例要慎用**。示例对模型的牵引力很强，反例可能反而让它困惑。只有当反例和正例对比非常鲜明时才值得用。

---

## 4. 示例放在哪里

### 放在 system message
```
SYSTEM = """任务...
约束...

示例：
<example>...</example>
<example>...</example>
"""
```

适合示例固定、不随输入变化的情况。

### 放在 messages 数组（多轮形式）
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

在 OpenAI 的 Chat Completions 里常见这种写法，它更贴近对话的语义。

### 放在 XML 容器里（适合 Claude）
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

示例库很大时，按输入检索最相关的 K 个：

```python
def get_relevant_examples(user_input: str, k: int = 3) -> list[dict]:
    # 用向量检索从示例库挑最像的 k 个
    embedding = embed(user_input)
    return example_store.search(embedding, k=k)
```

适合不同业务线、不同客户需要不同示例，静态示例覆盖不全，并且手上有几百条以上标注样本的情况。

它和 prompt caching 冲突：示例每次都变，缓存就命中不了，需要自己权衡。

---

## 6. 示例从生产数据里来

别手编示例，从真实数据里挑：

1. 找 100 到 500 条线上数据
2. 人工标注期望输出
3. 按代表性加关键边界，挑出 5 到 10 条
4. 放进 prompt

这样示例的风格天然贴近真实输入，真实分布里的问题也会暴露出来，边界情况来自真实数据而不是想象。

---

## 7. 示例的迭代规则

改 prompt 时，示例按下面的情况调整：

| 触发 | 调整 |
|------|------|
| 发现新的失败模式 | 补 1 个针对该模式的正确示例 |
| 示例和真实分布有偏差 | 重新采样 |
| 模型被某种风格带偏 | 让示例更多样 |
| 示例偏短，真实输入偏长 | 换成真实的长样本 |
| 类别或业务规则更新了 | 换成新规则下的示例 |

示例只要改了，就要重新跑一遍评测集。

---

## 8. 检测示例过拟合

模型可能学会了示例的风格，却丢了泛化能力。检测办法是在评测集里专门放一些和示例风格差别很大的样本，按风格分组统计：

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

如果风格和示例接近的样本通过率 95%，风格差别大的只有 50%，就是过拟合了，示例需要更多样。

---

## 9. demo：示例的影响

```python
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
            model="claude-haiku-5-5",
            max_tokens=1024,
            system=sys,
            messages=[{"role": "user", "content": text}],
        )
        actual = resp.content[-1].text.strip().lower()
        ok = actual == exp
        right += ok
        print(f"  {'✅' if ok else '❌'} {exp:12s} → {actual:12s} | {text}")
    print(f"  通过 {right}/{len(TESTS)}")
```

预期加了示例之后，反讽样本的判断会明显改善。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| 示例超过 10 个 | 通常该拆任务或改用 RAG |
| 示例格式不一致 | 严格统一 |
| 示例取自公开训练数据集 | 模型可能背过，泛化差 |
| 评测集样本混进了 prompt | 评测集里的样本不能当示例 |
| 示例是手编的 | 换成真实数据 |
| 示例输出里带解释或旁注 | 模型也会学，输出变啰嗦 |
| 改示例时顺手改了任务 | 变量混在一起，评测结果没法比 |

---

## 11. 下一步

- [边界条件与 Refusal 行为塑造](./07-boundaries-refusal.md)
- [Self-Critique（自我反思）](./08-self-critique.md)
- [RAG Prompting 与引用约束](../04-advanced/03-rag-prompting.md)：检索式动态 few-shot 的延伸

## 参考资料

- "Rethinking the Role of Demonstrations" (Min et al.): https://arxiv.org/abs/2202.12837
- Anthropic Multishot Prompting: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
