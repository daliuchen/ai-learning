# PE Technique 09：Self-Consistency 采样投票

> 同一个 prompt 在有随机性的设置下跑 N 次，对答案投票。推理任务上，这比只跑一次更稳也更准：成本是 N 倍，在多步推理题上通常有几个百分点的提升，采样数越多边际收益越小。

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

背后的假设是：错误往往是随机的，每次走偏的方向不同；正确答案会收敛，多条推理路径最后都落到 A。投票就是把这个收敛的信号放大。

---

## 2. 什么时候用

| 场景 | 用 SC? |
|------|--------|
| 多步推理 / 数学 | 强烈推荐 |
| 多类分类（边界判断） | 用 |
| 抽取（多字段） | 看情况，每个字段单独投票 |
| 开放式写作 / 创意生成 | 不适合多数投票，每次本来就该不同（硬要用只能走 4.4 的 LLM 汇总） |
| 简单答题 | 不用，浪费 |

---

## 3. 代码

```python
from collections import Counter
import anthropic

client = anthropic.Anthropic()


def run_once(question: str) -> str:
    # 不显式传 temperature：Claude 5.x 已不接受该参数，默认就是采样；
    # 用 Qwen/OpenAI 兼容接口等仍支持的模型时可加 temperature=0.7
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system="你是数学助手。先想，最后一行只写答案数字。",
        messages=[{"role": "user", "content": question}],
    )
    text = resp.content[-1].text
    # 取最后一行作为答案
    return text.strip().split("\n")[-1].strip()


def self_consistency(question: str, n: int = 5) -> tuple[str, int]:
    """跑 n 次，投票"""
    answers = [run_once(question) for _ in range(n)]
    counter = Counter(answers)
    most_common, count = counter.most_common(1)[0]
    return most_common, count


if __name__ == "__main__":
    q = "如果 a + b = 30，a - b = 4，那么 a*b 等于多少？只返回数字。"

    print("=== 单次 ===")
    for _ in range(5):
        print(" ", run_once(q))

    print("\n=== Self-consistency n=5 ===")
    ans, cnt = self_consistency(q, n=5)
    print(f"  最终答案: {ans}（{cnt}/5 票）")
```

---

## 4. 投票的几种变体

### 4.1 多数投票
最常用，取票数最多的答案。

### 4.2 加权投票
每次采样按各自的置信度加权：

```python
results = [(answer, confidence) for ... ]
weighted = defaultdict(float)
for a, c in results:
    weighted[a] += c
final = max(weighted, key=weighted.get)
```

### 4.3 多字段投票
每个字段单独投票，不要对整个对象投：

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

### 4.4 用 LLM 汇总
把 N 个答案交给一个更强的模型，让它挑选或综合：

```
"下面是同一个问题的 5 个候选答案。选最可能正确的，或者综合给一个。

候选:
1. ...
2. ...
...
"
```

答案是多段文本时，多数投票投不出结果，只能用这种。开放式写作不适合多数投票，如果要用，只能让 LLM 汇总多个版本，效果不保证。

---

## 5. N 取多少

| N | 准确率变化（定性） | 成本 |
|---|------------|------|
| 1 | baseline | 1x |
| 3 | 开始有提升 | 3x |
| 5 | 多数收益在这一档拿到 | 5x |
| 10 | 还能再涨一点 | 10x |
| 20 | 基本不再涨（边际递减） | 20x |

Wang et al. 2022 的原论文在 GSM8K 等推理基准上报告了随采样数增加、提升逐渐变平的曲线，具体数字随模型和数据集差别很大。按经验，N=5 的投入产出比通常最好。

---

## 6. Temperature 怎么设

```
N 次采样必须是「有随机性」的：temperature > 0（能设的模型设 0.7）。
Claude 5.x 不接受 temperature 参数（传了返回 400），默认就按采样生成，直接多次调用即可；
用 temperature=0 跑，N 次结果几乎一样，投票没意义。
```

| Temperature | 多样性 | 推荐 |
|-------------|--------|------|
| 0.3 | 变化很小 | 多样性不够，意义不大 |
| 0.7 | 中等 | 推荐 |
| 1.0 | 高 | 推理任务上依然稳 |
| 1.2+ | 容易跑偏 | 可能出现离谱的答案 |

---

## 7. 应用：提升分类器准确率

```python
def classify_with_sc(text: str, n: int = 5) -> dict:
    results = [classify(text) for _ in range(n)]   # 能设 temperature 的模型传 0.7
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

还有个附带的好处：投票分布本身就是一个置信度，比让模型自报的置信度客观。

---

## 8. 什么时候不该用

| 场景 | 原因 |
|------|------|
| 开放式写作 / 创意生成 | 不适合多数投票；要用只能让 LLM 汇总多个版本，效果不保证 |
| 简单的确定性任务 | temperature=0 跑一次就够 |
| 实时聊天 | 延迟变成 5 倍 |
| 对成本敏感 | 成本变成 5 倍 |
| 答案空间无限 | 每次都是不同的字符串，投不出结果 |

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| 用 temperature=0 跑 SC | 每次结果一样，投票没意义 |
| 答案格式不统一 | 没法投票，先把格式规整 |
| 只跑一次也叫 SC | 那只是普通调用 |
| N 设得太大 | 收益递减，成本飙升 |
| 开放式生成任务做多数投票 | 投不出结果，改用 LLM 汇总或干脆不用 |
| 只看赢家不看分布 | 同样 6 票，3:2:1 的赢家远不如 6:0:0 可靠 |

---

## 10. 下一步

- [分隔符策略（XML / JSON / Markdown / 三引号）](./10-delimiters.md)
- [进阶章（ReAct、Tool Use 与 PoT 等）](../04-advanced/01-react.md)
- [Research Agent 的 5 版迭代史](../08-practice/02-research-agent.md)

## 参考资料

- "Self-Consistency Improves CoT" (Wang et al. 2022): https://arxiv.org/abs/2203.11171
