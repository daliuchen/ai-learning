# EKB 44：引用回链，让答案可核对

> 员工信不信知识库的答案，最终要落到引用卡片上。卡片要写明出自哪篇文档的哪一节，点一下能跳到原文对应的位置，还要留一个「引用不准」的反馈入口。这篇做引用卡片组件，并把它和答案里的标注对应起来。

---

## 1. 引用卡片包含什么

后端 `build_citations`（见 [引用溯源](../06-basic-rag/03-citation.md)）返回的每条引用，渲染成一张卡片：

```
┌────────────────────────────────────────┐
│ 📄 差旅与报销制度                    [↗] │
│    报销流程 > 单次额度                    │
│    「单次上限 2000 元，15 个工作日内…」    │
│                          👍  👎 引用不准  │
└────────────────────────────────────────┘
```

卡片上有文档标题、section 路径、命中片段的预览、跳转按钮和反馈按钮。

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

## 2. 跳到原文的对应位置

只链到文档首页的话，用户还得自己往下翻。用 `section_path` 拼出锚点，就能直接跳到那一节：

```
source_url = https://wiki.internal/hr/travel-reimbursement
section    = 报销流程 > 单次额度
→ 链接 = https://wiki.internal/hr/travel-reimbursement#单次额度
```

前提是原文所在的系统支持标题锚点，大多数 wiki 和文档系统都支持。这样核对一条引用，就从打开长文找半天变成了点一下。

---

## 3. 答案内联标注（进阶）

再细一点，可以在答案的句子后面标上来源序号，点击后高亮对应的卡片：

```
差旅报销单次上限 2000 元 [1]，需在 15 个工作日内提交 [1]。
                        └─ 点击 [1] 高亮下方第 1 张引用卡片
```

实现时要让模型在结构化输出里返回句子级的引用映射，即哪句话对应哪篇文档。起步阶段可以不做，整体的引用列表已经够用，对溯源精度要求高时再加。

---

## 4. 「引用不准」反馈

每张卡片都带一个反馈按钮，用户标记「这条引用不对」时，把信号写回日志：

```tsx
async function reportBadCitation(queryId: number, docId: number) {
  await fetch('/api/feedback', {
    method: 'POST',
    body: JSON.stringify({ query_id: queryId, doc_id: docId, kind: 'bad_citation' }),
  })
}
```

后端把它记进 `query_logs`，或者单独的反馈表。这些信号对优化检索和分块非常有用（[引用溯源](../06-basic-rag/03-citation.md) 那篇提到过）：老被标错的引用，往往说明 rerank 排序或 chunk 切分有问题。详见 [反馈闭环](../10-production/03-feedback-loop.md)。

---

## 5. 兜底时的引用区

`found=false`（答不出）时，引用区不能空着让人摸不着头脑，要给出明确的提示：

```tsx
{!found && (
  <div className="no-citation">
    未找到可引用的文档。建议联系对应部门，或换个说法再问。
  </div>
)}
```

「没有引用」和「引用加载中」要区分开，前者是确定的兜底状态，后者只是临时状态。状态清楚，用户才不会以为系统出错了。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 引用只链到文档首页 | 用户还得自己翻 | 用锚点跳到小节 |
| 卡片不显示 section | 溯源不够精确 | 显示 section_path |
| 没有反馈入口 | 发现不了引用问题 | 加「引用不准」按钮 |
| 兜底时引用区一片空白 | 用户以为出错了 | 给出明确的兜底提示 |
| 引用没有按权限过滤 | 泄漏受限文档的标题 | 构造引用时也走权限过滤（见[验证越权内容不会被召回](../08-permission/05-no-leak-verify.md)） |

---

## 下一步

问答端做完了，接下来做文档管理后台，包括可见性设置：

→ [文档管理后台](./03-doc-admin.md)
