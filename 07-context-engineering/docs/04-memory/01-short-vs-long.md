# CE 04-01：短期记忆 vs 长期记忆

> **一句话**：短期记忆 = 当前上下文窗口里塞着的会话历史，跟着这次会话生死、跟着窗口大小封顶；长期记忆 = 落在数据库 / 向量库里的持久事实，跨会话存活，按需被「召回」进短期窗口。一个 Agent 想既「记得这次说过啥」又「记得你是谁」，必须两层都有，且让长期记忆能精准回流到短期窗口。

---

## 1. 为什么把记忆分两层

人脑也是分层的：你能记住刚才这句话（工作记忆），也能记住三年前的事（长期记忆）。LLM 应用里这个区分更硬性——因为它由**两种完全不同的存储介质**决定：

- **短期记忆**：活在 context window 里。每次调用模型，你把这次会话的 user/assistant 轮次拼进去，模型「看得见」它们。但这次 API 调用一结束，模型本身不记得任何东西——所谓「记得」，全靠你下次再把历史拼回去。
- **长期记忆**：活在窗口之外的存储里（向量库、KV、关系库、文档）。它不会自动进入模型视野，必须由你的代码在需要时检索出来、拼进窗口，模型才看得到。

一句话：**短期记忆是「窗口内的临时变量」，长期记忆是「外部数据库」**。模型本身无状态（stateless），所有「记忆」的错觉都来自你每次调用时往窗口里装了什么。

---

## 2. 一张对比表

| 维度 | 短期记忆（Short-term） | 长期记忆（Long-term） |
|------|------------------------|------------------------|
| 存在哪 | context window 内（会话历史） | 窗口外（向量库 / KV / 文档库） |
| 生命周期 | 单次会话，会话结束即消失 | 跨会话持久，可存数月数年 |
| 容量上限 | 受窗口大小限制（如 200K token） | 理论无限（受存储成本约束） |
| 进入模型的方式 | 直接拼进 prompt，模型默认可见 | 必须先检索 / 召回，再拼进窗口 |
| 典型内容 | 本次对话的前 N 轮 | 用户画像、历史偏好、过往事实、长期任务进度 |
| 成本 | 每轮重复进窗口，token 线性涨 | 存储便宜，召回时才付 token |
| 失败模式 | 超窗截断、context rot | 召回不准 / 召回不到 / 陈旧未更新 |
| 谁来管 | 会话记忆策略（buffer/window/summary） | 存储 + 召回管线（写入时机、检索方式） |

如果只记一句话：**短期解决「这次对话的连贯」，长期解决「跨会话的我认识你」**。

---

## 3. 只有短期记忆会怎样

绝大多数 chatbot 的最朴素实现就是「只有短期记忆」——把整段对话历史一路拼下去：

```python
# ❌ 只靠短期记忆：会话一关，全忘光
import anthropic

client = anthropic.Anthropic()
messages = []  # 这次会话的历史，进程一退就没了

def chat(user_input: str) -> str:
    messages.append({"role": "user", "content": user_input})
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=messages,
    )
    reply = resp.content[0].text
    messages.append({"role": "assistant", "content": reply})
    return reply
```

两个致命问题：

1. **会话结束就失忆**：用户今天告诉你「我对花生过敏」，明天新开一个会话，`messages` 是空的，你完全不记得。
2. **窗口会爆**：聊到几百轮，`messages` 累积超过 200K token，要么报错要么得截断——一截断短期记忆也丢了。

这就是为什么需要长期记忆兜底。

---

## 4. 为什么必须有长期记忆

两个根本原因，对应上面两个问题：

### 4.1 窗口有限，装不下「一切」

哪怕 Claude 200K、Gemini 1M，也扛不住一个用了一年的助手把所有历史原样堆着。更何况堆满了既贵又慢，还触发 context rot（中间内容召回变差，见 [01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)）。把不常用的内容沉到外部存储、用时再捞，是唯一可持续的做法。

### 4.2 跨会话个性化

一个真正好用的助手，应该「记得你」：你的名字、偏好、上次没做完的任务、你纠正过它的事。这些信息天然跨会话，短期记忆根本承载不了——它必须落在持久存储里，每次新会话时按需召回。

```python
# ✅ 长期记忆持久化（这里用最简单的本地 JSON 示意，生产用 DB/向量库）
import json
from pathlib import Path

MEM_FILE = Path("user_memory.json")

def load_long_term(user_id: str) -> list[str]:
    if not MEM_FILE.exists():
        return []
    return json.loads(MEM_FILE.read_text()).get(user_id, [])

def save_long_term(user_id: str, fact: str) -> None:
    data = json.loads(MEM_FILE.read_text()) if MEM_FILE.exists() else {}
    data.setdefault(user_id, []).append(fact)
    MEM_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# 第一天的会话里抽出来的事实，落盘
save_long_term("u_42", "对花生过敏")
# 第二天新会话，捞回来
facts = load_long_term("u_42")  # ['对花生过敏']
```

---

## 5. 两者怎么协作：长期记忆按需召回进短期窗口

关键设计点：**长期记忆不会自动进窗口**。它待在外部存储里，只有当本轮对话「相关」时，才被检索出来、拼进短期窗口。这一步是连接两层的桥梁。

```
新一轮用户输入
      │
      ▼
┌──────────────┐   语义检索 / 按 user_id 查
│  长期记忆库   │ ─────────────────────────┐
│ (向量库/KV)   │                          │
└──────────────┘                          ▼
                              ┌──────────────────────────┐
                              │      短期记忆 / 窗口        │
   本次会话历史 ─────────────▶ │  System                   │
   (buffer/window)            │  + 召回的长期记忆片段        │
                              │  + 最近 N 轮会话历史         │
                              │  + 当前用户输入             │
                              └──────────────────────────┘
                                          │
                                          ▼
                                       LLM 调用
```

组装一次窗口的伪代码：

```python
# ✅ 召回长期记忆 → 拼进短期窗口
def build_context(user_id: str, user_input: str, history: list[dict]) -> list[dict]:
    # 1. 从长期记忆按相关性召回（这里简化为全量；生产用向量检索）
    facts = load_long_term(user_id)
    memory_block = "已知关于该用户的事实：\n" + "\n".join(f"- {f}" for f in facts)

    # 2. 短期窗口 = system(含召回记忆) + 最近若干轮历史 + 当前输入
    return [
        {"role": "user", "content": memory_block},  # 也可放进 system
        *history[-10:],                              # 只留最近 10 轮，控预算
        {"role": "user", "content": user_input},
    ]
```

注意两个工程细节：

- 召回片段通常放在 system 区或历史最前，**重要信息靠头部**，别埋中间（lost in the middle）。
- 召回不是「全量倒进去」，而是**按相关性筛**——长期记忆的检索质量直接决定个性化效果，这一篇先建立分层观，存储与召回的细节见 [03-storage-recall.md](./03-storage-recall.md)。

---

## 6. 常见误区

| 误区 | 真相 |
|------|------|
| 「窗口够大就不用长期记忆了」 | 窗口再大也封顶，且跨会话历史本就不在窗口里，必须持久化 |
| 「长期记忆存了就会自动生效」 | 不召回就等于没有，模型只看得见进窗口的东西 |
| 「把所有长期记忆每次都拼进去」 | token 爆炸 + 噪声稀释，必须按相关性召回 |
| 「短期记忆等于长期记忆的子集」 | 两者介质、生命周期、管理方式都不同，是协作不是包含 |
| 「模型自己会记住上次说的话」 | 模型无状态，每次靠你重新拼历史，不拼就忘 |

---

## 7. 下一步

- 📖 单次会话内怎么管历史（buffer / window / summary） → [02-conversation-memory.md](./02-conversation-memory.md)
- 📖 长期记忆存哪、何时写、怎么召回 → [03-storage-recall.md](./03-storage-recall.md)
- 📖 Agent 的分层记忆架构（MemGPT / Letta） → [04-agent-memory-arch.md](./04-agent-memory-arch.md)
- 📖 上下文是有限预算，回看预算心法 → [01-foundations/05-context-budget.md](../01-foundations/05-context-budget.md)
- 📖 召回进窗口前的检索基础，看检索章 → [03-retrieval/01-rag-basics.md](../03-retrieval/01-rag-as-context.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangChain Memory 概念文档：https://python.langchain.com/docs/concepts/chat_history/
