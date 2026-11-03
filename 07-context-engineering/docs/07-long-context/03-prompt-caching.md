# CE 07-03：Prompt Caching 与上下文复用

> 长上下文里最大的浪费，是每次调用都把同一段稳定前缀重新 prefill 一遍。Prompt Caching 把这段前缀的 KV 存下来，命中时跳过重算，读取成本能降到原价的一折甚至更低，延迟也大幅下降。用好它只需要记住一条：稳定的内容放前面，常变的放后面。

---

## 1. 原理：缓存的是 KV，不是文本

模型生成之前要先 prefill 整个输入，逐个 token 算出 Key / Value 向量（KV），存进 KV cache，后面的注意力计算都依赖它。长输入又慢又贵，根源就在这一步。

只要前缀的 token 完全相同（位置也相同），它们的 KV 就完全相同。既然如此，没必要每次重算：把这段前缀的 KV 存起来，下次遇到同样的前缀直接复用，prefill 只需要算新增的后半段。

```
第一次调用：[████████ 稳定前缀（全量 prefill，写入缓存）████████][新内容]
第二次调用：[████████ 稳定前缀（命中缓存，跳过 prefill）████████][新内容']
                     ↑ 这一大段不重算
```

所以 caching 做的是前缀复用，有两个硬性前提：

1. 前缀必须逐 token 完全一致，改一个字，从这个字往后的缓存全部失效。
2. 缓存只认前缀，只能从开头连续命中，中间插进一段就断了。

由此直接得出组织原则：稳定内容放前面，易变内容放后面。

---

## 2. 各家的缓存：规则趋同，细节各异

| 维度 | Anthropic | OpenAI（GPT-5.6 及之后） | Gemini | 千问（百炼） |
|------|-----------|-------------------------|--------|--------------|
| 触发方式 | 显式 `cache_control` 断点；也可在请求顶层放一个 `cache_control`，断点随对话增长自动后移（automatic caching） | 默认自动，无需标记 | 隐式缓存自动生效；显式 context caching 要自己创建缓存对象、按小时付存储费 | 显式（`cache_control: ephemeral`）和隐式（自动、不可关）二选一 |
| 最小长度 | Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5 为 512 token；Sonnet 4.6 等 1,024；Opus 4.5/4.6、Haiku 4.5 为 4,096；不足时不缓存也不报错 | 1,024 visible input token | 以官方定价页/文档为准 | 1,024 token |
| 断点数量 | 每请求最多 4 个显式断点；向前最多回看 20 个 block 找缓存 | 无需指定 | — | 显式缓存最多 4 个标记，向前回溯最多 20 个 content 块 |
| TTL | 默认 5 分钟（命中会续期），可选 `"ttl": "1h"` | `prompt_cache_options.ttl` 设的是最短保留时长：默认 `"30m"`（至少 30 分钟），可选 `"12h"`（至少 12 小时，仅 Responses API） | 自己设置，按存储时长计费 | 显式 5 分钟（命中续期）；隐式无固定时长 |
| 写入价 | 5 分钟档 1.25×，1 小时档 2× 基础输入价 | 1.25×（30 分钟）/ 2.5×（12 小时） | 缓存 token 价 + 每小时存储费 | 以百炼定价页为准 |
| 读取价 | 默认 0.1×；Opus 5.5 / Sonnet 5.5 为 0.05×；Fable 5.1 为 0.025× | 多数模型 0.1×；GPT-6.1 Sol / Terra 为 0.05× | 以官方定价页为准 | 以百炼定价页为准，显式、隐式和各模型不同 |
| 你要做的事 | 摆好顺序 + 打断点（或用顶层自动缓存） | 摆好顺序；可选 `prompt_cache_key` 区分用户 | 创建缓存并在请求里引用 | 摆好顺序，选显式或隐式 |

看这张表要留意几点：

- 新一代有个变化：以前「OpenAI 自动缓存、不收写入费」的说法已经过时，GPT-5.6 起写入也有溢价（1.25× / 2.5×），读取最低到 0.05×。几家的模式越来越像，写入贵一点，读取极便宜，都适合写一次、读很多次的用法。
- 缓存只对前缀生效，所以不管用哪家，要做的事情都一样：保证稳定前缀逐字一致，放在最前面。
- Anthropic 的读取价在最新一代上更低（Opus 5.5 / Sonnet 5.5 是 5%，Fable 5.1 是 2.5%），各代不一样，估成本时按所用模型查表。

---

## 3. 怎么组织上下文最大化命中

各家的原则都一样，按变化频率从低到高排。Anthropic 拼前缀的顺序是 `tools → system → messages`，工具定义排在 system 前面：

```
[ 工具定义 schema ]      ← 稳定（tools 参数）
[ system 指令 ]         ← 最稳定，几乎不变
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

显式加 `cache_control`，把缓存断点放在稳定前缀的末尾：

```python
# pip install anthropic
import anthropic

client = anthropic.Anthropic()

LONG_DOC = open("product_manual.txt").read()   # 一份 80K token 的稳定手册

def ask(question: str):
    return client.messages.create(
        model="claude-opus-5-5",
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

# 5 分钟内再问：命中缓存（80K 前缀按缓存读价计费，Opus 5.5 是 0.05×；prefill 只算新问题）
r2 = ask("如何导出数据？")
print(r2.usage)  # cache_read_input_tokens 较大、input_tokens 很小
```

是否命中，看 `usage` 里的三个字段：

- `cache_creation_input_tokens`：写入缓存的 token 数，第一次调用时较大。
- `cache_read_input_tokens`：从缓存读取的 token 数，后续调用时较大，这部分计价极低。
- `input_tokens`：没有缓存的新输入，应该很小。

想让缓存保留更久，可以用 1 小时档：

```python
"cache_control": {"type": "ephemeral", "ttl": "1h"},   # 写入 2×，但能命中 1 小时
```

多轮对话里不想自己管理断点，可以用顶层的 automatic caching。下面是官方文档的示例：

```python
client.messages.create(
    model="claude-opus-5-5",
    max_tokens=1024,
    cache_control={"type": "ephemeral"},   # 放在请求顶层，断点随对话增长自动移到最后一个可缓存 block
    system="You are a helpful assistant.",
    messages=messages,
)
```

另外两点：混用 TTL 时，1 小时的条目必须排在 5 分钟条目前面；顶层 `cache_control` 会占掉 4 个断点名额中的一个。

---

## 5. 省钱省延迟的量级

举个例子：一段 80K token 的稳定前缀，5 分钟内问 10 次，按 Claude Opus 5.5 的官方标价算（基础输入 $4 / 1M，5 分钟写入 $5，读取 $0.20；价格会变，以官方实时定价为准）：

| 方案 | 前缀部分的输入成本 | 首 token 延迟 |
|------|--------------------|--------------|
| 不缓存，每次全量 prefill | 80K × 10 = 800K × $4/1M = $3.20 | 每次都慢（全量 prefill） |
| Anthropic 缓存 | 第 1 次 80K × $5/1M = $0.40 + 后 9 次 80K × 9 × $0.20/1M = $0.144 ≈ $0.54 | 命中后 prefill 大幅缩短 |

前缀部分的输入成本从 $3.20 降到约 $0.54，**省了约 83%**。延迟方面，命中后跳过了 80K 的 prefill，TTFT 通常能降好几倍。调用次数越多、前缀越长，收益越大。

> 注意反例：一段前缀如果只调用一次，开 Anthropic 缓存反而亏钱，写入溢价付了，却没有机会读。缓存适合高频复用同一段前缀的场景。

---

## 6. 常见坑

| 坑 | 说明 |
|----|------|
| 易变内容放前面 | 前缀一变缓存全部失效，长文档白缓存了，稳定内容必须放前面 |
| 前缀没有逐字一致 | 加了时间戳、随机 ID，或改了一个标点，都会命中不了 |
| 缓存断点位置不对 | Anthropic 的断点要放在稳定段的末尾，别放到易变段后面 |
| 只调用一次也开缓存 | 只写不读，多付了写入费 |
| 忽略 TTL | 默认 5 分钟，间隔太久缓存已经过期，等于没缓存 |
| 不看 usage 验证 | 以为命中了，实际 cache_read 是 0，要看字段确认 |
| 前缀低于最小长度 | 低于该模型的最小可缓存长度（Anthropic 当前主力为 512 token，部分型号 1,024～4,096；OpenAI / 千问 1,024）就不缓存，也不报错，要看 usage 确认 |
| 压缩、清理工具结果会破坏缓存 | 改写历史等于改了前缀，下一次必然 miss；Anthropic 的 context editing 提供 `clear_at_least`，就是让你权衡清掉这么多值不值得丢一次缓存 |

---

## 7. 下一步

- 长上下文成本与延迟的全貌：[长上下文模型现状](./01-long-context-models.md)
- 每次都塞不同的大文档时，缓存帮不上忙，这时该用 RAG：[有了长上下文还要不要 RAG](./02-long-context-vs-rag.md)
- 排好顺序之后，怎么进一步对抗 lost-in-the-middle：[长上下文的注意力优化实操](./04-attention-optimization.md)
- 上线后监控缓存命中率：[上下文可观测性](../08-production/01-observability.md)

## 参考资料

- Anthropic Prompt Caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI Prompt Caching: https://developers.openai.com/api/docs/guides/prompt-caching
- Gemini context caching 与定价: https://ai.google.dev/gemini-api/docs/long-context 、 https://ai.google.dev/gemini-api/docs/pricing
- 阿里云百炼 上下文缓存: https://help.aliyun.com/zh/model-studio/context-cache
