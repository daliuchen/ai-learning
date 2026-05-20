# Prompt Engineering 实战手册

> 一套关于"**一个好 prompt 是怎么产生的**"的中文深度教程。和前三本不同的是——本手册中轴线不是"列各种 prompt 技法"，而是"PE 完整开发流程：需求 → baseline → 评测集 → 迭代闭环 → 生产 → 监控"，把技法、模型差异、应用模式都挂在这条线上。

---

## 一、定位

写这本手册的动机是：网上 prompt engineering 教程已经很多了，但绝大多数都犯同一个错——**把"技法集合"教给读者，没教"开发流程"**。读者学完 CoT、ReAct、Few-Shot、XML 标签，依然不知道：

- 接到新需求第一步做什么？
- v0 prompt 怎么写？
- 评测集从哪来、要多少条？
- 改了一行 prompt，怎么判断是变好了还是变差了？
- 何时停止迭代？
- 反模式长什么样？

**本手册把"流程"放在中轴线（第 02 章），其他章节是流程里的零件。**

---

## 二、目标读者

- 写过几次 prompt 但没建立"评测先于 prompt"习惯的工程师
- 想从"靠感觉调 prompt"升级到"评测驱动迭代"的团队
- 在 LangChain / Pydantic AI / MCP 等框架里写 Agent，发现 prompt 是瓶颈的人
- 想搞清楚 Claude / GPT / Gemini 各家 prompt 写法差异的架构师

不适合：纯小白（建议先看任意一家官方 quickstart 写出第一个 hello-world prompt 再回来）。

---

## 三、目录结构

```
04-prompt-engineering/
├── README.md
├── requirements.txt
├── .env.example
├── docs/
│   ├── 01-foundations/                  # 基础（5 篇）
│   │   ├── 01-overview.md               # PE 是什么 / 不是什么
│   │   ├── 02-anatomy.md                # 一条 prompt 的解剖
│   │   ├── 03-how-models-read.md        # 模型怎么"读" prompt
│   │   ├── 04-sampling.md               # 不确定性的源头
│   │   └── 05-eval-first.md             # 评测先于 prompt
│   ├── 02-process/                      # ★ 中轴线（6 篇）
│   │   ├── 01-lifecycle.md              # PE 完整生命周期
│   │   ├── 02-from-spec-to-v0.md        # 从需求到 prompt v0
│   │   ├── 03-build-evalset.md          # 建评测集：5 → 50 → 500
│   │   ├── 04-iteration-loop.md         # 迭代闭环
│   │   ├── 05-when-to-stop.md           # 何时停止
│   │   └── 06-anti-patterns.md          # 反模式与失败案例
│   ├── 03-techniques/                   # 核心技法（10 篇）
│   │   ├── 01-zero-vs-few-shot.md
│   │   ├── 02-cot.md                    # 思维链
│   │   ├── 03-role-prompting.md         # 角色与边界
│   │   ├── 04-decomposition.md          # 任务拆解
│   │   ├── 05-structured-output.md      # JSON / XML / Schema
│   │   ├── 06-examples-design.md        # 好 few-shot 长什么样
│   │   ├── 07-boundaries-refusal.md     # 边界 / 拒绝
│   │   ├── 08-self-critique.md          # 自我反思
│   │   ├── 09-self-consistency.md       # 采样投票
│   │   └── 10-delimiters.md             # XML / JSON / Markdown 选哪个
│   ├── 04-advanced/                     # 进阶（6 篇）
│   │   ├── 01-react.md
│   │   ├── 02-tool-use.md
│   │   ├── 03-rag-prompting.md
│   │   ├── 04-multimodal.md
│   │   ├── 05-meta-prompting.md         # 用 LLM 写/优化 prompt
│   │   └── 06-injection-defense.md
│   ├── 05-by-task/                      # 按任务组装（5 篇）
│   │   ├── 01-classifier.md
│   │   ├── 02-extractor.md
│   │   ├── 03-generator.md
│   │   ├── 04-summarizer.md
│   │   └── 05-judge.md                  # LLM-as-judge
│   ├── 06-models/                       # 模型差异（4 篇）
│   │   ├── 01-claude.md
│   │   ├── 02-gpt.md
│   │   ├── 03-gemini-open.md
│   │   └── 04-cross-model.md
│   ├── 07-production/                   # 生产化（5 篇）
│   │   ├── 01-versioning.md
│   │   ├── 02-caching.md
│   │   ├── 03-templating.md
│   │   ├── 04-ab-observability.md
│   │   └── 05-team-collab.md
│   └── 08-practice/                     # 实战（3 篇）
│       ├── 01-build-classifier.md       # 从需求到分类器（完整闭环）
│       ├── 02-research-agent.md         # Research Agent prompt 迭代 5 个版本
│       └── 03-claude-code-as-optimizer.md  # 用 Claude Code 当 prompt 优化器
└── demos/
    ├── foundations/
    ├── process/
    ├── techniques/
    ├── advanced/
    ├── by_task/
    ├── models/
    ├── production/
    └── practice/
```

合计 **44 篇**。

---

## 四、学习路径

### 路径 A：建立 PE 工程方法论（推荐，必读）

```
01-foundations/05-eval-first
  → 02-process 全 6 篇
  → 08-practice/01-build-classifier
```

跑完这条线你就有"评测驱动 prompt 迭代"的肌肉记忆。

### 路径 B：补技法

按需挑 03-techniques、04-advanced、05-by-task 的相关篇。

### 路径 C：跨模型工程师

```
06-models 全部 → 03-techniques/05-structured-output → 07-production/01-versioning
```

### 路径 D：生产落地

```
07-production 全部 → 02-process/04-iteration-loop → 08-practice/03-claude-code-as-optimizer
```

---

## 五、和前三本的关系

| 你已读 | 本手册帮你 |
|--------|-----------|
| 01-langchain（LangSmith Hub） | 把 prompt 版本化、评测、Hub 协作的工程化方法补全 |
| 02-pydantic-ai（output_type / Pydantic Evals） | 结构化输出与评测在 Pydantic 生态的最佳实践 |
| 03-mcp（Tool descriptions） | Tool description 是 prompt 的一部分——本手册 04-advanced/02-tool-use 专门讲 |

PE 是这一切的"底层语言"——什么框架都绕不开。

---

## 六、版本与依赖

| 包 | 版本 | 用途 |
|----|------|------|
| `anthropic` | `>=0.30.0` | Claude API |
| `openai` | `>=1.30.0` | GPT API |
| `google-genai` | `>=0.5.0` | Gemini API |
| `pydantic` | `>=2.6.0` | Schema |
| `langsmith` | `>=0.1.0` | Prompt Hub + 评测 |
| `langfuse` | `>=2.0.0` | 可观测（07-production 用） |
| `promptfoo` | (npm) | 跨家批量评测 |

完整依赖见 `requirements.txt`。

---

## 七、写作约定

1. **每篇结构统一**：一句话总结 → 概念 → 最小可用代码（三家都给） → 进阶 → 生产建议 → 常见坑 → demo 入口
2. **三家 API 对照**：核心技法都给 Anthropic / OpenAI / Gemini 等价代码
3. **真实数据集**：实战篇用真实 dataset（中英文混合）
4. **错误示范**：`# ❌ 错误写法` / `# ✅ 正确写法`
5. **官方文档引用**：每篇结尾给 Anthropic / OpenAI / Google 对应章节链接

---

开始 👉 [01-foundations/01-overview.md](docs/01-foundations/01-overview.md)
