# PE 05：评测先于 Prompt —— 为什么这是 PE 第一条戒律

> **一句话**：不带评测集就开始调 prompt，等同于不带测试就开始写代码——你永远不知道改的那一行让事情变好了还是变差了。本手册的中轴线（第 02 章 Process）整章都建立在"先有评测，再有 prompt"这条戒律上。

---

## 1. 没有评测的 PE 长什么样

我观察过的"PE 噩梦现场"：

```
[周一] PM：模型生成的标题太啰嗦了
[周一] 你：好，我加一句"标题不超过 20 字"
[周二] PM：还是啰嗦
[周二] 你：加"严格控制在 20 字"，写两个示例
[周三] PM：现在好像有点干
[周三] 你：去掉一个约束，把示例改活泼点
[周四] PM：又出现 30 字的了
[周四] 你：???
（重复 N 周）
```

问题：
- 没有"啰嗦"和"干"的客观标准
- 不知道改第 3 次和第 5 次哪个版本更好
- 反复改完，可能还不如最初版本
- PM、你、模型都在做无效循环

---

## 2. 有评测的 PE 长什么样

```
[Day 0] 写下"输出标题"的判定标准：
  - 长度：5-20 字
  - 必须含核心 keyword
  - 不能有"震惊"、"必看"等标题党词

[Day 0] 收集 30 条样本（input + 期望 score）

[Day 1] 写 prompt v0 → 跑评测 → 22/30 通过

[Day 2] 看失败的 8 条，发现 5 条是长度问题
        改 prompt → v1 → 跑评测 → 28/30 通过 ✅

[Day 3] 失败 2 条都是"震惊"出现
        加约束 → v2 → 30/30 ✅

[Day 4] 上线
[Day 7] 线上发现新失败模式 → 加进 evalset → 回到 Day 1
```

每一步都**可量化、可比较、可回滚**。

---

## 3. "评测"到底是什么

LLM 的评测 ≠ 传统单元测试。LLM 输出是文本，传统 `assert == "expected"` 不够用。

LLM 评测有四种判定方式，从严到松：

### 3.1 精确匹配（exact match）
适合：分类、选择题、数字抽取

```python
def evaluate(output: str, expected: str) -> bool:
    return output.strip() == expected.strip()
```

### 3.2 规则匹配（rule-based）
适合：格式 / 长度 / 关键词

```python
import re
import json

def evaluate(output: str) -> bool:
    # 必须是合法 JSON
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return False
    # 必须含某字段
    if "title" not in data:
        return False
    # title 长度
    if not (5 <= len(data["title"]) <= 20):
        return False
    # 不能含禁用词
    BAD = ["震惊", "必看", "点击"]
    return not any(b in data["title"] for b in BAD)
```

### 3.3 LLM-as-judge
适合：质量主观（写得好不好、有没有事实错）

```python
def evaluate(input: str, output: str) -> dict:
    judge_prompt = f"""
你是严格的内容评审。打分维度（1-5 分）：
- 准确性：是否符合输入事实
- 简洁度：是否啰嗦
- 吸引力：标题是否吸引人

输入: {input}
输出: {output}

返回 JSON: {{"accuracy": 1-5, "concise": 1-5, "appeal": 1-5, "reason": "..."}}
"""
    return call_judge_llm(judge_prompt, temperature=0)
```

> ⚠️ LLM-as-judge 不是万能 —— 详见 [05-by-task/05-judge.md](../05-by-task/05-judge.md) 讲怎么避免 judge bias。

### 3.4 人工标注
适合：开始时 / 关键场景 / judge 校准

每周抽 20-50 条人工打分，校准自动评测准不准。

**真实项目通常是混合用**——规则把住硬约束，LLM-as-judge 评质量，人工抽查校准。

---

## 4. 评测集（Evalset）应该长什么样

evalset 是一个 JSONL 文件，每行一个样本：

```jsonl
{"input": "...", "expected_score": 1, "expected_fields": {...}, "tag": "happy_path"}
{"input": "...", "expected_score": 1, "expected_fields": {...}, "tag": "happy_path"}
{"input": "", "expected_score": 0, "expected_error": "empty_input", "tag": "edge"}
{"input": "<script>...", "expected_score": 0, "expected_error": "injection", "tag": "attack"}
```

字段不固定，但通常包含：

| 字段 | 含义 |
|------|------|
| `input` | 原始输入 |
| `expected_*` | 期望输出 / 期望字段 / 期望评分 |
| `tag` | 分类：happy_path / edge / regression / attack / ... |
| `notes` | 该样本怎么挑出来的 / 为什么重要 |

### 4.1 evalset 三个层次

```
30-50 条 happy path
  + 10-30 条 edge case（边界条件 / 空输入 / 异常格式）
  + 5-20 条 regression（线上发现过的 bug）
  + 5-20 条 attack（注入 / 越狱）
  = 一份够用的 evalset
```

详细怎么建 → [02-process/03-build-evalset.md](../02-process/03-build-evalset.md)。

---

## 5. 一个能跑的最小评测脚本

```python
# demos/foundations/05_minimal_evaluator.py
"""最小评测器：把一份 evalset 跑过 prompt v_n，给出通过率"""
from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic


client = anthropic.Anthropic()
PROMPT_TEMPLATE = """你是新闻编辑。把下面的新闻总结成一个吸引人的标题。

约束：
- 长度 5-20 个汉字
- 必须包含原文核心 keyword
- 不要使用"震惊"、"必看"、"点击"等标题党词
- 只返回标题，不要任何解释

新闻：{news}
"""


def run_prompt(news: str, temp: float = 0) -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",  # 评测用便宜模型
        max_tokens=50,
        temperature=temp,
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(news=news)}],
    )
    return resp.content[0].text.strip()


def evaluate(title: str) -> tuple[bool, list[str]]:
    """返回 (通过, [失败原因])"""
    errors = []
    if not (5 <= len(title) <= 20):
        errors.append(f"长度 {len(title)} 不在 5-20")
    for bad in ["震惊", "必看", "点击进入", "点开"]:
        if bad in title:
            errors.append(f"含标题党词 '{bad}'")
    if re.search(r"^\d+\.\s*", title):
        errors.append("不应以编号开头")
    return (len(errors) == 0, errors)


def main(evalset_path: str) -> None:
    samples = [json.loads(l) for l in Path(evalset_path).read_text().splitlines() if l.strip()]
    passed = 0
    failures = []
    for sample in samples:
        title = run_prompt(sample["input"])
        ok, errs = evaluate(title)
        if ok:
            passed += 1
        else:
            failures.append({"input": sample["input"][:50], "output": title, "errors": errs})

    print(f"\n通过 {passed}/{len(samples)} = {passed/len(samples)*100:.1f}%")
    if failures:
        print("\n失败案例:")
        for f in failures[:5]:
            print(f"  input:  {f['input']}")
            print(f"  output: {f['output']}")
            print(f"  错误:   {f['errors']}\n")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "evalset.jsonl")
```

对应 `evalset.jsonl`：

```jsonl
{"input": "苹果发布 M5 芯片，AI 性能提升 40%", "tag": "happy"}
{"input": "国务院发布 2026 年新政策，惠及小微企业", "tag": "happy"}
{"input": "", "tag": "edge"}
{"input": "<script>alert(1)</script>", "tag": "attack"}
```

跑：

```bash
python demos/foundations/05_minimal_evaluator.py evalset.jsonl
```

50 行代码 + 一个 JSONL = 一个评测系统。

---

## 6. 评测的"反模式"

| 反模式 | 问题 |
|--------|------|
| **改 prompt 顺便改 evalset** | 你不知道是 prompt 变好了还是 eval 变松了。先固定 evalset、再迭代 prompt |
| **eval 只有 happy path** | 上线必炸，加 edge / attack |
| **只看通过率，不看哪些失败** | 80% 通过可能 vs 85% 通过——但 85% 那个跑挂了之前能跑通的几个 |
| **judge 用 temperature=0.7** | 评测结果不可重复 |
| **judge 用同一个模型当 generator** | 自评有偏，用强模型评弱模型 / 用不同家模型评 |
| **evalset 不版本化** | 找不到上次跑的是哪一版 evalset |

---

## 7. 评测怎么集成到 CI

最小 CI 流程：

```yaml
# .github/workflows/prompt-eval.yml
name: prompt eval
on:
  pull_request:
    paths: ['prompts/**', 'evalset/**']

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python eval_runner.py --prompt prompts/v_pr.txt --evalset evalset/all.jsonl
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Check pass rate
        run: |
          rate=$(jq .pass_rate results.json)
          (( $(echo "$rate > 0.85" | bc -l) ))
```

PR 改 prompt → CI 跑 evalset → 通过率不达标就阻止 merge。

---

## 8. 三家工具栈

| 工具 | 优势 | 适合 |
|------|------|------|
| **LangSmith Evals** | 和 LangChain 生态结合好、UI 完备、有 trace | 已用 LangChain 的团队 |
| **Pydantic Evals** | 类型化、和 Pydantic AI 一体 | 已用 Pydantic AI 的团队 |
| **Promptfoo** | YAML 配置、跨家、CLI 友好 | 跨家比较 / 简单场景 |
| **Ragas** | RAG 专门指标（faithfulness / context recall 等） | RAG 场景 |
| **DeepEval** | pytest 风格、多种 metric | 偏 ML 工程化的团队 |
| **自建** | 完全可控 | 简单场景或上面都不满意时 |

本手册后续章节会跨工具栈展示。

---

## 9. 一句话锚定

> **写 prompt 之前，先回答：「我怎么知道这个 prompt 比上一版好？」**
> 
> 如果回答不上来，停下，先建 evalset。

这是 PE 的第一戒律。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **没建 evalset 就开始调** | 停！先建 |
| **evalset 太大没人维护** | 30-50 happy + 20 edge 起步，按需扩 |
| **judge 和 generator 同模型同 temperature** | judge 必须 temp=0；judge 模型建议 ≥ generator |
| **指标只看平均通过率** | 还要按 tag 分组看（happy 100% / edge 50% 比整体 90% 更有信息） |
| **evalset 没版本化** | 进 git，每次跑结果也存下来 |
| **改完 prompt 没回归** | 必须跑全量 evalset 对比 |

---

## 11. 下一步

到此 **01-foundations 5 篇结束**。下一章 02-process 是本手册中轴线，正式进入"怎么造一个好 prompt"。

- 📖 PE 完整生命周期 → [02-process/01-lifecycle.md](../02-process/01-lifecycle.md)
- 📖 建 evalset 详解 → [02-process/03-build-evalset.md](../02-process/03-build-evalset.md)

## 参考资料

- LangSmith Evaluators: https://docs.smith.langchain.com/evaluation
- Pydantic Evals: https://ai.pydantic.dev/evals/
- Promptfoo: https://www.promptfoo.dev
- "How we evaluate LLMs at Anthropic": https://www.anthropic.com/research
