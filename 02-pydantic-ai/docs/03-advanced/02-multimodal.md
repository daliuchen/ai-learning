# Pydantic AI 进阶 02：多模态输入（Image / Audio / Video / Document）

> **一句话**：Pydantic AI 用一组**统一的内容类型**（`ImageUrl` / `BinaryContent` / `DocumentUrl` / `AudioUrl` / `VideoUrl` / `UploadedFile`）抽象掉了"OpenAI 用 image_url，Anthropic 用 source.base64，Gemini 又是 inline_data"这一堆方言。

---

## 1. 一句话能力地图

LLM 应用的多模态需求基本只有四类：

1. **看图说话**：传一张图，问"这是什么"
2. **读 PDF**：传一份文档，让它总结 / 抽字段
3. **听音频**：传 mp3 / wav，让它转写或分析
4. **看视频**：传 mp4，让它描述场景（少数模型支持）

Pydantic AI 的解法是给每种内容定义一个**类型**，然后允许你把它和文本一起作为 `agent.run(...)` 的输入：

```python
result = agent.run_sync([
    "这张图里是什么品牌的 logo？",
    ImageUrl(url="https://example.com/logo.png"),
])
```

输入参数从"一段字符串"扩展成了"字符串 + 多模态类型 组成的列表"，这就是全部新语法。

---

## 2. 六种内容类型

| 类型 | 模块 | 构造参数 | 典型用途 |
|------|------|----------|----------|
| `ImageUrl` | `pydantic_ai` | `url`, `force_download?` | 公网图片 URL |
| `BinaryContent` | `pydantic_ai` | `data: bytes`, `media_type` | 本地 / 内存里的任意二进制 |
| `DocumentUrl` | `pydantic_ai` | `url`, `force_download?` | PDF / Word 文档 URL |
| `AudioUrl` | `pydantic_ai` | `url`, `force_download?` | 音频 URL |
| `VideoUrl` | `pydantic_ai` | `url`, `force_download?` | 视频 URL（仅部分 provider） |
| `UploadedFile` | `pydantic_ai` | `file_id`, `provider_name`, `media_type?` | 已经上传到 provider 的文件引用 |
| `TextContent` | `pydantic_ai` | `content`, `metadata?` | 想给文本附 metadata 时用 |

所有这些类型都从顶层导出，直接 `from pydantic_ai import ImageUrl, BinaryContent, ...` 即可。

---

## 3. ImageUrl：最常用

公网可访问的图片 URL：

```python
from pydantic_ai import Agent, ImageUrl

agent = Agent("openai:gpt-4o-mini")
result = agent.run_sync([
    "这张图里是什么 logo？",
    ImageUrl(url="https://iili.io/3Hs4FMg.png"),
])
print(result.output)
```

`force_download=True` 时 Pydantic AI 会**先把图下载到本地、再以 base64 发给模型**。这个开关在两种情况下有用：

1. provider 不支持直接传 URL（Anthropic 早期版本就这样）
2. 你的内网图床 LLM 访问不了，但客户端能访问

```python
ImageUrl(url="https://private-cdn.intra/logo.png", force_download=True)
```

---

## 4. BinaryContent：本地文件 / 内存数据

最通用的多模态类型，**没有 URL 的时候用它**：

```python
from pathlib import Path
from pydantic_ai import Agent, BinaryContent

agent = Agent("openai:gpt-4o-mini")
img_bytes = Path("invoice.png").read_bytes()
result = agent.run_sync([
    "这张发票的金额是多少？",
    BinaryContent(data=img_bytes, media_type="image/png"),
])
```

`media_type` 必填，常见值：

| 类型 | media_type |
|------|------------|
| PNG | `image/png` |
| JPEG | `image/jpeg` |
| WebP | `image/webp` |
| GIF | `image/gif` |
| PDF | `application/pdf` |
| MP3 | `audio/mpeg` |
| WAV | `audio/wav` |
| MP4 | `video/mp4` |

**注意大小限制**：

| Provider | 单图建议上限 | 单 PDF 建议上限 |
|----------|-------------|----------------|
| OpenAI | ~20MB | ~32MB |
| Anthropic | ~5MB（base64 编码后会膨胀 ~33%） | ~32MB / 100 页 |
| Gemini | ~20MB | ~50MB |

超过限制要么 resize，要么走 `UploadedFile` 路径（见第 8 节）。

---

## 5. DocumentUrl / AudioUrl / VideoUrl

用法和 `ImageUrl` 几乎一样：

```python
from pydantic_ai import Agent, DocumentUrl, AudioUrl, VideoUrl

# PDF
agent = Agent("anthropic:claude-sonnet-4-5")
result = agent.run_sync([
    "总结这份论文的核心贡献",
    DocumentUrl(url="https://arxiv.org/pdf/2307.06435.pdf"),
])

# 音频（OpenAI Responses / Gemini）
agent_audio = Agent("openai:gpt-4o-audio-preview")
result = agent_audio.run_sync([
    "这段音频里说了什么？转写成中文",
    AudioUrl(url="https://example.com/clip.mp3"),
])

# 视频（Gemini）
agent_video = Agent("google-gla:gemini-1.5-pro")
result = agent_video.run_sync([
    "描述这段视频里发生了什么",
    VideoUrl(url="https://example.com/demo.mp4"),
])
```

---

## 6. 模型支持矩阵

不是每个 provider 都支持每种模态。下面是 2026 年 5 月主流模型的支持矩阵（以官方文档为准，**可能随版本变化**）：

| Provider / 模态 | 图片 | PDF | 音频 | 视频 |
|----------------|------|-----|------|------|
| OpenAI Chat (gpt-4o) | ✅ URL/binary | ⚠️ 转图 | ⚠️ audio-preview | ❌ |
| OpenAI Responses (gpt-5/o-series) | ✅ | ✅ | ✅ | ⚠️ 部分 |
| Anthropic Claude | ✅ URL/binary | ✅ URL/binary | ❌ | ❌ |
| Google Gemini | ✅ | ✅ | ✅ | ✅ |
| xAI Grok | ✅ | ⚠️ | ❌ | ❌ |
| Mistral | ✅ | ✅ PDF only | ❌ | ❌ |
| Groq | ✅ vision 模型 | ❌ | ❌ | ❌ |
| Ollama | ⚠️ 需 vision 模型 | ❌ | ❌ | ❌ |
| OpenRouter | 转发 | 转发 | 转发 | 转发 |

**最稳妥的策略**：先看官方文档你要用的具体型号支不支持，再决定走哪个 Provider。比如要做"PDF 信息抽取"你应该首选 Claude 或 Gemini。

---

## 7. 一次传多个文件

`agent.run(...)` 的输入是 **list**，混搭随便：

```python
result = agent.run_sync([
    "对比下面两份合同，列出不同条款",
    DocumentUrl(url="https://acme.com/contract-v1.pdf"),
    DocumentUrl(url="https://acme.com/contract-v2.pdf"),
])
```

顺序就是模型看到的顺序，**文字 prompt 一般放前面**。

---

## 8. UploadedFile：超大文件 / 需要重复使用

OpenAI / Anthropic / Google / xAI 都有 Files API，能先把文件上传换一个 `file_id`，之后多次会话引用：

```python
from pydantic_ai import Agent, UploadedFile
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider()
model = OpenAIChatModel("gpt-4o", provider=provider)

# 1. 先用底层 client 上传
with open("big-report.pdf", "rb") as f:
    file = await provider.client.files.create(file=f, purpose="user_data")

# 2. 引用 file_id
agent = Agent(model)
result = await agent.run([
    "总结这份报告",
    UploadedFile(file_id=file.id, provider_name=model.system),
])
```

适用场景：

- 文件 > 20MB
- 同一份文档要被多个对话引用（避免每次重复上传计费）
- 文件包含敏感信息，不想以 base64 形式留在日志里

---

## 9. 多模态输出？

**绝大多数 provider 当前不支持模型直接"画图给你"**，多模态主要是输入方向。

少数能力：

- OpenAI 通过工具调用 DALL-E / `image_generation_call`，Pydantic AI 暴露为工具
- Gemini 2.0+ 的 `image generation` 模式
- Claude 暂不支持原生图片输出

如果你的 Agent 要"生成图片"，正确姿势是**注册一个调用 DALL-E API 的工具**：

```python
@agent.tool_plain
async def draw_image(prompt: str) -> bytes:
    """根据 prompt 生成一张图"""
    img = await openai_client.images.generate(model="dall-e-3", prompt=prompt)
    return await fetch_bytes(img.data[0].url)
```

---

## 10. 实战：发票图片 → 结构化字段

把视觉 + 结构化输出结合，三十秒搞定 OCR 替代品：

```python
from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

class Invoice(BaseModel):
    vendor: str = Field(description="开票方名称")
    amount: float = Field(description="金额（含税，元）")
    date: str = Field(description="开票日期 YYYY-MM-DD")
    items: list[str] = Field(default_factory=list, description="商品/服务清单")

agent = Agent(
    "openai:gpt-4o-mini",
    output_type=Invoice,
    system_prompt="你是一位发票识别专家，从图片中抽取结构化字段。",
)

img = Path("invoice.png").read_bytes()
result = agent.run_sync([
    "请抽取这张发票的字段",
    BinaryContent(data=img, media_type="image/png"),
])
print(result.output)
# Invoice(vendor='阿里云', amount=1280.0, date='2024-01-15', items=['ECS 实例', 'CDN 流量'])
```

注意：

- `output_type=Invoice` 与多模态输入完全正交，可以叠加
- `Field(description=...)` 是给模型的"字段提示"，强烈建议写
- 一张分辨率太高的图会撑大 token 数，**先 resize 到 2000px 内**通常足够

---

## 11. 性能与成本

多模态比纯文本贵很多：

| 模型 | 一张 512x512 图大约 | 一份 10 页 PDF |
|------|---------------------|---------------|
| GPT-4o | ~85 输入 tokens | ~3-5k tokens |
| Claude Sonnet | ~1.5k tokens | ~5-10k tokens |
| Gemini Flash | ~258 tokens | ~3k tokens |

**省钱手段**：

1. resize 到模型推荐尺寸（OpenAI 文档建议短边 ≤ 768px）
2. 用 `media_type="image/webp"` 比 png 小 50%
3. 多页 PDF 拆页处理，并行调用
4. 反复用同一文件就走 `UploadedFile`

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `BadRequestError: Invalid image` | URL 不可公网访问 / 403 | 改 `BinaryContent` 或 `force_download=True` |
| 模型说"我看不到图" | 用了非 vision 型号（如 `gpt-3.5-turbo`） | 换 `gpt-4o` / `claude-sonnet` 等支持 vision 的 |
| PDF 报"format not supported" | 选了不支持 PDF 的 provider（如 Groq） | 换 Claude / Gemini |
| 超大图直接 OOM | 没 resize | 先 PIL resize 再传 |
| 错误的 media_type 报 422 | 把 `image/jpg` 当 `image/jpeg`（标准是后者） | 用 `image/jpeg` |
| Anthropic 报"too large" | 实际 base64 后 > 5MB | resize 或拆分 |
| `force_download` 看似没生效 | 你传的是已 base64 的 BinaryContent | `force_download` 只对 URL 类型有意义 |
| TestModel 下没法测多模态 | TestModel 不真实调用模型 | 用 mock 校验"传给模型的参数里包含 ImageUrl"即可 |

---

## 13. 本章 demo

完整可运行代码：[`demos/advanced/02_multimodal.py`](../../demos/advanced/02_multimodal.py)

跑通后下一篇：[03-thinking.md](03-thinking.md) —— Claude / o-series 的"显式思考链"统一接入。
