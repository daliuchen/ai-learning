# PE Advanced 04：多模态 Prompting —— 图片 / 文档 / 视频

> **一句话**：现代 LLM 能"看图"（图片、PDF、屏幕截图）和"听音频"——但 prompt 写法和纯文本有差异：要明确引导模型**看哪里**、**看什么**，结构化提取比开放描述准得多。

---

## 1. 三家多模态支持

| 模型 | 图片 | PDF | 视频 | 音频 |
|------|------|-----|------|------|
| Claude Sonnet 4 | ✅ | ✅ | ⚠️（帧抽样）| ❌ |
| GPT-4o | ✅ | ✅ | ✅（逐帧） | ✅ |
| Gemini 2.0 | ✅ | ✅ | ✅（原生） | ✅ |

---

## 2. 基本传入方式

### Anthropic

```python
import anthropic, base64, httpx

client = anthropic.Anthropic()

# 用 URL
image_url = "https://example.com/chart.png"

# 或本地文件
image_data = base64.standard_b64encode(open("chart.png", "rb").read()).decode()

resp = client.messages.create(
    model="claude-sonnet-4-6",
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
    model="gpt-4o",
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
    model="gemini-2.0-flash",
    contents=[
        {"file_data": {"mime_type": "image/png", "file_uri": "..."}},
        "提取图表里的数字",
    ],
)
```

---

## 3. 多模态 prompt 设计原则

### 3.1 引导模型"看哪里"
模型不会自动聚焦——明确说：

```
❌ "分析这张图"
✅ "聚焦图表右下角的数据表，提取 Q3 和 Q4 的营收数字"
```

### 3.2 结构化提取 > 自由描述
```
❌ "描述这张发票"
✅ "从发票中提取以下字段，返回 JSON：
   - vendor (string)
   - total_amount (float)
   - currency (CNY/USD/...)
   - date (YYYY-MM-DD)
   - line_items (array of {name, qty, price})"
```

### 3.3 给参考点
```
"在图中找到红色圆圈标记的对象，描述它"
"分析以左下角时间戳为准的第三个柱状条"
```

### 3.4 防"看错"
```
"如果图片不清楚 / 看不见某字段，返回 null 而不是猜"
```

### 3.5 多图片处理
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

明确标号 + 任务描述。

---

## 4. 常见任务模板

### 4.1 OCR + 抽取
```
你是 OCR + 信息抽取系统。

任务：
1. 识别图片中所有文字
2. 提取以下字段（JSON）：...
3. 文字模糊 / 看不清 → 字段填 null

返回 JSON。
```

### 4.2 图表读取
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

### 4.3 UI 截图 → 代码
```
任务：这是一个网页截图。生成等价的 HTML + Tailwind CSS。

要求：
- 用 semantic HTML
- 颜色用 Tailwind class
- 不要 inline style
- 注释关键区域
```

### 4.4 文档解析（PDF）
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

## 5. 视频 / 长内容

Gemini 2.0 支持完整视频上传：

```python
video_file = client.files.upload(file="video.mp4")
resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=[video_file, "总结视频里 5:00-7:00 之间讨论的内容"],
)
```

GPT-4o / Claude 没有"原生视频"，需要先抽帧：

```python
frames = extract_frames("video.mp4", every_seconds=5)
content = [
    *[{"type": "image", "source": img} for img in frames],
    {"type": "text", "text": "总结这些帧描绘的事件"},
]
```

---

## 6. 多模态的失败模式

### 6.1 模型"瞎编"图里没有的东西
```
图：一只猫
prompt: "这只狗在做什么？"
模型: "这只柯基在草地上奔跑..." ← 把猫看成狗
```

对策：明确"先看 + 描述图里实际有的"。

### 6.2 OCR 出错
小字 / 复杂字体 / 模糊图 → OCR 错。

对策：
- 让模型返回 confidence + 原文片段
- 关键字段用结构化输出强制
- 不确定 → null

### 6.3 多图混淆
3 张图，模型把图 1 的事说成图 3 的。

对策：明确编号 + "图 X 中"。

### 6.4 Token 消耗
高分辨率图 = 大量 token。Claude 高分图 ~1500 tokens。

对策：
- 不必要时压缩图像
- 关键区域裁剪

---

## 7. demo：发票 OCR

```python
# demos/advanced/04_multimodal_invoice.py
import base64, json
import anthropic
client = anthropic.Anthropic()


def ocr_invoice(image_path: str) -> dict:
    data = base64.standard_b64encode(open(image_path, "rb").read()).decode()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
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
    return json.loads(resp.content[0].text)
```

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| **"分析这张图"** | 模糊，明确"提取 X / 描述 Y" |
| **不要求 null 兜底** | 模型瞎编 |
| **多图不编号** | 混淆 |
| **图很小但用最高分辨率** | 浪费 token |
| **PDF 一次塞太多页** | context 爆 + 中部丢失 |
| **OCR 出错没 confidence** | 下游不知道哪条不可靠 |

---

## 9. 下一步

- 📖 meta-prompting → [05-meta-prompting.md](./05-meta-prompting.md)
- 📖 注入防御 → [06-injection-defense.md](./06-injection-defense.md)
- 📖 实战 OCR + 流程 → [05-by-task/02-extractor.md](../05-by-task/02-extractor.md)

## 参考资料

- Anthropic Vision: https://docs.anthropic.com/en/docs/build-with-claude/vision
- OpenAI Vision: https://platform.openai.com/docs/guides/vision
- Gemini Vision: https://ai.google.dev/gemini-api/docs/vision
