# PE Process 02：从需求到 Prompt v0

> v0 不需要完美，它是能跑通、能拿去评测的最朴素版本。这篇讲我从模糊需求走到 v0 的固定做法：先问 7 个澄清问题，再套一个几行的起步模板，30 分钟内跑出基线。

---

## 1. 七个澄清问题

接到任务后先别写 prompt，把下面 7 个问题问清楚，答案尽量写进 spec.md。

### Q1：输入规格
- 文本长度范围（一行？500 字？几万字？）
- 语言（中？英？混？多语言？）
- 格式（自由文本？markdown？HTML？JSON？）
- 来源（用户输入？数据库？文档解析？）
- 干净度（已清洗？还是含 HTML 标签 / 错别字 / 表情符号？）

### Q2：输出规格
- 字段（哪些是必返？哪些可选？）
- 类型（string / int / enum / 嵌套对象？）
- 长度约束（最多 N 字？固定字段数？）
- 语言（要和输入一致还是固定中文？）

### Q3：类别 / 标签清单（如果是分类抽取）
- 完整 enum 列出
- 互斥还是可多标？
- 兜底类别（`other` / `unknown`）是不是必须？

### Q4：模糊 / 边界处理
- 输入是空字符串怎么办？
- 输入和任务无关怎么办（比如分类器收到一段乱码）？
- "看起来像 A 又像 B" 选哪个？
- 是否需要 confidence score？

### Q5：错误成本（最容易被漏掉）
- 误判 A 当 B，损失是什么？（业务影响、用户感知、补救成本）
- 漏判（false negative）和误判（false positive）哪个更严重？
- 要不要设一个拿不准就拒绝或转人工的兜底？

### Q6：性能预算
- QPS / 每天调用量
- p99 延迟目标
- 每月成本预算
- 同步 vs 异步（用户等响应 vs 批处理）

### Q7：评测方式
- 有没有现成的标注数据？
- 谁负责标注 / 评审？
- 标注成本（人工 5 块/条 vs LLM judge 5 分钱/条）
- 上线后怎么持续抽样

---

## 2. 例子：用 7 个问题澄清"客服反馈分类"

原始需求只有一行："帮我把客户反馈分类"。

按 7 个问题逐条澄清后，得到这样一份 spec：

```markdown
# 客服反馈分类 PE Spec

## Q1 输入
- 长度：1-2000 字，多数 < 200
- 语言：中英文混合，emoji 多
- 来源：APP 内反馈表单 + 应用商店评论
- 干净度：含 emoji、错别字、有时含 HTML

## Q2 输出
- JSON: {category: enum, confidence: 0-1, reasoning: <50字}
- category 必返，confidence 必返，reasoning 可选

## Q3 类别
互斥 8 类：
- bug：报告软件问题
- feature_request：功能建议
- complaint：抱怨（含服务、产品、价格）
- praise：好评
- question：使用问题
- billing：账单 / 支付相关
- account：账号 / 登录 / 密码
- other：上述外

## Q4 边界
- 空输入 → other + confidence=0
- 含反讽（"棒棒棒，再也不用了"）→ 优先按真实意图归类（complaint）
- 同时含 bug + feature_request → 选最主要的那个 + reasoning 标注另一个

## Q5 错误成本
- 漏判 bug（误归 other）= 业务最大损失（漏 escalation）
- 漏判 complaint（误归 praise）= 用户体验差
- confidence < 0.6 → 自动转人工

## Q6 性能预算
- QPS: 10
- p99: 2 秒
- 月预算: 含 LLM 调用 + judge 评测，$500 内
- 异步处理

## Q7 评测
- 已有标注数据 300 条（QA 团队半年来的人工标注）
- 抽 200 条做 evalset、100 条做线上抽测
- 每周抽线上 20 条 QA 人工 review
- judge 用 Claude Opus（generator 用 Haiku）
```

原本只有一行的需求，就这样变成了一份各项都有答案的 spec。

---

## 3. v0 的最小模板

从 spec 到 v0，我一般套这个模板：

```
你是 <身份>，专门处理 <任务领域>。

任务：根据用户输入，<具体动作>。

<必要约束（3-5 条 bullet）>

<输出格式（用 JSON Schema 或示例）>
```

### 客服分类器 v0

```python
PROMPT_V0 = """你是客服反馈分类师，擅长按业务类别快速归类用户反馈。

任务：把用户反馈分类到以下 8 个类别之一：
bug / feature_request / complaint / praise / question / billing / account / other

约束：
- 类别名必须是上述 8 个之一
- 如果反馈和上述无关或为空，归 other
- 反讽内容按真实意图归类（如"再也不用了"归 complaint）

输出 JSON 格式：
{
  "category": "<8 个类别之一>",
  "confidence": 0.0-1.0,
  "reasoning": "<不超过 50 字的判断依据>"
}

只返回 JSON，不要任何解释。
"""

def classify(feedback: str) -> dict:
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=PROMPT_V0,
        messages=[{"role": "user", "content": feedback}],
    )
    return json.loads(resp.content[-1].text)
```

v0 故意不用 CoT、Few-shot 和 XML 标签，为的是先看清基线在哪。

---

## 4. v0 应该满足的检查清单

写完 v0 自查：

- [ ] 能跑：随便给 3 个样本，都能拿到合法输出
- [ ] 遵守约束：输出格式 100% 正确（达不到就先调这一条）
- [ ] 没有编造：不在 enum 里的类别不会出现
- [ ] 空输入有兜底：给 `""` 能返回合理结果
- [ ] 超长输入有兜底：给 5000 字不报错

这 5 条都满足，v0 就够了。效果差没关系，那是 Stage 4 要解决的。

---

## 5. 跑 v0 的最小 evalset

v0 不用等完整的 evalset，先拿 5 到 10 个手写样本跑起来：

```python
# 临时跑 v0 的"试运行"样本
PROBE_SAMPLES = [
    # happy path
    "App 一打开就闪退",                      # → bug
    "希望能加上深色模式",                    # → feature_request
    "客服态度太差了！！",                    # → complaint
    "用得很顺手，五星好评",                  # → praise
    "怎么改密码？",                          # → question
    # edge
    "",                                       # → other
    "😡😡😡 退钱！我要退款",                # → billing or complaint
    # 反讽
    "棒棒棒，再也不用你们的服务了",          # → complaint （不是 praise）
    # 长输入
    "我从 2024 年开始用..." + "..." * 200,
]

for sample in PROBE_SAMPLES:
    result = classify(sample)
    print(f"{result['category']:20s} {sample[:30]}")
```

跑完先粗看一眼：

- 类别基本对得上：进入 Stage 3，建完整 evalset
- 大量分错：回头看 prompt 措辞，可能 enum 描述得不够清楚
- 输出格式都不对：加 structured output 或 tool use（见 [PE Technique 05：结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)）

---

## 6. 什么时候 v0 可以复杂一点

v0 默认要朴素。但有些任务本身就复杂，朴素版注定失败，这时可以一开始就加些东西：

| 情形 | v0 该带 |
|------|---------|
| 输出是嵌套 JSON | 用 structured output（tool use / response_format） |
| 任务含多步推理 | 加 "请一步步思考" 的 CoT |
| 输出风格非常具体 | 加 1-2 个 few-shot 示例 |
| 类别数 > 10 | 类别描述每个一句话（不只是 enum 名） |
| 任务有强外部知识 | 加业务背景段落 |

v0 是为了摸清在当前模型和任务难度下，朴素版本能做到什么程度。如果你已经预判朴素版会败得很惨，可以直接写一个中等复杂度的 v0，省掉一轮迭代。

---

## 7. 选模型的几条规矩

v0 阶段就要选好基线模型：

| 任务类型 | v0 推荐模型 |
|---------|-------------|
| 分类 / 抽取（简单） | Claude Haiku 5.5 / GPT-6 Luna / Gemini Flash-Lite / qwen-flash |
| 抽取（复杂嵌套） | Claude Sonnet 5.5 / GPT-6.1 Terra |
| 生成 / 创意 | Claude Sonnet 5.5 / GPT-6.1 Sol |
| 长链推理 / Agent | Claude Opus 5.5（高 effort）/ GPT-6 Astra / Gemini 4 Argon |
| 多模态 | Claude Sonnet 5.5 / GPT-6 / Gemini 3.8 Flash |

我的几条规矩：

1. 先用小模型。小模型能解决的就别上大模型。以 Claude 为例，Haiku 5.5 输入 $0.10/M、Sonnet 5.5 $2/M，单价差 20 倍，延迟也明显更低。
2. 同一个任务对比两个模型。v0 在 Haiku 和 Sonnet 上各跑一遍，按差距决定后面投入多少。
3. 分清是能力不够还是 prompt 不够。大模型也失败的话，问题多半在任务难度、输入歧义或上下文不足，不是 prompt 能解决的。

---

## 8. 完整 demo：30 分钟从需求到 v0

```python
"""客服分类 v0 起步"""
import json
import os
import anthropic

client = anthropic.Anthropic()

PROMPT_V0 = """你是客服反馈分类师。

任务：把用户反馈分类到以下 8 个类别之一：
bug / feature_request / complaint / praise / question / billing / account / other

约束：
- 类别名必须是 8 个之一
- 反馈为空 / 无关 / 乱码 → other
- 反讽按真实意图归类

输出 JSON：
{"category": "...", "confidence": 0.0-1.0, "reasoning": "..."}

只返回 JSON。
"""

PROBE = [
    ("App 一打开就闪退", "bug"),
    ("希望加深色模式", "feature_request"),
    ("客服太差", "complaint"),
    ("五星好评", "praise"),
    ("怎么改密码", "question"),
    ("", "other"),
    ("😡 退款！", "billing"),
    ("棒棒，再也不用了", "complaint"),
]


def classify(feedback: str) -> dict:
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=PROMPT_V0,
        messages=[{"role": "user", "content": feedback or "(empty)"}],
    )
    return json.loads(resp.content[-1].text)


def main():
    right, wrong = 0, []
    for text, expected in PROBE:
        result = classify(text)
        actual = result.get("category")
        ok = actual == expected
        if ok:
            right += 1
        else:
            wrong.append({"text": text, "expected": expected, "actual": actual, "reasoning": result.get("reasoning")})
        print(f"{'✅' if ok else '❌'} {expected:20s} → {actual:20s}  {text[:30]}")
    print(f"\n基线: {right}/{len(PROBE)}")
    if wrong:
        print("失败案例:")
        for w in wrong:
            print(f"  {w}")


if __name__ == "__main__":
    main()
```

把上面的代码保存为 `demos/process/02_v0_classifier.py` 后运行：

```bash
python demos/process/02_v0_classifier.py
```

预期 8 个 probe 能对 5 到 7 个。这就是基线分，之后每一版都和它比。

---

## 9. v0 阶段的交付物

v0 阶段结束时，应该有这些东西：

```
feature-name/
├── spec.md                 # Stage 1 的 7 问澄清
├── prompts/
│   └── v0/
│       ├── system.txt
│       └── notes.md        # 为什么这样写
├── probe.py                # Stage 2 的 5-10 条试运行样本
└── baseline_score.txt      # v0 在 probe 上的分数
```

第一天结束时把它们交出来。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| 跳过 spec 直接写 prompt | 之后每轮迭代都在重新发现需求 |
| v0 一上来就用 CoT + Few-shot + XML | 没有朴素版的基线，判断不了是不是过度设计 |
| probe 只有 happy path | 问题暴露不出来，到 Stage 3 才发现 |
| probe 用了线上真实数据但没脱敏 | 有隐私合规风险 |
| v0 用最强的模型 | 以后换不下来（一换 Haiku 效果就崩），成本也降不下去 |
| v0 把所有约束都堆进去 | 不知道哪条约束真起了作用 |

---

## 11. 下一步

- [建评测集，从 5 条到 500 条](./03-build-evalset.md)
- [迭代闭环（看失败、提假设、修改、验证）](./04-iteration-loop.md)
- [评测先于 Prompt](../01-foundations/05-eval-first.md)

## 参考资料

- "What is a Spec doc": https://eugeneyan.com/writing/llm-patterns/
- Anthropic Prompt Library: https://docs.anthropic.com/en/prompt-library
