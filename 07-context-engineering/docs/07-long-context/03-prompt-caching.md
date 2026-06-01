# CE 07-03：Prompt Caching 与上下文复用

> **一句话**：长上下文最大的浪费，是每次调用都把同一份稳定前缀重新 prefill 一遍。Prompt Caching 把这段前缀的 KV 缓存住，命中后跳过重算——读取成本能打到一两折、延迟大降。用好它的关键就一句：**稳定的放前面、易变的放后面**。

---

## 1. 原理：缓存的是 KV，不是文本

模型生成前要先 prefill 整个输入——逐 token 算出每个 token 的 **Key / Value 向量（KV）**，存进 KV cache，后续注意力都靠它。这一步是长输入又慢又贵的根源。

关键洞察：**只要前缀的 token 完全一样，它们的 KV 就完全一样**（位置也一样的前提下）。那何必每次重算？把这段前缀的 KV 存起来，下次同样前缀直接复用，prefill 只需算「新增的后半段」。

```
第一次调用：[████████ 稳定前缀（全量 prefill，写入缓存）████████][新内容]
第二次调用：[████████ 稳定前缀（命中缓存，跳过 prefill）████████][新内容']
                     ↑ 这一大段不重算
```

所以 caching 的本质是 **前缀复用**。有两个硬性前提：

1. **前缀必须逐 token 完全一致**——改一个字，从那个字往后的缓存全失效。
2. **缓存是「前缀」缓存**——只能从开头连续命中，中间插一段就断了。

这直接推出了组织原则：**稳定内容前置，易变内容后置。**

---

## 2. Anthropic vs OpenAI：两种缓存

两家都做了 prompt caching，但机制和你要做的事不一样。

| 维度 | Anthropic（显式） | OpenAI（自动） |
|------|------------------|----------------|
| 触发方式 | **手动**打 `cache_control` 标记 | **自动**，无需标记 |
| 控制粒度 | 你指定缓存断点（最多 4 个） | 系统自动按前缀缓存 |
| 最小长度 | 一般需 ≥1024 token（小模型 2048） | 一般 ≥1024 token 才自动缓存 |
| TTL | 默认 5 分钟（可选 1 小时档） | 几分钟到约 1 小时，系统管理 |
| 写入成本 | **比普通输入贵**（5 分钟档约 1.25×，1 小时档约 2×） | 不额外收写入费 |
| 读取成本 | **极便宜**（约 0.1× 普通输入） | 命中部分打折（约 0.5×） |
| 你要做的事 | 摆好顺序 + 打 cache_control | 摆好顺序就行 |

要点：

- **Anthropic 写入更贵、读取极便宜**。所以它适合「写一次、读很多次」——前缀要在 5 分钟内被多次命中才划算；只调一次反而亏（多付了写入费）。
- **OpenAI 全自动**，你唯一能做的就是**保证稳定前缀逐字一致地放最前面**，让它自动命中。

---

## 3. 怎么组织上下文最大化命中

不管哪家，原则一致——**按「变化频率」从低到高排列**：

```
[ system 指令 ]         ← 最稳定，几乎不变
[ 工具定义 schema ]      ← 稳定
[ 长文档 / 知识库 ]      ← 稳定（同一份反复问）
[ few-shot 示例 ]        ← 稳定
─────── 缓存断点划在这里 ───────
[ 对话历史 ]             ← 每轮变
[ 本轮用户问题 ]         ← 每次变
```

```python
# ❌ 易变内容混在前面，前缀每次都变，缓存永远命中不了
messages = [{
    "role": "user",
    "content": f"问题：{user_question}\n\n参考文档：\n{huge_doc}",
    # user_question 在最前 → 一变问题，整个前缀失效，huge_doc 白缓存
}]

# ✅ 稳定的长文档前置，易变的问题后置
messages = [{
    "role": "user",
    "content": f"参考文档：\n{huge_doc}\n\n问题：{user_question}",
}]
```

---

## 4. Anthropic 缓存代码示例

显式打 `cache_control`，把缓存断点划在「稳定前缀」的末尾：

```python
# pip install anthropic
import anthropic

client = anthropic.Anthropic()

LONG_DOC = open("product_manual.txt").read()   # 一份 80K token 的稳定手册

def ask(question: str):
    return client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=1024,
        # system 是数组，最后一个 block 打 cache_control → 缓存到这里为止的全部前缀
        system=[
            {"type": "text", "text": "你是产品支持助手，只依据手册作答。"},
            {
                "type": "text",
                "text": LONG_DOC,
                "cache_control": {"type": "ephemeral"},   # 5 分钟 TTL，缓存断点
            },
        ],
        # 易变的问题放 messages，在缓存断点之后
        messages=[{"role": "user", "content": question}],
    )

# 第一次：写入缓存（付写入费，prefill 全量 80K）
r1 = ask("怎么重置密码？")
print(r1.usage)  # cache_creation_input_tokens 较大

# 5 分钟内再问：命中缓存（80K 前缀几乎免费，prefill 只算新问题）
r2 = ask("如何导出数据？")
print(r2.usage)  # cache_read_input_tokens 较大、input_tokens 很小
```

看 `usage` 字段验证命中：

- `cache_creation_input_tokens`：写入缓存的 token 数（第一次大）。
- `cache_read_input_tokens`：命中读取的 token 数（后续大，且这部分计价极低）。
- `input_tokens`：未缓存的新输入（应该很小）。

想延长复用窗口可用 1 小时档：

```python
"cache_control": {"type": "ephemeral", "ttl": "1h"},   # 写入更贵，但能命中 1 小时
```

---

## 5. 省钱省延迟的量级

以一份 80K token 稳定前缀、5 分钟内问 10 次为例（数量级估算，按官方实时定价为准）：

| 方案 | 输入 token 成本（相对） | 首 token 延迟 |
|------|------------------------|--------------|
| 不缓存，每次全量 prefill | 80K × 10 = 800K，全价 | 每次都慢（全量 prefill） |
| Anthropic 缓存 | 第 1 次 80K×1.25 写入 + 后 9 次 80K×0.1 读取 ≈ 172K 等效 | 命中后 prefill 大幅缩短 |

输入成本从 ~800K 等效降到 ~172K，**省约 75%~90%**；延迟上，命中后跳过了 80K 的 prefill，TTFT 通常能降几倍。次数越多、前缀越长，收益越大。

> ⚠️ 反例：如果一份前缀**只调一次**，Anthropic 缓存反而亏——你付了写入溢价却没机会读。缓存适合「高频复用同一前缀」。

---

## 6. 常见坑

| 坑 | 说明 |
|----|------|
| 易变内容放前面 | 前缀一变缓存全失效，长文档白缓存——稳定的必须前置 |
| 前缀没逐字一致 | 加了时间戳 / 随机 ID / 改了一个标点，命中不了 |
| 缓存断点划错位置 | Anthropic 要把断点划在「稳定段末尾」，别划到易变段后 |
| 单次调用也开 Anthropic 缓存 | 只写不读，多付写入费，得不偿失 |
| 忽略 TTL | 默认 5 分钟，间隔太久缓存已过期，等于没缓存 |
| 不看 usage 验证 | 自以为命中了，实际 cache_read 为 0，要看字段确认 |
| 前缀低于最小长度 | 不足 ~1024 token 不缓存，小前缀别指望命中 |

---

## 7. 下一步

- 📖 长上下文成本与延迟的全貌 → [01-long-context-models.md](./01-long-context-models.md)
- 📖 缓存救不了「每次塞不同大文档」，这时该用 RAG → [02-long-context-vs-rag.md](./02-long-context-vs-rag.md)
- 📖 排好顺序后怎么进一步对抗 lost-in-middle → [04-attention-optimization.md](./04-attention-optimization.md)
- 📖 上线后监控缓存命中率 → [../08-production/01-observability.md](../08-production/01-observability.md)

## 参考资料

- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI Prompt Caching: https://platform.openai.com/docs/guides/prompt-caching
