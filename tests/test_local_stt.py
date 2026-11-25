"""Endpointing logic of LocalSTT with a fake VAD and a fake ASR engine (no models needed)."""

import asyncio

import numpy as np

from core.config import Settings
from core.local_stt import FRAME, LocalSTT

FRAME_S = FRAME / 16000


class FakeVAD:
    """Loud frames are speech."""

    def __call__(self, frame):
        return 0.95 if np.abs(frame).max() > 0.1 else 0.02


class FakeEngine:
    ready = True

    def __init__(self, delay=0.01):
        self.delay = delay
        self.pending = 0
        self.calls = []  # (partial, seconds of audio, context)

    async def wait_ready(self):
        pass

    async def transcribe(self, audio, *, partial, context=""):
        self.pending += 1
        try:
            self.calls.append((partial, round(len(audio) / 16000, 2), context))
            await asyncio.sleep(self.delay)
            return f"{'partial' if partial else 'final'}:{len(audio) // FRAME}"
        finally:
            self.pending -= 1


def settings(**kw):
    base = dict(stt_engine="local", vad_min_silence_ms=600, vad_eager_ms=250, vad_min_speech_ms=250,
                vad_max_speech_s=30.0, vad_speech_pad_ms=250, asr_partial_interval_ms=300, asr_hotwords="小星")
    base.update(kw)
    return Settings(**base)


def pcm(seconds, loud):
    n = int(seconds * 16000)
    x = np.full(n, 0.5 if loud else 0.0, dtype=np.float32)
    return (x * 32767).astype(np.int16).tobytes()


async def feed(stt, *parts, realtime=0.002):
    """parts: (seconds, loud) pairs, pushed in 32 ms chunks with a tiny sleep between."""
    for seconds, loud in parts:
        data = pcm(seconds, loud)
        step = FRAME * 2
        for i in range(0, len(data), step):
            stt.push_audio(data[i : i + step])
            await asyncio.sleep(realtime)


def run_case(parts, engine=None, **kw):
    events = []
    engine = engine or FakeEngine()

    async def main():
        stt = LocalSTT(settings(**kw), engine, lambda k, t: events.append((k, t)), vad=FakeVAD())
        task = asyncio.create_task(stt.run())
        await feed(stt, *parts)
        await asyncio.sleep(0.2)
        task.cancel()
        return stt

    stt = asyncio.run(main())
    return events, engine, stt


def kinds(events, kind):
    return [t for k, t in events if k == kind]


def test_one_utterance_gives_one_final_from_the_speculative_run():
    events, engine, _ = run_case([(0.2, False), (1.2, True), (1.0, False)])
    assert events[0] == ("ready", "")
    assert kinds(events, "turn_start") == [""]
    finals = kinds(events, "final")
    assert len(finals) == 1
    final_calls = [c for c in engine.calls if not c[0]]
    assert len(final_calls) == 1, "the eager transcription should be reused, not redone"
    assert final_calls[0][2] == "小星"  # hotwords passed as context
    assert kinds(events, "partial"), "expected live partial transcripts while talking"
    # Every partial comes before the final.
    last_partial = max(i for i, (k, _) in enumerate(events) if k == "partial")
    first_final = min(i for i, (k, _) in enumerate(events) if k == "final")
    assert last_partial < first_final


def test_pause_then_continue_discards_speculative_transcript():
    # 400 ms pause: long enough to start the eager run (250 ms), too short to end the turn (600 ms).
    events, engine, _ = run_case([(1.0, True), (0.4, False), (1.0, True), (1.0, False)])
    finals = kinds(events, "final")
    assert len(finals) == 1
    final_calls = [c for c in engine.calls if not c[0]]
    assert len(final_calls) == 2  # first eager run discarded
    assert final_calls[1][1] > final_calls[0][1] + 0.9  # the used one covers both halves
    assert finals[0] == f"final:{int(final_calls[1][1] * 16000) // FRAME}"


def test_short_noise_is_ignored():
    events, engine, _ = run_case([(0.12, True), (1.0, False)])
    assert not kinds(events, "final")


def test_two_utterances_in_order():
    events, engine, _ = run_case([(0.8, True), (0.8, False), (0.8, True), (0.8, False)])
    assert len(kinds(events, "final")) == 2
    assert len(kinds(events, "turn_start")) == 2


def test_very_long_speech_is_split():
    events, engine, _ = run_case([(2.5, True), (0.8, False)], vad_max_speech_s=1.0)
    assert len(kinds(events, "final")) >= 2


def test_dialog_context_is_added_to_hotwords():
    events = []
    engine = FakeEngine()

    async def main():
        stt = LocalSTT(settings(), engine, lambda k, t: events.append((k, t)), vad=FakeVAD())
        stt.set_context("我们刚才聊到了公式推导")
        task = asyncio.create_task(stt.run())
        await feed(stt, (1.0, True), (1.0, False))
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(main())
    final_ctx = [c[2] for c in engine.calls if not c[0]][0]
    assert final_ctx == "小星 我们刚才聊到了公式推导"
