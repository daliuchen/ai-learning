# Prompt Engineering 实战手册

> 这本手册讲一个好 prompt 是怎么做出来的。前三本按框架组织，这本按开发流程组织：需求、baseline、评测集、迭代、上线、监控。各种技法、模型差异和应用模式都放在这条流程的某个环节里讲。

---

## 一、定位

网上的 prompt engineering 教程已经很多，大多数在讲技法：CoT、ReAct、Few-Shot、XML 标签。学完这些，很多人还是答不上下面几个问题：

- 接到新需求第一步做什么？
- v0 prompt 怎么写？
- 评测集从哪来、要多少条？
- 改了一行 prompt，怎么判断是变好了还是变差了？
- 何时停止迭代？
- 反模式长什么样？

所以这本手册以第 02 章的流程为主线，其他章节都是流程里用到的零件。

---

## 二、目标读者

- 写过一些 prompt，但还没养成先建评测再改 prompt 习惯的工程师
- 想从凭感觉调 prompt 转到用评测驱动迭代的团队
- 在 LangChain / Pydantic AI / MCP 等框架里写 Agent，发现 prompt 是瓶颈的人
- 想搞清楚 Claude / GPT / Gemini 各家 prompt 写法差异的架构师

如果你还没调用过任何一家的 API，建议先照官方 quickstart 跑通第一个请求再来读。

---

## 三、目录结构

```
04-prompt-engineering/
├── README.md
├── requirements.txt
├── .env.example
└── docs/
    ├── 01-foundations/                  # 基础（5 篇）
    │   ├── 01-overview.md               # PE 是什么 / 不是什么
    │   ├── 02-anatomy.md                # 一条 prompt 的解剖
    │   ├── 03-how-models-read.md        # 模型怎么"读" prompt
    │   ├── 04-sampling.md               # 不确定性的源头
    │   └── 05-eval-first.md             # 评测先于 prompt
    ├── 02-process/                      # 流程（6 篇）
    │   ├── 01-lifecycle.md              # PE 完整生命周期
    │   ├── 02-from-spec-to-v0.md        # 从需求到 prompt v0
    │   ├── 03-build-evalset.md          # 建评测集：5 → 50 → 500
    │   ├── 04-iteration-loop.md         # 迭代闭环
    │   ├── 05-when-to-stop.md           # 何时停止
    │   └── 06-anti-patterns.md          # 反模式与失败案例
    ├── 03-techniques/                   # 核心技法（10 篇）
    │   ├── 01-zero-vs-few-shot.md
    │   ├── 02-cot.md                    # 思维链
    │   ├── 03-role-prompting.md         # 角色与边界
    │   ├── 04-decomposition.md          # 任务拆解
    │   ├── 05-structured-output.md      # JSON / XML / Schema
    │   ├── 06-examples-design.md        # 好 few-shot 长什么样
    │   ├── 07-boundaries-refusal.md     # 边界 / 拒绝
    │   ├── 08-self-critique.md          # 自我反思
    │   ├── 09-self-consistency.md       # 采样投票
    │   └── 10-delimiters.md             # XML / JSON / Markdown 选哪个
    ├── 04-advanced/                     # 进阶（6 篇）
    │   ├── 01-react.md
    │   ├── 02-tool-use.md
    │   ├── 03-rag-prompting.md
    │   ├── 04-multimodal.md
    │   ├── 05-meta-prompting.md         # 用 LLM 写/优化 prompt
    │   └── 06-injection-defense.md
    ├── 05-by-task/                      # 按任务组装（5 篇）
    │   ├── 01-classifier.md
    │   ├── 02-extractor.md
    │   ├── 03-generator.md
    │   ├── 04-summarizer.md
    │   └── 05-judge.md                  # LLM-as-judge
    ├── 06-models/                       # 模型差异（4 篇）
    │   ├── 01-claude.md
    │   ├── 02-gpt.md
    │   ├── 03-gemini-open.md
    │   └── 04-cross-model.md
    ├── 07-production/                   # 生产化（5 篇）
    │   ├── 01-versioning.md
    │   ├── 02-caching.md
    │   ├── 03-templating.md
    │   ├── 04-ab-observability.md
    │   └── 05-team-collab.md
    └── 08-practice/                     # 实战（3 篇）
        ├── 01-build-classifier.md       # 从需求到分类器（完整闭环）
        ├── 02-research-agent.md         # Research Agent prompt 迭代 5 个版本
        └── 03-claude-code-as-optimizer.md  # 用 Claude Code 当 prompt 优化器
```

示例代码都在正文里，可以直接复制运行。

合计 44 篇。

---

## 四、学习路径

### 路径 A：建立工程方法（推荐先读）

```
01-foundations/05-eval-first
  → 02-process 全 6 篇
  → 08-practice/01-build-classifier
```

读完这条线，你会有一套从需求到上线、用评测驱动迭代的完整做法。

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
| 03-mcp（Tool descriptions） | Tool description 也是 prompt，[PE Advanced 02：Tool Use Prompting](./docs/04-advanced/02-tool-use.md) 专门讲 |

不管用哪个框架，最后都要落到 prompt 上。

---

## 六、版本与依赖

以下 API 与模型名以 2026 年 11 月的官方文档为准。

| 包 | 版本 | 用途 |
|----|------|------|
| `anthropic` | `>=2.0`（写作时 2.3） | Claude API（Claude 5 家族：Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5） |
| `openai` | `>=3.0`（写作时 3.42） | GPT API（GPT-6 家族；推荐 Responses API）+ Qwen 等 OpenAI 兼容接口 |
| `google-genai` | `>=2.0`（写作时 2.34） | Gemini API（Gemini 4 Argon / 3.8 Flash / 3.5 Flash-Lite） |
| `pydantic` | `>=2.6.0` | Schema |
| `langsmith` | `>=0.14` | Prompt Hub + 评测 |
| `langfuse` | `>=4`（写作时 5.1；旧的 `langfuse.decorators` 已移除，用 `from langfuse import observe`） | 可观测（07-production 用） |
| `logfire` | `>=5`（写作时 5.4） | 可观测，Pydantic 出品 |
| `promptfoo` | (npm) | 跨家批量评测 |

完整依赖见 `requirements.txt`。千问示例统一用 `qwen-flash`（阿里云百炼的 OpenAI 兼容接口）。

环境变量有两种写法，效果一样：

- 本手册正文的千问示例读 `DASHSCOPE_API_KEY`，`base_url` 直接写在代码里（`https://dashscope.aliyuncs.com/compatible-mode/v1`）。
- 本仓库其余几本手册用 `OPENAI_API_KEY` + `OPENAI_BASE_URL` 指向同一个百炼兼容端点。如果你已经按那种方式配好，把 `DASHSCOPE_API_KEY` 的值填到 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 设为上面的地址即可；反过来也一样，两个变量里放的是同一个百炼 key。

---

## 七、写作约定

1. 每篇大致按这个顺序写：开头交代主题，然后是概念、最小可用代码、进阶用法、生产建议、常见坑、示例代码
2. 核心技法都给出 Anthropic / OpenAI / Gemini 三家的等价代码
3. 实战篇的评测集和迭代记录是示意数据（中英文混合），用来演示流程和变化量级，不是某个真实项目的统计
4. 错误示范在代码注释里标 `# ❌ 错误写法` / `# ✅ 正确写法`
5. 每篇结尾附 Anthropic / OpenAI / Google 官方文档的对应章节

---

从第一篇开始：[Prompt Engineering 是什么 / 不是什么](docs/01-foundations/01-overview.md)
