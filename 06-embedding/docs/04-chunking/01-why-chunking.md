# 为什么 Chunking 决定 RAG 的上限

> 在 RAG 里，chunking 策略对效果的影响常常不亚于换 embedding 模型、换向量库或改 prompt，而且 chunk 一旦切坏，后面用再好的模型也很难补回来。这篇先讲清楚为什么要切、切坏了是什么样、怎么判断切得好不好。

---

## 1. 什么是 chunk

```
原文档 (PDF, 5000 字)
        ↓
切分成多个 chunk
        ↓
chunk_1 (200 字) → embedding → 索引
chunk_2 (200 字) → embedding → 索引
chunk_3 (200 字) → embedding → 索引
...
```

每个 chunk 是检索的最小单元。

---

## 2. 为什么不整篇做 embedding

最省事的做法是一篇文档只算一个向量，但这样有几个问题。一篇 5000 字的文档压成一个向量，关键句的信息会被大量无关内容稀释，和具体问题的相似度上不去。模型的输入长度也有上限，各家从几百到十几万 token 不等（见第 10 节），超长部分会被截掉。就算长度放得下，召回的粒度也太粗：用户只关心其中一段，检索却只能告诉你「在这篇里」，整篇塞给 LLM 又白白浪费上下文。

主流做法是先把长文档切成 N 个 chunk，每个 chunk 各算一个向量。一个 200 字左右的 chunk 只讲一件事，向量更聚焦，召回能精确到「我要的那一段」，交给 LLM 的上下文也更省。

---

## 3. 切错有多严重

### 坏例子 1：硬切句

```
"用户可以通过设置→账户→订阅，点击取消按钮停止订阅。每月最后一天截止。"

❌ 在 "通过设置→账户→订阅，点击" 之间硬切
chunk_1: "用户可以通过设置→账户→订阅，点击"
chunk_2: "取消按钮停止订阅。每月最后一天截止。"

→ 搜"取消订阅"，chunk_2 找到，但没了上下文（"在哪点击"）
```

### 坏例子 2：切到表格中间

```
| Plan | Price |
|------|-------|
| Free | $0 |
| Pro  | $20 |  ← chunk_1 到这切了
chunk_2: "| Enterprise | Contact sales |"

→ chunk_2 完全没意义，"Enterprise" 失去 header context
```

### 坏例子 3：跨主题切

```
"...如何升级套餐。第二章：账号安全。怎么开启 2FA..."

❌ chunk 跨过 "第二章" 边界
→ 检索"账号安全" 召回这个 chunk，但前半是升级套餐的（无关 + 干扰 LLM）
```

---

## 4. 衡量 chunking 好坏

可以从三个互相独立的角度看。召回：答案所在的 chunk 能不能被找回来。完整：找回来的 chunk 有没有足够的上下文来回答问题。信噪比：chunk 里有多少内容和答案无关。

```
理想 chunk：
  ✅ 用户问题对应的关键句包含
  ✅ 上下文够（前面定义 / 例子）
  ✅ 没有大段无关内容
```

---

## 5. chunk 大小的取舍

| Chunk size | 优 | 劣 |
|------------|-----|-----|
| 太小（< 100 字） | 信息密度高，召回准 | 上下文不足，LLM 难答 |
| 中等（200-500 字） | 平衡 | 有时仍不够 |
| 大（800-1500 字） | 上下文足 | 信息被稀释，召回偏 |
| 太大（> 2000 字） | 几乎等于整篇 | 召回粒度太粗 |

常见的起点是中文每块 400–600 字，英文每块 200–400 tokens（大约 150–300 个单词）。这只是起点，没有标准答案，最后要用 evalset 验证。

---

## 6. Overlap

```python
chunk_size = 500
overlap = 100

text = "0123456789...（长文本）"

chunks = [
    "0...499",     # 字符 0-499
    "400...899",   # 字符 400-899（重叠 400-499）
    "800...1299",
    ...
]
```

重叠的作用是让跨在边界上的内容在相邻两个 chunk 里都出现，从而提高召回率。代价是 chunk 数量和 embedding 成本按重叠比例上涨：上面 500 / 100 的设置每次只前进 400 个字符，chunk 数大约多出 25%。overlap 一般取 chunk_size 的 10–20%。

---

## 7. 常见 chunking 方法（章节预告）

| 方法 | 何时用 | 详见 |
|------|--------|------|
| 固定 char / token | 简单兜底 | [固定 vs 语义 vs 递归切分](./02-strategies.md) |
| 递归切（按段落 / 句子）| 通用文本 | 同上 |
| 语义切（按 embedding 相似度）| 对质量要求高 | 同上 |
| 结构感知（按 heading / table） | 文档结构明确 | [文档结构感知切分](./03-structure-aware.md) |
| Small-to-big（多粒度） | RAG 进阶 | [多粒度切分：Small-to-Big / Parent Document](./04-small-to-big.md) |

---

## 8. demo：错切对比

```python
from openai import OpenAI
import numpy as np


client = OpenAI()


text = """用户取消订阅的方法：
登录账号后，进入"设置"页面。
然后点击"账户"标签。
找到"订阅"部分，点击"取消订阅"按钮。
系统会要求确认，确认后订阅在当前周期结束后停止。
你仍可使用至周期末。"""


# 方案 A：硬切（错的）
chunk_a1 = "用户取消订阅的方法：\n登录账号后，进入"
chunk_a2 = '"设置"页面。\n然后点击"账户"标签。\n找到"订阅"部分，点击'
chunk_a3 = '"取消订阅"按钮。\n系统会要求确认，确认后订阅在当前周期结束后停止。'

# 方案 B：按句子切（对的）
chunk_b1 = "用户取消订阅的方法：登录账号后，进入设置页面。"
chunk_b2 = "然后点击账户标签，找到订阅部分，点击取消订阅按钮。"
chunk_b3 = "系统会要求确认，确认后订阅在当前周期结束后停止。"


def embed(t):
    resp = client.embeddings.create(model="text-embedding-3-small", input=[t])
    return np.array(resp.data[0].embedding)


query = embed("怎么取消订阅")


for label, chunks in [
    ("A（硬切）", [chunk_a1, chunk_a2, chunk_a3]),
    ("B（按句）", [chunk_b1, chunk_b2, chunk_b3]),
]:
    sims = [float(query @ embed(c)) for c in chunks]
    best = np.argmax(sims)
    print(f"[{label}]")
    print(f"  best chunk sim = {sims[best]:.4f}")
    print(f"  best chunk = {chunks[best][:60]}")
    print()
```

方案 A 排第一的 chunk 往往是个残缺片段，意思不完整；方案 B 排第一的是一个完整的操作步骤。

---

## 9. 不同任务最优 chunk 不同

```
问答 / 客服 FAQ：300-500 字
代码搜索：按函数 / 类切
长文档摘要：1000-2000 字
法律条款：按"条款"切（结构感知）
对话历史：按发言人切 + 时间
论文搜索：按 section / paragraph
```

按结构切的做法见[文档结构感知切分](./03-structure-aware.md)。

---

## 10. 和 embedding 模型的输入上限配合

```python
# 各模型单条输入上限（tokens）
OPENAI_EMBEDDING_MAX = 8191      # text-embedding-3-*
BGE_MAX = 512                    # bge-large-zh-v1.5 等
BGE_M3_MAX = 8192
QWEN3_EMBEDDING_MAX = 32_768     # Qwen3-Embedding，32k
COHERE_V3_MAX = 512              # embed-*-v3.0；embed-v4.0 / v5.0 是 128k

# 你的 chunk size 不能超过模型上限
chunk_size_tokens = min(your_target, model_max - safety_margin)
```

上限只是不能超过的天花板，chunk 该切多大还是由检索效果决定（见第 5 节），对长上下文模型来说，效果最好的大小往往远小于上限。另外别图省事把超长文本整段喂进去：OpenAI 的接口超长会直接报错，sentence-transformers 这类本地模型则会静默截断，超出部分根本没有被 embed。

---

## 11. chunk 元数据

每个 chunk 除了文本，还要带上元数据：

```python
{
    "text": "用户取消订阅的方法...",
    "doc_id": "kb_42",
    "doc_title": "订阅管理 FAQ",
    "chunk_idx": 3,
    "total_chunks": 8,
    "page": 5,         # PDF 来源
    "section": "FAQ",
    "url": "https://docs.example.com/cancel#section-3",
}
```

有了这些字段，前端可以显示引用、跳回原文，也可以把相邻的 chunk_idx-1 和 +1 一起交给 LLM 补上下文。字段怎么设计见[Chunk Metadata 设计](./05-metadata.md)。

---

## 12. 实战 checklist

动手切之前，先把这几件事想清楚：

- [ ] 文档类型（PDF / Markdown / HTML / 表格）？
- [ ] 是否有明显结构（heading / 章节 / 段落）？
- [ ] 单个 chunk 上限多少？
- [ ] 重叠多少？
- [ ] 用什么 embedding 模型，单条输入上限多少？
- [ ] chunk metadata 怎么设计？
- [ ] 怎么测好坏（evalset）？

---

## 13. 下一步

- 具体切分策略：[固定 vs 语义 vs 递归切分](./02-strategies.md)
- PDF / Markdown / 表格怎么切：[文档结构感知切分](./03-structure-aware.md)
- 多粒度切分：[多粒度切分：Small-to-Big / Parent Document](./04-small-to-big.md)
- metadata 设计：[Chunk Metadata 设计](./05-metadata.md)
