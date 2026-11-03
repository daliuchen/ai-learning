# Voice Pipeline：STT + LLM + TTS

> `VoicePipeline` 把语音转文字（STT）、Agent 运行、文字转语音（TTS）串成一条流水线。和 Realtime 相比，它更灵活、更便宜，但延迟更高。
>
> 默认的 STT 和 TTS 用的是 OpenAI 的服务，真实的转写和合成需要 OpenAI API key。没有 key 也能把流水线跑起来：SDK 自带的测试替身（`agents.voice.testing` 里的 `ScriptedSTTModel` / `ScriptedTTSModel`）顶替 STT 和 TTS，中间的 Agent 照样用真实模型（比如千问，工具调用也照常走）。这样能验证编排和 Agent 工作流，音频质量和 STT/TTS 的效果则要接上真实服务才看得到。

---

## 1. Voice Pipeline vs Realtime

| | Voice Pipeline | Realtime |
|---|---|---|
| 架构 | STT → LLM → TTS 三段式 | LLM 直接处理音频 |
| 延迟 | 高（多了 STT、TTS 两段，通常是秒级，取决于模型） | 低 |
| 成本 | 便宜（按 token + STT/TTS） | 贵（音频时长/音频 token） |
| 灵活性 | 高（STT / LLM / TTS 各段都能换，中间的 LLM 可以是任意模型，包括千问） | 只能用 OpenAI Realtime API |
| Tool / Handoffs | 用普通 Agent，都支持 | 一样 |
| 打断 | 流水线本身不处理（需自己做） | 支持（VAD） |

不需要打断、想换成 Claude / Gemini / 千问又要保留语音、对成本敏感，或者已经有现成的 LLM 流程只想加一层语音外壳，这些情况用 Pipeline。电话或实时对话、要求低延迟、用户体验优先时，用 Realtime。

---

## 2. 最简示例

```bash
pip install "openai-agents[voice]"     # 额外依赖 numpy / websockets
```

```python
import numpy as np
from agents import Agent
from agents.voice import AudioInput, SingleAgentVoiceWorkflow, VoicePipeline

# 1. 普通 Agent
agent = Agent(
    name="A",
    instructions="你是语音助手，回答简洁。",
    model="gpt-6-luna",
)

# 2. 用 SingleAgentVoiceWorkflow 包一层（直接 VoicePipeline(workflow=agent) 不行：workflow 必须是 VoiceWorkflowBase）
pipeline = VoicePipeline(workflow=SingleAgentVoiceWorkflow(agent))


# 3. 给音频
async def main():
    audio_data = np.zeros(24000 * 3, dtype=np.int16)      # 实际从文件 / 麦克风读 24kHz 单声道 int16 PCM
    result = await pipeline.run(AudioInput(buffer=audio_data))

    # result 是 StreamedAudioResult
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            play(event.data)                              # event.data 是 numpy int16 数组，不是 bytes
        elif event.type == "voice_stream_event_lifecycle":
            print(f"[Lifecycle] {event.event}")           # turn_started / turn_ended / session_ended
        elif event.type == "voice_stream_event_error":
            print("error:", event.error)
```

输入有两种。`AudioInput` 是一整段录好的音频（按键说话或预录音频），说话什么时候结束是已知的；`StreamedAudioInput` 是你边收边推音频块，由流水线做活动检测，判断什么时候说完、什么时候调用工作流。

---

## 3. 完整的音频处理 demo

运行这段需要 OpenAI API key 和声卡。

```python
import asyncio
import sounddevice as sd
import numpy as np
from agents import Agent
from agents.voice import AudioInput, SingleAgentVoiceWorkflow, VoicePipeline

SAMPLE_RATE = 24000

agent = Agent(
    name="VoiceBot",
    instructions="你是语音助手，用中文，回答简洁。",
    model="gpt-6-luna",
)

pipeline = VoicePipeline(workflow=SingleAgentVoiceWorkflow(agent))


async def main():
    # 1. 录 3 秒
    print("录音 3 秒...")
    audio = sd.rec(int(3 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype=np.int16)
    sd.wait()

    # 2. 喂给 pipeline
    result = await pipeline.run(AudioInput(buffer=audio.flatten()))

    # 3. 流式播放回应
    print("回应:")
    with sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype=np.int16) as player:
        async for event in result.stream():
            if event.type == "voice_stream_event_audio":
                player.write(event.data)


asyncio.run(main())
```

---

## 4. 不连 OpenAI 也能验证：用测试替身

SDK 从 0.21 起提供不绑定 provider 的测试工具：`ScriptedSTTModel` 顶替转写，`ScriptedTTSModel` 顶替合成，Agent 仍然是真的（这里用千问）：

```python
import asyncio, numpy as np
from agents import Agent, function_tool
from agents.voice import (AudioInput, SingleAgentVoiceWorkflow, STTModelSettings, TTSModelSettings,
                          VoicePipeline, VoicePipelineConfig)
from agents.voice.testing import ScriptedSTTModel, ScriptedTTSModel, TTSResult, pcm16_samples


@function_tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city}今天 22°C 晴"


agent = Agent(name="VoiceBot", instructions="语音助手，用 get_weather 查天气，回答一句话",
              tools=[get_weather], model=MODEL)       # MODEL = 千问的 OpenAIChatCompletionsModel（见 01-basics/02）


async def main():
    stt = ScriptedSTTModel("北京天气怎么样")                       # “转写”出这句话
    tts = ScriptedTTSModel([TTSResult([pcm16_samples([0, 100, -100, 0])])])   # “合成”出一小段 PCM
    pipeline = VoicePipeline(
        workflow=SingleAgentVoiceWorkflow(agent),
        stt_model=stt, tts_model=tts,
        config=VoicePipelineConfig(
            tracing_disabled=True,
            stt_settings=STTModelSettings(language="zh"),
            tts_settings=TTSModelSettings(voice="marin", buffer_size=1),
        ),
    )
    result = await pipeline.run(AudioInput(np.zeros(2400, dtype=np.int16)))
    async for ev in result.stream():
        print(ev.type, getattr(ev, "event", ""))
    print("交给 TTS 的文本：", [c.text for c in tts.calls])


asyncio.run(main())
```

运行输出：

```
voice_stream_event_lifecycle turn_started
voice_stream_event_audio
voice_stream_event_lifecycle turn_ended
voice_stream_event_lifecycle session_ended
交给 TTS 的文本： ['北京今天天气晴朗，气温22°C。']
```

Agent 收到“北京天气怎么样”，调用 `get_weather`，生成回答，再交给 TTS。`stt.assert_complete()` / `tts.assert_complete()` 可以断言脚本里的步骤都被用掉了。如果只想测 STT/TTS 的生命周期、不测 Agent，换成 `ScriptedVoiceWorkflow`。

---

## 5. 自定义 STT / TTS provider

```python
from agents.voice import OpenAIVoiceModelProvider, VoicePipeline, VoicePipelineConfig

# 默认 OpenAI（STT / TTS 的具体模型名由 provider 决定；可在 pipeline 里用 stt_model= / tts_model= 传名字或自己的模型对象）
provider = OpenAIVoiceModelProvider()
pipeline = VoicePipeline(
    workflow=SingleAgentVoiceWorkflow(agent),
    config=VoicePipelineConfig(model_provider=provider),
)
```

`OpenAIVoiceModelProvider` 用它配置的 `AsyncOpenAI` 客户端发送非流式转写、TTS 请求和流式 STT 连接。想换成本地 Whisper 或 Coqui TTS，就实现 `STTModel` / `TTSModel` 接口（或 `VoiceModelProvider`），传给 `VoicePipeline(stt_model=..., tts_model=...)`。上面的 `ScriptedSTTModel` / `ScriptedTTSModel` 就是这个接口的实现。

---

## 6. 配置 STT / TTS 选项

```python
from agents.voice import STTModelSettings, TTSModelSettings, VoicePipelineConfig

config = VoicePipelineConfig(
    stt_settings=STTModelSettings(
        language="zh",
        prompt="一段关于产品 AC-42 的客服通话。",       # 给转写模型的上下文提示
        # 流式输入（StreamedAudioInput）专用：
        # languages=["zh", "en"], keywords=["AC-42"],   # gpt-transcribe / gpt-live-transcribe 支持多语言 + 关键词
        # turn_detection=...,
    ),
    tts_settings=TTSModelSettings(
        voice="marin",           # alloy / ash / ballad / coral / echo / fable / onyx / nova / sage / shimmer / verse / marin / cedar
        speed=1.0,
        instructions="语速适中，语气友好",
        buffer_size=120,         # 攒够多少采样再吐一个音频块
        dtype="int16",           # int16 或 float32
    ),
    tracing_disabled=False,
    trace_include_sensitive_audio_data=False,    # trace 里默认带 base64 音频，隐私场景关掉
    workflow_name="voice-assistant",
)

pipeline = VoicePipeline(workflow=SingleAgentVoiceWorkflow(agent), config=config)
```

有资格的组织还可以传自定义音色 `voice={"id": "voice_123abc"}`。

---

## 7. 流式音频输出

输出的音频流可以写到文件、直接播放、通过 WebSocket 推给前端，或者转推到 SIP 和电话系统。

```python
import wave


async def save_to_wav(result, path: str):
    chunks = []
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            chunks.append(event.data.tobytes())       # numpy int16 → bytes

    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(chunks))
```

---

## 8. 搭配完整的 Agent 能力

```python
# Tools + Handoffs 通通保留；handoff 的提示词建议用 prompt_with_handoff_instructions
from agents.extensions.handoff_prompt import prompt_with_handoff_instructions

@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


billing = Agent(name="Billing", handoff_description="账单", instructions=prompt_with_handoff_instructions("账单，口语化、简短"))
support = Agent(name="Support", handoff_description="技术", instructions=prompt_with_handoff_instructions("技术，口语化、简短"))


triage = Agent(
    name="Triage",
    instructions=prompt_with_handoff_instructions("语音客服分流。你在和真人说话，礼貌简洁。"),
    tools=[get_weather],
    handoffs=[billing, support],
)


pipeline = VoicePipeline(workflow=SingleAgentVoiceWorkflow(triage))
# 用户语音 → STT → triage → handoff → tool → TTS → 音频
```

整套 Agent 流程照常运行，只是输入输出换成了音频。要给工具或 hooks 传应用状态，用 `SingleAgentVoiceWorkflow(agent, context=VoiceContext(user_id="u1"))`。同一个 context 对象会传给之后每一轮转写触发的 run，它只留在你的应用里，不会发给模型。

---

## 9. Lifecycle Events

```python
async for event in result.stream():
    if event.type == "voice_stream_event_lifecycle":
        # "turn_started" / "turn_ended" / "session_ended"
        print(f"[{event.event}]")
    elif event.type == "voice_stream_event_audio":
        play(event.data)
    elif event.type == "voice_stream_event_error":
        log.error("voice error", e=event.error)
```

可以用这些事件在前端显示“思考中...”“播放中...”之类的状态。终端性的错误在你迭代 `result.stream()` 时抛出；转写会话在一次正常运行之后关闭失败，也会在这里抛出，如果这一轮已经失败过，则以最初的错误为准。

---

## 10. 跟前端集成（WebSocket）

```python
import numpy as np
from fastapi import FastAPI, WebSocket
from agents.voice import AudioInput, SingleAgentVoiceWorkflow, VoicePipeline


app = FastAPI()
pipeline = VoicePipeline(workflow=SingleAgentVoiceWorkflow(agent))


@app.websocket("/voice")
async def voice_ws(ws: WebSocket):
    await ws.accept()

    # 前端发一整段 PCM bytes（按键说话）
    audio_bytes = await ws.receive_bytes()
    audio_input = AudioInput(buffer=np.frombuffer(audio_bytes, dtype=np.int16))

    result = await pipeline.run(audio_input)

    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            await ws.send_bytes(event.data.tobytes())

    await ws.close()
```

想边说边识别，就用 `StreamedAudioInput`：前端持续发送音频块，后端 `await streamed_input.add_audio(chunk)`，流水线检测到用户说完一句后自动触发工作流。

---

## 11. 成本估算

Pipeline 一次完整对话的成本是 STT（按音频时长）、LLM（按 token）和 TTS（按字符或音频）三项之和。中间的 LLM 只处理文本 token，通常比 Realtime 按音频 token 计费便宜不少。具体价格会变，以 OpenAI 当前的价格页为准，这里不列单价。中间的 LLM 换成千问这类便宜模型，成本还能再降。

---

## 12. 跟 Pydantic AI 对比

Pydantic AI 没有内置的 Voice Pipeline，要自己把 STT API、agent 和 TTS 拼起来。OpenAI Agents 的 `VoicePipeline` 一行就把这些做了，活动检测、分句、流式合成都包含在内。

---

## 13. 完整 demo（带工具，测试替身版）

```python
import asyncio
import numpy as np
from agents import Agent, function_tool
from agents.voice import AudioInput, SingleAgentVoiceWorkflow, VoicePipeline, VoicePipelineConfig
from agents.voice.testing import ScriptedSTTModel, ScriptedTTSModel, TTSResult, pcm16_samples


@function_tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city}今天 22°C 晴"


agent = Agent(
    name="VoiceBot",
    instructions="语音助手，用 get_weather 查天气，回答简洁",
    tools=[get_weather],
    model="gpt-6-luna",      # 用千问：换成 OpenAIChatCompletionsModel（见 01-basics/02）
)

# 真实环境把 stt_model / tts_model 去掉即可（默认走 OpenAI）
stt = ScriptedSTTModel("北京天气怎么样")
tts = ScriptedTTSModel([TTSResult([pcm16_samples([0, 100, -100, 0])])])
pipeline = VoicePipeline(
    workflow=SingleAgentVoiceWorkflow(agent), stt_model=stt, tts_model=tts,
    config=VoicePipelineConfig(tracing_disabled=True),
)


async def main():
    result = await pipeline.run(AudioInput(np.zeros(2400, dtype=np.int16)))
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            print("收到音频块", event.data.shape, event.data.dtype)
    print("TTS 收到的文本：", [c.text for c in tts.calls])
    stt.assert_complete(); tts.assert_complete()


asyncio.run(main())
```

---

## 14. 下一步

- 实战：完整的语音助手 → [实战 3：语音助手（Realtime API）](../08-practice/03-voice-assistant.md)
- 部署 FastAPI WebSocket → [FastAPI / Lambda 部署](../06-integration/03-fastapi-deploy.md)
