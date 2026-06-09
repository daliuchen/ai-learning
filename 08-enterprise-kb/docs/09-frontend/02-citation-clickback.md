# EKB 44：引用回链——让答案可核对

> **一句话**：引用卡片是企业知识库信任的落点。它要显示「来自哪篇文档、哪一节」，点击能跳到原文对应位置，还要带一个「引用不准」的反馈入口。本篇做引用卡片组件，并把它和答案中的标注关联起来。

---

## 1. 引用卡片要素

后端 `build_citations`（[29 篇](../06-basic-rag/03-citation.md)）返回的每条引用，渲染成一张卡片：

```
┌────────────────────────────────────────┐
│ 📄 差旅与报销制度                    [↗] │
│    报销流程 > 单次额度                    │
│    「单次上限 2000 元，15 个工作日内…」    │
│                          👍  👎 引用不准  │
└────────────────────────────────────────┘
```

要素：文档标题、section 路径、命中片段预览、跳转按钮、反馈按钮。

```tsx
// app/components/CitationList.tsx
export function CitationList({ items }: { items: Citation[] }) {
  if (!items.length) return null
  return (
    <div className="citations">
      <h4>引用来源（{items.length}）</h4>
      {items.map((c) => (
        <a key={c.doc_id} href={sectionAnchor(c)} target="_blank"
           className="citation-card">
          <span className="title">📄 {c.title}</span>
          <span className="section">{c.sections.join(' / ')}</span>
        </a>
      ))}
    </div>
  )
}

function sectionAnchor(c: Citation) {
  const last = c.sections[0]?.split(' > ').pop() ?? ''
  return `${c.source_url}#${encodeURIComponent(last)}`   // 跳到对应小节锚点
}
```

---

## 2. 跳到原文「对应位置」，不只是文档

只链到文档首页，用户还要自己翻。利用 `section_path` 拼锚点，**直接跳到那一节**：

```
source_url = https://wiki.internal/hr/travel
section    = 报销流程 > 单次额度
→ 链接 = https://wiki.internal/hr/travel#单次额度
```

前提是原文系统支持标题锚点（多数 wiki/文档系统都支持）。这把「核对成本」从「打开长文找半天」降到「点一下就到」——引用的价值在于**能被快速核对**，跳得越准越有用。

---

## 3. 答案内联标注（进阶）

更细的体验：在答案句子后标注来源序号，点击高亮对应卡片：

```
差旅报销单次上限 2000 元 [1]，需在 15 个工作日内提交 [1]。
                        └─ 点击 [1] 高亮下方第 1 张引用卡片
```

实现要让模型在结构化输出里返回**句子级引用映射**（哪句话对应哪个 doc）。起步可以不做这个，整体引用列表已经够用；对溯源精度要求高时再上。

---

## 4. 「引用不准」反馈：回流的入口

每张卡片带反馈按钮，用户标记「这个引用不对」时，回流到日志：

```tsx
async function reportBadCitation(queryId: number, docId: number) {
  await fetch('/api/feedback', {
    method: 'POST',
    body: JSON.stringify({ query_id: queryId, doc_id: docId, kind: 'bad_citation' }),
  })
}
```

后端把它记进 `query_logs`（或单独的反馈表）。这些「引用不准」信号是优化检索/分块的金矿（[30 篇](../06-basic-rag/03-citation.md) 提过）——总被标错的引用，往往暴露 rerank 排序或 chunk 切分的问题。详见 [10-production/03-feedback-loop](../10-production/03-feedback-loop.md)。

---

## 5. 兜底时的引用区

当 `found=false`（答不出），引用区不能空着让人困惑，要给明确提示：

```tsx
{!found && (
  <div className="no-citation">
    未找到可引用的文档。建议联系对应部门，或换个说法再问。
  </div>
)}
```

把「没引用」和「正在加载引用」区分开——前者是确定的兜底状态，后者是临时态。状态明确，用户才不会误以为系统出错了。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 引用只链到文档首页 | 用户还要自己翻 | 锚点跳到小节 |
| 引用卡片不显示 section | 溯源不精确 | 显示 section_path |
| 没有反馈入口 | 引用问题发现不了 | 加「引用不准」按钮 |
| 兜底时引用区空白 | 用户以为出错 | 明确兜底提示 |
| 引用未按权限过滤 | 泄漏受限文档标题 | 引用构造也走权限（42篇） |

---

## 下一步

问答端做完，做文档管理后台（含可见性设置）：

→ [03-doc-admin](./03-doc-admin.md)
