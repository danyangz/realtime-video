"""Live check of every cloud piece, plus an LLM latency comparison.

    python scripts/smoke_test.py            # TTS -> STT round trip + LLM latency
    python scripts/smoke_test.py --e2e      # also talk to a running server (python -m server)
    python scripts/smoke_test.py --models together:Qwen/Qwen3.5-9B openrouter:qwen/qwen3.6-35b-a3b

Writes audio to out/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import struct
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.config import Settings  # noqa: E402
from server.llm import LLM  # noqa: E402
from server.stt import TogetherSTT  # noqa: E402
from server.tts import TogetherTTS  # noqa: E402
from server.text import normalize_transcript  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out"
SENTENCE = "你好，我想问一下，今天下午有什么好玩的地方可以去？"


def save_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(x) * dst / src)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return y.astype(np.int16).tobytes()


async def test_tts(s: Settings) -> bytes:
    print(f"\n== TTS  {s.tts_model} / {s.tts_voice}")
    tts = TogetherTTS(s)
    t0 = time.monotonic()
    stream = await tts.open()
    t_conn = time.monotonic()
    # Two pieces, like the app sends them; log every event type to check the protocol.
    pieces = ["你好，我想问一下，", "今天下午有什么好玩的地方可以去？"]
    for p in pieces:
        await stream.send_text(p)
    await stream.finish()
    t_sent = time.monotonic()
    pcm = bytearray()
    first = None
    async for chunk in stream.audio():
        if first is None:
            first = time.monotonic()
        pcm += chunk
    stream.close()
    await tts.aclose()
    dur = len(pcm) / 2 / s.tts_sample_rate
    print(f"   connect {1000 * (t_conn - t0):.0f} ms · first audio {1000 * ((first or t_sent) - t_sent):.0f} ms after text"
          f" · {dur:.1f}s of audio · done events {stream.done}/{stream.sent} pieces")
    if not pcm:
        raise SystemExit("   ✗ no audio received")
    save_wav(OUT / "tts_test.wav", bytes(pcm), s.tts_sample_rate)
    print(f"   ✓ saved {OUT / 'tts_test.wav'}")
    return bytes(pcm)


async def test_stt(s: Settings, pcm24: bytes) -> None:
    print(f"\n== STT  {s.stt_model} (language={s.stt_language})")
    pcm16 = resample(pcm24, s.tts_sample_rate, 16000) + b"\x00\x00" * 16000 * 2  # + 2 s silence
    finals: list[str] = []
    ready = asyncio.Event()
    got_final = asyncio.Event()
    marks: dict[str, float] = {}

    def on_event(kind: str, text: str) -> None:
        if kind == "ready":
            ready.set()
        elif kind == "partial":
            marks.setdefault("first_partial", time.monotonic())
            print(f"   … {text}")
        elif kind == "final":
            marks["final"] = time.monotonic()
            finals.append(text)
            print(f"   ✓ final: {text}  →  {normalize_transcript(text)}")
            got_final.set()
        elif kind == "error":
            print(f"   ✗ {text}")

    stt = TogetherSTT(s, on_event)
    task = asyncio.create_task(stt.run())
    await asyncio.wait_for(ready.wait(), 20)
    speech_bytes = len(pcm16) - 16000 * 4
    for i in range(0, len(pcm16), 640):  # 20 ms, real-time pace
        stt.push_audio(pcm16[i : i + 640])
        if i == speech_bytes - speech_bytes % 640:
            marks["speech_end"] = time.monotonic()
        await asyncio.sleep(0.02)
    try:
        await asyncio.wait_for(got_final.wait(), 10)
    except TimeoutError:
        print("   ✗ no final transcript")
    task.cancel()
    if "final" in marks and "speech_end" in marks:
        print(f"   final transcript {1000 * (marks['final'] - marks['speech_end']):.0f} ms after speech ended"
              f" (includes VAD silence {s.vad_min_silence_ms} ms)")


async def test_llm(s: Settings, specs: list[str], runs: int) -> None:
    print("\n== LLM time to first token (lower is better)")
    prompt = [
        {"role": "system", "content": s.system_prompt},
        {"role": "user", "content": "你好呀，今天过得怎么样？"},
    ]
    for spec in specs:
        provider, model = spec.split(":", 1)
        cfg = replace(s, llm_provider=provider, llm_model=model)
        if not cfg.llm_api_key:
            print(f"   - {spec}: skipped (no API key)")
            continue
        llm = LLM(cfg)
        ttfts, reply = [], ""
        try:
            for _ in range(runs):
                t0 = time.monotonic()
                first = None
                reply = ""
                async for d in llm.stream(prompt):
                    if first is None:
                        first = time.monotonic()
                    reply += d
                ttfts.append(1000 * ((first or time.monotonic()) - t0))
            print(f"   ✓ {spec}: median {statistics.median(ttfts):.0f} ms  (runs: {', '.join(f'{x:.0f}' for x in ttfts)})")
            print(f"     reply: {reply.strip()[:80]}")
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ {spec}: {e}")
        finally:
            await llm.aclose()


async def test_e2e(s: Settings, pcm24: bytes) -> None:
    print(f"\n== End to end via ws://localhost:{s.port}/ws")
    pcm16 = resample(pcm24, s.tts_sample_rate, 16000)
    reply = bytearray()
    async with websockets.connect(f"ws://localhost:{s.port}/ws", max_size=None) as ws:
        await ws.send(json.dumps({"type": "start"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if m["type"] == "fatal":
                raise SystemExit(f"   ✗ {m['message']}")
            if m["type"] == "stt_ready":
                break
        for i in range(0, len(pcm16), 640):
            await ws.send(pcm16[i : i + 640])
            await asyncio.sleep(0.02)
        speech_end = time.monotonic()

        async def silence() -> None:
            while True:
                await ws.send(b"\x00" * 640)
                await asyncio.sleep(0.02)

        sil = asyncio.create_task(silence())
        first_audio = None
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), 20)
                if isinstance(msg, bytes):
                    (turn,) = struct.unpack("<I", msg[:4])
                    first_audio = first_audio or time.monotonic()
                    reply += msg[4:]
                    continue
                m = json.loads(msg)
                if m["type"] in ("user_final", "assistant_done", "metrics", "error"):
                    print(f"   {m['type']}: {json.dumps({k: v for k, v in m.items() if k != 'type'}, ensure_ascii=False)}")
                if m["type"] == "tts_end":
                    break
        finally:
            sil.cancel()
    if first_audio:
        print(f"   ✓ first reply audio {1000 * (first_audio - speech_end):.0f} ms after you stopped talking")
        save_wav(OUT / "e2e_reply.wav", bytes(reply), s.tts_sample_rate)
        print(f"   ✓ saved {OUT / 'e2e_reply.wav'}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true", help="also test a running server")
    ap.add_argument("--models", nargs="*", help="provider:model specs to compare")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--skip-audio", action="store_true")
    args = ap.parse_args()

    s = Settings()
    if not s.together_api_key:
        raise SystemExit("缺少 TOGETHER_API_KEY：在项目根目录创建 .env（参考 .env.example）")

    specs = args.models or [
        "together:Qwen/Qwen3.5-9B",
        "together:deepseek-ai/DeepSeek-V4.1-Flash",
        "openrouter:qwen/qwen3.6-35b-a3b",
        "openrouter:qwen/qwen3.5-35b-a3b",
    ]
    pcm = b""
    if not args.skip_audio:
        pcm = await test_tts(s)
        await test_stt(s, pcm)
    await test_llm(s, specs, args.runs)
    if args.e2e and pcm:
        await test_e2e(s, pcm)


if __name__ == "__main__":
    asyncio.run(main())
