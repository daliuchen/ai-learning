# PE Advanced 04：多模态 Prompting（图片 / 文档 / 视频）

> 现在的大模型能看图片、PDF、屏幕截图，有的还能听音频。多模态 prompt 的写法和纯文本不太一样：你得明确告诉模型看哪里、看什么，而且结构化提取比让它自由描述准得多。

---

## 1. 三家的多模态支持

| 模型 | 图片 | PDF | 视频 | 音频 |
|------|------|-----|------|------|
| Claude 5 家族（Sonnet 5.5 等） | 支持 | 支持 | 不支持原生视频（需自己抽帧） | 不支持 |
| GPT-6 家族 | 支持 | 支持 | 抽帧 | 走专门的音频模型 |
| Gemini 3.8 Flash / 4 Argon | 支持 | 支持 | 支持（原生） | 支持 |

> 这张表按 2026-11 各家官方模型页整理。视频、音频这类细项变化快，上线前请以模型页为准。Claude 的 PDF 上限是单次请求 32MB、600 页（200K 窗口的模型为 100 页）。

---

## 2. 怎么传入

### Anthropic

```python
import anthropic, base64

client = anthropic.Anthropic()

# 本地文件转 base64；图片在公网上时也可以直接用
# {"type": "image", "source": {"type": "url", "url": "https://example.com/chart.png"}}
image_data = base64.standard_b64encode(open("chart.png", "rb").read()).decode()

resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=1000,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_data}},
            {"type": "text", "text": "提取图表里的数字"}
        ],
    }],
)
```

### OpenAI

```python
from openai import OpenAI
client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-6.1-terra",
    messages=[{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/chart.png"}},
            {"type": "text", "text": "提取图表里的数字"},
        ],
    }],
)
```

### Gemini

```python
from google import genai
client = genai.Client()
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=[
        {"file_data": {"mime_type": "image/png", "file_uri": "..."}},
        "提取图表里的数字",
    ],
)
```

---

## 3. 多模态 prompt 的写法

### 3.1 告诉模型看哪里
模型不会自己聚焦，要说清楚：

```
❌ "分析这张图"
✅ "聚焦图表右下角的数据表，提取 Q3 和 Q4 的营收数字"
```

### 3.2 结构化提取，别让它自由描述
```
❌ "描述这张发票"
✅ "从发票中提取以下字段，返回 JSON：
   - vendor (string)
   - total_amount (float)
   - currency (CNY/USD/...)
   - date (YYYY-MM-DD)
   - line_items (array of {name, qty, price})"
```

### 3.3 给出参照物
```
"在图中找到红色圆圈标记的对象，描述它"
"分析以左下角时间戳为准的第三个柱状条"
```

### 3.4 允许它说看不清
```
"如果图片不清楚 / 看不见某字段，返回 null 而不是猜"
```

### 3.5 多张图片
```
messages=[{
    "role": "user",
    "content": [
        {"type": "image", "source": img1},
        {"type": "image", "source": img2},
        {"type": "text", "text": "图 1 是 before，图 2 是 after。对比两图的变化。"},
    ],
}]
```

每张图都编号，任务里按编号引用。

---

## 4. 常用任务模板

### 4.1 OCR + 抽取
```
你是 OCR + 信息抽取系统。

任务：
1. 识别图片中所有文字
2. 提取以下字段（JSON）：...
3. 文字模糊 / 看不清 → 字段填 null

返回 JSON。
```

### 4.2 读图表
```
任务：读取图表数据。

返回 JSON:
{
  "chart_type": "bar|line|pie|...",
  "title": "...",
  "x_label": "...",
  "y_label": "...",
  "data": [{"x": ..., "y": ...}, ...],
  "trend": "rising|falling|flat|mixed"
}
```

### 4.3 UI 截图转代码
```
任务：这是一个网页截图。生成等价的 HTML + Tailwind CSS。

要求：
- 用 semantic HTML
- 颜色用 Tailwind class
- 不要 inline style
- 注释关键区域
```

### 4.4 解析 PDF
```
任务：从 PDF 提取以下信息：

- 标题
- 作者
- 摘要（如果有）
- 章节标题列表
- 关键数据点（表格、图）

输入 PDF 可能含多页。如果信息分散在多页，标注页码。
```

---

## 5. 视频和长内容

Gemini 原生支持上传完整视频：

```python
video_file = client.files.upload(file="video.mp4")
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=[video_file, "总结视频里 5:00-7:00 之间讨论的内容"],
)
```

Claude 和 OpenAI 的通用模型不支持原生视频，要先抽帧：

```python
frames = extract_frames("video.mp4", every_seconds=5)
content = [
    *[{"type": "image", "source": img} for img in frames],
    {"type": "text", "text": "总结这些帧描绘的事件"},
]
```

---

## 6. 多模态的失败模式

### 6.1 编造图里没有的东西
```
图：一只猫
prompt: "这只狗在做什么？"
模型: "这只柯基在草地上奔跑..." ← 把猫看成狗
```

对策：先让模型描述图里实际有什么，再回答问题。

### 6.2 OCR 识别错
字太小、字体复杂、图片模糊，都会让 OCR 出错。可以让模型同时返回置信度和原文片段，关键字段用结构化输出约束，拿不准的填 null。

### 6.3 多张图搞混
给了 3 张图，模型把图 1 的内容说成图 3。对策是明确编号，并在问题里写「图 X 中」。

### 6.4 Token 消耗大
高分辨率图片很占 token，Claude 一张高分辨率图大约 1500 tokens。不需要细节时就压缩图片，或者只裁出关键区域。

---

## 7. demo：发票 OCR

```python
import base64, json
import anthropic
client = anthropic.Anthropic()


def ocr_invoice(image_path: str) -> dict:
    data = base64.standard_b64encode(open(image_path, "rb").read()).decode()
    resp = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=1024,
        system="""你是发票 OCR + 信息抽取系统。
从图片提取以下字段，返回 JSON：
- vendor (string)
- total_amount (float)
- currency ("CNY" | "USD" | "EUR" | "JPY")
- date (YYYY-MM-DD)
- tax_id (string | null)

约束：
- 看不清的字段填 null（不要猜）
- 金额必须是数字，去掉货币符号
- 日期统一 ISO 格式
- 只返回 JSON 不要其他文字
""",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                {"type": "text", "text": "提取这张发票的信息"},
            ],
        }],
    )
    return json.loads(resp.content[-1].text)
```

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| 只说「分析这张图」 | 太含糊，改成「提取 X」「描述 Y」 |
| 没让模型在看不清时填 null | 模型会编 |
| 多张图不编号 | 容易混淆 |
| 小图也用最高分辨率 | 浪费 token |
| PDF 一次塞太多页 | context 撑爆，中间部分丢失 |
| OCR 结果不带置信度 | 下游不知道哪条不可靠 |

---

## 9. 下一步

- [Meta-Prompting，用 LLM 写和优化 Prompt](./05-meta-prompting.md)
- [Prompt Injection 防御](./06-injection-defense.md)
- [信息抽取（Extractor）](../05-by-task/02-extractor.md)：OCR 加抽取的完整流程

## 参考资料

- Anthropic Vision: https://docs.anthropic.com/en/docs/build-with-claude/vision
- OpenAI Vision: https://platform.openai.com/docs/guides/vision
- Gemini Vision: https://ai.google.dev/gemini-api/docs/vision
