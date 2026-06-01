# CE 04-04：Agent 记忆架构

> **一句话**：MemGPT / Letta 的核心洞见——把有限的上下文窗口当「内存（RAM）」，把外部存储当「磁盘」，让模型像操作系统管虚拟内存一样，自己决定把什么换入窗口、把什么换出到外部。于是无限长的交互历史被分层成 working / recall / archival 三层，配上「模型自主调用记忆工具」，就有了能跨会话、跨海量历史还不撞窗的 Agent。

---

## 1. 核心类比：上下文 = RAM，外部存储 = 磁盘

操作系统玩了几十年的把戏：物理内存装不下所有程序，于是有「虚拟内存」——常用的页留在 RAM，不常用的换出到磁盘，需要时再换回来，程序感觉自己有「无限内存」。

MemGPT（2023，后来产品化为 Letta）把这套搬到了 LLM：

| 操作系统 | LLM Agent | 对应 |
|----------|-----------|------|
| 物理内存 RAM | context window（200K） | 快但小，模型直接可见 |
| 磁盘 | 外部存储（向量库/DB） | 慢但大，模型默认看不见 |
| 缺页中断 / 换页 | 记忆工具调用（搜索/写入） | 模型主动把内容换入换出 |
| 进程 | 一个长期运行的 Agent | — |

关键突破：**让模型自己管这套换页**。模型通过工具调用（function calling）主动「我现在需要查一下用户上次说的部署环境」→ 从 archival 检索回 working，或者「这段不重要了，写回归档」。窗口因此从「硬约束」变成「可调度的资源」。

---

## 2. 三层记忆

MemGPT/Letta 把记忆分成三层，各有职责：

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
│  - 模型用 archival_search / archival_insert 读写             │
└──────────────────────────────────────────────────────────┘
        ▲                                        │
        │ 换入（search → 拼进窗口）                  │ 换出（insert → 写回存储）
        └────────────────────────────────────────┘
                  模型自主调度（function calling）
```

- **Working memory**：常驻窗口的一小块核心信息，模型能直接看见，也能用工具改写（比如发现用户改名了，就更新 `human` 块）。
- **Recall memory**：本会话/近期对话的完整存档。窗口里只留最近几轮，更早的在这，模型需要时 `conversation_search("上次提到的报错")` 捞回来。
- **Archival memory**：无限大的长期知识库，向量检索。模型主动 `archival_insert` 写、`archival_search` 读。

---

## 3. 自编辑记忆：模型自己当管理员

MemGPT 的精髓是「self-editing memory」——不是外部代码替模型管记忆，而是**给模型一组记忆工具，让它在推理中自己决定何时换页**。系统 prompt 里告诉它：窗口快满了会收到警告，重要的东西记得写进 archival。

```python
# Letta（MemGPT 的产品化框架）创建一个有分层记忆的 Agent
from letta_client import Letta

client = Letta(base_url="http://localhost:8283")

agent = client.agents.create(
    model="anthropic/claude-sonnet-4-5",
    embedding="openai/text-embedding-3-small",
    # working memory 的核心块，常驻窗口
    memory_blocks=[
        {"label": "persona", "value": "我是一个有长期记忆的助手，会主动记住用户的事。"},
        {"label": "human", "value": ""},  # 随对话填充用户画像
    ],
    # 内置工具：conversation_search / archival_insert / archival_search / memory_replace 等
)

# 用户告诉它一个事实——模型会自己决定调 memory 工具写进 working 或 archival
client.agents.messages.create(
    agent_id=agent.id,
    messages=[{"role": "user", "content": "记住，我的生产环境跑在 AWS 东京区。"}],
)
# 模型内部可能触发：core_memory_append(label="human", content="生产环境 AWS 东京区")
# 或 archival_insert(...)，无需外部代码干预

# 下次会话，问相关问题时，模型自己 archival_search 把它捞回窗口
client.agents.messages.create(
    agent_id=agent.id,
    messages=[{"role": "user", "content": "我该选哪个区部署延迟低？"}],
)
```

模型「换页」的触发逻辑（系统 prompt 教它的）大致是：

```
# ✅ 模型自主调度的心智模型
- 用户给了一条该长期记的事实 → 调 core_memory_append / archival_insert（换出到存储）
- 当前问题需要过去的信息但窗口里没有 → 调 archival_search / conversation_search（换入）
- 窗口要满了（收到 memory pressure 警告）→ 把不活跃内容总结后写回 recall/archival
```

---

## 4. 现代框架的不同做法

2025-2026 各家给「Agent 长期记忆」的方案，思路同源但抽象层次不同：

| 方案 | 谁来管换页 | 抽象 | 特点 |
|------|-----------|------|------|
| MemGPT / Letta | 模型自己（self-editing） | 三层记忆 + 记忆工具 | 最贴近"虚拟内存"原教旨，模型自治 |
| Claude memory tool | 模型调工具，开发者管存储后端 | 一个 `memory` 工具（文件式读写） | Claude 主动 `str_replace`/`view` 记忆文件，开发者实现存储 |
| OpenAI memory（ChatGPT） | 平台后台自动抽取 | 对用户透明的"记忆" | 自动从对话提炼、跨会话注入，用户可查看/删除 |
| 框架自管（LangGraph store） | 开发者代码管 | store + checkpointer | 开发者显式 put/get，模型不直接操作 |

Claude 的 memory tool 值得单独说：它把记忆暴露成一个**类文件系统**，模型用 `view` / `create` / `str_replace` / `insert` 等命令读写记忆文件，存储后端由开发者实现（本地目录、S3、DB 都行）。本质还是「模型自主换页」，只是接口长得像文件操作：

```python
# ✅ Claude memory tool（概念示意）：模型主动读写记忆"文件"
import anthropic

client = anthropic.Anthropic()
resp = client.beta.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    tools=[{"type": "memory_20250818", "name": "memory"}],  # 内置记忆工具
    betas=["context-management-2025-06-27"],
    messages=[{"role": "user", "content": "记住我偏好用 Rust，以后默认给 Rust 例子。"}],
)
# 模型会发起 memory 工具调用（如 create /memories/preferences.md 写入 "prefers Rust"）
# 你的代码接住这个 tool_use，落到实际存储，再把结果回传——下次会话模型 view 回来
```

---

## 5. 这套架构解决了什么

| 朴素做法的痛 | 分层记忆怎么解 |
|--------------|----------------|
| 历史超窗就崩 | 超出部分换出到 recall/archival，窗口只留活跃部分 |
| 跨会话失忆 | working + archival 持久化，新会话自动/按需换入 |
| 全量历史又贵又触发 rot | 只把相关片段换入窗口，token 可控 |
| 外部代码硬编码"该记什么" | 模型自己判断、自己读写，更灵活 |

代价也要认清：模型自主换页意味着**多了记忆工具调用的开销**（每次 search/insert 都是一次推理+调用），且模型可能误判（该记的没记、不该捞的瞎捞）。所以生产里常给它加护栏：限制记忆工具调用频率、对写入做校验、关键事实仍用确定性代码兜底。

---

## 6. 下一步

- 📖 working memory 里的 human 块怎么构建 = 用户画像 → [05-personalization.md](./05-personalization.md)
- 📖 archival 里的东西怎么过期/更新/遗忘 → [06-forgetting.md](./06-forgetting.md)
- 📖 换页的存储召回基础 → [03-storage-recall.md](./03-storage-recall.md)
- 📖 窗口满了换出 = 压缩，详见压缩章 → [05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md)
- 📖 Agent 的整体上下文管理 → [06-agent-context/01-agent-context-loop.md](../06-agent-context/01-accumulation.md)

## 参考资料

- Packer et al., "MemGPT: Towards LLMs as Operating Systems"：https://arxiv.org/abs/2310.08560
- Letta 文档：https://docs.letta.com/
- Anthropic Claude memory tool：https://docs.anthropic.com/en/docs/build-with-claude/tool-use/memory-tool
- LangGraph 长期记忆 store：https://langchain-ai.github.io/langgraph/concepts/persistence/
