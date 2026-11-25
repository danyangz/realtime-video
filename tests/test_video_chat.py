"""Orchestration tests with fake STT / LLM / TTS (no network)."""

import asyncio
import json
import struct

from apps.video_chat.session import Session
from core.config import Settings


class FakeTransport:
    def __init__(self):
        self.items = []  # ("json", dict) or ("audio", turn, bytes)

    async def send_text(self, data):
        self.items.append(("json", json.loads(data)))

    async def send_bytes(self, data):
        (turn,) = struct.unpack("<I", data[:4])
        self.items.append(("audio", turn, data[4:]))

    def events(self, type_=None):
        return [i[1] for i in self.items if i[0] == "json" and (type_ is None or i[1]["type"] == type_)]


class FakeLLM:
    label = "fake:llm"

    def __init__(self, reply="你好呀，很高兴见到你。今天想聊点什么呢？", first_delay=0.0, delay=0.005):
        self.reply = reply
        self.first_delay = first_delay
        self.delay = delay
        self.calls = []

    async def stream(self, messages):
        self.calls.append(messages)
        await asyncio.sleep(self.first_delay)
        for ch in self.reply:
            await asyncio.sleep(self.delay)
            yield ch


class FakeTTSStream:
    def __init__(self, per_piece_delay):
        self.q = asyncio.Queue()
        self.delay = per_piece_delay
        self.closed = False
        self.texts = []

    async def send_text(self, text):
        self.texts.append(text)
        await self.q.put(text)

    async def finish(self):
        await self.q.put(None)

    async def audio(self):
        while True:
            text = await self.q.get()
            if text is None:
                return
            for _ in range(3):
                await asyncio.sleep(self.delay)
                yield b"\x01\x00" * 240

    def close(self):
        self.closed = True


class FakeTTS:
    sample_rate = 24000

    def __init__(self, per_piece_delay=0.005):
        self.delay = per_piece_delay
        self.streams = []

    def warm(self):
        pass

    async def open(self):
        s = FakeTTSStream(self.delay)
        self.streams.append(s)
        return s


class FakeSTT:
    def __init__(self, on_event):
        self.on_event = on_event

    def push_audio(self, pcm):
        pass

    async def run(self):
        await asyncio.Event().wait()


async def wait_until(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.005)


def make(llm=None, tts=None):
    t = FakeTransport()
    s = Session(Settings(), t, llm or FakeLLM(), tts or FakeTTS(), stt_factory=FakeSTT)
    return s, t


def run(coro):
    asyncio.run(coro)


def test_basic_turn():
    async def main():
        s, t = make()
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "start"})
        s._on_stt_event("final", "你好")
        await wait_until(lambda: t.events("tts_end"))
        types = [e["type"] for e in t.events()]
        assert types.index("user_final") < types.index("audio_start") < types.index("tts_end")
        audio = [i for i in t.items if i[0] == "audio"]
        assert audio and all(i[1] == 1 for i in audio)
        assert t.events("metrics")[0]["total_ms"] is not None
        assert s.phase == "speaking"  # until browser says playback finished
        s.on_client_message({"type": "playback_done", "turn": 1})
        await wait_until(lambda: s.phase == "idle")
        assert [m["role"] for m in s.history] == ["user", "assistant"]
        assert s.history[1]["content"] == "你好呀，很高兴见到你。今天想聊点什么呢？"
        assert s.tts.streams[0].closed
        task.cancel()

    run(main())


def test_barge_in_stops_audio_and_keeps_partial_reply():
    async def main():
        llm = FakeLLM(reply="这是一个很长的回答，" * 20, delay=0.01)
        s, t = make(llm=llm)
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "start"})
        s._on_stt_event("final", "讲个故事")
        await wait_until(lambda: t.events("audio_start"))
        s._on_stt_event("partial", "等一下")
        await wait_until(lambda: t.events("interrupt"))
        await wait_until(lambda: s.phase == "idle")
        idx = next(i for i, it in enumerate(t.items) if it[0] == "json" and it[1]["type"] == "interrupt")
        await asyncio.sleep(0.1)
        assert not any(it[0] == "audio" for it in t.items[idx + 1 :]), "audio sent after interrupt"
        assert s.history[-1]["role"] == "assistant"
        assert s.history[-1]["content"].endswith("（被用户打断）")
        assert s.tts.streams[0].closed
        # The user's next sentence starts a fresh turn.
        s._on_stt_event("final", "等一下，我想换个话题")
        await wait_until(lambda: len(llm.calls) == 2)
        assert llm.calls[1][-1] == {"role": "user", "content": "等一下，我想换个话题"}
        task.cancel()

    run(main())


def test_user_pause_mid_sentence_merges_into_one_question():
    async def main():
        llm = FakeLLM(first_delay=0.3)
        s, t = make(llm=llm)
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "start"})
        s._on_stt_event("final", "我想问")
        await wait_until(lambda: s.phase == "thinking")
        s._on_stt_event("partial", "一个问题")  # still talking -> cancel the half-started reply
        await wait_until(lambda: s.phase == "idle")
        s._on_stt_event("final", "一个问题")
        await wait_until(lambda: t.events("tts_end"))
        assert llm.calls[-1][-1]["content"] == "我想问 一个问题"
        assert [m["content"] for m in s.history if m["role"] == "user"] == ["我想问 一个问题"]
        assert not any(i[0] == "audio" and i[1] == 1 for i in t.items)
        task.cancel()

    run(main())


def test_echo_of_own_voice_does_not_interrupt():
    async def main():
        llm = FakeLLM(reply="今天天气很好，我们可以去公园散步，顺便买杯咖啡。" * 3, delay=0.01)
        s, t = make(llm=llm)
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "start"})
        s._on_stt_event("final", "今天干嘛")
        await wait_until(lambda: t.events("audio_start"))
        s._on_stt_event("partial", "今天天气很好")
        # Echo can only contain words that were already generated (audio lags text).
        await wait_until(lambda: "公园" in s.reply_so_far)
        s._on_stt_event("final", "今天天气很好，我们可以去公园")
        await wait_until(lambda: t.events("tts_end"))
        assert not t.events("interrupt")
        assert len(llm.calls) == 1
        task.cancel()

    run(main())


def test_whisper_hallucination_is_ignored():
    async def main():
        llm = FakeLLM()
        s, t = make(llm=llm)
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "start"})
        s._on_stt_event("final", "谢谢观看")
        s._on_stt_event("final", "  ")
        await asyncio.sleep(0.1)
        assert not llm.calls
        task.cancel()

    run(main())


def test_typed_text_without_call():
    async def main():
        llm = FakeLLM()
        s, t = make(llm=llm)
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "text", "text": "打字也可以吗"})
        await wait_until(lambda: t.events("tts_end"))
        assert t.events("user_final")[0]["typed"] is True
        task.cancel()

    run(main())


def test_llm_error_reports_and_recovers():
    class BrokenLLM(FakeLLM):
        async def stream(self, messages):
            self.calls.append(messages)
            raise RuntimeError("boom")
            yield  # pragma: no cover

    async def main():
        llm = BrokenLLM()
        s, t = make(llm=llm)
        task = asyncio.create_task(s.run())
        s.on_client_message({"type": "start"})
        s._on_stt_event("final", "你好")
        await wait_until(lambda: t.events("error"))
        await wait_until(lambda: s.phase == "idle")
        assert s.pending_user == ""
        task.cancel()

    run(main())
