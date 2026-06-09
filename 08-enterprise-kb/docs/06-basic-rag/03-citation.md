# EKB 29：引用溯源——让答案能点回原文

> **一句话**：企业知识库里，**没引用的答案没人敢信**。员工要能点开引用、核对原文，才敢拿着答案去操作。本篇讲怎么把 `cited_doc_ids` 变成带标题、小节、链接的引用卡片，以及如何做到「引用精确到段落」。

---

## 1. 为什么引用是企业知识库的刚需

| 没有引用 | 有引用 |
|----------|--------|
| 「报销上限 2000 元」——真的吗？ | 「报销上限 2000 元 📄《报销制度》3.1 节」——可核对 |
| 答错了无从追溯 | 点开就知道模型有没有读对 |
| 员工不敢照做 | 员工能自己确认 |

引用把「相信模型」变成「相信文档，模型只是帮你找到」。这是企业场景信任的基础——也直接影响数据模型设计（chunk 必须能追到 doc 的标题/链接，见 [02-design/04](../02-design/04-data-model.md)）。

---

## 2. 从 doc_id 组装引用卡片

`generate` 返回的 `cited_doc_ids` 只是 id，要补全展示信息。这些信息检索时已经带回来了（join 了 documents）：

```python
def build_citations(answer: Answer, chunks: list[dict]) -> list[dict]:
    by_doc = {}
    for c in chunks:
        if c["doc_id"] in answer.cited_doc_ids:
            by_doc.setdefault(c["doc_id"], {
                "doc_id": c["doc_id"],
                "title": c["title"],
                "source_url": c["source_url"],
                "sections": set(),
            })
            by_doc[c["doc_id"]]["sections"].add(c["section_path"])
    return [
        {**v, "sections": sorted(v["sections"])}
        for v in by_doc.values()
    ]
```

返回给前端的结构：

```json
{
  "text": "差旅报销单次上限 2000 元，需在 15 个工作日内提交。",
  "citations": [
    {"doc_id": 7, "title": "差旅与报销制度",
     "source_url": "https://wiki.internal/hr/travel",
     "sections": ["差旅与报销制度 > 报销流程 > 单次额度"]}
  ],
  "found": true
}
```

前端把它渲染成可点击的引用卡片（详见 [09-frontend/02-citation-clickback](../09-frontend/02-citation-clickback.md)）。

---

## 3. 引用精确到「段落」而非「文档」

只说「来自《报销制度》」还不够好——那篇文档可能很长。最好精确到**小节**，让用户直接跳到对应位置。

这就是为什么分块时存了 `section_path`（[05-ingest/03](../05-ingest/03-semantic-chunking.md)）。如果原文链接支持锚点，可以把 section 拼成锚点：

```python
def section_anchor(source_url: str, section_path: str) -> str:
    # 「报销流程 > 单次额度」→ #单次额度
    last = section_path.split(" > ")[-1]
    return f"{source_url}#{last}"
```

精确引用大幅降低用户核对成本——点一下直接到那一段，而不是打开长文档自己找。

---

## 4. 引用和答案要对得上（防张冠李戴）

一个微妙的问题：模型可能答案对、但引用挂错文档（用 A 的内容却标了 B 的 id）。防范手段：

- **后处理校验**（上一篇的 `sanitize`）：剔除不在检索片段里的引用
- **更严的做法**：用 LLM-as-judge 抽查「每条引用是否真支撑了对应论断」，纳入评估（[04-eval/03](../04-eval/03-metrics.md) 的引用准确率）
- **答案内联标注**（进阶）：让模型在句子级标注来源，如「报销上限 2000 元[7]」，溯源更细

起步用后处理校验够了，对引用准确率要求高时再上 judge。

---

## 5. 引用也是调试和反馈的抓手

引用不只给用户看，也是你优化系统的信号：

- 用户标「这个引用不对」→ 进 `query_logs` → 暴露检索或分块问题
- 答案好但引用总指错 → rerank 把对的片段排后了
- 引用的 section 总是大而泛 → chunk 切得太粗

所以前端的引用卡片最好带个「引用不准」反馈按钮，回流到 [10-production/03-feedback-loop](../10-production/03-feedback-loop.md)。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 答案不带引用 | 没人敢信 | 强制引用（结构化输出） |
| 引用只到文档级 | 用户要自己翻长文 | 精确到 section |
| 不校验引用-答案一致 | 张冠李戴 | 后处理 + judge 抽查 |
| chunk 没存 source_url | 点不回原文 | ingest 时存好链接 |
| 引用错误无反馈渠道 | 问题发现不了 | 卡片带「引用不准」按钮 |

---

## 下一步

引用解决了「答得对怎么证明」，下一篇解决「答不出怎么办」：

→ [04-say-i-dont-know](./04-say-i-dont-know.md)
