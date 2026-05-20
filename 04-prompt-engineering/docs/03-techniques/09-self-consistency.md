# PE Technique 09：Self-Consistency —— 采样投票

> **一句话**：同一个 prompt 跑 N 次（temperature > 0），按答案投票——在推理任务上比单次跑稳定且准确。成本 N 倍但准确率涨 5-15%。

---

## 1. 思路

```
[问题] → run 5 次 (temperature=0.7)
   ├→ "答案 A"
   ├→ "答案 A"
   ├→ "答案 B"
   ├→ "答案 A"
   └→ "答案 C"
        ↓
       投票: A (3) > B (1) = C (1)
        ↓
     [最终答案: A]
```

理论依据：错误是"随机"的（不同路径走偏），正确答案是"收敛"的（多条推理路径都到 A）。投票放大正确信号。

---

## 2. 何时用

| 场景 | 用 SC? |
|------|--------|
| 多步推理 / 数学 | ✅ 强推 |
| 多类分类（边界判断） | ✅ |
| 抽取（多字段） | ⚠️ 字段独立投票 |
| 创意生成 | ❌ 不适用（每次都该不同） |
| 简单答题 | ❌ 浪费 |

---

## 3. 代码

```python
# demos/techniques/09_self_consistency.py
from collections import Counter
import anthropic

client = anthropic.Anthropic()


def run_once(question: str, temp: float = 0.7) -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        temperature=temp,
        system="你是数学助手。先想，最后一行只写答案数字。",
        messages=[{"role": "user", "content": question}],
    )
    text = resp.content[0].text
    # 取最后一行作为答案
    return text.strip().split("\n")[-1].strip()


def self_consistency(question: str, n: int = 5, temp: float = 0.7) -> tuple[str, int]:
    """跑 n 次，投票"""
    answers = [run_once(question, temp) for _ in range(n)]
    counter = Counter(answers)
    most_common, count = counter.most_common(1)[0]
    return most_common, count


if __name__ == "__main__":
    q = "如果 a + b = 30，a - b = 4，那么 a*b 等于多少？只返回数字。"

    print("=== 单次（temp=0.7） ===")
    for _ in range(5):
        print(" ", run_once(q))

    print("\n=== Self-consistency n=5 ===")
    ans, cnt = self_consistency(q, n=5)
    print(f"  最终答案: {ans}（{cnt}/5 票）")
```

---

## 4. 投票变体

### 4.1 多数投票（简单）
最常见。

### 4.2 加权投票
不同采样按"confidence"权重投：

```python
results = [(answer, confidence) for ... ]
weighted = defaultdict(float)
for a, c in results:
    weighted[a] += c
final = max(weighted, key=weighted.get)
```

### 4.3 多字段投票
每个字段独立投票（不要整体投）：

```python
results = [
    {"name": "Alice", "age": 30},
    {"name": "Alice", "age": 30},
    {"name": "Alice", "age": 25},  # age 不同
]
final = {
    "name": Counter(r["name"] for r in results).most_common(1)[0][0],
    "age": Counter(r["age"] for r in results).most_common(1)[0][0],
}
```

### 4.4 LLM-as-aggregator
把 N 个答案给一个 strong 模型，让它选 / 综合：

```
"下面是同一个问题的 5 个候选答案。选最可能正确的，或者综合给一个。

候选:
1. ...
2. ...
...
"
```

适合答案不是离散值（写作 / 多段文本）。

---

## 5. N 怎么选

| N | 准确率提升 | 成本 |
|---|------------|------|
| 1 | baseline | 1x |
| 3 | +3-5% | 3x |
| 5 | +5-10% | 5x |
| 10 | +8-12% | 10x |
| 20 | +9-13%（边际递减） | 20x |

经验值：**N=5** 通常是 ROI 最优。

---

## 6. Temperature 选择

```
N 次采样必须 temperature > 0，否则全一样没意义。
```

| Temperature | 多样性 | 推荐 |
|-------------|--------|------|
| 0.3 | 微小变化 | 太少多样性，意义不大 |
| 0.7 | 中等 | ✅ 推荐 |
| 1.0 | 高 | 推理任务上稳 |
| 1.2+ | 偏离 | 可能出现"乱跑" |

---

## 7. 应用：分类器涨准确率

```python
def classify_with_sc(text: str, n: int = 5) -> dict:
    results = [classify(text, temperature=0.7) for _ in range(n)]
    cats = Counter(r["category"] for r in results)
    most_cat, cnt = cats.most_common(1)[0]
    
    # 一致性 = 多数票占比
    consistency = cnt / n
    
    return {
        "category": most_cat,
        "confidence": consistency,
        "votes": dict(cats),
    }
```

副产品：投票分布天然给出 **confidence**——比模型自报 confidence 客观。

---

## 8. 何时不要用 SC

| 不要 | 原因 |
|------|------|
| 创意生成 | 每次本来就该不同 |
| 简单 deterministic 任务 | temperature=0 即可 |
| 实时聊天 | 延迟 5x |
| 成本敏感 | 5x cost |
| 答案空间无限 | 投票无意义（每次都不同字符串） |

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **temperature=0 跑 SC** | 全一样，没意义 |
| **答案格式不统一** | 没法投票，先规整格式 |
| **N=1 也叫 SC** | 不是 SC，是普通跑 |
| **N 过大** | 收益递减，cost 飙 |
| **生成任务用 SC** | 不适用 |
| **没看分布只看 winner** | "3:2:1" 的赢家不如"5:0:0"可靠，要看分布 |

---

## 10. 下一步

- 📖 delimiter 选择 → [10-delimiters.md](./10-delimiters.md)
- 📖 进阶模式（ToT / PoT） → [04-advanced/](../04-advanced/)
- 📖 实战 → [08-practice/02-research-agent.md](../08-practice/02-research-agent.md)

## 参考资料

- "Self-Consistency Improves CoT" (Wang et al. 2022): https://arxiv.org/abs/2203.11171
