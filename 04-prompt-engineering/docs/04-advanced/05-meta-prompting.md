# PE Advanced 05：Meta-Prompting —— 用 LLM 写 / 优化 Prompt

> **一句话**：让 LLM 自己帮你写 prompt、改 prompt、生成 evalset——能极大提速但**不是替代你的判断**。Meta-prompting 是 2025 关键趋势，本篇讲三种用法和反模式。

---

## 1. 三种 meta-prompting

### 1.1 用 LLM 起草初版 prompt

```
你是 prompt engineering 专家。

任务：根据下面的需求，写一个 system prompt。

需求：
- 输入：客户反馈文本
- 输出：JSON {category, confidence, reasoning}
- 类别：bug / feature / complaint / praise / question / billing / account / other
- 反讽要按真实意图归类
- 空 / 无关输入 → other

请给一个高质量 system prompt，遵循以下原则：
1. 角色明确
2. 任务一句话
3. 约束 bullet 化
4. 输出 schema 明确
5. 不啰嗦

返回 prompt 内容（不要 markdown 包装）。
```

LLM 写出来的 prompt 通常**有 70 分**——比你从零写省时间，但**不能直接上线**。

### 1.2 用 LLM 改进 prompt（基于 eval 结果）

```
我有一个 prompt v3 和它在 evalset 上的失败案例。请提出改进 prompt v4。

<current_prompt>
{prompt_v3}
</current_prompt>

<failures>
失败 1:
  input: "App 闪退"
  output: {"category": "complaint", ...}
  expected: {"category": "bug", ...}

失败 2:
  ...
</failures>

请：
1. 分析失败模式
2. 给出 prompt v4（只改 v3 必要的部分）
3. 说明 v3 → v4 的关键改动
```

LLM 看到具体失败 → 提改进建议——比你盯着 prompt 调更系统。

### 1.3 用 LLM 生成 evalset

```
你是测试用例生成专家。任务：为下面的 prompt 生成 evalset。

<prompt>
{prompt_under_test}
</prompt>

生成 30 个测试样本，要求：
- 20 个 happy path（覆盖 8 个类别）
- 5 个 edge case（空输入、emoji、反讽、多类）
- 5 个 attack（注入、role override）

每条 JSON：{"input": ..., "expected_category": ..., "tag": "happy|edge|attack", "notes": "为什么挑这条"}

输出 JSONL。
```

LLM 生成的 evalset **必须人工 review**——但确实快。

---

## 2. Meta-prompting 的工程化

### 2.1 用模板而非自由文本
```python
META_PROMPT_TEMPLATE = """
你是 prompt engineering 专家。

需求：
{spec}

请按以下 schema 输出（JSON）：
{
  "prompt": "...",
  "key_design_decisions": ["...", "..."],
  "potential_failure_modes": ["...", "..."],
  "suggested_examples": ["...", "..."]
}
"""

def draft_prompt(spec: str) -> dict:
    resp = llm.generate(META_PROMPT_TEMPLATE.format(spec=spec))
    return json.loads(resp)
```

### 2.2 迭代 loop
```python
def auto_iterate(initial_prompt: str, evalset: list, max_iter: int = 5):
    prompt = initial_prompt
    history = []
    for i in range(max_iter):
        results = run_evalset(prompt, evalset)
        history.append({"version": i, "prompt": prompt, "rate": results["rate"]})
        
        if results["rate"] > 0.92:
            break
            
        prompt = llm_improve(prompt, results["failures"])
    return prompt, history
```

把"看失败 → 提假设 → 改 prompt"循环交给 LLM。**但人必须最后 review**。

---

## 3. Claude Code 作为 prompt 优化器

Claude Code（或类似 Agent）可以当 PE 协作者：

```
我有一个客服分类 prompt 在 evalset 上 78% 通过率。
失败案例都在 evalset/v1.jsonl 里标记了 _fail=true。
看一下，写出 v4 prompt + 一份 diff 说明改了什么。
```

Claude Code 会：
1. 读你的 prompt 文件
2. 读 evalset + 失败案例
3. 分析失败模式
4. 提出 v4
5. 跑 evalset 验证
6. 给 diff 解释

详见 [08-practice/03-claude-code-as-optimizer.md](../08-practice/03-claude-code-as-optimizer.md)。

---

## 4. Meta-prompting 的局限

### 4.1 LLM 写的 prompt 倾向"安全 / 模糊"
LLM 写出来的 prompt 经常**过度防御**：

```
LLM 写的 prompt：
"请仔细分析、确保准确、不要出错、考虑各种情况..."  ← 啰嗦
```

要你自己删掉冗余。

### 4.2 LLM 不知道你的真实业务
"在 evalset 上 90% 通过"和"在真实业务上有用"是两件事。LLM 优化的是 evalset 表现——可能 evalset 本身有偏。

### 4.3 LLM 优化容易陷入局部
LLM 在已知失败模式上修——但可能没看见"新的失败方向"。

### 4.4 隐藏 bug
LLM 改 prompt 时可能改了重要约束你没注意到。每次 LLM 改完要 diff 检查。

---

## 5. 推荐工作流

```
[你] 写 Spec
   ↓
[LLM] 起草 prompt v0
   ↓
[你] Review + 改
   ↓
[LLM 或你] 生成 evalset
   ↓
[你] Review evalset（关键！）
   ↓
[LLM] 改进 prompt（多轮）
   ↓
[你] 每轮 review + 上线决策
   ↓
[你 + 用户] 监控、反哺
```

LLM 帮你做"草稿、初版、grind work"，你做"判断、决策、终审"。

---

## 6. Meta-prompting 工具

| 工具 | 用途 |
|------|------|
| **DSPy** | 程序化 prompt 优化（自动改 + 评测） |
| **Promptfoo prompt-pipeline** | 自动 A/B prompts |
| **Anthropic Prompt Generator** | console.anthropic.com 自带 |
| **OpenAI Playground "Generate"** | console 自带 |
| **LangSmith Prompt Hub + Playground** | 配合评测一体 |

详细对比在 [07-production/01-versioning.md](../07-production/01-versioning.md)。

---

## 7. demo：用 LLM 起草 prompt

```python
# demos/advanced/05_meta_prompt_drafter.py
import anthropic
client = anthropic.Anthropic()

SPEC = """
任务：客服反馈分类
输入：1-2000 字中英文混合
输出：JSON {category: enum, confidence: float, reasoning: string}
类别：bug / feature / complaint / praise / question / billing / account / other
特殊要求：反讽按真实意图归类；空输入归 other
模型：Claude Haiku
"""

META = f"""你是 prompt engineering 专家。

根据下面的 Spec，写一个高质量 system prompt：

{SPEC}

要求：
1. 角色明确（一句话）
2. 任务清晰（一句话）
3. 约束 bullet 化（3-5 条）
4. 输出 schema 明确（含字段类型 + 取值范围）
5. 不啰嗦、不重复

返回 JSON：
{{
  "prompt": "<system prompt 全文>",
  "key_decisions": ["...", "..."],
  "potential_issues": ["...", "..."]
}}
"""

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2000,
    messages=[{"role": "user", "content": META}],
)
print(resp.content[0].text)
```

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| **直接用 LLM 写的 prompt 上线** | 必须 review |
| **让 LLM 同时改 prompt + evalset** | 优化无意义（变量乱） |
| **LLM 优化陷入局部** | 加新 evalset / 新失败模式 |
| **LLM 写的 prompt 啰嗦** | 必须精简 |
| **没让 LLM 解释 diff** | 不知道改了什么 |

---

## 9. 下一步

- 📖 Injection 防御 → [06-injection-defense.md](./06-injection-defense.md)
- 📖 按任务组装 → [05-by-task/](../05-by-task/)
- 📖 实战：Claude Code 当 optimizer → [08-practice/03-claude-code-as-optimizer.md](../08-practice/03-claude-code-as-optimizer.md)

## 参考资料

- DSPy: https://dspy.ai
- "Automatic Prompt Engineer" (Zhou et al. 2022): https://arxiv.org/abs/2211.01910
- Anthropic Prompt Generator: https://console.anthropic.com
