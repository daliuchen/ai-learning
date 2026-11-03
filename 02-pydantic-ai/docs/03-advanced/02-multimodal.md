# Pydantic AI 进阶 02：多模态输入（Image / Audio / Video / Document）

> 各家 API 传图片、文档的格式都不一样：OpenAI 用 image_url，Anthropic 用 source.base64，Gemini 又是 inline_data。Pydantic AI 用一组统一的内容类型（`ImageUrl` / `BinaryContent` / `DocumentUrl` / `AudioUrl` / `VideoUrl` / `UploadedFile`）把这些差异挡在框架里面。这篇讲每种类型怎么用、各 provider 支持到什么程度，以及成本上要注意什么。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. 能力地图

LLM 应用里的多模态需求，基本就这四类：

1. 看图：传一张图，问「这是什么」
2. 读 PDF：传一份文档，让它总结或抽取字段
3. 听音频：传 mp3 / wav，让它转写或分析
4. 看视频：传 mp4，让它描述场景（只有少数模型支持）

Pydantic AI 给每种内容定义了一个类型，你把它和文本一起放进 `agent.run(...)` 的输入：

```python
result = agent.run_sync([
    "这张图里是什么品牌的 logo？",
    ImageUrl(url="https://example.com/logo.png"),
])
```

输入从一个字符串变成了「字符串和多模态对象组成的列表」，新语法就这么多。

---

## 2. 内容类型一览

| 类型 | 模块 | 构造参数 | 常见用途 |
|------|------|----------|----------|
| `ImageUrl` | `pydantic_ai` | `url`, `force_download?` | 公网图片 URL |
| `BinaryContent` | `pydantic_ai` | `data: bytes`, `media_type` | 本地 / 内存里的任意二进制 |
| `DocumentUrl` | `pydantic_ai` | `url`, `force_download?` | PDF / Word 文档 URL |
| `AudioUrl` | `pydantic_ai` | `url`, `force_download?` | 音频 URL |
| `VideoUrl` | `pydantic_ai` | `url`, `force_download?` | 视频 URL（仅部分 provider） |
| `UploadedFile` | `pydantic_ai` | `file_id`, `provider_name`, `media_type?` | 已经上传到 provider 的文件引用 |
| `TextContent` | `pydantic_ai` | `content`, `metadata?` | 想给文本附 metadata 时用 |
| `StructuredContent` | `pydantic_ai` | `value`, `tag?`, `format?` | 把结构化数据（如页面状态）渲染成带标签的 JSON/XML 文本放进提示 |

这些类型都从顶层导出，直接 `from pydantic_ai import ImageUrl, BinaryContent, ...` 就行。

---

## 3. ImageUrl：最常用

公网能访问的图片 URL：

```python
from pydantic_ai import Agent, ImageUrl

agent = Agent("openai:gpt-6.1-sol")
result = agent.run_sync([
    "这张图里是什么 logo？",
    ImageUrl(url="https://iili.io/3Hs4FMg.png"),
])
print(result.output)
```

`force_download=True` 时，Pydantic AI 会先把图下载到本地，再以 base64 发给模型。两种情况下用得上：

1. provider 不支持直接传 URL（Anthropic 以前就是这样）
2. 图放在内网图床上，模型那边访问不到，但你的客户端可以

```python
ImageUrl(url="https://private-cdn.intra/logo.png", force_download=True)
```

---

## 4. BinaryContent：本地文件 / 内存数据

没有 URL 的时候用它，适用面最广：

```python
from pathlib import Path
from pydantic_ai import Agent, BinaryContent

agent = Agent("openai:gpt-6.1-sol")
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

大小也有限制：

| Provider | 单图建议上限 | 单 PDF 建议上限 |
|----------|-------------|----------------|
| OpenAI | ~20MB | ~32MB |
| Anthropic | ~5MB（base64 编码后会膨胀 ~33%） | ~32MB / 100 页 |
| Gemini | ~20MB | ~50MB |

超了就先缩小，或者改用 `UploadedFile`（见第 8 节）。

---

## 5. DocumentUrl / AudioUrl / VideoUrl

用法和 `ImageUrl` 差不多：

```python
from pydantic_ai import Agent, DocumentUrl, AudioUrl, VideoUrl

# PDF
agent = Agent("anthropic:claude-sonnet-5-5")
result = agent.run_sync([
    "总结这份论文的核心贡献",
    DocumentUrl(url="https://arxiv.org/pdf/2307.06435.pdf"),
])

# 音频（OpenAI Responses / Gemini）
agent_audio = Agent("openai:gpt-6.1-sol")  # 需要支持音频输入的型号
result = agent_audio.run_sync([
    "这段音频里说了什么？转写成中文",
    AudioUrl(url="https://example.com/clip.mp3"),
])

# 视频（Gemini）
agent_video = Agent("google:gemini-4-argon")
result = agent_video.run_sync([
    "描述这段视频里发生了什么",
    VideoUrl(url="https://example.com/demo.mp4"),
])
```

---

## 6. 模型支持情况

并非每个 provider 都支持每种模态，也并非每种 URL 都能直接交给 provider。官方文档拆成了两个问题来讲。

第一个问题：URL 是直接发给 provider、由它去下载，还是 Pydantic AI 先下载再以字节发送（节选）：

| 模型类 | 直接发 URL | 下载后发字节 | 不支持 |
|--------|-----------|-------------|--------|
| `OpenAIChatModel`（Chat Completions） | `ImageUrl` | `AudioUrl`（仅音频模型）、`DocumentUrl` | `VideoUrl`；`AlibabaProvider`（千问）/ Azure 不支持 `DocumentUrl` |
| `OpenAIResponsesModel` | `ImageUrl` / `AudioUrl` / `DocumentUrl` | — | `VideoUrl` |
| `AnthropicModel` | `ImageUrl`、`DocumentUrl`（PDF） | `DocumentUrl`（text/plain） | `AudioUrl` / `VideoUrl` |
| `GoogleModel`（Vertex / Google Cloud） | 所有 URL 类型 | — | — |
| `GoogleModel`（Gemini API） | YouTube、Files API | 其它 URL | — |
| `XaiModel` | `ImageUrl` | `DocumentUrl` | `AudioUrl` / `VideoUrl` |
| `MistralModel` | `ImageUrl`、`DocumentUrl`（PDF） | `DocumentUrl`（text/plain） | `AudioUrl` / `VideoUrl` |

默认是把 URL 发给 provider，由它那边下载；`force_download=True` 改成 Pydantic AI 先下载再发字节，适合私网图床这类 provider 访问不到的情况。

第二个问题：模型本身能不能读这种文件。看 `model.profile` 里的 `supports_image_input` / `supports_document_input` / `supports_audio_input` / `supports_video_input`。这些标志默认是 `True`，只有已知不支持时才是 `False`，**所以它们不能当作模型一定能读的保证**。

我的经验：

- 抽取 PDF，首选 Anthropic、Gemini、OpenAI Responses；用千问兼容端点就先自己抽文本
- 音频和视频，基本只有 Gemini（视频）和 OpenAI 的音频型号（音频）
- 本地或小模型，要看具体型号是否支持视觉

模型读不了的文件，可以用 `FileUnderstanding` capability：遇到模型不支持的文件类型，先交给另一个模型（`fallback_subagent_model=`）转写成文字，再把描述交给 agent 自己的模型：

```python
from pydantic_ai import Agent, DocumentUrl
from pydantic_ai.capabilities import FileUnderstanding

agent = Agent(
    "openai-chat:qwen-flash",       # 不能读 PDF 的模型
    capabilities=[FileUnderstanding(fallback_subagent_model="openai:gpt-6.1-sol")],
)
agent.run_sync(["这份文档在讲什么？", DocumentUrl("https://example.com/field-guide.pdf")])
```

---

## 7. 一次传多个文件

`agent.run(...)` 的输入是一个列表，可以随意混合：

```python
result = agent.run_sync([
    "对比下面两份合同，列出不同条款",
    DocumentUrl(url="https://acme.com/contract-v1.pdf"),
    DocumentUrl(url="https://acme.com/contract-v2.pdf"),
])
```

列表顺序就是模型看到的顺序，文字提示一般放在最前面。

---

## 8. UploadedFile：超大文件或需要反复使用

OpenAI、Anthropic、Google、xAI 都有 Files API，可以先上传文件拿到一个 `file_id`，之后在多次对话里引用：

```python
from pydantic_ai import Agent, UploadedFile
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider()
model = OpenAIResponsesModel("gpt-6.1-sol", provider=provider)

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

适合这些情况：

- 文件超过 20MB
- 同一份文档要在多个对话里引用，避免每次重复上传、重复计费
- 文件里有敏感信息，不想以 base64 的形式留在日志里

---

## 9. 多模态输出？

多模态主要说的是输入，但 V2 已经支持图片输出：

- 能出图的模型（OpenAI Responses 的 `ImageGenerationTool`、Gemini 图片模型）：`output_type=BinaryImage` 加上 `NativeTool(ImageGenerationTool())`，见[Native Tools 模型内置工具](../02-tools/05-native-tools.md)；
- `ImageGenerator`（`pydantic_ai.images`）：直接调图像生成 API（包括参考图编辑），不经过对话模型；
- Claude 目前没有原生图片输出。

如果 Agent 要生成图片，而模型或 provider 没有原生支持，还可以注册一个调用图像 API 的工具：

```python
@agent.tool_plain
async def draw_image(prompt: str) -> bytes:
    """根据 prompt 生成一张图"""
    img = await openai_client.images.generate(model=IMAGE_MODEL, prompt=prompt)   # 图像模型名以 OpenAI 文档为准
    return await fetch_bytes(img.data[0].url)
```

---

### 9.1 `StructuredContent`：把应用状态放进提示

```python
from pydantic import BaseModel
from pydantic_ai import Agent, StructuredContent

class UIState(BaseModel):
    url: str
    cart_total: float

agent = Agent("openai:gpt-6.1-sol")
result = agent.run_sync([
    StructuredContent(UIState(url="/products/42", cart_total=949.0), tag="ui_state"),
    "购物车总价为什么涨了？",
])
# 模型实际收到：<ui_state>{"url":"/products/42","cart_total":949.0}</ui_state>  然后是问题
```

默认渲染成紧凑的 JSON，外面包上 `tag`，并转义 `< > &`，防止不可信的值把标签提前闭合；`format='xml'` 则渲染成 XML。文本在创建时渲染一次并存进 `content`，所以历史存下来再读出来发送时内容完全一致，不会破坏提示词缓存的前缀。

---

## 10. 实战：发票图片 → 结构化字段

视觉加结构化输出，很快就能做出一个 OCR 的替代方案：

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
    "openai:gpt-6.1-sol",
    output_type=Invoice,
    instructions="你是一位发票识别专家，从图片中抽取结构化字段。",
)

img = Path("invoice.png").read_bytes()
result = agent.run_sync([
    "请抽取这张发票的字段",
    BinaryContent(data=img, media_type="image/png"),
])
print(result.output)
# Invoice(vendor='阿里云', amount=1280.0, date='2024-01-15', items=['ECS 实例', 'CDN 流量'])
```

几点说明：

- `output_type=Invoice` 和多模态输入互不影响，可以一起用
- `Field(description=...)` 是给模型看的字段说明，建议都写上
- 分辨率太高的图会让 token 数暴涨，先缩到 2000px 以内一般就够了

---

## 11. 性能与成本

多模态比纯文本贵得多。下表按各家文档里的计算规则估的量级（示意），具体以各家定价和计费规则为准：

| 模型 | 一张 512x512 图大约 | 一份 10 页 PDF |
|------|---------------------|---------------|
| OpenAI 系 | 取决于型号与分辨率档位 | 取决于型号（文本 + 每页图像） |
| Claude 系 | 约 350 tokens（约等于 宽×高 / 750） | 约 2 万–4 万 tokens（每页文本约 1.5k–3k，另加每页图像） |
| Gemini 系 | 约 258 tokens（按 768×768 tile 计） | 约 2.6k tokens（每页约 258） |

省钱的办法：

1. 缩到模型实际会用的尺寸：图片 token 按像素算，超过的部分 provider 也会先缩小（OpenAI 高细节模式会缩到短边 768px），多传只是浪费带宽
2. 换 `image/webp` 能减小传输体积，但 token 按像素计，不会因此变少
3. 多页 PDF 拆页处理，并行调用
4. 同一个文件反复用，就走 `UploadedFile`

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `BadRequestError: Invalid image` | URL 公网访问不到或返回 403 | 改用 `BinaryContent`，或 `force_download=True` |
| 模型说「看不到图」，或者编造图片内容 | 用了不支持视觉的型号，或图片部件没送到（比如 `BinaryContent` 里是占位字节） | 换支持视觉的型号；确认 `media_type` 和数据有效 |
| 千问 / Azure 传 PDF 报 500 或不支持 | `AlibabaProvider`、Azure 的 Chat 接口不接受文档部件 | 先抽文本，或用 `FileUnderstanding`，或换 provider |
| PDF 报 "format not supported" | 选了不支持 PDF 的 provider（如 Groq） | 换 Claude / Gemini |
| 超大图直接 OOM | 没有缩小 | 先用 PIL 缩小再传 |
| media_type 写错报 422 | 把 `image/jpg` 当成 `image/jpeg`（标准写法是后者） | 用 `image/jpeg` |
| Anthropic 报 "too large" | base64 之后超过 5MB | 缩小或拆分 |
| `force_download` 好像没生效 | 传的是已经 base64 的 BinaryContent | `force_download` 只对 URL 类型有意义 |
| TestModel 下没法测多模态 | TestModel 不会真的调用模型 | 用 mock 检查传给模型的参数里有没有 ImageUrl |

---

## 13. 本章 demo

完整可运行代码：[`demos/advanced/02_multimodal.py`](../../demos/advanced/02_multimodal.py)

下一篇：[Pydantic AI 进阶 03：Thinking（显式思考链）](03-thinking.md)，讲怎么用 `Thinking` capability 统一接入各家的思考模式。
