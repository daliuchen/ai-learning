# CE 04-04：Agent 记忆架构

> MemGPT / Letta 的出发点是：把有限的上下文窗口看成内存（RAM），把外部存储看成磁盘，让模型像操作系统管理虚拟内存那样，自己决定什么换入窗口、什么换出到外部。这样，无限长的交互历史就被分成 working / recall / archival 三层，再配上由模型自己调用的记忆工具，Agent 就能跨会话、面对海量历史也不撞窗。

---

## 1. 类比：上下文 = RAM，外部存储 = 磁盘

操作系统用了几十年的办法：物理内存装不下所有程序，于是有了虚拟内存。常用的页留在 RAM，不常用的换出到磁盘，需要时再换回来，程序觉得自己的内存是无限的。

MemGPT（2023 年提出，后来产品化为 Letta）把这一套搬到了 LLM 上：

| 操作系统 | LLM Agent | 对应 |
|----------|-----------|------|
| 物理内存 RAM | context window（有上限） | 快但小，模型直接可见 |
| 磁盘 | 外部存储（向量库/DB） | 慢但大，模型默认看不见 |
| 缺页中断 / 换页 | 记忆工具调用（搜索/写入） | 模型主动把内容换入换出 |
| 进程 | 一个长期运行的 Agent | — |

它的突破在于**让模型自己管换页**。模型通过工具调用（function calling）主动行动：「我需要查一下用户上次说的部署环境」，就从 archival 检索回 working；「这段不重要了」，就写回归档。窗口于是从一条硬约束变成了一种可以调度的资源。

---

## 2. 三层记忆

MemGPT/Letta 把记忆分成三层：

```
┌──────────────────────────────────────────────────────────┐
│  WORKING MEMORY（核心记忆 / 一直在窗口里）                    │
│  - persona：Agent 自己的人设                                 │
│  - human：当前用户的关键画像（名字、核心偏好）                  │
│  - 容量小、常驻窗口，模型可直接读写（用工具改）                  │
├──────────────────────────────────────────────────────────┤
│  RECALL MEMORY（会话历史 / 可检索的近期对话）                  │
│  - 完整对话历史的存档，超出窗口的部分在这                       │
│  - 模型用 conversation_search 工具按需捞回                    │
├──────────────────────────────────────────────────────────┤
│  ARCHIVAL MEMORY（归档 / 海量长期事实）                       │
│  - 任意长期事实、文档、知识，向量库存储                         │
│  - 模型用 archival_memory_search / archival_memory_insert 读写             │
└──────────────────────────────────────────────────────────┘
        ▲                                        │
        │ 换入（search → 拼进窗口）                  │ 换出（insert → 写回存储）
        └────────────────────────────────────────┘
                  模型自主调度（function calling）
```

Working memory 是常驻窗口的一小块核心信息，模型直接看得见，也能用工具改写，比如发现用户改了名字，就更新 `human` 块。Recall memory 是本会话和近期对话的完整存档，窗口里只留最近几轮，更早的放在这里，模型需要时调用 `conversation_search("上次提到的报错")` 捞回来。Archival memory 是不限大小的长期知识库，走向量检索，模型用 `archival_memory_insert` 写、`archival_memory_search` 读。

---

## 3. 自编辑记忆：模型自己当管理员

MemGPT 最有意思的设计是 self-editing memory：给模型一组记忆工具，让它在推理过程中自己决定何时换页，外部代码不替它管。系统 prompt 会告诉它：窗口快满时会收到警告，重要的东西记得写进 archival。

> 下面这段是概念示意。Letta 的 SDK 变化很快（官方文档现在主推 Letta Agent SDK），`letta_client` 的参数名请以 https://docs.letta.com/ 最新的 quickstart 为准，这里只用来说明「记忆块 + 记忆工具」的结构。

```python
# Letta（MemGPT 的产品化框架）创建一个有分层记忆的 Agent
from letta_client import Letta

client = Letta(base_url="http://localhost:8283")

agent = client.agents.create(
    model="anthropic/claude-sonnet-5-5",
    embedding="openai/text-embedding-3-small",
    # working memory 的核心块，常驻窗口
    memory_blocks=[
        {"label": "persona", "value": "我是一个有长期记忆的助手，会主动记住用户的事。"},
        {"label": "human", "value": ""},  # 随对话填充用户画像
    ],
    # 内置工具：conversation_search / archival_memory_insert / archival_memory_search / memory_replace 等（工具名随 Letta 版本有变化）
)

# 用户告诉它一个事实——模型会自己决定调 memory 工具写进 working 或 archival
client.agents.messages.create(
    agent_id=agent.id,
    messages=[{"role": "user", "content": "记住，我的生产环境跑在 AWS 东京区。"}],
)
# 模型内部可能触发：core_memory_append(label="human", content="生产环境 AWS 东京区")
# 或 archival_memory_insert(...)，无需外部代码干预

# 下次会话，问相关问题时，模型自己 archival_memory_search 把它捞回窗口
client.agents.messages.create(
    agent_id=agent.id,
    messages=[{"role": "user", "content": "我该选哪个区部署延迟低？"}],
)
```

系统 prompt 教给模型的换页逻辑大致如下：

```
# ✅ 模型自主调度的心智模型
- 用户给了一条该长期记的事实 → 调 core_memory_append / archival_memory_insert（换出到存储）
- 当前问题需要过去的信息但窗口里没有 → 调 archival_memory_search / conversation_search（换入）
- 窗口要满了（收到 memory pressure 警告）→ 把不活跃内容总结后写回 recall/archival
```

---

## 4. 现代框架的不同做法

到 2026 年，各家的 Agent 长期记忆方案思路同源，抽象层次不一样：

| 方案 | 谁来管换页 | 抽象 | 特点 |
|------|-----------|------|------|
| MemGPT / Letta | 模型自己（self-editing） | 三层记忆 + 记忆工具 | 最接近虚拟内存的原始设想，模型自治 |
| Claude memory tool | 模型调工具，开发者管存储后端 | 一个 `memory` 工具（文件式读写） | Claude 主动 `str_replace`/`view` 记忆文件，开发者实现存储 |
| OpenAI memory（ChatGPT） | 平台后台自动抽取 | 对用户透明的"记忆" | 自动从对话提炼、跨会话注入，用户可查看/删除 |
| 框架自管（LangGraph store） | 开发者代码管 | store + checkpointer | 开发者显式 put/get，模型不直接操作 |

Claude 的 memory tool 单独说一下。它把记忆做成一个类文件系统，模型用 `view` / `create` / `str_replace` / `insert` 等命令读写记忆文件，存储后端由开发者实现（本地目录、S3、数据库都可以）。思路仍是模型自主换页，只是接口长得像文件操作：

```python
# ✅ Claude memory tool（语法按官方文档整理）：模型主动读写记忆"文件"
import anthropic

client = anthropic.Anthropic()
resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=2048,
    tools=[{"type": "memory_20250818", "name": "memory"}],  # Anthropic 提供的客户端记忆工具，工具本身不需要 beta header
    messages=[{"role": "user", "content": "记住我偏好用 Rust，以后默认给 Rust 例子。"}],
)
# 模型会发起 memory 工具调用（如 create /memories/preferences.md 写入 "prefers Rust"）
# 你的代码接住这个 tool_use，落到实际存储，再把结果回传——下次会话模型 view 回来
```

官方 Memory tool 页面还有几点补充：

- 工具定义就是 `{"type": "memory_20250818", "name": "memory"}`，不需要写 schema，`name` 必须是 `memory`。
- 命令有 6 个：`view` / `create` / `str_replace` / `insert` / `delete` / `rename`，路径限定在 `/memories` 下。处理器必须校验路径，防止目录穿越（比如 `/memories/../../secrets.env`）。
- 带上记忆工具后，API 会自动往 system prompt 里加一段协议，要求模型先 view 记忆目录，并假设自己随时会被中断。
- 官方 SDK 提供了辅助类：Python 有 `BetaAbstractMemoryTool` 和现成的本地文件实现 `BetaLocalFilesystemMemoryTool`（配合 `client.beta.messages.tool_runner`），TypeScript 是 `betaMemoryTool`。
- 可以和 compaction 搭配：压缩让活跃上下文变小，记忆负责保存摘要之后还必须留下的信息。

---

## 5. 这套架构解决了什么

| 朴素做法的痛 | 分层记忆怎么解 |
|--------------|----------------|
| 历史超窗就崩 | 超出部分换出到 recall/archival，窗口只留活跃部分 |
| 跨会话失忆 | working + archival 持久化，新会话自动/按需换入 |
| 全量历史又贵又触发 rot | 只把相关片段换入窗口，token 可控 |
| 外部代码硬编码"该记什么" | 模型自己判断、自己读写，更灵活 |

代价也要算清楚。模型自主换页，就多了记忆工具调用的开销，每次 search 或 insert 都是一次推理加一次调用；模型也可能判断失误，该记的没记，不该捞的捞了一堆。所以生产里一般会加护栏：限制记忆工具的调用频率，对写入做校验，关键事实仍然用确定性的代码兜底。

---

## 6. 下一步

- working memory 里的 human 块，其实就是用户画像：[用户画像与个性化上下文](./05-personalization.md)
- archival 里的内容怎么过期、更新、遗忘：[记忆的遗忘与更新](./06-forgetting.md)
- 换页依赖的存储与召回：[记忆的存储与召回](./03-storage-recall.md)
- 窗口满了往外换，就是压缩：[为什么要压缩上下文](../05-compaction/01-why-compact.md)
- Agent 整体的上下文管理：[Agent 的上下文累积问题](../06-agent-context/01-accumulation.md)

## 参考资料

- Packer et al., "MemGPT: Towards LLMs as Operating Systems"：https://arxiv.org/abs/2310.08560
- Letta 文档：https://docs.letta.com/
- Anthropic Claude memory tool：https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- LangGraph 长期记忆 store：https://langchain-ai.github.io/langgraph/concepts/persistence/
