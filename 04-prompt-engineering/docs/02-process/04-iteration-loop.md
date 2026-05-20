# PE Process 04：迭代闭环 —— 看失败 → 假设 → 改 → 验证

> **一句话**：Stage 4 是 PE 最耗时的环节——核心是一个严格的循环：**只看失败、只改一处、必须回归**。把这个循环走十次，prompt 通过率从 60% 涨到 90% 比"一次性想出完美 prompt" 现实得多。

---

## 1. 循环骨架

```
[当前版本 v_n + 上次评测结果]
   ↓
1. 看失败样本：把失败按"模式"分类
   "10 条失败里 6 条是长度问题、3 条是漏字段、1 条是其他"
   ↓
2. 提假设：针对**最大的失败类**
   "加'长度严格 5-20 字'约束应该能解决这 6 条"
   ↓
3. 改 prompt：**只改一处**
   ↓
4. 跑同一份 evalset，得到 v_{n+1} 的指标
   ↓
5. 三向比较：
   - 之前失败的：变好了几条？
   - 之前通过的：挂了几条？（regression）
   - 整体通过率变化？
   ↓
6. 决策：
   - 净正 → 接受 → v_{n+1} 成为新基线
   - 净负 → 回滚 → 重新提假设
   - 净 0 / 互有胜负 → 看哪边失败更严重
   ↓
[继续下一轮]
```

---

## 2. Step 1：把失败"归类"

20 条失败样本一字排开你会无从下手——必须先聚类。

### 怎么聚类

人工方式：直接拉 spreadsheet 看每条的 errors 字段，按"看着像一类"归。

LLM 辅助：

```python
def cluster_failures(failures: list[dict]) -> dict:
    """让 LLM 把失败案例聚类"""
    text = "\n".join(
        f"- input: {f['input'][:50]}\n  output: {f['output']}\n  errors: {f['errors']}"
        for f in failures[:30]
    )
    prompt = f"""下面是一些 LLM 分类器的失败案例。请把它们按"失败模式"聚类，给出最多 5 个簇。

每个簇返回:
- name: 这一类失败的简短描述
- count: 这类有几条
- example_ids: 这类的样本编号
- hypothesis: 这类可能的根因猜测

{text}

返回 JSON: [{{...}}, ...]
"""
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        temperature=0,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(resp.content[0].text)
```

输出大致这样：

```json
[
  {
    "name": "长度超限",
    "count": 6,
    "example_ids": ["s003", "s014", ...],
    "hypothesis": "模型没把'5-20字'当强约束，需要在 prompt 末尾再强调"
  },
  {
    "name": "缺 source_quote 字段",
    "count": 3,
    "example_ids": ["s002", "s011", ...],
    "hypothesis": "字段约束写在中间被忽视"
  },
  ...
]
```

---

## 3. Step 2：提假设——但只针对最大簇

很多人看到 5 个失败簇，想一次都修——结果改 5 处，效果不可分析。

**铁律**：每轮迭代只针对**最大的那个簇**。

```
失败聚类:
  ▒▒▒▒▒▒ 长度超限 (6) ← 选这个
  ▒▒▒    缺字段 (3)
  ▒      其他 (1)

针对"长度超限"提假设:
  A. 长度约束在中间被忽视 → 移到末尾 + 加 "重要"
  B. 模型没理解"字"的概念（按 token 算了）→ 改成 "10-30 字（汉字/英文单词）"
  C. 示例里有 22 字的标题做了正例 → 改示例

挑 A 最容易也最便宜，先试。
```

提假设的**好习惯**：

- 假设要**具体**："加约束" → "在末尾加'重要：长度 5-20 字'"
- 假设要**可验证**：能用 evalset 测出来
- 假设要**便宜**：每轮成本最低先试
- 一次**一个**假设，不要绑

---

## 4. Step 3：改 prompt——只改一处

最难做到的一条：忍住不"顺便"改别的。

```python
# v_n
prompt_vn = """
你是新闻编辑。把下面新闻总结成吸引人的标题。

约束：
- 长度 5-20 字
- 必须包含核心 keyword
- 不要标题党词

只返回标题。
"""

# v_{n+1}: 只改一处
prompt_vn_plus_1 = """
你是新闻编辑。把下面新闻总结成吸引人的标题。

约束：
- 长度 5-20 字
- 必须包含核心 keyword
- 不要标题党词

只返回标题。

重要：长度必须严格 5-20 字，超长会被截断。
"""
```

**别**顺便改第 2 处约束顺序、第 3 处加 few-shot 示例。

### "忍不住"怎么办

如果脑子里冒出"诶这里顺手也改一下"——记到 TODO 文档：

```markdown
# Prompt TODO (随手记)
- 把"必须包含核心 keyword"改成 "至少包含一个 keyword"（先这轮做长度，下一轮再做这个）
- 加 1 个反讽的 few-shot 示例
- 试试把示例和约束顺序换一下
```

下一轮、再下一轮、再下一轮——一次解决一个。

---

## 5. Step 4：跑同一份 evalset

evalset 不变 + 只 prompt 变 → 唯一变量。

```bash
python eval_runner.py prompts/v1.txt evalset/v1.0.jsonl > results/v1.json
python eval_runner.py prompts/v2.txt evalset/v1.0.jsonl > results/v2.json
```

注意：

- ❌ 一边改 prompt 一边改 evalset
- ❌ 一边改 prompt 一边换模型
- ❌ 一边改 prompt 一边调 temperature
- ✅ 只 prompt 变，其他全冻结

---

## 6. Step 5：三向比较

不要只看"总通过率从 80 → 85"——这只告诉你净变化，不告诉你**结构**。

### 必看三个数

```python
def compare(prev: dict, curr: dict) -> dict:
    prev_pass = {f["id"] for f in prev["failures"]}
    curr_pass = {f["id"] for f in curr["failures"]}

    # 之前失败现在通过了
    fixed = prev_pass - curr_pass
    # 之前通过现在挂了（regression！）
    broken = curr_pass - prev_pass
    # 两次都失败
    still_failing = prev_pass & curr_pass

    return {
        "fixed": list(fixed),
        "broken": list(broken),
        "still_failing": list(still_failing),
        "net": len(fixed) - len(broken),
    }
```

### 决策表

| fixed | broken | 决策 |
|-------|--------|------|
| 6 | 0 | 完美，接受 ✅ |
| 6 | 1 | 看 broken 那条严不严重；多半接受 |
| 6 | 3 | 互有胜负——分析 trade-off |
| 0 | 0 | 假设不成立；不要 merge |
| 0 | 3 | 改坏了，回滚 ❌ |
| 3 | 6 | 改坏了，回滚 |

### Regression 是大警报

`broken` > 0 意味着改 prompt 让原来好的变差了。这种情况要：

1. **看 broken 的样本** —— 是不是某种 happy path 类被牺牲了
2. **加进 regression set** —— 防止未来同类问题
3. **重新假设** —— 也许改的方向不对

---

## 7. Step 6：记录迭代历史

每个版本都要留 CHANGELOG：

```markdown
# prompts/feedback-classifier/CHANGELOG.md

## v0.3.0 (2026-05-20)
- 改了什么：末尾加"重要：长度 5-20 字"
- 假设：长度约束在中间被忽视
- evalset: v1.0
- 结果：78% → 84%
  - fixed: s003, s014, s021, s033, s045
  - broken: s019（之前通过的，现在长度截断把 keyword 切掉了）
- 决策：接受；s019 加入 regression set 下轮处理

## v0.2.0 (2026-05-19)
- 改了什么：加了"反讽按真实意图归类"约束
- ...
```

CHANGELOG **不是**给老板看的——是给三个月后回来排查的你自己看的。

---

## 8. 一轮迭代的时间预算

一个**熟练**的迭代轮次：

| 步骤 | 时间 |
|------|------|
| 看失败 + 聚类 | 15-30 分钟 |
| 提假设 + 讨论 | 5-10 分钟 |
| 改 prompt | 5-10 分钟 |
| 跑 evalset（50 条 + Haiku） | 1-3 分钟 |
| 看结果 + 决策 + CHANGELOG | 10 分钟 |
| **合计** | **30-60 分钟** |

一天能跑 5-8 轮。从 60% 通过率到 90% 通常 5-15 轮足够。

如果一轮花你 2 小时——多半是没把 evalset 跑起来自动化。

---

## 9. 关键技巧：用 git 管理迭代

每轮迭代是一个 commit：

```bash
git checkout -b prompt/feedback-classifier-iter
# 改 prompt
git add prompts/feedback-classifier/v0.3.0.txt
git commit -m "v0.3.0: 末尾加长度强调；78%→84%"
# 跑 evalset 把结果也存
git add results/v0.3.0.json
git commit -m "v0.3.0 results"
```

任何一版改坏了：`git revert` 秒级回滚。

---

## 10. 完整 demo：迭代一轮

```python
# demos/process/04_iteration_one_round.py
"""模拟一轮 PE 迭代：读 evalset、跑 v1 / v2、比较、给建议"""
import json
import sys
from pathlib import Path

from collections import defaultdict


def cluster_failures(failures: list[dict]) -> dict:
    """按 error 类型简单聚类"""
    by_type = defaultdict(list)
    for f in failures:
        for e in f.get("errors", []):
            if "category:" in e:
                by_type["wrong_category"].append(f)
            elif "confidence:" in e:
                by_type["low_confidence"].append(f)
            else:
                by_type["other"].append(f)
    return {k: len(v) for k, v in by_type.items()}


def diff(prev: dict, curr: dict) -> dict:
    prev_failed = {f["id"] for f in prev["failures"]}
    curr_failed = {f["id"] for f in curr["failures"]}
    fixed = prev_failed - curr_failed
    broken = curr_failed - prev_failed
    return {
        "fixed_count": len(fixed),
        "fixed_ids": sorted(fixed),
        "broken_count": len(broken),
        "broken_ids": sorted(broken),
        "net": len(fixed) - len(broken),
        "prev_rate": prev["total_pass_rate"],
        "curr_rate": curr["total_pass_rate"],
        "delta_pct": (curr["total_pass_rate"] - prev["total_pass_rate"]) * 100,
    }


def decide(d: dict) -> str:
    if d["broken_count"] == 0 and d["fixed_count"] > 0:
        return "✅ 接受 v_{n+1}（修了 {} 条，没有 regression）".format(d["fixed_count"])
    if d["broken_count"] > 0 and d["fixed_count"] == 0:
        return "❌ 回滚（破坏了 {} 条，没有改进）".format(d["broken_count"])
    if d["net"] > 0:
        return f"✅ 接受 v_{{n+1}}（净正 {d['net']}，但破坏了 {d['broken_count']} 条，加进 regression）"
    if d["net"] < 0:
        return f"❌ 回滚（净负 {-d['net']}）"
    return "⚠️ 净 0，看具体 broken 严重度决定"


def main():
    prev = json.loads(Path(sys.argv[1]).read_text())
    curr = json.loads(Path(sys.argv[2]).read_text())

    print(f"\n=== {Path(sys.argv[1]).stem} vs {Path(sys.argv[2]).stem} ===\n")

    print("=== 失败聚类 ===")
    print("prev:", cluster_failures(prev["failures"]))
    print("curr:", cluster_failures(curr["failures"]))

    print("\n=== Diff ===")
    d = diff(prev, curr)
    print(json.dumps(d, ensure_ascii=False, indent=2))

    print("\n=== 决策 ===")
    print(decide(d))


if __name__ == "__main__":
    main()
```

跑：

```bash
python demos/process/04_iteration_one_round.py results/v1.json results/v2.json
```

---

## 11. 常见坑

| 坑 | 后果 |
|----|------|
| **一次改多处** | 不知道哪一处的功劳 / 锅 |
| **不看 regression** | 隐性退化累积，整体趋势好但用户感知差 |
| **不写 CHANGELOG** | 三个月后回来不知道当时为什么改 |
| **失败聚类时人工随便分** | 假设没针对性，改了没用 |
| **迭代到 92% 死磕到 99%** | 投入产出比急剧下降，详见 [05-when-to-stop.md](./05-when-to-stop.md) |
| **evalset 一边迭代一边补** | 评测变量改了，前后不可比 |
| **不版本化 prompt** | 改坏了无法回滚 |

---

## 12. 下一步

- 📖 何时停止迭代 → [05-when-to-stop.md](./05-when-to-stop.md)
- 📖 反模式合集 → [06-anti-patterns.md](./06-anti-patterns.md)
- 📖 实战完整闭环 → [08-practice/01-build-classifier.md](../08-practice/01-build-classifier.md)
- 📖 各种技法（用于"提假设"时挑工具） → 03-techniques / 04-advanced

## 参考资料

- "How to evaluate LLMs in production": https://www.langchain.com/blog/
- "Iterating on prompts": OpenAI Cookbook
