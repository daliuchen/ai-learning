# 实战 3：语音助手（Realtime API）

> **一句话**：用 `RealtimeAgent` + 麦克风 / 扬声器流，做一个能听能说能调 tool 的助手——可用作智能家居控制台、车机助手、客服电话。

---

## 1. 需求

- 用户对着麦克风说话
- 助手实时回应（语音播放）
- 能调天气 / 日历 / 闹钟工具
- 多种语言（中英）
- 可打断（用户开口模型停说）

---

## 2. 架构

```
[Mic 麦克风]
   ↓ PCM 流
[RealtimeSession]
   ↓
[RealtimeAgent: gpt-4o-realtime-preview]
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

## 4. Agent

```python
from agents.realtime import RealtimeAgent


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
    voice="alloy",  # 也可以 nova / shimmer / echo
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
        sd.play(data, samplerate=SAMPLE_RATE)
```

---

## 6. 主循环

```python
from agents.realtime import RealtimeRunner


async def main():
    runner = RealtimeRunner(starting_agent=assistant)
    audio = AudioIO()

    async with await runner.run() as session:
        loop = asyncio.get_event_loop()
        audio.start_mic(loop)
        print("🎙️ 说话吧（Ctrl+C 退出）...")

        # 任务 A：从 mic queue 推到 session
        async def mic_to_session():
            while True:
                pcm = await audio.mic_queue.get()
                await session.send_audio(pcm)

        mic_task = asyncio.create_task(mic_to_session())

        # 任务 B：从 session 读 event，处理音频 / 文本
        try:
            async for event in session:
                if event.type == "audio":
                    audio.play(event.audio)
                elif event.type == "response.text.delta":
                    print(event.delta, end="", flush=True)
                elif event.type == "tool_call":
                    print(f"\n[Tool] {event.tool_name}({event.args})")
                elif event.type == "response.done":
                    print()
        finally:
            mic_task.cancel()
            audio.stop_mic()


if __name__ == "__main__":
    asyncio.run(main())
```

⚠️ 真实代码中需要：

- VAD / 打断处理
- 错误重连
- 网络断了的优雅降级

---

## 7. 简化版（不带麦克风，只演示）

```python
# demos/practice/03_voice_simple.py
import asyncio
from agents.realtime import RealtimeAgent, RealtimeRunner
from agents import function_tool


@function_tool
def get_weather(city: str) -> str:
    return f"{city} 今天 22 度 晴天"


assistant = RealtimeAgent(
    name="VoiceAssistant",
    instructions="语音助手，中文回答，简短。",
    tools=[get_weather],
    voice="alloy",
)


async def main():
    runner = RealtimeRunner(starting_agent=assistant)

    async with await runner.run() as session:
        # 用文本模拟输入
        await session.send_message("北京天气怎么样")

        async for event in session:
            if event.type == "response.text.delta":
                print(event.delta, end="", flush=True)
            elif event.type == "response.done":
                print()
                break


asyncio.run(main())
```

---

## 8. 跟 Voice Pipeline 切换

如果不需要打断、要省钱 → 切 Pipeline：

```python
from agents import Agent
from agents.voice import VoicePipeline


# 普通 Agent 即可
assistant = Agent(
    name="VoiceAssistant",
    instructions="...",
    tools=[get_weather, get_calendar, set_alarm],
    model="gpt-4o-mini",
)


pipeline = VoicePipeline(workflow=assistant)
```

详见 [05-advanced/06-voice-pipeline.md](../05-advanced/06-voice-pipeline.md)。

---

## 9. 部署：WebRTC + 浏览器

前端用 WebRTC 收麦克风音频，转 PCM 推给后端 WebSocket。

```python
@app.websocket("/voice")
async def voice_ws(ws: WebSocket):
    await ws.accept()
    runner = RealtimeRunner(starting_agent=assistant)

    async with await runner.run() as session:
        async def ws_to_session():
            while True:
                pcm = await ws.receive_bytes()
                await session.send_audio(pcm)

        async def session_to_ws():
            async for event in session:
                if event.type == "audio":
                    await ws.send_bytes(event.audio)
                elif event.type == "response.text.delta":
                    await ws.send_json({"type": "text", "delta": event.delta})

        await asyncio.gather(ws_to_session(), session_to_ws())
```

---

## 10. 评测：测听得懂 / 调对工具

```python
test_cases = [
    {
        "audio_file": "test_audio/beijing_weather.wav",
        "expected_tool": "get_weather",
        "expected_args": {"city": "北京"},
    },
    {
        "audio_file": "test_audio/set_alarm.wav",
        "expected_tool": "set_alarm",
    },
]


for case in test_cases:
    runner = RealtimeRunner(starting_agent=assistant)
    async with await runner.run() as session:
        # 喂音频
        with open(case["audio_file"], "rb") as f:
            await session.send_audio(f.read())

        # 收集 tool calls
        tool_calls = []
        async for event in session:
            if event.type == "tool_call":
                tool_calls.append({"name": event.tool_name, "args": event.args})
            if event.type == "response.done":
                break

        # 检查
        passed = any(
            tc["name"] == case["expected_tool"]
            for tc in tool_calls
        )
        print(f"{'✅' if passed else '❌'} {case['audio_file']}")
```

---

## 11. 成本

Realtime API 按音频时长计费，大致：

- $0.06 / 分钟（输入）
- $0.24 / 分钟（输出）

10 分钟对话约 $3。生产里**必须**有用量监控、超限熔断。

---

## 12. 注意事项

- **网络要稳**：Realtime 是 WebSocket，断了体验差
- **延迟**：本地音频 buffer 大小影响，调 100-200ms 一帧
- **隐私**：音频流过 OpenAI，敏感场景考虑 Voice Pipeline + 本地 STT
- **错误恢复**：session 断了自动重连
- **多人语音**：Realtime 不擅长，考虑分音轨

---

## 13. 下一步

- 📖 Computer Use Agent → [04-computer-use.md](./04-computer-use.md)
- 📖 横向对比 → [05-vs-others.md](./05-vs-others.md)
- 📖 Voice Pipeline → [05-advanced/06-voice-pipeline.md](../05-advanced/06-voice-pipeline.md)
