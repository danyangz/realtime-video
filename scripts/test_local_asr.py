"""Stream WAV files through the local STT pipeline (Silero VAD + Qwen3-ASR) at real-time pace.

    python scripts/test_local_asr.py clip1.wav clip2.wav ...
    python scripts/test_local_asr.py --context "我们刚才聊到了公式推导" clip.wav

Prints live partials, the final transcript, and how long after the end of speech
the final transcript arrived (that delay includes the VAD_MIN_SILENCE_MS pause).
No API key needed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import Settings  # noqa: E402
from core.local_stt import ASREngine, LocalSTT, ensure_silero  # noqa: E402


def load_16k(path: str) -> bytes:
    with wave.open(path) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16 if width == 2 else np.int32)
    x = x.reshape(-1, ch).mean(axis=1) if ch > 1 else x.astype(np.float64)
    if width == 4:
        x = x / 65536
    if rate != 16000:
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x.astype(np.int16).tobytes()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--context", default="", help="dialog context passed to the ASR")
    args = ap.parse_args()

    s = Settings(stt_engine="local")
    print(f"final model: {s.asr_model}\npartial model: {s.asr_partial_model}\nloading ...")
    ensure_silero()
    engine = ASREngine(s)
    t0 = time.time()
    await engine.start()
    print(f"loaded in {time.time() - t0:.1f}s\n")

    for path in args.wavs:
        got_final = asyncio.Event()
        marks: dict[str, float] = {}

        def on_event(kind: str, text: str) -> None:
            now = time.monotonic()
            if kind == "partial":
                print(f"   … {text}")
            elif kind == "final":
                marks["final"] = now
                print(f"   ✓ {text}")
                got_final.set()
            elif kind == "error":
                print(f"   ✗ {text}")

        stt = LocalSTT(s, engine, on_event)
        if args.context:
            stt.set_context(args.context)
        task = asyncio.create_task(stt.run())
        pcm = load_16k(path)
        print(f"{Path(path).name} ({len(pcm) / 32000:.1f}s)")
        silence = b"\x00\x00" * 320
        for _ in range(10):
            stt.push_audio(silence)
            await asyncio.sleep(0.02)
        for i in range(0, len(pcm), 640):
            stt.push_audio(pcm[i : i + 640])
            await asyncio.sleep(0.02)
        speech_end = time.monotonic()
        for _ in range(150):  # up to 3 s of trailing silence
            if got_final.is_set():
                break
            stt.push_audio(silence)
            await asyncio.sleep(0.02)
        if "final" in marks:
            print(f"   final {1000 * (marks['final'] - speech_end):.0f} ms after the clip ended"
                  f" (pause needed to end the turn: {s.vad_min_silence_ms} ms)\n")
        else:
            print("   ✗ no final transcript\n")
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
