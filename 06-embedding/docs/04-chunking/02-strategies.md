# 固定 vs 语义 vs 递归切分

> **一句话**：通用文本用 **RecursiveCharacterTextSplitter**（递归按段落→句子→词级别切），重要场景 + 高 budget 上 **semantic chunker**（按语义边界），简单兜底用 **fixed char chunker**。

---

## 1. 三大主流策略

| 策略 | 怎么切 | 优 | 劣 |
|------|--------|----|----|
| **Fixed (char / token)** | 固定字数硬切 | 简单 | 切断语义 |
| **Recursive** | 按"段落→句子→词" 多层递归 | 通用、保留结构 | 不感知"语义" |
| **Semantic** | embed 每句，相似度低处切 | 语义边界自然 | 慢、贵 |

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

**问题**：

- 切到词中间 ("订阅" 切成 "订" 和 "阅")
- 切到句中间，语义破碎

只适合：兜底 / 极简场景。

---

## 3. Recursive Character Text Splitter（LangChain 默认）

按优先级递归切：先尝试大粒度分隔符（段落），切到上限再用小粒度（句子 → 词）：

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter


splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""],   # 从大到小
)


chunks = splitter.split_text(text)
```

工作原理：

```
1. 先用 "\n\n" 切 → 看每段大小
2. 太大 → 用 "\n" 再切
3. 还太大 → "。" 再切
4. 直到都 ≤ chunk_size
```

**默认分隔符（英文）**：`["\n\n", "\n", " ", ""]`

**中文加这些**：`["。", "！", "？", "，"]`

---

## 4. Token-aware Recursive

按 token 数而不是字符数（更精确控 LLM context）：

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.text_splitter import TokenTextSplitter
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

按 embedding 相似度找"语义边界"：

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
    breakpoint_threshold_amount=95,            # 95 分位的相似度变化处切
)


chunks = splitter.split_text(text)
```

**优**：切在自然语义边界。

**劣**：

- 慢（每句都要 embed）
- 贵（embed cost 翻倍）
- 阈值难调

实战：高价值数据（法律 / 医疗）值得用，普通业务用 recursive 就行。

---

## 6. 各方法对比 demo

```python
# demos/chunking/02_strategies.py
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

通常 Recursive 切出来的 chunk 是完整段落 / 句子，Fixed 切出来一坨碎片。

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

LangChain 和 LlamaIndex 都行，原理一样。

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

中文一字一 token 估算（粗略）：

- 1 char ≈ 1 token（cl100k_base）
- chunk_size=500 → ~500 tokens

英文：

- 1 token ≈ 0.7 word ≈ 4 chars
- chunk_size=2000 chars ≈ 500 tokens

---

## 9. 高级：按"对话单元"切

聊天 / 客服历史：

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

如果 `too_short` 或 `too_long` 多 → 调整策略。

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
| chunk_size 单位混用（char vs token） | 用 token 计算（tiktoken / hf tokenizer）|
| 中文用英文 default separator | 加上 ["。", "！", "？", "，"] |
| overlap 太大 | 浪费 embed cost，10-20% chunk_size 即可 |
| chunk 没 metadata（doc_id / page） | 加上，方便回溯 |

---

## 13. 下一步

- 📖 结构感知切分（PDF / MD / HTML / table） → [03-structure-aware.md](./03-structure-aware.md)
- 📖 多粒度（small-to-big） → [04-small-to-big.md](./04-small-to-big.md)
- 📖 metadata 设计 → [05-metadata.md](./05-metadata.md)
