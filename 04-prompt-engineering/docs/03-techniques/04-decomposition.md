# PE Technique 04：Decomposition 任务拆解

> 让一个 prompt 同时做五件事，往往五件都做不好；拆成五个 prompt 串起来，每一步都能做好。拆解是把复杂任务变成几个简单子任务的工程方法，很多时候比叠加 CoT、few-shot 这些技巧更管用。

---

## 1. 什么时候要拆

出现下面这些信号，就该考虑拆了：

- 一个 prompt 要输出多个独立的东西（既要提取，又要分类，还要生成）
- 输出的字段互相影响，一个错了连带别的也错
- prompt 超过 1500 token，约束已经塞不下
- 评测时发现 A 做好了 B 就变差，反过来也一样

不用拆的情况：任务本来就是一步完成的（翻译、单一分类），或者简单到 zero-shot 就能做到 90% 以上。

---

## 2. 几种拆法

### 2.1 Sequential（顺序拆）
A 的输出作为 B 的输入：

```
[Step 1] 抽取 → JSON of fields
   ↓
[Step 2] 用 fields 生成 → 文案
```

比如先抽取产品参数，再据此生成营销文案。

### 2.2 Parallel（并行拆）
互不依赖的子任务同时跑：

```
        ┌→ [分类]
[输入] ─┼→ [情感分析]
        └→ [实体抽取]
        最后合并
```

比如分析一条评论时，分类、情感判断、产品名抽取同时进行。

### 2.3 Map-Reduce（长输入拆）
输入太长时，切片后逐段处理，再汇总：

```
[长文档] → 切 10 段 → [总结每段] (并行) → [汇总] → 最终
```

比如 100 页财报，先逐页总结，再汇总成 1 页。

### 2.4 Router（路由拆）
按输入类型分流到不同的子 prompt：

```
[输入] → [Router 判断类型] → [type=A 的 prompt] or [type=B 的 prompt] or ...
```

比如客服系统先判断意图是咨询、投诉还是退款，再交给对应的处理流程。

### 2.5 Plan + Execute
先列计划，再执行：

```
[Step 1: Planner] "列出解决这个问题的步骤"
   ↓
[Step 2-N: Executor] 按计划逐步执行
```

多用在复杂的 Agent 工作流里。

---

## 3. 例子：发票处理

需求：用户上传一张发票图片，系统提取数据、入库，再给用户一段摘要。

### 单 prompt（不推荐）

```
你是发票处理助手。请：
1. 提取金额、商家、日期、税号
2. 判断是否报销范围内
3. 写一段中文摘要给用户
4. 如果是公司发票，加 reimbursable: true

返回 JSON {amount, vendor, date, tax_id, reimbursable, user_summary}
```

这里的问题在于：判断报销范围涉及业务规则，和其他事混在一个 prompt 里容易出错；summary 的长度每次不同，连带整个输出都不稳定；只要一个字段错了，整份结果都不可信。

### 拆开（推荐）

```
[Step 1] 抽取（结构化输出，要稳定）
   → {amount, vendor, date, tax_id}

[Step 2] 业务规则判定（Python 代码，非 LLM）
   → reimbursable = is_in_policy(amount, vendor, date)

[Step 3] 生成 summary（独立 prompt，自由些）
   → "这是 5 月 20 日小李在便利店买的零食，金额 ¥28，超出零食类 ¥20 的报销额度。"
```

拆开之后，每一步都能单独测试和监控；业务规则交给代码，不会出错；summary 写得怎么变都不影响数据准确性。

---

## 4. 用 Agent 框架管理拆解

步骤一多，手工串起来很累。可以用 LangGraph、Pydantic Graph 或 LangChain LCEL 把流程显式画出来：

```python
# LangGraph 例子
from typing import TypedDict

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

详细用法见 LangChain 手册的 LangGraph 章节，从 [LangGraph 01：介绍、定位与其他框架的对比](../../../01-langchain/docs/03-langgraph/01-introduction.md) 开始。

---

## 5. 拆还是不拆

| 维度 | 单 prompt | 多 prompt |
|------|-----------|-----------|
| 总 token 成本 | 通常更低 | 高一些（system 会重复） |
| 总延迟 | 1 次调用 | N 次累加 |
| 每步可控性 | 难 | 易 |
| 调试 | 混在一起 | 能定位到具体哪一步 |
| Evalset | 整体评测 | 每步独立评测，再做集成评测 |
| 失败影响 | 整体不可用 | 局部失败可以降级 |
| 改一处 | 整个 prompt 重测 | 只重测改动的那一步 |

我自己的经验：

- 输出不超过 3 个相关字段，不拆
- 超过 5 个字段，或者字段之间互相独立，倾向于拆
- 任务里有业务规则，一定拆出来交给代码
- 对延迟敏感，倾向于不拆
- 复杂度高、要长期维护，拆

---

## 6. 拆解的成本：上下文传递

子 prompt 之间要传上下文，每一步都得带上 system 和一些重复信息：

```
Step 1 system: 1500 token
Step 2 system: 1500 token (大部分重复)
Step 3 system: 1500 token (大部分重复)
```

办法有两个：把共享的 system 部分交给 prompt caching（见 [Prompt Caching 实战](../07-production/02-caching.md)）；用模板系统统一维护 system。

---

## 7. 反例：拆过头

```
❌ "判断这条评论是不是 bug"
   拆成:
   Step 1: 用户是否在抱怨？
   Step 2: 是不是 software 相关？
   Step 3: 是不是 reproducible？
   Step 4: 综合判断
```

拆到这种程度，token 和延迟都翻了 4 倍，而一个 prompt 本来就能解决；每一步又太琐碎，单独评测也没有意义。

---

## 8. demo：发票处理的顺序拆解

```python
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
        model="claude-haiku-5-5",
        max_tokens=1024,
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
    return json.loads(resp.content[-1].text)


# === Step 2: 业务规则（Python，不是 LLM）===
EXPENSE_POLICY = {
    "snacks_max_per_meal": 20,
    "approved_vendors_keyword": ["午餐", "晚餐", "出租车", "高铁"],
}

def is_reimbursable(data: dict) -> tuple[bool, str]:
    if data["amount"] is None:
        return False, "missing amount"
    if "便利店" in (data.get("vendor") or "") and data["amount"] > EXPENSE_POLICY["snacks_max_per_meal"]:
        return False, f"零食类超过 ¥{EXPENSE_POLICY['snacks_max_per_meal']} 限额"
    return True, "符合"


# === Step 3: 生成摘要 ===
def summarize(data: dict, reimbursable: bool, reason: str) -> str:
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system="给用户写一段 100 字以内的发票处理摘要，不要重复字段，要自然。",
        messages=[{
            "role": "user",
            "content": f"发票: {data}\n报销结果: {'通过' if reimbursable else '拒绝'} ({reason})",
        }],
    )
    return resp.content[-1].text


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

三步可以分别评测：Step 1 用 100 张发票加期望字段做评测集；Step 2 的业务规则是纯函数，直接写 pytest；Step 3 的摘要质量交给 LLM-as-judge 打分。

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| 不该拆的也拆了 | 单 prompt 能到 90% 就别拆 |
| 业务规则混在 LLM 里 | 业务规则交给代码，LLM 只做模糊判断 |
| 拆完没有分步评测 | 每步有独立评测集，才找得到瓶颈在哪一步 |
| 共享的 system 没开缓存 | 多步调用成本暴涨，开 prompt caching |
| 拆成 10 步以上 | 九成任务 3 到 5 步就够，超过就该怀疑设计 |
| 失败处理只放在最后 | 每步各自处理失败，否则一步出错全盘作废 |

---

## 10. 下一步

- [结构化输出（JSON / XML / Schema）](./05-structured-output.md)
- [ReAct（Reasoning + Acting）](../04-advanced/01-react.md)：在 Agent 里拆解任务并调用工具
- [Self-Critique（自我反思）](./08-self-critique.md)：让模型再审一遍，也算一种拆解
- 跨手册：LangGraph 状态机见 ../../../01-langchain/docs/03-langgraph/

## 参考资料

- "Decomposed Prompting" (Khot et al. 2022): https://arxiv.org/abs/2210.02406
- "Plan-and-Solve Prompting" (Wang et al. 2023): https://arxiv.org/abs/2305.04091
