# CE 04-01：短期记忆 vs 长期记忆

> 短期记忆是当前窗口里的会话历史，会话结束就没了，容量也被窗口大小卡死；长期记忆是存在数据库或向量库里的持久事实，跨会话保留，需要时再召回进窗口。一个 Agent 既要记得这次聊过什么，又要记得你是谁，两层缺一不可，而且长期记忆得能准确地回到短期窗口里。

---

## 1. 为什么把记忆分两层

人脑也分层：刚听到的话在工作记忆里，三年前的事在长期记忆里。LLM 应用里的区分更硬，因为两层用的是完全不同的存储介质：

- 短期记忆：在 context window 里。每次调用模型，你把这次会话的 user/assistant 轮次拼进去，模型才看得见。API 调用一结束，模型本身什么也不记得，下次能「记得」，是因为你又把历史拼了回去。
- 长期记忆：在窗口外的存储里（向量库、KV、关系库、文档）。它不会自己进入模型视野，要由你的代码在需要时检索出来、拼进窗口。

所以可以把短期记忆看成窗口内的临时变量，长期记忆看成外部数据库。模型本身无状态（stateless），所谓「记忆」，全看你每次调用时往窗口里装了什么。

---

## 2. 一张对比表

| 维度 | 短期记忆（Short-term） | 长期记忆（Long-term） |
|------|------------------------|------------------------|
| 存在哪 | context window 内（会话历史） | 窗口外（向量库 / KV / 文档库） |
| 生命周期 | 单次会话，会话结束即消失 | 跨会话持久，可存数月数年 |
| 容量上限 | 受窗口大小限制（如 200K～1M token） | 理论无限（受存储成本约束） |
| 进入模型的方式 | 直接拼进 prompt，模型默认可见 | 必须先检索 / 召回，再拼进窗口 |
| 常见内容 | 本次对话的前 N 轮 | 用户画像、历史偏好、过往事实、长期任务进度 |
| 成本 | 每轮重复进窗口，token 线性涨 | 存储便宜，召回时才付 token |
| 失败模式 | 超窗截断、context rot | 召回不准 / 召回不到 / 陈旧未更新 |
| 谁来管 | 会话记忆策略（buffer/window/summary） | 存储 + 召回管线（写入时机、检索方式） |

短期记忆管这次对话前后连贯，长期记忆管跨会话还认得你。

---

## 3. 只有短期记忆会怎样

大多数 chatbot 最朴素的实现就只有短期记忆，把整段对话历史一路拼下去：

```python
# ❌ 只靠短期记忆：会话一关，全忘光
import anthropic

client = anthropic.Anthropic()
messages = []  # 这次会话的历史，进程一退就没了

def chat(user_input: str) -> str:
    messages.append({"role": "user", "content": user_input})
    resp = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=1024,
        messages=messages,
    )
    reply = next(b.text for b in resp.content if b.type == "text")
    messages.append({"role": "assistant", "content": reply})
    return reply
```

这样写有两个问题。第一，会话结束就失忆：用户今天说「我对花生过敏」，明天新开会话，`messages` 是空的，你完全不知道。第二，窗口会爆：聊到几百轮，`messages` 超过窗口（200K～1M），要么报错，要么截断，一截断，短期记忆也丢了。

长期记忆就是用来兜这两个底的。

---

## 4. 为什么必须有长期记忆

原因有两个，正好对应上面两个问题。

### 4.1 窗口有限，装不下所有东西

就算窗口已经是 1M（Claude 5.x、GPT-6、Gemini 4 都在这个量级），一个用了一年的助手也不可能把全部历史原样堆着。堆满了又贵又慢，还会触发 context rot，中间内容的召回变差（见 [Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)）。不常用的内容沉到外部存储、用时再捞，才能长期维持下去。

### 4.2 跨会话个性化

好用的助手应该记得你：名字、偏好、上次没做完的任务、你纠正过它的地方。这些信息天然跨会话，短期记忆装不下，只能落进持久存储，每次新会话按需召回。

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

设计时要记住一点：**长期记忆不会自动进窗口**。它待在外部存储里，只有本轮对话用得上时，才被检索出来拼进短期窗口。两层就靠这一步连起来。

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
        *history[-10:],                              # 只留最近 10 条消息（约 5 轮），控预算
        {"role": "user", "content": user_input},
    ]
```

两个工程细节：召回的片段通常放在 system 区或历史最前面，重要信息靠前，别埋在中间（lost in the middle）；召回时按相关性筛，别全量倒进去。长期记忆的检索质量直接决定个性化做得好不好。这篇只建立分层的概念，存储和召回的细节见 [记忆的存储与召回](./03-storage-recall.md)。

---

## 6. 常见误区

| 误区 | 实际情况 |
|------|------|
| 「窗口够大就不用长期记忆了」 | 窗口再大也有上限，而且跨会话的历史本来就不在窗口里，必须持久化 |
| 「长期记忆存了就会自动生效」 | 不召回就等于没有，模型只看得见进了窗口的东西 |
| 「把所有长期记忆每次都拼进去」 | token 暴涨，噪声稀释注意力，要按相关性召回 |
| 「短期记忆等于长期记忆的子集」 | 两者介质、生命周期、管理方式都不同，是协作关系，不是包含关系 |
| 「模型自己会记住上次说的话」 | 模型无状态，每次靠你重新拼历史，不拼就忘 |

---

## 7. 下一步

- 单次会话内怎么管历史（buffer / window / summary）：[会话记忆管理](./02-conversation-memory.md)
- 长期记忆存哪、何时写、怎么召回：[记忆的存储与召回](./03-storage-recall.md)
- Agent 的分层记忆架构（MemGPT / Letta）：[Agent 记忆架构](./04-agent-memory-arch.md)
- 把上下文当预算来分配：[Context Budget](../01-foundations/05-context-budget.md)
- 召回进窗口之前的检索基础：[RAG 作为上下文来源](../03-retrieval/01-rag-as-context.md)

## 参考资料

- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangChain Memory 概念文档：https://docs.langchain.com/oss/python/langchain/short-term-memory
