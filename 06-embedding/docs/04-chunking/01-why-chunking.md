# Chunking 为啥决定 RAG 上限

> **一句话**：用什么 embedding 模型、什么向量库、什么 prompt 都比不上 **chunking 策略**对 RAG 效果影响大——chunk 切错了，再好的模型也救不回来。

---

## 1. Chunk 是啥

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

## 2. 为啥不直接整篇 embed

```
方案 A：整篇 embed
  长文档 → 1 个 vector
  
问题：
  1. 信息密度低（5000 字塞 1 个 vec → 关键信息被稀释）
  2. 长度限制（多数模型 max 512 / 2048 tokens）
  3. 召回粒度太粗（找到整篇，但用户只关心其中一段）
  4. context 给 LLM 浪费 token
```

```
方案 B：chunk-level embed（主流）
  长文档 → N 个 chunks → N 个 vectors
  
好处：
  1. 信息密度高（200 字 1 个 vec → 精准）
  2. 召回粒度细（"我要的那一段"）
  3. LLM context 高效
```

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

3 个独立维度：

1. **召回**：能不能把"答案所在的 chunk" 召回来
2. **完整**：召回的 chunk 是否包含足够上下文回答问题
3. **信噪比**：召回的 chunk 里有多少跟答案无关

```
理想 chunk：
  ✅ 用户问题对应的关键句包含
  ✅ 上下文够（前面定义 / 例子）
  ✅ 没有大段无关内容
```

---

## 5. 大小 trade-off

| Chunk size | 优 | 劣 |
|------------|-----|-----|
| 太小（< 100 字） | 信息密度高，召回准 | 上下文不足，LLM 难答 |
| 中等（200-500 字） | 平衡 | 有时仍不够 |
| 大（800-1500 字） | 上下文足 | 信息稀释，召回偏 |
| 太大（> 2000 字） | 几乎等于整篇 | 召回粒度太粗 |

实践默认 **400-600 字**（约 200-400 tokens 英文 / 400-600 字中文）。

但**没有金科玉律**——必须用 evalset 验证。

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

**作用**：

- 让"跨边界"的语义在两个 chunk 都出现
- 提高召回率
- 代价：存储 / embed 成本增加 ~20%

通常 overlap = 10-20% chunk_size。

---

## 7. 常见 chunking 方法（章节预告）

| 方法 | 何时用 | 详见 |
|------|--------|------|
| 固定 char / token | 简单兜底 | [02-strategies.md](./02-strategies.md) |
| 递归切（按段落 / 句子）| 通用文本 | 同上 |
| 语义切（按 embedding 相似度）| 高质量需求 | 同上 |
| 结构感知（按 heading / table） | 文档明确结构 | [03-structure-aware.md](./03-structure-aware.md) |
| Small-to-big（多粒度） | RAG 进阶 | [04-small-to-big.md](./04-small-to-big.md) |

---

## 8. demo：错切对比

```python
# demos/chunking/01_why.py
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

A 方案的 best chunk 可能是片段，意思不完整；B 方案 best chunk 是完整步骤。

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

详见 [03-structure-aware.md](./03-structure-aware.md)。

---

## 10. 跟 embedding 模型 max_tokens 配合

```python
# 模型 max_tokens
OPENAI_EMBEDDING_MAX = 8191  # text-embedding-3-*
BGE_MAX = 512
BGE_M3_MAX = 8192
COHERE_MAX = 512

# 你的 chunk size 不能超过模型上限
chunk_size_tokens = min(your_target, model_max - safety_margin)
```

不要"勉强不切"喂超长——大多模型会**截断**（你后面那段没被 embed）。

---

## 11. chunk 元数据

每个 chunk 不只是 text，还要带元数据：

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

帮 LLM / 前端做：

- 显示引用
- 跳到原文
- 加上下文（找 chunk_idx-1 和 +1 一起给 LLM）

详见 [05-metadata.md](./05-metadata.md)。

---

## 12. 实战 checklist

实施 chunking 前问自己：

- [ ] 文档类型（PDF / Markdown / HTML / 表格）？
- [ ] 是否有明显结构（heading / 章节 / 段落）？
- [ ] 单个 chunk 上限多少？
- [ ] 重叠多少？
- [ ] 用什么 embed 模型 → max_tokens 多少？
- [ ] chunk metadata 怎么设计？
- [ ] 怎么测好坏（evalset）？

---

## 13. 下一步

- 📖 具体切分策略 → [02-strategies.md](./02-strategies.md)
- 📖 结构感知（PDF / MD / 表格）→ [03-structure-aware.md](./03-structure-aware.md)
- 📖 多粒度（small-to-big）→ [04-small-to-big.md](./04-small-to-big.md)
- 📖 metadata 设计 → [05-metadata.md](./05-metadata.md)
