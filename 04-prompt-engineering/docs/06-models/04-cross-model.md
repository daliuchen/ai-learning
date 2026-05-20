# PE Models 04：跨模型可移植 + 成本性能权衡

> **一句话**：写 prompt 时考虑"换家是否能用"，而不是死绑一家。本篇讲怎么设计可移植 prompt、什么时候必须分叉、以及三家成本性能 cheatsheet。

---

## 1. 可移植 prompt 设计原则

### 1.1 用"中性"结构
不偏 XML 或 markdown 任一极端——用两者都识别的：

```
任务: 分类客服反馈

类别:
- bug: ...
- feature: ...

输出: JSON {category, confidence}

约束:
- 严格用 enum
- 反讽按意图归
```

简洁标题 + bullet——三家都识别好。

### 1.2 抽象 structured output 层
用 Pydantic AI / LiteLLM / instructor 统一：

```python
# Pydantic AI 自动选三家正确 API
agent = Agent("openai:gpt-4o-mini", output_type=Classification)
agent2 = Agent("anthropic:claude-haiku-4-5", output_type=Classification)
agent3 = Agent("google:gemini-2.0-flash", output_type=Classification)
# 同一份 Pydantic 模型，三家都用
```

### 1.3 不依赖独家 feature
| 独家 feature | 移植时怎么办 |
|--------------|-------------|
| Claude prefill | 改成在 prompt 里说"以 { 开头返回 JSON" |
| Claude extended thinking | 改成 "在 <thinking> 里推理" |
| OpenAI Structured Outputs | 在 prompt 里加 JSON Schema 描述 + retry |
| GPT-5 reasoning_effort | 换成 "let's think step by step" + 用 CoT prompt |
| Gemini 1M context | 切片 + RAG |

### 1.4 测试矩阵
每个 prompt 都在**至少 2 家**测：

```python
def cross_model_test(prompt, evalset):
    results = {}
    for model in ["openai:gpt-4o-mini", "anthropic:claude-haiku-4-5", "google:gemini-2.0-flash"]:
        results[model] = run_eval(prompt, evalset, model=model)
    return results
```

发现"某家挂得厉害"时，决定：
- 改 prompt 让三家都过得去（牺牲单家最优）
- 或针对该家做单独版本

---

## 2. 什么时候必须分叉

### 必须分叉的场景

- 用了 Claude prefill / extended thinking 等独家 feature
- 业务 100% 在一家上（无跨家需求）
- 一家 prompt 涨 10+% 但其他家暴跌

### 该统一的场景

- prompt 移植后各家 80%+ 通过率
- 跨家差 < 5 个百分点
- 维护成本 > 性能收益

---

## 3. 适配层模式

中等复杂度的项目可以做 **prompt adapter**：

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

但**别过度工程**——简单项目直接用 Pydantic AI 即可。

---

## 4. 成本对比（2026-05 价格）

| 模型 | Input / 1M tokens | Output / 1M tokens | 适合 |
|------|-------------------|---------------------|------|
| Claude Haiku 4.5 | $0.80 | $4 | 分类 / 抽取 |
| Claude Sonnet 4.6 | $3 | $15 | 通用 / 写作 |
| Claude Opus 4 | $15 | $75 | 旗舰 |
| GPT-4o-mini | $0.15 | $0.60 | 极便宜分类 |
| GPT-4o | $2.5 | $10 | 通用 |
| GPT-5 (reasoning) | ~$10 / 1M | ~$30 / 1M | 推理 |
| Gemini 2.0 Flash | $0.075 | $0.30 | 最便宜 |
| Gemini 2.5 Pro | $1.25 | $10 | 长 context |
| Llama 3.3 70B (Groq) | $0.59 | $0.79 | 开源 / 速度 |

价格随时间下降——上线时实时查。

---

## 5. 性能特性 cheatsheet

| 维度 | 最强 |
|------|------|
| 指令遵循 / refusal 准 | Claude |
| 推理 / 数学 | GPT-5 / Claude Opus |
| 写作风格 | Claude Sonnet |
| 长 context | Gemini |
| 视频 / 音频 | Gemini |
| 速度（小模型） | Gemini Flash, Groq Llama |
| 价格 | Gemini Flash |
| Schema 强制 | OpenAI Structured Outputs |
| Tool use 稳定性 | Claude Sonnet |
| 开源 / on-prem | Llama 3.3 / Qwen 2.5 |

---

## 6. 选模型决策树

```
任务类型?
├── 简单分类 / 抽取
│   ├── 极低成本 → Gemini 2.0 Flash
│   ├── 平衡 → GPT-4o-mini / Claude Haiku
│   └── 严格 schema → OpenAI structured output
│
├── 通用对话 / 写作
│   ├── 写作风格优 → Claude Sonnet
│   ├── 通用 → GPT-4o
│   └── 极长 context → Gemini Pro
│
├── 复杂推理 / 编程
│   ├── 数学 / 算术 → GPT-5 reasoning
│   ├── 编程 → Claude Sonnet 4.6 / Opus 4
│   └── 极复杂 → Opus / o3
│
└── 多模态
    ├── 视频 / 音频 → Gemini
    └── 图 / PDF → 三家都行
```

---

## 7. A/B 测试模型

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

实际项目上：
- 通过率差 < 3% → 选便宜的
- 通过率差 5%+ → 选高的，除非业务允许
- 看 p95 latency 是否能接受

---

## 8. demo：Pydantic AI 跨家分类

```python
# demos/models/04_cross_model.py
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
    "openai:gpt-4o-mini",
    "anthropic:claude-haiku-4-5",
    "google:gemini-2.0-flash",
]

TESTS = ["App 闪退", "希望加深色模式", "客服真差", "好用", "今天天气真好"]

for model in MODELS:
    print(f"\n=== {model} ===")
    agent = Agent(model, output_type=Result, system_prompt=PROMPT)
    for text in TESTS:
        try:
            r = agent.run_sync(text)
            print(f"  {r.output.category:12s} {text}")
        except Exception as e:
            print(f"  failed: {e}")
```

---

## 9. 跨家迁移检查清单

切换模型时过一遍：

```
□ 移植后 evalset 通过率 ≥ 90% 原家？
□ p95 延迟可接受？
□ 成本预算够？
□ 独家 feature（prefill / extended thinking）替换了？
□ Structured output 重新配？
□ tool 描述格式适配？
□ Few-shot 位置调整（system vs messages）？
□ XML / Markdown 结构调整？
□ Prompt caching 重新设计？
```

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **跨家用同一份 prompt 不测** | 通过率掉 20% 才发现 |
| **死绑独家 feature** | 切换成本高 |
| **不评成本** | 月底账单爆 |
| **不评 latency** | 业务体验差 |
| **同 task 跨家维护多版本** | 维护爆炸；用 Pydantic AI 抽象层 |
| **小任务用旗舰模型** | 浪费 |

---

## 11. 06-models 章总结

| 篇 | 主题 |
|---|------|
| 01 | Claude（XML / prefill / thinking / caching） |
| 02 | GPT（markdown / structured outputs / reasoning） |
| 03 | Gemini / 开源 |
| 04 | 跨模型可移植（本篇） |

---

## 12. 下一步

- 📖 Production 化 → [07-production/](../07-production/)
- 📖 实战 → [08-practice/](../08-practice/)

## 参考资料

- LiteLLM cross-provider: https://docs.litellm.ai
- Pydantic AI Models: https://ai.pydantic.dev/models/
- OpenRouter (聚合 100+ 模型): https://openrouter.ai
