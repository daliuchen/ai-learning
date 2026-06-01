# 🧩 Context Engineering 上下文工程

> 把"喂给模型的整个上下文"当作工程对象来管理——指令之外、窗口之内的一切。

Prompt Engineering 教你**怎么写指令**，Embedding 教你**怎么检索**。但真正决定一个 LLM 应用上限的，往往是**模型在某一次推理时，窗口里到底装了什么、按什么顺序、占了多少预算**——这就是 Context Engineering。

本手册把上下文拆成可独立优化的环节：组成 → 检索注入 → 记忆 → 压缩 → Agent 上下文 → 长上下文 → 生产化，每个环节讲清 trade-off 和工业默认值。

---

## 章节

| 章节 | 主题 | 篇数 |
|------|------|------|
| 01-foundations | 基础：什么是上下文工程 / 窗口本质 / Context Rot / 成本预算 | 6 |
| 02-anatomy | 上下文的组成：指令 / 历史 / 检索 / 工具 / few-shot / 组织结构 | 6 |
| 03-retrieval | 检索与注入：静态 vs 动态 / JIT / 排序裁剪 / 归因 | 5 |
| 04-memory | 记忆系统：短期 vs 长期 / 存储召回 / Agent 记忆架构 / 遗忘 | 6 |
| 05-compaction | 压缩与裁剪：摘要 / 滑窗 / 剪枝 / 触发时机 | 5 |
| 06-agent-context | Agent 上下文：累积 / 工具结果 / 多 Agent 传递 / 隔离 / 状态 | 6 |
| 07-long-context | 长上下文：1M token / vs RAG / 缓存复用 / 注意力 | 4 |
| 08-production | 生产化：可观测 / 成本 / 排障 / 评测 / 安全 | 5 |
| 09-practice | 实战：带记忆客服 / 长文档问答 / 多 Agent 编排 | 3 |

合计 **42 篇**。

---

## 写作约定

与本集合其它手册一致：一句话总结 → 概念 → 最小代码 → 进阶 → 生产建议 → 常见坑（表格）→ 下一步。代码可独立运行，错误示范用 `# ❌ / # ✅` 标注。

## 前置依赖

```bash
pip install -r requirements.txt
```

需要 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`（按 demo）。
