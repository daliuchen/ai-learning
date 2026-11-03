# 固定 vs 语义 vs 递归切分

> 我的默认选择是：通用文本用 RecursiveCharacterTextSplitter，按段落、句子、词一层层往下切；数据价值高、预算也够的场景再上 semantic chunker，按语义边界切；固定字数切分只拿来兜底。下面逐个看它们的做法和代价。

---

## 1. 三大主流策略

| 策略 | 怎么切 | 优 | 劣 |
|------|--------|----|----|
| Fixed (char / token) | 按固定字数硬切 | 简单 | 会切断语义 |
| Recursive | 按段落 → 句子 → 词逐层递归 | 通用，能保留结构 | 不感知语义 |
| Semantic | embed 每句，在相似度低的地方切 | 边界落在语义转折处 | 慢、贵 |

---

## 2. Fixed Character Chunking

```python
def fixed_chunk(text: str, size: int, overlap: int) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


chunks = fixed_chunk(text, size=500, overlap=50)
```

这种切法会把词切开（"订阅"被切成"订"和"阅"），也会切在句子中间，语义变得零碎。只适合兜底，或者对质量没要求的极简场景。

---

## 3. Recursive Character Text Splitter（LangChain 默认）

按优先级递归切：先用大粒度的分隔符（段落），切出来还超过上限，再换小粒度的（句子、词）：

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter


splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""],   # 从大到小
)


chunks = splitter.split_text(text)
```

工作过程：

```
1. 先用 "\n\n" 切 → 看每段大小
2. 太大 → 用 "\n" 再切
3. 还太大 → "。" 再切
4. 直到都 ≤ chunk_size
```

英文的默认分隔符是 `["\n\n", "\n", " ", ""]`。处理中文要把 `["。", "！", "？", "，"]` 加进去。

---

## 4. Token-aware Recursive

按 token 数而不是字符数来算，能更精确地控制 LLM 的 context：

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_text_splitters import TokenTextSplitter
import tiktoken


# 方法 1：cl100k_base tokenizer
splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    chunk_size=256,         # 256 tokens
    chunk_overlap=20,
    encoding_name="cl100k_base",
)


# 方法 2：HuggingFace tokenizer
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("BAAI/bge-base-zh-v1.5")
splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
    tok,
    chunk_size=256,
    chunk_overlap=20,
)
```

---

## 5. Semantic Chunking

思路是用 embedding 相似度找语义边界：

```
1. 把文本分成句子
2. 给每个句子算 embedding
3. 相邻句子算 cosine similarity
4. 相似度低于阈值的地方 → 切
```

```python
# pip install langchain-experimental
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai import OpenAIEmbeddings


splitter = SemanticChunker(
    OpenAIEmbeddings(model="text-embedding-3-small"),
    breakpoint_threshold_type="percentile",   # 或 "standard_deviation" / "gradient"
    breakpoint_threshold_amount=95,            # 相邻句的距离（1 - 相似度）超过 95 分位处切
)


chunks = splitter.split_text(text)
```

好处是切点落在自然的语义边界上。代价有三个：每句都要 embed，所以慢；embed 成本翻倍；阈值也不好调。法律、医疗这类高价值数据值得用，普通业务用 recursive 就够了。

---

## 6. 各方法对比 demo

```python
from langchain_text_splitters import (
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
)


text = """订阅管理常见问题

第一部分：取消订阅
要取消订阅，请按以下步骤操作：
1. 登录您的账户
2. 进入"设置"页面
3. 点击"账户"标签
4. 在"订阅"部分点击"取消订阅"
确认后，订阅将在当前周期结束后停止。

第二部分：退款政策
退款仅适用于年付订阅。
首次订阅后 7 天内可申请全额退款。
超过 7 天将按比例退款。"""


# 方案 1：Fixed Character
fixed = CharacterTextSplitter(chunk_size=150, chunk_overlap=20, separator="")
print("=== Fixed ===")
for i, c in enumerate(fixed.split_text(text)):
    print(f"{i}: {c[:80]}...")


# 方案 2：Recursive (default)
recursive = RecursiveCharacterTextSplitter(
    chunk_size=150,
    chunk_overlap=20,
    separators=["\n\n", "\n", "。", " ", ""],
)
print("\n=== Recursive ===")
for i, c in enumerate(recursive.split_text(text)):
    print(f"{i}: {c[:80]}...")
```

跑一下就能看出差别：Recursive 切出来的基本是完整的段落或句子，Fixed 切出来是一堆碎片。

---

## 7. 跟 chunking 等价的 LlamaIndex 用法

```python
from llama_index.core.node_parser import SentenceSplitter


splitter = SentenceSplitter(
    chunk_size=500,
    chunk_overlap=50,
)


nodes = splitter.get_nodes_from_documents(documents)
```

LangChain 和 LlamaIndex 原理一样，用哪个都行。

---

## 8. 中文专门考虑

```python
import re

CN_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""]


splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=CN_SEPARATORS,
    length_function=len,
    is_separator_regex=False,
)
```

粗略估算的话，中文在 cl100k_base 下大约 1 个字对应 1 个 token，chunk_size=500 约等于 500 tokens。英文大约 1 token ≈ 0.7 个词 ≈ 4 个字符，chunk_size=2000 字符约等于 500 tokens。

---

## 9. 高级：按对话单元切

聊天记录、客服历史适合按一问一答切：

```python
def chunk_dialog(messages: list[dict]) -> list[str]:
    """把多轮对话切成"单轮 QA pair" chunks"""
    chunks = []
    for i in range(0, len(messages), 2):
        if i + 1 < len(messages):
            user = messages[i]["content"]
            assistant = messages[i + 1]["content"]
            chunks.append(f"User: {user}\nAssistant: {assistant}")
    return chunks
```

---

## 10. 切完后的 sanity check

```python
def validate_chunks(chunks: list[str]) -> dict:
    sizes = [len(c) for c in chunks]
    return {
        "count": len(chunks),
        "avg_size": sum(sizes) / len(sizes),
        "min_size": min(sizes),
        "max_size": max(sizes),
        "too_short": sum(1 for s in sizes if s < 50),   # 可能错切
        "too_long": sum(1 for s in sizes if s > 1500),  # 可能没切
    }


stats = validate_chunks(my_chunks)
print(stats)
```

`too_short` 或 `too_long` 偏多，就说明策略需要调整。

---

## 11. 选哪个？

```
通用文本（80% 场景）→ Recursive
有明确结构（PDF / Markdown） → Structure-aware（详见 03-structure-aware）
高价值 + 预算够 → Semantic
代码 → 按函数 / 类切
对话 / 工单 → 按对话单元切
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| chunk_size 的单位混用（字符 vs token） | 统一按 token 算（tiktoken / hf tokenizer）|
| 中文沿用英文的默认分隔符 | 加上 ["。", "！", "？", "，"] |
| overlap 设得太大 | 白白多花 embed 成本，chunk_size 的 10-20% 就够 |
| chunk 没带 metadata（doc_id / page） | 加上，方便回溯 |

---

## 13. 下一步

- PDF / Markdown / HTML / 表格怎么切：[文档结构感知切分](./03-structure-aware.md)
- 多粒度切分：[多粒度切分：Small-to-Big / Parent Document](./04-small-to-big.md)
- metadata 设计：[Chunk Metadata 设计](./05-metadata.md)
