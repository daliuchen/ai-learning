# PE Process 02：从需求到 Prompt v0

> **一句话**：v0 不是"完美的第一版"，而是"能跑通且能被评测"的最朴素版本。本篇给一套从模糊需求 → v0 的标准化流程：7 个澄清问题 → 5 行起步模板 → 30 分钟跑通基线。

---

## 1. 七个标准澄清问题

接到任务后**第一件事**不是写 prompt——是把下面 7 个问题问清楚。能写下来的就写在 spec.md：

### Q1：输入规格
- 文本长度范围（一句话？500 字？几万字？）
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

### Q5：错误成本（很重要！）
- 误判 A 当 B，损失是什么？（业务影响、用户感知、补救成本）
- 漏判（false negative）和误判（false positive）哪个更严重？
- 要不要"拿不准就拒绝/转人工"的兜底机制？

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

## 2. 真实样例：用 7 问澄清"客服反馈分类"

模糊需求："帮我把客户反馈分类"

通过 7 问澄清后变成：

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

模糊需求 → 7 问澄清 → 写得清清楚楚的 Spec。

---

## 3. v0 的"最小可用版"模板

把 Spec 转成 v0 的标准模板：

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
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        temperature=0,
        system=PROMPT_V0,
        messages=[{"role": "user", "content": feedback}],
    )
    return json.loads(resp.content[0].text)
```

v0 故意**不用** CoT / Few-shot / XML 标签——目的是看基线。

---

## 4. v0 应该满足的检查清单

写完 v0 自查：

- [ ] **能跑**：随便给 3 个样本能拿到合法输出
- [ ] **遵守约束**：输出格式 100% 正确（这一条达不到再调）
- [ ] **没瞎编字段**：不在 enum 里的类别不出现
- [ ] **空输入兜底**：给 `""` 返回合理结果
- [ ] **极长输入兜底**：给 5000 字不报错

满足这 5 条，v0 就够了。性能差不要紧——那是 Stage 4 的事。

---

## 5. 跑 v0 的最小 evalset

v0 不需要等完整 evalset，先用 **5-10 个手工样本**起跑：

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

跑完看输出，第一眼定性：

- 类别基本对得上 → 进入 Stage 3 建完整 evalset
- 大量分错 → 反思 prompt 措辞，可能 enum 描述不够清晰
- 输出格式都对不上 → 加 structured output / tool use（详见 03-techniques/05）

---

## 6. 何时该用更"高级"的 v0

默认 v0 最朴素。但有些任务"复杂程度本身高"，朴素版必败，可以一上来就堆点东西：

| 情形 | v0 该带 |
|------|---------|
| 输出是嵌套 JSON | 用 structured output（tool use / response_format） |
| 任务含多步推理 | 加 "请一步步思考" 的 CoT |
| 输出风格非常具体 | 加 1-2 个 few-shot 示例 |
| 类别数 > 10 | 类别描述每个一句话（不只是 enum 名） |
| 任务有强外部知识 | 加业务背景段落 |

**判定**：v0 的目的是看"在当前模型 + 当前任务难度下，朴素版本能到多少"——如果你预判朴素版必挂得很惨，可以直接用"中等复杂度 v0"，省一轮迭代。

---

## 7. 选模型的纪律

v0 阶段就要选好基线模型：

| 任务类型 | v0 推荐模型 |
|---------|-------------|
| 分类 / 抽取（简单） | Claude Haiku / GPT-4o-mini / Gemini Flash |
| 抽取（复杂嵌套） | Claude Sonnet / GPT-4o |
| 生成 / 创意 | Claude Sonnet / GPT-4o |
| 长链推理 / Agent | Claude Sonnet thinking / GPT-5 reasoning / Opus |
| 多模态 | Claude Sonnet / GPT-4o / Gemini 2.0 |

**纪律**：

1. **先用小模型**：能用小模型解决就别上大模型（成本和延迟差 5-10 倍）
2. **同任务两个模型对比**：v0 在 Haiku 和 Sonnet 各跑一遍，看差距决定后续投入
3. **判断"是能力不足还是 prompt 不足"**：大模型也挂 = 任务难度问题 / 输入有歧义 / 需要更多上下文 = 不是 prompt 能解决的事

---

## 8. 完整 demo：30 分钟从需求到 v0

```python
# demos/process/02_v0_classifier.py
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
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        temperature=0,
        system=PROMPT_V0,
        messages=[{"role": "user", "content": feedback or "(empty)"}],
    )
    return json.loads(resp.content[0].text)


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

跑：

```bash
python demos/process/02_v0_classifier.py
```

预期：8 个 probe 大概对 5-7 个。这就是你的**基线分**——后续每一版都跟它比。

---

## 9. v0 完成的"交付"

一个合格的 v0 阶段产出：

```
feature-name/
├── spec.md                 # Stage 1 的 7 问澄清
├── prompts/
│   └── v0/
│       ├── system.txt
│       └── notes.md        # 为什么这样写
├── probe.py                # Stage 2 的 10 条试运行样本
└── baseline_score.txt      # v0 在 probe 上的分数
```

Day 1 结束时应该交付这套东西。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **跳过 Spec 直接写 prompt** | 后续每一轮迭代都在"重新发现需求" |
| **v0 一上来用 CoT + Few-shot + XML** | 不知道朴素版的基线，不知道是不是过度设计 |
| **probe 只有 happy path** | 不暴露问题，进入 Stage 3 才发现 |
| **probe 用线上真数据但不脱敏** | 隐私合规问题 |
| **v0 用最强模型** | 后期换不下来（"切换到 Haiku 性能就崩了"），成本下不去 |
| **v0 把所有约束都堆进去** | 不知道哪条约束实际起作用 |

---

## 11. 下一步

- 📖 建完整 evalset → [03-build-evalset.md](./03-build-evalset.md)
- 📖 迭代闭环 → [04-iteration-loop.md](./04-iteration-loop.md)
- 📖 评测先于 prompt 的设计哲学 → [01-foundations/05-eval-first.md](../01-foundations/05-eval-first.md)

## 参考资料

- "What is a Spec doc": https://eugeneyan.com/writing/llm-patterns/
- Anthropic Prompt Library: https://docs.anthropic.com/en/prompt-library
