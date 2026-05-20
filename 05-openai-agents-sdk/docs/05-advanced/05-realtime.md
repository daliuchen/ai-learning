# Realtime Agent：实时语音 API

> **一句话**：用 `RealtimeAgent` + `RealtimeRunner` 走 OpenAI Realtime API，模型直接处理音频 in/out 流——做电话助手、语音 chatbot、AI 主播都行。

---

## 1. Realtime API 是啥

OpenAI 的 Realtime API（`gpt-4o-realtime-preview`）：

- 输入：音频流 + 文本（可选）
- 输出：音频流 + 文本 + tool call
- 全双工：能边听边说（用户打断也行）

**跟 Voice Pipeline 区别**：

- Voice Pipeline = STT → LLM → TTS 三段式
- Realtime = LLM 直接吃音频流（少一跳，延迟低）

详见 [06-voice-pipeline.md](./06-voice-pipeline.md)。

---

## 2. 安装

```bash
pip install "openai-agents[voice]"
# 或
pip install openai-agents sounddevice numpy
```

需要的依赖：

- `sounddevice`（录音 / 播放）
- `numpy`

---

## 3. 最简 RealtimeAgent

```python
from agents.realtime import RealtimeAgent, RealtimeRunner


agent = RealtimeAgent(
    name="VoiceBot",
    instructions="你是友好的语音助手，简短回答。",
)


runner = RealtimeRunner(starting_agent=agent)


async with await runner.run() as session:
    # session 是 RealtimeSession
    # 输入流 / 输出流通过 session 收发
    await session.send_message("讲个冷笑话")

    async for event in session:
        if event.type == "audio":
            play(event.audio)
        elif event.type == "response.text.delta":
            print(event.delta, end="", flush=True)
        elif event.type == "response.done":
            break
```

---

## 4. 带麦克风的完整 demo

```python
# demos/advanced/05_realtime.py
import asyncio
import sounddevice as sd
import numpy as np
from agents.realtime import RealtimeAgent, RealtimeRunner


agent = RealtimeAgent(
    name="VoiceBot",
    instructions="你是语音助手，用中文，回答简洁。",
    model="gpt-4o-realtime-preview",
    voice="alloy",   # alloy / echo / fable / onyx / nova / shimmer
)


SAMPLE_RATE = 24000


async def main():
    runner = RealtimeRunner(starting_agent=agent)

    async with await runner.run() as session:
        # 起一个 task：从 mic 读音频，推给 session
        async def mic_to_session():
            def callback(indata, frames, time_info, status):
                pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
                asyncio.create_task(session.send_audio(pcm))

            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, callback=callback):
                while True:
                    await asyncio.sleep(0.1)

        mic_task = asyncio.create_task(mic_to_session())

        # 从 session 拿事件，播放回声
        async for event in session:
            if event.type == "audio":
                audio = np.frombuffer(event.audio, dtype=np.int16).astype(np.float32) / 32767
                sd.play(audio, samplerate=SAMPLE_RATE)
                sd.wait()
            elif event.type == "response.text.delta":
                print(event.delta, end="", flush=True)


asyncio.run(main())
```

⚠️ 这是简化版，实战要：

- 处理打断（VAD）
- 缓冲音频帧
- 错误重连

---

## 5. RealtimeAgent 跟普通 Agent 区别

| | Agent | RealtimeAgent |
|---|---|---|
| 输入 | 文本 / messages | 音频流 + 文本 |
| 输出 | final_output | 流式（音频 + 文本） |
| Tools | function_tool | function_tool（一样） |
| Handoffs | ✅ | ✅ |
| Guardrails | ✅ | 仅 input |
| Runner | Runner.run | RealtimeRunner |
| 模型 | 任意 | 限 `gpt-4o-realtime-preview` |

---

## 6. 带 Tool 的 Realtime

```python
from agents import function_tool


@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


agent = RealtimeAgent(
    name="VoiceWeatherBot",
    instructions="用户问天气时用 get_weather 工具",
    tools=[get_weather],
)
```

模型听到"北京天气" → 调 get_weather → 用结果生成语音回答。**全程在一次 LLM 调用里**完成。

---

## 7. Handoffs 在 Realtime 里

```python
billing = RealtimeAgent(name="Billing", instructions="账单")
support = RealtimeAgent(name="Support", instructions="技术")

triage = RealtimeAgent(
    name="Triage",
    instructions="分流",
    handoffs=[billing, support],
)
```

跟普通 Handoffs 一样工作。模型决定 transfer 时切换 active agent，继续在同一个语音会话里。

---

## 8. 打断 / VAD

Realtime API 内置 VAD（voice activity detection）：用户说话时模型自动停。配置：

```python
agent = RealtimeAgent(
    name="A",
    instructions="...",
    # 通过 session config 控制 VAD 灵敏度
)
```

API 层细节看官方 docs；SDK 上手通常不用碰这些。

---

## 9. 模型选项

```python
RealtimeAgent(
    name="A",
    model="gpt-4o-realtime-preview",
    voice="alloy",          # alloy / echo / fable / onyx / nova / shimmer
    # 其它 model_settings 通过 RealtimeRunner config 传
)
```

---

## 10. 适合 / 不适合的场景

✅ 适合：

- 电话客服（接听 / 转人工）
- 语音助手（Siri-like）
- AI 主播 / 解说
- 视频会议实时翻译

❌ 不适合：

- 离线批处理（用普通 Agent）
- 需要长上下文（Realtime 比 chat completion 上下文短）
- 非 OpenAI 模型（只有 OpenAI 提供 Realtime API）

---

## 11. 成本

Realtime 比文本贵得多——按音频时长计费。一段 1 分钟对话可能 $0.10+。开发时省着用，生产做计费监控。

---

## 12. 完整 demo（最小可跑）

```python
# demos/advanced/05_realtime_minimal.py
import asyncio
import sounddevice as sd
from agents.realtime import RealtimeAgent, RealtimeRunner


agent = RealtimeAgent(
    name="VoiceBot",
    instructions="你是友好语音助手，回答简洁。",
    voice="alloy",
)


async def main():
    runner = RealtimeRunner(starting_agent=agent)

    print("说话吧（Ctrl+C 退出）...")

    async with await runner.run() as session:
        # 演示：发一条文本消息
        await session.send_message("你好，简单介绍下你自己。")

        async for event in session:
            print(f"[event] {event.type}")
            if event.type == "response.done":
                break


asyncio.run(main())
```

---

## 13. 下一步

- 📖 Voice Pipeline（三段式）→ [06-voice-pipeline.md](./06-voice-pipeline.md)
- 📖 实战：完整语音助手 → [08-practice/03-voice-assistant.md](../08-practice/03-voice-assistant.md)
