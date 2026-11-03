# PE Models 03：Gemini 与开源模型

> Gemini 的长上下文是真能用的（旗舰 1M），多模态是原生的，思考深度用 `thinking_level` 控制。开源模型（Llama / Qwen / DeepSeek）则是另一种情况：指令遵循没那么严格，prompt 要写得更细，few-shot 也要给得更多。这篇分别讲两边的脾气。
>
> 本篇以 2026 年 11 月的版本为准：`gemini-4-argon`（旗舰）/ `gemini-3.8-flash` / `gemini-3.5-flash-lite`，`google-genai` 2.x。`gemini-2.0-flash` 已关停，2.5 系列仅对已在使用的用户保留。

---

## 1. Gemini 的特点

| 特性 | 说明 |
|------|------|
| 超长 context | 旗舰 `gemini-4-argon` 统一 1M context（旧资料里 1.5 Pro 的 2M 已过时） |
| 原生多模态 | 文本 / 图 / 视频 / 音频 走同一个 API |
| structured output | `response_mime_type` + `response_json_schema`（或 Pydantic 的 `response_schema`） |
| 默认开启思考 | 3.x/4 全系列默认动态思考，用 `thinking_level` 调 |
| 函数调用稳定 | 但生态没有 OpenAI 大 |
| 价格低 | Flash / Flash-Lite 是小模型里最便宜的一档（具体价格以官方页为准） |
| 采样参数建议保持默认 | Gemini 3 官方建议别调低 temperature（低于 1.0 可能出现循环或推理变差） |

---

## 2. Gemini 推荐的 prompt 结构

```
# Task
Classify customer feedback.

# Context
我们是 SaaS 公司，目标客户中小企业。

# Categories
- bug: ...
- feature: ...
- ...

# Output Format
JSON: {category: str, confidence: float}

# Examples
- Input: "App crashes" / Output: {"category": "bug", "confidence": 0.95}
```

风格和 GPT 的 markdown 写法接近。Gemini 也认 XML，只是不像 Claude 那样偏爱。

---

## 3. system_instruction 用法

```python
from google import genai

client = genai.Client()
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="App crashes",
    config={
        "system_instruction": "You are a classifier...",
        "response_mime_type": "application/json",
        "response_json_schema": MyPydanticSchema.model_json_schema(),   # 或 "response_schema": MyPydanticSchema
    },
)
```

`system_instruction` 相当于 system message。人设、关键约束、输出格式都放在这里。

> config 里不用设 `temperature`。Gemini 3+ 官方建议保持默认，想要稳定就靠 prompt、schema 和评测。

---

## 4. 长 context（1M+ tokens）实战

Gemini 的长 context 不是纸面参数，实际塞进去确实能用：

```python
# 把 500k token 的代码库塞进去（官方建议：文档/数据放前面，问题放最后）
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=[
        "下面是项目代码:",
        load_entire_repo(),
        "找出所有调用 deprecated API 的位置。",
    ],
)
```

不过有几点要注意：

- 关键问题放在开头和结尾，1M 窗口也一样有注意力衰减
- 大文档用 File API 上传，不要 inline
- 长 context 场景下 prompt caching 特别省钱

---

## 5. 多模态

三家里 Gemini 的多模态用起来最省事：

```python
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=[
        "Summarize this video:",
        video_file,  # 视频文件 upload
    ],
)
```

视频、长 PDF、音频都是原生支持。

---

## 6. Thinking：thinking_level

Gemini 3+ 默认就会动态思考（`gemini-3.8-flash` / `gemini-4-argon` 默认 `medium`，`gemini-3.5-flash-lite` 默认 `minimal`）。用 `thinking_level`（`minimal` / `low` / `medium` / `high`，各模型支持的档位不同）调深浅：

```python
from google.genai import types

resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="...",
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="low", include_thoughts=True),
    ),
)
```

- `thinking_budget`（按 token 数）是 2.5 时代的写法，SDK 里还保留着，新模型用 `thinking_level`
- `max_output_tokens` 包含思考 token。思考把额度吃完会返回不完整的结果，想控成本先降 level
- 官方还提供了新的 Interactions API（`client.interactions.create(..., generation_config={"thinking_level": ...})`），本手册仍以 `generate_content` 为准
- 同样，不要在 prompt 里再要求"一步步推理"

---

## 7. 开源模型（Llama / Qwen / DeepSeek）

> Qwen 在百炼上有 OpenAI 兼容接口，`qwen-flash` 是最便宜的一档（本手册的 demo 就用它）；`qwen-flash`、`qwen-plus` 这类别名具体指向哪个版本，以百炼官方模型列表为准。

可以通过 LiteLLM / Together / Groq / 自建 vLLM 调用：

```python
from openai import OpenAI
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=...,
)
resp = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[...],
)
```

给开源模型写 prompt，要留意这些差别：

| 维度 | 开源模型 |
|------|---------|
| 指令遵循 | 不如商业模型严格，要写得更明确 |
| Few-shot | 经常需要 2-5 个示例 |
| JSON 输出 | 不一定稳，用 outlines / sglang 做约束 |
| 拒答 | 较弱（instruct 微调过的除外） |
| 工具调用 | 看模型版本，Llama 3.3+ / Qwen 2.5+ 才稳 |
| 长 context | 受训练时的 context 长度限制 |

### 7.1 Llama 的 prompt 结构

Llama 系列有自己的 chat template：

```
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a helpful assistant.<|eot_id|>
<|start_header_id|>user<|end_header_id|>
What is 2+2?<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
```

走托管 API 或 OpenAI 兼容接口时服务端会自动套 template；自己用 transformers 直接推理时，要用 `tokenizer.apply_chat_template` 处理。

### 7.2 Qwen 的 prompt 结构

```
<|im_start|>system
You are ...<|im_end|>
<|im_start|>user
...<|im_end|>
<|im_start|>assistant
```

### 7.3 开源模型强制 JSON 输出

用 [Outlines](https://github.com/dottxt-ai/outlines)，或者 vLLM / SGLang 自带的结构化输出（约束解码）：

```python
import outlines
from transformers import AutoModelForCausalLM, AutoTokenizer

model = outlines.from_transformers(
    AutoModelForCausalLM.from_pretrained("..."),
    AutoTokenizer.from_pretrained("..."),
)
result = model(prompt, MyPydanticModel)  # 约束解码，输出是符合 schema 的 JSON 字符串
obj = MyPydanticModel.model_validate_json(result)
```

商业 API 自带 schema 强制，开源模型得靠这些库。

---

## 8. 开源模型常见坑

| 坑 | 排查 |
|----|------|
| Llama 不遵循 system instruction | 把重要约束放到 user 消息末尾 |
| Qwen 输出中英文混杂 | 明确写"用中文"或"用英文" |
| 小模型（< 13B）做不动 CoT | 换更大的模型，或改用 self-consistency |
| DeepSeek-R1 自带 thinking，但会漏到输出里 | 后处理删掉 `<think>...</think>` |
| 开源模型指令遵循弱 | 多给 few-shot |
| 没开 prompt caching | 自托管时打开 vLLM / SGLang 的前缀缓存；托管 API 支不支持看各家文档 |

---

## 9. 三家对比

| 维度 | Claude | GPT | Gemini |
|------|--------|-----|--------|
| 结构偏好 | XML（官方推荐） | Markdown / XML 都认 | Markdown / XML 都认，全程统一一种 |
| Few-shot 位置 | system 内 XML | messages 数组 | system / contents |
| Structured output | `output_config.format` / strict tool | `text_format` / `response_format` | `response_json_schema` |
| Reasoning | adaptive thinking + `effort` | `reasoning.effort` | `thinking_level` |
| 长 context | 1M（Haiku 4.5 为 200K） | 1.05M | 1M |
| 采样参数 | 已移除 | 视模型而定 | 建议保持默认 |
| 多模态 | 图 / PDF | 图 / 视频 / 音频 | 图 / 视频 / 音频 / 长 PDF |
| Prompt caching | `cache_control` 断点 / 顶层自动 | 前缀自动命中 | 隐式 + 显式 |
| 价格（小模型，输入） | Haiku 5.5 $0.10/M | Luna $0.10/M | Flash-Lite 最低一档（见官方页） |

---

## 10. demo：Gemini 长 context

```python
from google import genai
client = genai.Client()

def read_repo_files(repo_dir: str) -> str:
    import pathlib
    out = []
    for p in pathlib.Path(repo_dir).rglob("*.py"):
        out.append(f"=== {p} ===\n{p.read_text()}\n")
    return "\n".join(out)


code = read_repo_files("./my-project")  # 可以几十万 token
question = "找出所有使用 print() 调试的位置。"

resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=[question, code],
    config={
        "system_instruction": "你是 senior Python engineer 做 code review。",
    },
)
print(resp.text)
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 以为 1M 里的内容模型都"看得见" | 关键信息放首尾 |
| 纠结 Gemini 该用 XML 还是 markdown | 两者都行，但一个 prompt 里选定一种并保持一致 |
| 开源模型不做 schema 强制 | 用 outlines / sglang |
| 跨厂商共用同一份 prompt 却只在一家上评测 | 每家都跑一遍评测集，掉得多的那家再单独适配，见 [跨模型可移植](./04-cross-model.md) |
| 开源模型小于 7B | 复杂任务做不动，别在 prompt 上硬磨 |

---

## 12. 下一步

- [跨模型可移植 + 成本性能权衡](./04-cross-model.md)
- 生产化（版本、缓存）：[07-production/](../07-production/)

## 参考资料

- Gemini Prompting strategies: https://ai.google.dev/gemini-api/docs/prompting-strategies
- Gemini models: https://ai.google.dev/gemini-api/docs/models
- Thinking: https://ai.google.dev/gemini-api/docs/thinking
- Llama 3.3 Documentation: https://www.llama.com/docs/
- Outlines (structured output for open models): https://github.com/dottxt-ai/outlines
- LiteLLM (unified API): https://docs.litellm.ai
