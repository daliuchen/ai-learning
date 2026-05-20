# PE Technique 04：Decomposition —— 任务拆解

> **一句话**：一个 prompt 想干 5 件事 → 5 件都做不好；拆成 5 个 prompt 串起来 → 各自做好。Decomposition 是把"复杂任务"变成"简单子任务"的工程方法，效果常常比堆技法（CoT、Few-shot）更有效。

---

## 1. 什么时候要拆

诊断信号：

- 单 prompt 输出**多个独立字段**（提取 + 分类 + 生成）
- 输出中**字段间互相影响**（一个错连锁错）
- 单 prompt 超 1500 token，约束塞不下
- 评测发现"任务 A 好但 B 差，反之亦然"

不需要拆的：

- 任务确实是"一步"做的（翻译、单一分类）
- 简单到 zero-shot 就能 90%+

---

## 2. 几种拆法

### 2.1 Sequential（顺序拆）
A 的输出是 B 的输入：

```
[Step 1] 抽取 → JSON of fields
   ↓
[Step 2] 用 fields 生成 → 文案
```

例子：先抽取产品参数，再生成营销文案。

### 2.2 Parallel（并行拆）
独立子任务并行跑：

```
        ┌→ [分类]
[输入] ─┼→ [情感分析]
        └→ [实体抽取]
        最后合并
```

例子：分析一条评论，同时分类、情感、抽取产品名。

### 2.3 Map-Reduce（长输入拆）
输入太长 → 切片每段单独处理 → 汇总：

```
[长文档] → 切 10 段 → [总结每段] (并行) → [汇总] → 最终
```

例子：100 页财报 → 每页总结 → 汇总成 1 页。

### 2.4 Router（路由拆）
按输入类型走不同子 prompt：

```
[输入] → [Router 判断类型] → [type=A 的 prompt] or [type=B 的 prompt] or ...
```

例子：客服系统先判断意图（咨询 / 投诉 / 退款），再走专门处理。

### 2.5 Plan + Execute
先列计划，再执行：

```
[Step 1: Planner] "列出解决这个问题的步骤"
   ↓
[Step 2-N: Executor] 按计划逐步执行
```

例子：复杂 Agent 工作流。

---

## 3. 例子：发票处理

需求：上传一张发票图片，提取数据、入库、给用户摘要。

### 单 prompt（坏）

```
你是发票处理助手。请：
1. 提取金额、商家、日期、税号
2. 判断是否报销范围内
3. 写一段中文摘要给用户
4. 如果是公司发票，加 reimbursable: true

返回 JSON {amount, vendor, date, tax_id, reimbursable, user_summary}
```

问题：
- "判断报销范围" 涉及业务规则，混在一个 prompt 容易错
- summary 影响输出长度，每次不稳
- 一个字段错，全部不可信

### 拆开（好）

```
[Step 1] 抽取（结构化输出 + temperature=0）
   → {amount, vendor, date, tax_id}

[Step 2] 业务规则判定（Python 代码，非 LLM）
   → reimbursable = is_in_policy(amount, vendor, date)

[Step 3] 生成 summary（独立 prompt，temperature=0.5 自由些）
   → "这是 5 月 20 日小李在便利店买的零食，金额 ¥12，超出零食类报销额度。"
```

好处：
- 每步独立可测 / 可监控
- 业务规则用代码，0 错误率
- summary 多样性不影响数据准确性

---

## 4. 拆解的工程化：用 Agent 框架

拆解很多步时手工串太累——用 LangGraph / Pydantic Graph / LangChain LCEL 把流程画出来：

```python
# LangGraph 例子
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    raw: str
    extracted: dict
    reimbursable: bool
    summary: str

def extract(state):
    return {"extracted": llm_extract(state["raw"])}

def check_policy(state):
    return {"reimbursable": is_in_policy(state["extracted"])}

def gen_summary(state):
    return {"summary": llm_summarize(state["extracted"], state["reimbursable"])}

graph = StateGraph(State)
graph.add_node("extract", extract)
graph.add_node("check", check_policy)
graph.add_node("summarize", gen_summary)
graph.add_edge(START, "extract")
graph.add_edge("extract", "check")
graph.add_edge("check", "summarize")
graph.add_edge("summarize", END)
app = graph.compile()
```

详细看 01-langchain/03-langgraph 章节。

---

## 5. 拆 vs 不拆的权衡

| 维度 | 单 prompt | 多 prompt |
|------|-----------|-----------|
| 总 token 成本 | 通常更低 | 高一些（重复 system） |
| 总延迟 | 1 次调用 | N 次累加 |
| 每步可控性 | 难 | 易 |
| 调试 | 一锅炖 | 单步可定位 |
| Evalset | 整体评测 | 每步独立评测 + 集成 |
| 失败影响 | 整体不可用 | 局部失败可降级 |
| 改一处 | 全 prompt 重测 | 只重测改的步 |

**经验法则**：

- 输出 ≤ 3 个相关字段 → 不拆
- 输出 > 5 个字段 / 字段独立 → 倾向拆
- 任务有"业务规则" → 一定拆出来用代码做
- 延迟敏感 → 倾向不拆
- 复杂度高 / 长期维护 → 拆

---

## 6. 拆解的成本：上下文传递

子 prompt 之间要传递上下文 → 每步都要 system + 一些重复信息：

```
Step 1 system: 1500 token
Step 2 system: 1500 token (大部分重复)
Step 3 system: 1500 token (大部分重复)
```

**对策**：

- 共享 system 部分提到 prompt caching（详 [07-production/02-caching.md](../07-production/02-caching.md)）
- 用 template 系统统一 system

---

## 7. 反例：过度拆解

```
❌ "判断这条评论是不是 bug"
   拆成:
   Step 1: 用户是否在抱怨？
   Step 2: 是不是 software 相关？
   Step 3: 是不是 reproducible？
   Step 4: 综合判断
```

这种程度的拆解：
- 4 倍 token + 4 倍延迟
- 单 prompt 都能解决
- 各步评测无意义（每步太琐碎）

---

## 8. demo：发票处理 sequential 拆解

```python
# demos/techniques/04_decomposition_invoice.py
"""3 步处理发票"""
import json
import anthropic

client = anthropic.Anthropic()

INVOICE = """
发票
商家: 全家便利店
日期: 2026-05-20
金额: ¥18.00
税号: 91110000000000000X
"""

# === Step 1: 抽取 ===
def extract(raw: str) -> dict:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        temperature=0,
        system="""你是发票数据抽取器。从输入中提取 JSON：
{
  "amount": <数字>,
  "currency": "CNY/USD/...",
  "vendor": "...",
  "date": "YYYY-MM-DD",
  "tax_id": "..."
}
缺失字段填 null。只返回 JSON。""",
        messages=[{"role": "user", "content": raw}],
    )
    return json.loads(resp.content[0].text)


# === Step 2: 业务规则（Python，不是 LLM）===
EXPENSE_POLICY = {
    "snacks_max_per_meal": 20,
    "approved_vendors_keyword": ["午餐", "晚餐", "出租车", "高铁"],
}

def is_reimbursable(data: dict) -> tuple[bool, str]:
    if data["amount"] is None:
        return False, "missing amount"
    if "便利店" in data.get("vendor", "") and data["amount"] > EXPENSE_POLICY["snacks_max_per_meal"]:
        return False, f"零食类超过 ¥{EXPENSE_POLICY['snacks_max_per_meal']} 限额"
    return True, "符合"


# === Step 3: 生成摘要 ===
def summarize(data: dict, reimbursable: bool, reason: str) -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        temperature=0.3,
        system="给用户写一段 100 字以内的发票处理摘要，不要重复字段，要自然。",
        messages=[{
            "role": "user",
            "content": f"发票: {data}\n报销结果: {'通过' if reimbursable else '拒绝'} ({reason})",
        }],
    )
    return resp.content[0].text


# === 流程 ===
def process_invoice(raw: str) -> dict:
    extracted = extract(raw)
    print(f"[Step 1] 抽取: {extracted}")
    ok, reason = is_reimbursable(extracted)
    print(f"[Step 2] 报销判定: {ok} ({reason})")
    summary = summarize(extracted, ok, reason)
    print(f"[Step 3] 摘要: {summary}")
    return {**extracted, "reimbursable": ok, "reason": reason, "summary": summary}


if __name__ == "__main__":
    process_invoice(INVOICE)
```

每步可独立评测：

- Step 1 抽取的 evalset = 100 张发票 + 期望字段
- Step 2 业务规则是 pure function，pytest 测试
- Step 3 summary 的 evalset 用 LLM-as-judge 评质量

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **能不拆就尽量不拆** | 单 prompt 能 90% 就别拆 |
| **业务规则混在 LLM** | 业务规则用代码！LLM 只做"模糊"决策 |
| **拆完没分别评测** | 每步独立 evalset 才能找出哪步是瓶颈 |
| **共享 system 不 caching** | 多步成本爆，开 prompt caching |
| **拆成 10+ 步** | 90% 任务 3-5 步足够；超过要质疑 |
| **失败处理放最后** | 每步独立失败处理，否则一步挂全废 |

---

## 10. 下一步

- 📖 结构化输出 → [05-structured-output.md](./05-structured-output.md)
- 📖 ReAct（在 Agent 里拆解 + 工具调用） → [04-advanced/01-react.md](../04-advanced/01-react.md)
- 📖 自我反思（"再 review 一遍"也是一种拆解） → [08-self-critique.md](./08-self-critique.md)
- 📖 跨手册：LangGraph 状态机 → ../../01-langchain/docs/03-langgraph/

## 参考资料

- "Decomposed Prompting" (Khot et al. 2022): https://arxiv.org/abs/2210.02406
- "Plan-and-Solve Prompting" (Wang et al. 2023): https://arxiv.org/abs/2305.04091
