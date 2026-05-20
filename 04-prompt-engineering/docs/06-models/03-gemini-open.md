# PE Models 03：Gemini 与开源模型

> **一句话**：Gemini 2.0+ 长 context 强（1M+ tokens）、原生多模态；开源模型（Llama / Qwen / DeepSeek）需要更详细的 prompt 引导和更多 few-shot。本篇讲它们各自的"个性"。

---

## 1. Gemini 的特点

| 特性 | 说明 |
|------|------|
| **超长 context** | 2.0 Flash 支持 1M token，1.5 Pro 支持 2M |
| **原生多模态** | 文本 / 图 / 视频 / 音频 一份 API |
| **structured output** | 用 `response_schema` |
| **Thinking 模式** | 2.5 起支持 |
| **Function calling 稳定** | 但生态没 OpenAI 大 |
| **价格便宜** | Flash 比 GPT-4o-mini 便宜 |

---

## 2. Gemini 推荐 prompt 结构

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

风格接近 GPT 的 markdown 写法。Gemini 对 XML 也支持但不如 Claude 强烈偏好。

---

## 3. system_instruction 用法

```python
from google import genai

client = genai.Client()
resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="App crashes",
    config={
        "system_instruction": "You are a classifier...",
        "temperature": 0,
        "response_mime_type": "application/json",
        "response_schema": MyPydanticSchema,
    },
)
```

`system_instruction` 是 system message 的等价。

---

## 4. 长 Context（1M+ tokens）实战

Gemini 在长 context 上**真的能用**——不是名义而已：

```python
# 把 500k token 的代码库塞进去
resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=[
        "下面是项目代码:",
        load_entire_repo(),
        "找出所有调用 deprecated API 的位置。",
    ],
)
```

但有 caveats：
- 关键问题放**开头和结尾**（即使有 1M 也有 attention 衰减）
- 用 file API 上传大文档而非 inline
- prompt caching 在长 context 上特别值钱

---

## 5. 多模态

Gemini 多模态体验是**最一体化**的：

```python
resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=[
        "Summarize this video:",
        video_file,  # 视频文件 upload
    ],
)
```

视频、长 PDF、音频都 native。

---

## 6. Thinking 模式

Gemini 2.5+：

```python
config = {
    "thinking_config": {"include_thoughts": True},
}
resp = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="...",
    config=config,
)
```

类似 Claude extended thinking / GPT reasoning。

---

## 7. 开源模型（Llama / Qwen / DeepSeek）

通过 LiteLLM / Together / Groq / 自建 vLLM 调用：

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

开源模型 prompt 写法注意：

| 维度 | 开源模型 |
|------|---------|
| **指令遵循** | 不如商业模型严格，需要更明确 |
| **Few-shot** | 经常需要 2-5 个示例 |
| **JSON 输出** | 不一定稳，用 outlines / sglang 做 constraint |
| **Refusal** | 弱（除非 instruct-tuned） |
| **Tool use** | 看模型版本，Llama 3.3+ / Qwen 2.5+ 才稳 |
| **长 context** | 训练 context 限制 |

### 7.1 Llama prompt 结构

Llama 系列用特定的 chat template：

```
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a helpful assistant.<|eot_id|>
<|start_header_id|>user<|end_header_id|>
What is 2+2?<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
```

用 API 时 tokenizer 自动应用 template，但**自托管时**要手动处理。

### 7.2 Qwen prompt 结构

```
<|im_start|>system
You are ...<|im_end|>
<|im_start|>user
...<|im_end|>
<|im_start|>assistant
```

### 7.3 强制 JSON 输出（开源）

用 [Outlines](https://github.com/outlines-dev/outlines) 或 vLLM 的 `guided_json`：

```python
import outlines
model = outlines.models.transformers("...")
generator = outlines.generate.json(model, MyPydanticModel)
result = generator(prompt)  # 100% 符合 schema
```

商业 API 自带 schema 强制；开源要靠这些库。

---

## 8. 开源模型常见坑

| 坑 | 排查 |
|----|------|
| **Llama 不遵循 system instruction** | 重要约束放 user 末尾 |
| **Qwen 输出中英文混** | 明确"用中文 / 用英文" |
| **小模型（< 13B）做不动 CoT** | 用更大或上 self-consistency |
| **DeepSeek-R1 自带 thinking 但泄漏** | 后处理删 `<think>...</think>` |
| **开源指令遵循弱** | 大量 few-shot |
| **没 prompt caching** | 开源 API 多半不支持 caching |

---

## 9. 三家短对比

| 维度 | Claude | GPT | Gemini |
|------|--------|-----|--------|
| 结构偏好 | XML | Markdown | Markdown |
| Few-shot 位置 | system 内 XML | messages 数组 | system / contents |
| Structured output | tool use | response_format | response_schema |
| Reasoning | extended thinking | reasoning_effort | thinking_config |
| 长 context | 200k | 128k | 1M-2M |
| 多模态 | 图 / PDF | 图 / 视频 / 音频 | 图 / 视频 / 音频 / 长 PDF |
| Prompt caching | 自动 + cache_control | 自动 | 显式 |
| 价格（小模型） | Haiku $0.25/M | mini $0.15/M | Flash $0.075/M |

---

## 10. demo：Gemini 长 context 示例

```python
# demos/models/03_gemini_long_context.py
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
    model="gemini-2.0-flash",
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
| **以为 1M 都"看得见"** | 关键信息放首尾 |
| **Gemini 用 XML** | 偏好 markdown |
| **开源模型不带 schema 强制** | 用 outlines / sglang |
| **跨家用同一份 prompt** | 必须适配 |
| **开源模型小到 < 7B** | 复杂任务做不动，不要硬调 prompt |

---

## 12. 下一步

- 📖 跨模型可移植 → [04-cross-model.md](./04-cross-model.md)
- 📖 Production 化（version / caching） → [07-production/](../07-production/)

## 参考资料

- Gemini Prompting Guide: https://ai.google.dev/gemini-api/docs/prompting-intro
- Llama 3.3 Documentation: https://www.llama.com/docs/
- Outlines (structured output for open models): https://github.com/outlines-dev/outlines
- LiteLLM (unified API): https://docs.litellm.ai
