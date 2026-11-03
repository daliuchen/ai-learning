# EKB 29：引用溯源，让答案能点回原文

> 在企业知识库里，不带引用的答案没人敢信。员工得能点开引用、核对原文，才敢照着答案去办事。这篇讲怎么把 `cited_doc_ids` 变成带标题、小节和链接的引用卡片，以及怎样把引用精确到段落。

---

## 1. 引用为什么是刚需

| 没有引用 | 有引用 |
|----------|--------|
| 「报销上限 2000 元」，真的吗？ | 「报销上限 2000 元，见《报销制度》3.1 节」，可核对 |
| 答错了无从追溯 | 点开就知道模型有没有读对 |
| 员工不敢照做 | 员工能自己确认 |

有了引用，用户相信的是文档本身，模型只负责帮他找到出处。企业场景的信任就建立在这上面。它也直接影响数据模型：chunk 必须能追溯到所属文档的标题和链接，见 [数据模型设计](../02-design/04-data-model.md)。

---

## 2. 从 doc_id 组装引用卡片

`generate` 返回的 `cited_doc_ids` 只是一串 id，展示信息还得补上。好在检索时已经 join 过 documents，这些信息都带回来了：

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
     "source_url": "https://wiki.internal/hr/travel-reimbursement",
     "sections": ["差旅与报销制度 > 报销流程 > 单次额度"]}
  ],
  "found": true
}
```

前端把它渲染成可点击的引用卡片，见 [引用回链](../09-frontend/02-citation-clickback.md)。

---

## 3. 引用精确到小节

只说「来自《报销制度》」还不够，那篇文档可能很长。最好精确到小节，让用户直接跳到对应位置。

分块时专门存了 `section_path`（见 [语义分块](../05-ingest/03-semantic-chunking.md)），用处就在这里。如果原文链接支持锚点，可以把小节名拼成锚点：

```python
def section_anchor(source_url: str, section_path: str) -> str:
    # 「报销流程 > 单次额度」→ #单次额度
    last = section_path.split(" > ")[-1]
    return f"{source_url}#{last}"
```

精确到小节之后，用户点一下就到了那一段，不用打开长文档自己找，核对成本低很多。

---

## 4. 引用要和答案对得上

有个不太显眼的问题：答案本身没错，引用却挂错了文档，用了 A 的内容却标成 B 的 id。可以从三个层次防范。起步用上一篇的 `sanitize` 做后处理校验，剔除不在检索片段里的引用就够了。对引用准确率要求高时，再用 LLM-as-judge 抽查每条引用是否真的支撑了对应论断，并计入评估（即 [评估指标](../04-eval/03-metrics.md) 里的引用准确率）。更进一步，可以让模型在句子级标注来源，比如「报销上限 2000 元[7]」，溯源会更细。

---

## 5. 引用也是调试和反馈的线索

引用不只是给用户看的，它也能告诉你系统哪里有问题：

- 用户标了「这个引用不对」，记进 `query_logs`，往往能暴露检索或分块的问题
- 答案不错但引用总是指错，通常是 rerank 把对的片段排到了后面
- 引用的小节总是又大又泛，说明 chunk 切得太粗

所以前端的引用卡片最好带一个「引用不准」的反馈按钮，数据回流到 [反馈闭环](../10-production/03-feedback-loop.md)。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 答案不带引用 | 没人敢信 | 用结构化输出强制引用 |
| 引用只到文档级 | 用户要自己翻长文 | 精确到 section |
| 不校验引用和答案是否一致 | 张冠李戴 | 后处理 + judge 抽查 |
| chunk 没存 source_url | 点不回原文 | ingest 时存好链接 |
| 引用错了没有反馈渠道 | 问题发现不了 | 卡片带「引用不准」按钮 |

---

## 下一步

引用解决的是答对了怎么证明，下一篇讲答不出时怎么办：[答不出就承认](./04-say-i-dont-know.md)
