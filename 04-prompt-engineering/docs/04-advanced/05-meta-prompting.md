# PE Advanced 05：Meta-Prompting，用 LLM 写和优化 Prompt

> 让 LLM 帮你写 prompt、改 prompt、生成评测集，能省下大量时间，但判断还得你自己来做。Meta-prompting 从 2025 年起用得越来越多，这篇讲三种用法和几个容易踩的坑。

---

## 1. 三种用法

### 1.1 让 LLM 起草初版 prompt

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

LLM 写出来的 prompt 一般能打 70 分左右。比你从零开始写省时间，但不能直接上线。

### 1.2 根据评测结果改进 prompt

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

把具体的失败案例摆在 LLM 面前让它提改进建议，比你自己盯着 prompt 反复调要有条理。

### 1.3 让 LLM 生成评测集

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

速度确实快，但 LLM 生成的评测集必须人工逐条过一遍。

---

## 2. 把 meta-prompting 工程化

### 2.1 用模板，不用自由文本
```python
META_PROMPT_TEMPLATE = """
你是 prompt engineering 专家。

需求：
{spec}

请按以下 schema 输出（JSON）：
{{
  "prompt": "...",
  "key_design_decisions": ["...", "..."],
  "potential_failure_modes": ["...", "..."],
  "suggested_examples": ["...", "..."]
}}
"""  # 模板要走 .format()，JSON 里的花括号写成 {{ }}

def draft_prompt(spec: str) -> dict:
    resp = llm.generate(META_PROMPT_TEMPLATE.format(spec=spec))
    return json.loads(resp)
```

### 2.2 自动迭代循环
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

「看失败、提假设、改 prompt」这个循环可以交给 LLM 去跑，但最后一定要人来审。

---

## 3. 用 Claude Code 当 prompt 优化器

Claude Code 这类 Agent 工具可以当 prompt 工程的协作者，比如这样交代任务：

```
我有一个客服分类 prompt 在 evalset 上 78% 通过率。
失败案例都在 evalset/v1.jsonl 里标记了 _fail=true。
看一下，写出 v4 prompt + 一份 diff 说明改了什么。
```

它会依次读你的 prompt 文件，读评测集和失败案例，分析失败模式，提出 v4，跑评测集验证，最后给出 diff 和说明。

完整流程见 [用 Claude Code 当 Prompt 优化器](../08-practice/03-claude-code-as-optimizer.md)。

---

## 4. Meta-prompting 的局限

### 4.1 LLM 写的 prompt 偏保守、啰嗦
LLM 写的 prompt 经常防御过度：

```
LLM 写的 prompt：
"请仔细分析、确保准确、不要出错、考虑各种情况..."  ← 啰嗦
```

这些冗余得你自己删。

### 4.2 LLM 不了解你的业务
评测集上 90% 通过，和在真实业务里好用，是两回事。LLM 优化的只是评测集上的分数，而评测集本身可能就有偏差。

### 4.3 容易陷在局部
LLM 只会针对已知的失败模式修修补补，看不到新的失败方向。

### 4.4 悄悄改掉重要约束
LLM 改 prompt 时，可能顺手改掉了某条重要约束，而你没注意到。每次改完都要看 diff。

---

## 5. 我推荐的工作流

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

草稿、初版和重复性的体力活交给 LLM，判断、决策和最终审核留给自己。

---

## 6. 相关工具

| 工具 | 用途 |
|------|------|
| DSPy | 程序化优化 prompt（自动修改 + 评测） |
| Promptfoo | 用同一份测试集横向对比多个 prompt / 模型 |
| Anthropic Prompt Generator | Claude Console 自带 |
| OpenAI Playground "Generate" | console 自带 |
| LangSmith Prompt Hub + Playground | 和评测集成在一起 |

其中 LangSmith Prompt Hub 怎么配合版本管理，见 [Prompt 版本管理](../07-production/01-versioning.md)。

---

## 7. demo：让 LLM 起草 prompt

```python
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
    model="claude-sonnet-5-5",
    max_tokens=2000,
    messages=[{"role": "user", "content": META}],
)
print(resp.content[-1].text)
```

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| LLM 写的 prompt 直接上线 | 必须人工审 |
| 让 LLM 同时改 prompt 和评测集 | 两个变量一起变，优化失去意义 |
| 优化陷在局部 | 补充新的评测样本和新的失败模式 |
| LLM 写的 prompt 太啰嗦 | 手动精简 |
| 没让 LLM 解释改动 | 不知道它到底改了什么 |

---

## 9. 下一步

- [Prompt Injection 防御](./06-injection-defense.md)
- [按任务类型组装 prompt](../05-by-task/)
- [用 Claude Code 当 Prompt 优化器](../08-practice/03-claude-code-as-optimizer.md)

## 参考资料

- DSPy: https://dspy.ai
- "Automatic Prompt Engineer" (Zhou et al. 2022): https://arxiv.org/abs/2211.01910
- Anthropic Prompt Generator: https://console.anthropic.com
