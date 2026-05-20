# Voice Pipeline：STT + LLM + TTS

> **一句话**：`VoicePipeline` 把 STT（语音转文字）→ Agent 跑 → TTS（文字转语音）串成一个流水线——比 Realtime 灵活、便宜，但延迟更大。

---

## 1. Voice Pipeline vs Realtime

| | Voice Pipeline | Realtime |
|---|---|---|
| 架构 | STT → LLM → TTS 三段式 | LLM 直接吃音频 |
| 延迟 | 高（~2-4s 端到端） | 低（亚秒） |
| 成本 | 便宜（按 token + STT/TTS 时长） | 贵（音频时长） |
| 灵活 | 高（STT / LLM / TTS 各部分可换） | 仅 OpenAI Realtime API |
| Tool / Handoffs | 用普通 Agent，啥都行 | 一样 |
| 打断 | 不支持 | 支持（VAD） |

**何时用 Pipeline**：

- 不需要"打断"
- 想换 LLM（Claude / Gemini）但保留语音
- 成本敏感
- 已有现成 LLM 流程，加语音壳

**何时用 Realtime**：

- 电话 / 实时对话
- 需要低延迟
- 用户体验优先

---

## 2. 最简示例

```python
from agents import Agent
from agents.voice import VoicePipeline, AudioInput


# 1. 普通 Agent
agent = Agent(
    name="A",
    instructions="你是语音助手，回答简洁。",
    model="gpt-4o-mini",
)


# 2. Pipeline 包一层
pipeline = VoicePipeline(workflow=agent)


# 3. 给音频
async def main():
    # 从文件 / 麦克风读 PCM
    import numpy as np
    audio_data = read_wav("user_input.wav")  # int16 PCM

    audio_input = AudioInput(buffer=audio_data)

    result = await pipeline.run(audio_input)

    # result 是 StreamedAudioResult
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            play(event.data)
        elif event.type == "voice_stream_event_lifecycle":
            print(f"[Lifecycle] {event.event}")
```

---

## 3. 完整音频处理 demo

```python
# demos/advanced/06_voice_pipeline.py
import asyncio
import sounddevice as sd
import numpy as np
from agents import Agent
from agents.voice import VoicePipeline, AudioInput


SAMPLE_RATE = 24000


agent = Agent(
    name="VoiceBot",
    instructions="你是语音助手，用中文，回答简洁。",
    model="gpt-4o-mini",
)


pipeline = VoicePipeline(workflow=agent)


async def main():
    # 1. 录 3 秒
    print("录音 3 秒...")
    audio = sd.rec(
        int(3 * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype=np.int16,
    )
    sd.wait()
    audio_buffer = audio.flatten()

    # 2. 喂给 pipeline
    audio_input = AudioInput(buffer=audio_buffer)
    result = await pipeline.run(audio_input)

    # 3. 流式播放回应
    print("回应:")
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            # PCM int16 → float for sounddevice
            data = np.frombuffer(event.data, dtype=np.int16).astype(np.float32) / 32767
            sd.play(data, samplerate=SAMPLE_RATE)
            sd.wait()


asyncio.run(main())
```

---

## 4. 自定义 STT / TTS provider

```python
from agents.voice import OpenAIVoiceModelProvider


# 默认 OpenAI（STT: whisper / TTS: tts-1）
provider = OpenAIVoiceModelProvider()


pipeline = VoicePipeline(
    workflow=agent,
    voice_model_provider=provider,
)
```

或自己实现 `VoiceModelProvider` 用本地 Whisper / Coqui TTS。

---

## 5. 配置 STT / TTS 选项

```python
from agents.voice import VoicePipelineConfig


config = VoicePipelineConfig(
    tts_settings={
        "voice": "alloy",
        "speed": 1.0,
    },
    stt_settings={
        "model": "whisper-1",
        "language": "zh",
    },
)


pipeline = VoicePipeline(workflow=agent, config=config)
```

---

## 6. Streamed audio 输出

输出是音频流，可以：

- 写到文件
- 播放
- 流给前端 WebSocket
- 转推到 SIP / 电话系统

```python
import wave


async def save_to_wav(result, path: str):
    audio_chunks = []
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            audio_chunks.append(event.data)

    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(audio_chunks))
```

---

## 7. 跟 Agent 全套搭配

```python
# Tools + Handoffs 通通保留
@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


billing = Agent(name="Billing", instructions="账单")
support = Agent(name="Support", instructions="技术")


triage = Agent(
    name="Triage",
    instructions="语音客服分流",
    tools=[get_weather],
    handoffs=[billing, support],
)


pipeline = VoicePipeline(workflow=triage)
# 用户语音 → STT → triage → handoff → tool → TTS → 音频
```

整套 Agent 流程都跑通，输入输出换成音频。

---

## 8. Lifecycle Events

```python
async for event in result.stream():
    if event.type == "voice_stream_event_lifecycle":
        # "turn_started" / "turn_ended" 等
        print(f"[{event.event}]")
    elif event.type == "voice_stream_event_audio":
        play(event.data)
    elif event.type == "voice_stream_event_error":
        log.error("voice error", e=event.error)
```

适合在前端做"思考中..." / "播放中..."状态指示。

---

## 9. 跟前端集成（WebSocket）

```python
from fastapi import FastAPI, WebSocket
from agents.voice import VoicePipeline, AudioInput


app = FastAPI()


@app.websocket("/voice")
async def voice_ws(ws: WebSocket):
    await ws.accept()

    # 前端发 PCM bytes
    audio_bytes = await ws.receive_bytes()
    audio_input = AudioInput(buffer=np.frombuffer(audio_bytes, dtype=np.int16))

    result = await pipeline.run(audio_input)

    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            await ws.send_bytes(event.data)

    await ws.close()
```

---

## 10. 成本估算

Pipeline 一次完整对话（5 秒输入 + 10 秒输出 + 1k token LLM）：

| 项 | 价格 | 一次成本 |
|----|------|----------|
| Whisper STT | $0.006/min | $0.0005 |
| GPT-4o-mini LLM | $0.15/M in + $0.6/M out | $0.0003 |
| TTS-1 | $15/1M chars | ~$0.001 |
| **合计** | | **~$0.002** |

Realtime 同等对话约 $0.10+。Pipeline 便宜 ~50x。

---

## 11. 跟 Pydantic AI 对比

Pydantic AI 没有内置 Voice Pipeline，要自己拼 Whisper API + agent + TTS。OpenAI Agents 的 `VoicePipeline` 一行替你做完。

---

## 12. 完整 demo（带工具）

```python
# demos/advanced/06_voice_full.py
import asyncio
import sounddevice as sd
import numpy as np
from agents import Agent, function_tool
from agents.voice import VoicePipeline, AudioInput


@function_tool
def get_weather(city: str) -> str:
    return f"{city}今天 22°C 晴"


agent = Agent(
    name="VoiceBot",
    instructions="语音助手，用 get_weather 查天气，回答简洁",
    tools=[get_weather],
    model="gpt-4o-mini",
)


pipeline = VoicePipeline(workflow=agent)


SAMPLE_RATE = 24000


async def main():
    print("录音 5 秒（试着说: 北京天气）...")
    audio = sd.rec(int(5 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype=np.int16)
    sd.wait()

    result = await pipeline.run(AudioInput(buffer=audio.flatten()))

    print("播放回应...")
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            data = np.frombuffer(event.data, dtype=np.int16).astype(np.float32) / 32767
            sd.play(data, samplerate=SAMPLE_RATE)
            sd.wait()


asyncio.run(main())
```

---

## 13. 下一步

- 📖 实战：完整语音助手 → [08-practice/03-voice-assistant.md](../08-practice/03-voice-assistant.md)
- 📖 部署：FastAPI WebSocket → [06-integration/03-fastapi-deploy.md](../06-integration/03-fastapi-deploy.md)
