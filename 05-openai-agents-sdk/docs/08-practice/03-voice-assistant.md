# 实战 3：语音助手（Realtime API）

> 这个实战用 `RealtimeAgent` 加上麦克风和扬声器的音频流，做一个能听、能说、能调工具的助手，智能家居控制台、车机助手、电话客服都是类似的结构。
>
> 真实的语音对话要用 OpenAI API key 连 Realtime 服务。工具调用和事件流不连服务也能验证，第 7、10 节用 `ScriptedRealtimeModel` 测试替身顶替 OpenAI 那一端。代码按 0.24 的写法：音色不再写在 `RealtimeAgent(voice=...)` 上，事件也不再是 `response.text.delta` 这类原始名字。

---

## 1. 需求

- 用户对着麦克风说话
- 助手实时用语音回应
- 能调天气、日历、闹钟工具
- 支持中英文
- 可打断：用户一开口，模型就停下

---

## 2. 架构

```
[Mic 麦克风]
   ↓ PCM 流
[RealtimeSession]
   ↓
[RealtimeAgent（会话模型 gpt-realtime-2.1）]
   ├─ Tool: get_weather
   ├─ Tool: get_calendar
   └─ Tool: set_alarm
   ↓ PCM 流
[Speaker 扬声器]
```

---

## 3. Tools

```python
from agents import function_tool


@function_tool
def get_weather(city: str) -> str:
    """查天气"""
    fake = {"北京": "23°C 晴", "上海": "26°C 多云", "深圳": "28°C 雨"}
    return fake.get(city, f"{city}: 暂无数据")


@function_tool
def get_calendar(date: str) -> str:
    """查日历安排。date 格式 YYYY-MM-DD"""
    return f"{date}: 上午 10:00 团队会议; 下午 3:00 客户演示"


@function_tool
def set_alarm(time_str: str, label: str = "") -> str:
    """设闹钟。time_str 格式 HH:MM"""
    print(f"\n[ALARM SET] {time_str} - {label}")
    return f"已设 {time_str} 闹钟"
```

---

## 4. Agent 与会话配置

模型、音色、转写、打断策略都配置在 `RealtimeRunner(config=...)` 上，`RealtimeAgent` 本身不管这些：

```python
from agents.realtime import RealtimeAgent, RealtimeRunner


assistant = RealtimeAgent(
    name="VoiceAssistant",
    instructions="""你是语音助手，跟用户中文对话。

规则：
- 简短（10-30 字一句）
- 不要列表 / 不要 markdown
- 不会的礼貌说不会
- 涉及天气 / 日历 / 闹钟 → 用对应工具

工具用法：
- 用户问天气 → get_weather
- 用户问安排 → get_calendar
- 用户要设闹钟 → set_alarm

回应自然，像跟朋友聊天。
""",
    tools=[get_weather, get_calendar, set_alarm],
)

runner = RealtimeRunner(
    starting_agent=assistant,
    config={
        "model_settings": {
            "model_name": "gpt-realtime-2.1",
            "audio": {
                "input": {
                    "format": "pcm16",
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {"type": "semantic_vad", "interrupt_response": True},   # 用户开口自动打断
                },
                "output": {"format": "pcm16", "voice": "ash"},     # 音色：会话里一旦说过话就不能再改
            },
        }
    },
)
```

---

## 5. 麦克风 / 扬声器 IO

```python
import asyncio
import sounddevice as sd
import numpy as np


SAMPLE_RATE = 24000
CHANNELS = 1
DTYPE = np.int16


class AudioIO:
    def __init__(self):
        self.mic_queue: asyncio.Queue = asyncio.Queue()
        self.stream_in = None
        self.stream_out = None

    def start_mic(self, loop):
        def callback(indata, frames, time_info, status):
            pcm = indata.astype(DTYPE).tobytes()
            asyncio.run_coroutine_threadsafe(self.mic_queue.put(pcm), loop)

        self.stream_in = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=callback,
        )
        self.stream_in.start()

    def stop_mic(self):
        if self.stream_in:
            self.stream_in.stop()
            self.stream_in.close()

    def play(self, pcm_bytes: bytes):
        data = np.frombuffer(pcm_bytes, dtype=DTYPE).astype(np.float32) / 32767
        sd.play(data, samplerate=SAMPLE_RATE)     # 演示用；每个音频块都 sd.play 会截断上一块，真实代码请用 sd.OutputStream 连续 write

    def stop_playback(self):
        sd.stop()                                 # 用户打断时清掉正在播的音频
```

---

## 6. 主循环（需要 OpenAI key 和声卡）

```python
async def main():
    audio = AudioIO()
    session = await runner.run()                 # 返回 RealtimeSession

    async with session:                          # 进入时才建立 WebSocket 连接
        loop = asyncio.get_running_loop()
        audio.start_mic(loop)
        print("🎙️ 说话吧（Ctrl+C 退出）...")

        # 任务 A：从 mic queue 推到 session
        async def mic_to_session():
            while True:
                pcm = await audio.mic_queue.get()
                await session.send_audio(pcm)

        mic_task = asyncio.create_task(mic_to_session())

        # 任务 B：从 session 读事件，处理音频 / 工具 / 历史
        try:
            async for event in session:
                if event.type == "audio":
                    audio.play(event.audio.data)               # PCM 字节
                elif event.type == "audio_interrupted":
                    audio.stop_playback()                      # 用户打断：清空播放缓冲（自己实现）
                elif event.type == "tool_start":
                    print(f"\n[Tool] {event.tool.name}({event.arguments})")
                elif event.type == "tool_end":
                    print(f"[Tool result] {event.output}")
                elif event.type == "history_added":
                    print(event.item)                          # 含转写后的用户话和助手回复
                elif event.type == "error":
                    print("error:", event.error)
        finally:
            mic_task.cancel()
            audio.stop_mic()


if __name__ == "__main__":
    asyncio.run(main())
```

真实项目里还要补几件事。打断时要清空播放缓冲；电话或远程播放的场景，用 `RealtimePlaybackTracker` 告诉服务端用户实际听到了多少，服务端才能把被打断的回复截在正确的位置。网络断了要能重连或优雅降级。用量也要监控，数据在 `raw_model_event` 里的 `RealtimeModelUsageEvent`。

---

## 7. 简化版（不带麦克风）：用测试替身跑通

没有真实连接，也能验证工具编排和事件流。做法是用 `ScriptedRealtimeModel` 顶替 OpenAI 那一端：它「说」要调工具，SDK 就真的去执行工具：

```python
import asyncio
from agents.decorators import tool
from agents.realtime import (RealtimeAgent, RealtimeModelSendToolOutput, RealtimeModelToolCallEvent, RealtimeRunner)
from agents.realtime.testing import RealtimeStep, ScriptedRealtimeModel


@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city}: 22°C"


assistant = RealtimeAgent(name="VoiceAssistant", instructions="语音助手，中文回答，简短。", tools=[get_weather])


async def main():
    call = RealtimeModelToolCallEvent(name="get_weather", call_id="call_1", arguments='{"city":"北京"}')
    model = ScriptedRealtimeModel(
        [RealtimeStep(expect=lambda ev: isinstance(ev, RealtimeModelSendToolOutput))],   # 期望 SDK 把工具结果回送给模型
        connect_events=[call],
    )
    runner = RealtimeRunner(assistant, model=model, config={"async_tool_calls": False, "tracing_disabled": True})

    async with await runner.run() as session:
        async for event in session:
            print(event.type, getattr(event, "tool", ""), getattr(event, "output", ""))
            if event.type == "tool_end":
                break
    model.assert_complete()


asyncio.run(main())
```

运行输出：

```
raw_model_event
tool_start <agent tool object> ...
tool_end ... 北京: 22°C
```

换成真实的 OpenAI 连接时，去掉 `model=model`，按第 4 节配置 `model_settings`，然后 `await session.send_message("北京天气怎么样")`。

---

## 8. 跟 Voice Pipeline 切换

如果用不着打断，又想省钱，可以换成 Voice Pipeline：

```python
from agents import Agent
from agents.voice import SingleAgentVoiceWorkflow, VoicePipeline


# 普通 Agent 即可（模型可以是任意：OpenAI / 千问 / …）
assistant = Agent(
    name="VoiceAssistant",
    instructions="...",
    tools=[get_weather, get_calendar, set_alarm],
    model="gpt-6-luna",
)


pipeline = VoicePipeline(workflow=SingleAgentVoiceWorkflow(assistant))     # workflow 必须是 VoiceWorkflowBase，不能直接传 Agent
```

详见 [Voice Pipeline：STT + LLM + TTS](../05-advanced/06-voice-pipeline.md)。

---

## 9. 部署：浏览器怎么接

官方明确说了，Python SDK 没有浏览器 WebRTC 传输，Python 这边只有服务端 WebSocket 和 SIP。常见的接法有三种。

第一种是浏览器经过你的服务器转发：前端用 Web Audio / AudioWorklet 采集 24kHz 的 PCM，推给后端的 WebSocket；后端用 `session.send_audio(...)` 转给 OpenAI，再把 `audio` 事件推回前端：

```python
@app.websocket("/voice")
async def voice_ws(ws: WebSocket):
    await ws.accept()
    runner = RealtimeRunner(starting_agent=assistant, config=CONFIG)
    session = await runner.run()

    async with session:
        async def ws_to_session():
            while True:
                pcm = await ws.receive_bytes()
                await session.send_audio(pcm)

        async def session_to_ws():
            async for event in session:
                if event.type == "audio":
                    await ws.send_bytes(event.audio.data)
                elif event.type == "audio_interrupted":
                    await ws.send_json({"type": "interrupted"})          # 通知前端清播放队列
                elif event.type == "history_added":
                    await ws.send_json({"type": "history", "item": str(event.item)})
                elif event.type == "tool_approval_required":
                    await ws.send_json({"type": "approval", "call_id": event.call_id})   # 前端点同意后调 session.approve_tool_call(call_id)

        await asyncio.gather(ws_to_session(), session_to_ws())
```

第二种是让浏览器直连 OpenAI，延迟更低：用 JS SDK 的 WebRTC，Python 只负责签发临时 key 和提供服务端工具。

第三种是电话：用 SIP attach（`model_config={"call_id": ...}`，参考官方 `examples/realtime/twilio_sip`）；Twilio 这类走 WebSocket 的场景要配 `RealtimePlaybackTracker`。

---

## 10. 评测：测调对工具 / 事件处理

要测「听不听得懂」，得用真音频加真模型，花钱而且结果不稳定。能零成本测的是编排层：假设模型已经发出了某个工具调用，检查 agent 有没有执行对的工具，参数校验、审批、事件转发是否正确。这部分用 `ScriptedRealtimeModel` 就行（见第 7 节）：

```python
async def run_case(tool_name: str, arguments: str) -> list[str]:
    call = RealtimeModelToolCallEvent(name=tool_name, call_id="c1", arguments=arguments)
    model = ScriptedRealtimeModel(
        [RealtimeStep(expect=lambda ev: isinstance(ev, RealtimeModelSendToolOutput))], connect_events=[call],
    )
    runner = RealtimeRunner(assistant, model=model, config={"async_tool_calls": False, "tracing_disabled": True})
    seen = []
    async with await runner.run() as session:
        async for event in session:
            seen.append(event.type)
            if event.type == "tool_end":
                break
    model.assert_complete()
    return seen
```

要评估模型是不是真听对了、选对了工具，就得开真实的 Realtime 会话：用 `session.send_audio(pcm, commit=True)` 喂预先录好的音频（关掉自动轮次检测时需要 `commit=True`），收集 `tool_start` 事件里的 `event.tool.name` / `event.arguments`，和期望值对比。这类评测成本不低，抽样跑就好。

---

## 11. 成本

Realtime API 按音频计费（token 或时长），具体价格以 OpenAI 当前的价格页为准，模型换代时单价变化很大，这里不列数字。总体来说，它比纯文本和 Voice Pipeline 贵得多。**上生产必须有用量监控和超限熔断**，用量来自 `RealtimeModelUsageEvent`，累计值在 `event.info.context.usage`。

---

## 12. 注意事项

- 网络：Realtime 走 WebSocket，断线体验很差，网络要稳
- 延迟：受本地音频 buffer 大小影响，buffer 越大延迟越高，按设备尽量调小
- 隐私：音频会经过 OpenAI，敏感场景考虑 Voice Pipeline + 本地 STT
- 错误恢复：session 断了要自动重连
- 多人语音：Realtime 不擅长，考虑分音轨

---

## 13. 下一步

- Computer Use Agent → [实战 4：Computer Use Agent（操作浏览器）](./04-computer-use.md)
- 横向对比 → [横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph](./05-vs-others.md)
- Voice Pipeline → [Voice Pipeline：STT + LLM + TTS](../05-advanced/06-voice-pipeline.md)
