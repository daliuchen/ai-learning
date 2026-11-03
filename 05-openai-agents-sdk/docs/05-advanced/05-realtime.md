# Realtime Agent：实时语音 API

> `RealtimeAgent` 加 `RealtimeRunner` 走的是 OpenAI Realtime API，模型直接处理输入输出的音频流，可以用来做电话助手、语音聊天机器人、AI 主播。
>
> Realtime 要用 OpenAI API key 连 Realtime 服务。文中配置以 0.24 为准；“文本收发”和“工具调用”两个流程不连服务也能跑，用 SDK 自带的测试替身 `ScriptedRealtimeModel`（在 `agents.realtime.testing` 里）顶替模型。这只能验证 SDK 的会话和工具编排，真实模型会怎么回答还得接上服务才知道。

---

## 1. Realtime API 是什么

OpenAI 的 Realtime API 输入是音频流，可以附带文本；输出是音频流、文本和 tool call。它是全双工的，能边听边说，用户中途打断也没问题。SDK 0.24 的默认模型是 `gpt-realtime-2.1`（0.3 起 SDK 从 beta 的 `gpt-4o-realtime-preview` 迁到了 GA 接口，之后默认模型又随版本换过几次）。

它和 Voice Pipeline 的区别在于：Voice Pipeline 是 STT → LLM → TTS 三段式，Realtime 让 LLM 直接处理音频流，少了一跳，延迟更低。详见 [Voice Pipeline：STT + LLM + TTS](./06-voice-pipeline.md)。

Python SDK 的能力边界（官方说明）：它只提供服务端的 WebSocket 传输和 SIP 接入（电话），没有浏览器端的 WebRTC 传输。要做浏览器语音，前端用 JS SDK 或 WebRTC 直连，Python 这边负责服务端编排、工具、审批和电话集成。

---

## 2. 安装

```bash
pip install openai-agents        # Realtime 在核心包里，WebSocket 依赖会一并装好
pip install sounddevice numpy    # 只有做本地麦克风 / 扬声器 demo 才需要；Python >= 3.10
```

`pip install "openai-agents[voice]"` 会额外装上 Voice Pipeline 需要的 `numpy` 等依赖。

---

## 3. 最简 RealtimeAgent

`RealtimeAgent` 有意做得比普通 `Agent` 窄。模型、音色这些配置放在会话（runner）级别，不在 agent 上；它不支持结构化输出；instructions、function tools、handoffs、hooks、output guardrails 都可以用。

```python
import asyncio
from agents.realtime import RealtimeAgent, RealtimeRunner

agent = RealtimeAgent(
    name="Assistant",
    instructions="你是友好的语音助手，简短回答。",
)

runner = RealtimeRunner(
    starting_agent=agent,
    config={
        "model_settings": {
            "model_name": "gpt-realtime-2.1",
            "audio": {
                "input": {
                    "format": "pcm16",
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {"type": "semantic_vad", "interrupt_response": True},
                },
                "output": {"format": "pcm16", "voice": "ash"},
            },
        }
    },
)


async def main():
    session = await runner.run()           # 返回 RealtimeSession；进入 async with 时才真正建立连接

    async with session:
        await session.send_message("用一句话打个招呼")

        async for event in session:
            if event.type == "audio":
                pass                       # event.audio.data 是 PCM 字节：转发给扬声器 / 前端
            elif event.type == "history_added":
                print(event.item)
            elif event.type == "agent_end":
                break                      # 助手一轮结束
            elif event.type == "error":
                print("Error:", event.error)


asyncio.run(main())
```

> 0.24 里没有 `RealtimeAgent(model=..., voice=...)` 这种写法，事件也不再按 `event.type == "response.text.delta"`、`"response.done"`、`event.audio` 来判断。模型和音色走 `config["model_settings"]`；高层事件名是 `audio` / `audio_end` / `audio_interrupted` / `agent_start` / `agent_end` / `tool_start` / `tool_end` / `tool_approval_required` / `handoff` / `history_added` / `history_updated` / `guardrail_tripped` / `error` / `raw_model_event`，需要原始事件时从 `raw_model_event` 里取。

### 认证和连接选项

```python
session = await runner.run(model_config={"api_key": "sk-..."})   # 不传则读 OPENAI_API_KEY
```

`model_config` 还支持 `url`（自定义 WebSocket 端点）、`headers`、`call_id`（接入一个已有的 realtime 通话，目前文档里写的是 SIP）和 `playback_tracker`。传了 `headers` 之后，SDK 不再自动注入 `Authorization`。连 Azure OpenAI 要用 GA 的 Realtime 端点 URL，并自己传 headers，别用旧的 beta 路径。

---

## 4. 带麦克风的完整 demo（需要 OpenAI key 和声卡）

官方 `examples/realtime` 里有完整的麦克风和扬声器代码，下面是结构示意：

```python
import asyncio
import numpy as np
import sounddevice as sd
from agents.realtime import RealtimeAgent, RealtimeRunner

SAMPLE_RATE = 24000

agent = RealtimeAgent(name="VoiceBot", instructions="你是语音助手，用中文，回答简洁。")
runner = RealtimeRunner(
    starting_agent=agent,
    config={"model_settings": {
        "model_name": "gpt-realtime-2.1",
        "audio": {"input": {"format": "pcm16", "turn_detection": {"type": "semantic_vad", "interrupt_response": True}},
                  "output": {"format": "pcm16", "voice": "ash"}},
    }},
)


async def main():
    session = await runner.run()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def callback(indata, frames, time_info, status):            # 音频线程里回调：用线程安全的方式入队
        loop.call_soon_threadsafe(queue.put_nowait, indata.astype(np.int16).tobytes())

    async def mic_to_session():
        while True:
            await session.send_audio(await queue.get())

    async with session:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
            mic_task = asyncio.create_task(mic_to_session())
            out = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            out.start()
            try:
                async for event in session:
                    if event.type == "audio":
                        out.write(event.audio.data)
                    elif event.type == "audio_interrupted":
                        pass                                    # 用户打断：这里应清空播放缓冲
                    elif event.type == "history_added":
                        print(event.item)
            finally:
                mic_task.cancel()
                out.stop()


asyncio.run(main())
```

这是简化版。真要上线，还得处理打断时清空播放缓冲（远程或电话场景用 `RealtimePlaybackTracker` 报告用户实际听到的位置，服务端才能把被打断的回复截在正确的地方）、断线重连和背压。

---

## 5. RealtimeAgent 跟普通 Agent 的区别

| | Agent | RealtimeAgent |
|---|---|---|
| 输入 | 文本 / messages | 音频流 + 文本 / 图片（`send_message` 的结构化消息） |
| 输出 | final_output | 流式事件（音频 + 文本 + 历史项） |
| Tools | function_tool | function_tool（一样，含 `needs_approval`） |
| Handoffs | 支持 | 支持（`realtime_handoff(...)`，不支持 `input_filter`） |
| Guardrails | input + output + tool | 只有 output（对累积文本去抖后检查，触发时发 `guardrail_tripped` 事件，不抛异常）+ 函数工具的 input guardrails |
| 结构化输出 | 支持 | 不支持 |
| Runner | `Runner.run` | `RealtimeRunner(...).run()` → `RealtimeSession` |
| 模型 | 任意 | 在会话级配置（`model_name`），默认 `gpt-realtime-2.1` |

---

## 6. 带 Tool 的 Realtime

```python
from agents.decorators import tool      # 或 from agents import function_tool，等价
from agents.realtime import RealtimeAgent


@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city}: 22°C 晴"


agent = RealtimeAgent(
    name="VoiceWeatherBot",
    instructions="用户问天气时用 get_weather 工具",
    tools=[get_weather],
)
```

模型听到“北京天气”，调用 get_weather，再根据结果生成语音回答。工具在会话里后台执行，`config={"async_tool_calls": ...}` 控制是否异步，默认是异步，免得卡住音频流。

下面用测试替身验证 SDK 的工具编排，不连接任何服务：

```python
import asyncio
from agents.realtime import RealtimeModelSendToolOutput, RealtimeModelToolCallEvent, RealtimeRunner
from agents.realtime.testing import RealtimeStep, ScriptedRealtimeModel


async def main():
    call = RealtimeModelToolCallEvent(name="get_weather", call_id="call_1", arguments='{"city":"北京"}')

    def tool_output_ok(ev) -> bool:      # 期望 SDK 把工具结果回送给模型
        return (isinstance(ev, RealtimeModelSendToolOutput)
                and ev.tool_call.call_id == "call_1" and ev.output == "北京: 22°C 晴")

    model = ScriptedRealtimeModel([RealtimeStep(expect=tool_output_ok)], connect_events=[call])
    runner = RealtimeRunner(agent, model=model, config={"async_tool_calls": False, "tracing_disabled": True})
    async with await runner.run():
        pass
    model.assert_complete()              # 真实的工具查找、参数校验、执行、输出回送都走了
    print("tool workflow ok")

asyncio.run(main())
```

### 工具审批

函数工具可以设 `needs_approval`。会话会发出 `tool_approval_required` 并暂停，你调用 `await session.approve_tool_call(event.call_id)` 或 `reject_tool_call(...)` 来处理：

```python
async for event in session:
    if event.type == "tool_approval_required":
        await session.approve_tool_call(event.call_id)
```

---

## 7. Realtime 里的 Handoffs

```python
from agents.realtime import RealtimeAgent, realtime_handoff

billing = RealtimeAgent(name="Billing", instructions="账单")
support = RealtimeAgent(name="Support", instructions="技术")

triage = RealtimeAgent(
    name="Triage",
    instructions="分流",
    handoffs=[billing, realtime_handoff(support, tool_description_override="转技术支持")],
)
```

直接放一个 `RealtimeAgent` 进去，会被自动包成 handoff；用 `realtime_handoff(...)` 可以自定义名字、描述、校验、回调和可用性。模型决定转交时，会切换 active agent，但仍在同一个语音会话里。会话里一旦已经播出过音频，音色就**不能再改**了，所以各个专家最好用同一个音色。

---

## 8. 打断、VAD 和转写

打断由 Realtime API 内置的 VAD 处理：用户一开口，模型自动停下，会话发出 `audio_interrupted` 并更新历史，让服务端的对话记录和用户实际听到的内容保持一致。

轮次检测在 `audio.input.turn_detection` 里配置，可以是 `{"type": "semantic_vad", "interrupt_response": True}`（语义 VAD）或 `server_vad`。设成 `None` 就关掉自动检测，由你自己用 `await session.send_audio(bytes, commit=True)` 标记一轮结束，需要时再通过 `session.model.send_event(RealtimeModelSendRawMessage(message={"type": "response.create"}))` 让模型开口。

输入转写在 `audio.input.transcription` 里配置。0.20 起支持 `gpt-transcribe`、`gpt-live-transcribe`、`gpt-realtime-whisper`：要低延迟的增量转写用 `gpt-live-transcribe`（可以带 `prompt` / `keywords` / `languages`），提交一段音频后再整体转写用 `gpt-transcribe`。降噪在 `audio.input.noise_reduction`。

---

## 9. 模型选项

```python
config = {
    "model_settings": {
        "model_name": "gpt-realtime-2.1",
        "audio": {
            "input":  {"format": "pcm16", "transcription": {...}, "noise_reduction": {...}, "turn_detection": {...}},
            "output": {"format": "pcm16", "voice": "ash", "speed": 1.0},
        },
        "output_modalities": ["audio"],
        "tool_choice": "auto",
        "prompt": ...,                  # 引用平台托管提示词
        "tracing": ...,
    },
    # runner 级：
    "async_tool_calls": True,
    "output_guardrails": [...],
    "guardrails_settings": {"debounce_text_length": 100},
    "tool_error_formatter": ...,
    "tracing_disabled": False,
}
```

早先的平铺写法（`input_audio_format`、`turn_detection` 等）还能用，新代码建议用嵌套的 `audio.input` / `audio.output`。完整 schema 见 `RealtimeRunConfig` 和 `RealtimeSessionModelSettings`。

关于用量：响应带 usage 时，会话会发出 `raw_model_event`（`RealtimeModelUsageEvent`），累计值在 `event.info.context.usage` 里，比如可以在 `agent_end` 事件里读。

---

## 10. 适合和不适合的场景

适合电话客服（SIP 接听、转人工）、类似 Siri 的语音助手、AI 主播或解说、视频会议实时翻译。

不适合离线批处理（用普通 Agent 就行）和需要结构化输出的场景（Realtime 不支持）。非 OpenAI 模型也用不了，只有 OpenAI 提供 Realtime API，Python 端也没有第三方 Realtime 适配。

---

## 11. 成本

Realtime 比文本贵得多，按音频 token 和时长计费，具体以 OpenAI 当前的价格页为准。开发时省着点用；生产环境要做用量监控（用上面的 usage 事件），并设好超限熔断。

---

## 12. 完整 demo（测试替身版，最小可运行）

这个例子没有 OpenAI key 也能跑，用 `ScriptedRealtimeModel` 代替真实连接，验证你的事件处理逻辑：

```python
import asyncio
from agents.realtime import (
    RealtimeAgent,
    RealtimeModelOutputTextDeltaEvent,
    RealtimeModelSendUserInput,
    RealtimeRawModelEvent,
    RealtimeRunner,
)
from agents.realtime.testing import RealtimeStep, ScriptedRealtimeModel


async def main():
    reply = RealtimeModelOutputTextDeltaEvent(item_id="item_1", delta="你好！我是语音助手。", response_id="response_1")
    model = ScriptedRealtimeModel(
        [RealtimeStep(expect=RealtimeModelSendUserInput(user_input="你好"), emit=[reply])]
    )
    runner = RealtimeRunner(
        RealtimeAgent(name="VoiceBot", instructions="你是友好语音助手，回答简洁。"),
        model=model,
        config={"tracing_disabled": True},
    )

    async with await runner.run() as session:
        await session.send_message("你好")
        async for event in session:
            print(f"[event] {event.type}")
            if isinstance(event, RealtimeRawModelEvent) and event.data == reply:
                break

    model.assert_complete()


asyncio.run(main())
```

实际输出是 `[event] history_updated` 和 `[event] raw_model_event`。接真实 Realtime 时，去掉 `model=model`，再按第 3 节配置 `model_settings` 就行。

---

## 13. 下一步

- 三段式的做法 → [Voice Pipeline：STT + LLM + TTS](./06-voice-pipeline.md)
- 实战：完整的语音助手 → [实战 3：语音助手（Realtime API）](../08-practice/03-voice-assistant.md)
- 官方 Realtime 指南（SIP、手动轮次、审批）→ https://openai.github.io/openai-agents-python/realtime/guide/
