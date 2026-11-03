# PE Models 04：跨模型可移植 + 成本性能权衡

> 写 prompt 时最好先想一下：哪天换一家模型，这份 prompt 还能不能直接用？死绑一家，迁移时会很痛。这篇讲怎么设计可移植的 prompt、什么情况下应该为某一家单独分叉，最后附一份三家的成本性能速查表。

---

## 1. 可移植 prompt 的设计原则

### 1.1 用 XML 标签分隔几个部分
Anthropic 官方推荐用 XML 标签把指令、数据和示例分开；GPT、Gemini 对 XML 标签和 Markdown 标题都能正确理解。所以跨模型时，用 XML 标签把「指令 / 资料 / 示例 / 输出格式」分隔开是最稳的做法，标签里面照常写小标题和列表：

```
<instructions>
分类客服反馈。
类别:
- bug: ...
- feature: ...
约束:
- 严格用 enum
- 反讽按意图归
</instructions>

<feedback>
{用户反馈原文}
</feedback>

<output_format>
JSON {category, confidence}
</output_format>
```

一层平铺的标签就够了，不必把整个 prompt 写成深层嵌套的 XML。

### 1.2 把 structured output 抽象出来
用 Pydantic AI / LiteLLM / instructor 统一封装：

```python
# Pydantic AI 默认用工具调用拿结构化结果，三家同一套写法（要原生 schema 输出用 NativeOutput）
agent = Agent("openai:gpt-6-luna", output_type=Classification)
agent2 = Agent("anthropic:claude-haiku-5-5", output_type=Classification)
agent3 = Agent("google:gemini-3.8-flash", output_type=Classification)
# 同一份 Pydantic 模型，三家都用
```

### 1.3 不依赖某一家独有的功能
| 独有功能 | 移植时怎么办 |
|--------------|-------------|
| Claude prefill（Claude 4.6+ 已移除） | 本来就别用；改用结构化输出，或在 prompt 里写"直接输出 JSON，不要前言" |
| Claude adaptive thinking + effort | 其他家对应 `reasoning.effort` / `thinking_level`；不支持的模型改成"在 <thinking> 里推理" |
| OpenAI Structured Outputs | 在 prompt 里写 JSON Schema 描述，再加 retry |
| GPT-6 `reasoning.effort` | 对没有内置推理的模型，换成 "let's think step by step" 这类 CoT prompt |
| `temperature` | Claude 已移除，Gemini 3 建议保持默认：可移植的 prompt 不要依赖它 |
| Gemini 1M context | 切片 + RAG |

### 1.4 测试矩阵
每个 prompt 至少在两家模型上测：

```python
def cross_model_test(prompt, evalset):
    results = {}
    for model in ["openai:gpt-6-luna", "anthropic:claude-haiku-5-5", "google:gemini-3.8-flash"]:
        results[model] = run_eval(prompt, evalset, model=model)
    return results
```

如果发现某一家掉得厉害，有两种选择：改 prompt 让三家都过得去，代价是放弃在单家上的最优；或者专门给那一家做一个版本。

---

## 2. 什么时候该分叉

应该分叉的情况：

- 用了 Claude cache_control、adaptive thinking 这类独有功能
- 业务完全跑在一家上，没有跨厂商的需求
- 某一家的专用写法能涨 10 个百分点以上，但同样的写法会让其他家大幅下滑

应该保持统一的情况：

- prompt 移植后各家通过率都在 80% 以上
- 各家之间通过率差距不超过 3 个百分点
- 分叉带来的维护成本超过了性能收益

---

## 3. 适配层

中等复杂度的项目可以做一个 prompt adapter：

```python
class PromptAdapter:
    def __init__(self, base_prompt: str):
        self.base = base_prompt
    
    def for_claude(self) -> dict:
        # XML 转换
        return {"system": self._to_xml(self.base), ...}
    
    def for_gpt(self) -> dict:
        # Markdown 转换
        return {"system": self._to_markdown(self.base), ...}
    
    def for_gemini(self) -> dict:
        return {"system_instruction": self._to_markdown(self.base), ...}
```

如果按 1.1 统一用一层 XML 标签分段，三家可以共用同一份 system 文本，adapter 只需要处理 API 字段上的差异（`system` / `system_instruction`、缓存标记等），上面的格式转换可以省掉。别做过头，简单项目直接用 Pydantic AI 就够了。

---

## 4. 成本对比（2026-11 价格，以官方页为准）

| 模型 | Input / 1M tokens | Output / 1M tokens | 适合 |
|------|-------------------|---------------------|------|
| Claude Haiku 5.5 | $0.10 | $0.50 | 分类 / 抽取 |
| Claude Sonnet 5.5 | $2 | $10 | 通用 / 写作 / Agent |
| Claude Opus 5.5 | $4 | $20 | 复杂推理 |
| Claude Fable 5.1 | $10 | $50 | 旗舰 |
| GPT-6 Luna | $0.10 | $0.50 | 低成本分类 |
| GPT-6.1 Terra | $1 | $5 | 通用 |
| GPT-6.1 Sol | $2 | $10 | 通用 / 编程 |
| GPT-6 Astra | $10 | $50 | 旗舰 |
| Gemini 3.8 Flash / 3.5 Flash-Lite / 4 Argon | 见 [官方定价页](https://ai.google.dev/gemini-api/docs/pricing) | | Flash-Lite 最便宜；Argon 旗舰 |
| Qwen qwen-flash（百炼） | 见百炼官方 | | 本手册 demo 在用，国内最便宜的一档 |

价格一直在降，上线前以实时价格为准。Claude 和 GPT-6 有三档价格完全相同：Haiku 5.5 与 GPT-6 Luna 都是 $0.10 / $0.50，Sonnet 5.5 与 GPT-6.1 Sol 都是 $2 / $10，Fable 5.1 与 GPT-6 Astra 都是 $10 / $50。同档之间价格拉不开差距，选型主要看评测集上的表现。

---

## 5. 性能速查

下表是写作时的经验判断，只用来缩小候选范围；具体选哪家，以你自己评测集上的结果为准。

| 维度 | 常见选择 |
|------|------|
| 指令遵循、拒答分寸 | Claude |
| 推理 / 数学 | GPT-6 Astra / Claude Opus 5.5 / Fable 5.1 / Gemini 4 Argon（用你自己的评测集来定） |
| 写作风格 | Claude Sonnet |
| 长 context | Gemini |
| 视频 / 音频 | Gemini |
| 速度（小模型） | Gemini Flash、Groq Llama |
| 价格 | Gemini Flash-Lite / Claude Haiku 5.5 / GPT-6 Luna / qwen-flash 都在最低档 |
| Schema 强制 | 三家都有原生结构化输出（OpenAI / Claude 的 strict 模式、Gemini 的 `response_json_schema`） |
| 工具调用稳定性 | Claude Sonnet |
| 开源 / 本地部署 | Llama / Qwen 最新开源版 |

---

## 6. 选模型决策树

```
任务类型?
├── 简单分类 / 抽取
│   ├── 极低成本 → Gemini Flash-Lite / Haiku 5.5 / Luna / qwen-flash
│   ├── 平衡 → GPT-6 Luna / Claude Haiku 5.5
│   └── 严格 schema → 用各家的原生结构化输出
│
├── 通用对话 / 写作
│   ├── 写作风格优 → Claude Sonnet
│   ├── 通用 → GPT-6.1 Terra
│   └── 极长 context → Gemini Argon / Claude 5 家族（均 1M）
│
├── 复杂推理 / 编程
│   ├── 数学 / 算术 → GPT-6 Astra / Sol（高 effort）
│   ├── 编程 → Claude Sonnet 5.5 / Opus 5.5
│   └── 极复杂 → Fable 5.1 / Opus 5.5 / Gemini 4 Argon
│
└── 多模态
    ├── 视频 / 音频 → Gemini
    └── 图 / PDF → 三家都行
```

---

## 7. 对模型做 A/B

```python
def ab_test_models(prompt: str, evalset: list, candidates: list[str]):
    results = {}
    for model in candidates:
        r = run_eval(prompt, evalset, model)
        cost = compute_cost(r["tokens_used"], model)
        results[model] = {
            "pass_rate": r["pass_rate"],
            "p95_latency": r["p95_latency"],
            "cost_per_1k": cost / r["total_calls"] * 1000,
        }
    return results
```

我在项目里的取舍是：通过率差距不超过 3 个百分点，选便宜的；超过 3 个百分点，选通过率高的，除非业务能接受那点差距。最后再确认一下 p95 延迟能不能接受。

---

## 8. demo：用 Pydantic AI 跨厂商分类

```python
from pydantic import BaseModel
from typing import Literal
from pydantic_ai import Agent


class Result(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise", "other"]
    confidence: float


PROMPT = """你是客服反馈分类师。把输入归到 5 类之一。
- bug: 软件错误
- feature: 功能建议
- complaint: 抱怨（非 bug）
- praise: 好评
- other: 其他

反讽按意图归。"""


MODELS = [
    "openai:gpt-6-luna",
    "anthropic:claude-haiku-5-5",
    "google:gemini-3.8-flash",
]

TESTS = ["App 闪退", "希望加深色模式", "客服真差", "好用", "今天天气真好"]

for model in MODELS:
    print(f"\n=== {model} ===")
    agent = Agent(model, output_type=Result, instructions=PROMPT)
    for text in TESTS:
        try:
            r = agent.run_sync(text)
            print(f"  {r.output.category:12s} {text}")
        except Exception as e:
            print(f"  failed: {e}")
```

---

## 9. 换模型检查清单

切换模型时逐项过一遍：

```
□ 移植后 evalset 通过率 ≥ 90% 原家？
□ p95 延迟可接受？
□ 成本预算够？
□ 独家 feature（cache_control / adaptive thinking 等）替换了？
□ Structured output 重新配？
□ tool 描述格式适配？
□ Few-shot 位置调整（system vs messages）？
□ XML / Markdown 结构调整？
□ Prompt caching 重新设计？
```

---

## 10. 常见坑

| 坑 | 后果 |
|----|------|
| 跨厂商共用一份 prompt 却不测 | 通过率掉了 20% 才发现 |
| 死绑某一家的独有功能 | 以后切换成本高 |
| 不算成本 | 月底账单超预算 |
| 不测延迟 | 用户体验变差 |
| 同一个任务为每家维护一个版本 | 版本越来越多维护不过来；用 Pydantic AI 这类抽象层 |
| 小任务用旗舰模型 | 白花钱 |

---

## 11. 本章回顾

| 篇 | 主题 |
|---|------|
| 01 | Claude（XML / 用结构化输出替代 prefill / thinking / caching） |
| 02 | GPT（markdown / structured outputs / reasoning） |
| 03 | Gemini / 开源 |
| 04 | 跨模型可移植（本篇） |

---

## 12. 下一步

- 生产化：[07-production/](../07-production/)
- 实战：[08-practice/](../08-practice/)

## 参考资料

- LiteLLM cross-provider: https://docs.litellm.ai
- Pydantic AI Models: https://ai.pydantic.dev/models/
- OpenRouter (聚合 100+ 模型): https://openrouter.ai
