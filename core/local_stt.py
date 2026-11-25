"""Local speech-to-text on Apple Silicon: Silero VAD + Qwen3-ASR (MLX).

Per utterance:
  * Silero VAD (ONNX, CPU, ~0.1 ms per 32 ms frame) finds where speech starts and stops.
  * While you talk, a small model (Qwen3-ASR-0.6B) re-transcribes the utterance every
    ~0.7 s for live captions and barge-in detection ("partial").
  * After a short pause (VAD_EAGER_MS) the accurate model (Qwen3-ASR-1.7B) starts on the
    utterance speculatively; once the pause is long enough to end the turn
    (VAD_MIN_SILENCE_MS), its result is usually already done ("final").
    If you resume talking, that speculative result is discarded.

Emits the same events as stt.TogetherSTT: ready, partial, final, turn_start, error
(plus "loading" while models load).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .config import Settings

log = logging.getLogger("local_stt")

SR = 16000
FRAME = 512  # Silero needs 512-sample frames at 16 kHz (32 ms)
FRAME_MS = FRAME * 1000 // SR

SILERO_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/v6.3/src/silero_vad/data/silero_vad.onnx"
SILERO_SHA256 = "f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31"
CACHE_DIR = Path.home() / ".cache" / "realtime-video"

LANGUAGES = {"zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean"}

EventCallback = Callable[[str, str], None]


def ensure_silero() -> Path:
    """Download the Silero VAD model once (MIT license), verifying its checksum."""
    path = CACHE_DIR / "silero_vad_v6.3.onnx"
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == SILERO_SHA256:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("downloading Silero VAD model ...")
    data = urllib.request.urlopen(SILERO_URL, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != SILERO_SHA256:
        raise RuntimeError("Silero VAD model checksum mismatch")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


class DebugRecorder:
    """DEBUG_AUDIO=1: save the mic stream and every utterance exactly as the ASR saw it.

    Writes out/debug/<time>/mic.wav, uttNNN.wav and utterances.jsonl (local disk only).
    """

    def __init__(self, root: Path) -> None:
        import wave

        self.dir = root / time.strftime("%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._wave = wave
        self.mic_wav = wave.open(str(self.dir / "mic.wav"), "wb")
        self.mic_wav.setnchannels(1)
        self.mic_wav.setsampwidth(2)
        self.mic_wav.setframerate(SR)
        self.log = open(self.dir / "utterances.jsonl", "a", encoding="utf-8")
        log.info("DEBUG_AUDIO: recording to %s", self.dir)

    def mic(self, pcm16: bytes) -> None:
        self.mic_wav.writeframes(pcm16)

    def utterance(self, n: int, audio: np.ndarray, info: dict) -> None:
        import json

        name = f"utt{n:03d}.wav"
        with self._wave.open(str(self.dir / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        self.log.write(json.dumps({"file": name, "seconds": round(len(audio) / SR, 2), **info}, ensure_ascii=False) + "\n")
        self.log.flush()

    def close(self) -> None:
        self.mic_wav.close()
        self.log.close()


class SileroVAD:
    """Streaming Silero VAD v5/v6 ONNX: speech probability per 512-sample frame."""

    def __init__(self, model_path: Path) -> None:
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.sess = onnxruntime.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.sr = np.array(SR, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.reshape(1, -1)], axis=1)
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.context = x[:, -64:]
        return float(out[0][0])


class ASREngine:
    """Loads Qwen3-ASR once and runs every MLX call on a single dedicated thread."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
        self.language = LANGUAGES.get(settings.stt_language, settings.stt_language or None)
        self.final_model = None
        self.partial_model = None
        self.pending = 0  # jobs submitted but not finished
        self._ready: asyncio.Future | None = None

    def start(self) -> asyncio.Future:
        """Begin loading models in the background (idempotent)."""
        if self._ready is None:
            loop = asyncio.get_running_loop()
            self._ready = loop.run_in_executor(self.executor, self._load)
        return self._ready

    @property
    def ready(self) -> bool:
        return self._ready is not None and self._ready.done() and self._ready.exception() is None

    async def wait_ready(self) -> None:
        await asyncio.shield(self.start())

    def _load(self) -> None:
        from mlx_qwen3_asr import Session as ASRSession

        t0 = time.time()
        self.final_model = ASRSession(model=self.s.asr_model)
        if self.s.asr_partial_model and self.s.asr_partial_model != self.s.asr_model:
            self.partial_model = ASRSession(model=self.s.asr_partial_model)
        else:
            self.partial_model = self.final_model
        warm = np.zeros(SR // 2, dtype=np.float32)
        self.final_model.transcribe(warm, language=self.language)
        if self.partial_model is not self.final_model:
            self.partial_model.transcribe(warm, language=self.language)
        log.info("ASR ready in %.1fs (final=%s, partial=%s)", time.time() - t0, self.s.asr_model,
                 self.s.asr_partial_model or self.s.asr_model)

    def _run(self, audio: np.ndarray, partial: bool, context: str) -> str:
        model = self.partial_model if partial else self.final_model
        return model.transcribe(audio, language=self.language, context=context).text.strip()

    async def transcribe(self, audio: np.ndarray, *, partial: bool, context: str = "") -> str:
        await self.wait_ready()
        self.pending += 1
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self.executor, self._run, audio, partial, context)
        finally:
            self.pending -= 1


class LocalSTT:
    """Per-connection VAD + endpointing; transcription runs on the shared ASREngine."""

    def __init__(
        self, settings: Settings, engine: ASREngine, on_event: EventCallback, vad=None, hotwords: str = ""
    ) -> None:
        self.s = settings
        # App-specific terms (names etc.) plus any user-wide ASR_HOTWORDS.
        self.hotwords = " ".join(x for x in (hotwords, settings.asr_hotwords) if x).strip()
        self.engine = engine
        self.on_event = on_event
        self.vad = vad if vad is not None else SileroVAD(ensure_silero())
        self.q: asyncio.Queue[np.ndarray] = asyncio.Queue()
        self.buf = np.zeros(0, dtype=np.float32)

        self.threshold = settings.local_vad_threshold
        self.neg_threshold = max(0.05, settings.local_vad_threshold - 0.15)
        self.pad_frames = max(1, settings.vad_speech_pad_ms // FRAME_MS)
        self.preroll: deque[np.ndarray] = deque(maxlen=self.pad_frames)

        self.in_speech = False
        self.start_run = 0
        self.segment: list[np.ndarray] = []
        self.speech_frames = 0
        self.silence_frames = 0
        self.utt = 0
        # Speculative final transcription: (task, audio, context), started after VAD_EAGER_MS of silence.
        self.eager: tuple[asyncio.Task, np.ndarray, str] | None = None
        self.partial_task: asyncio.Task | None = None
        self.frames_at_last_partial = 0
        self.finalized_utt = 0
        self.last_partial = ""
        self.dialog_context = ""
        self._tasks: set[asyncio.Task] = set()
        self.rec = DebugRecorder(settings.debug_dir) if settings.debug_audio else None

    # -- inputs -------------------------------------------------------------

    def push_audio(self, pcm16: bytes) -> None:
        if self.rec:
            self.rec.mic(pcm16)
        self.q.put_nowait(np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0)

    def set_context(self, text: str) -> None:
        """Recent conversation text; helps Qwen3-ASR pick the right homophones."""
        self.dialog_context = text[-120:] if self.s.asr_dialog_context else ""

    def _context(self) -> str:
        return " ".join(x for x in (self.hotwords, self.dialog_context) if x).strip()

    # -- main loop ----------------------------------------------------------

    async def run(self) -> None:
        if not self.engine.ready:
            self.on_event("loading", "")
        try:
            await self.engine.wait_ready()
        except Exception as e:  # noqa: BLE001
            log.exception("ASR model failed to load")
            self.on_event("error", f"本地语音识别模型加载失败：{e}")
            return
        self.on_event("ready", "")
        try:
            while True:
                chunk = await self.q.get()
                self.buf = np.concatenate([self.buf, chunk]) if self.buf.size else chunk
                while self.buf.size >= FRAME:
                    frame, self.buf = self.buf[:FRAME], self.buf[FRAME:]
                    self._frame(frame)
        finally:
            for t in list(self._tasks):
                t.cancel()
            if self.rec:
                self.rec.close()

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)

        def done(task: asyncio.Task) -> None:
            self._tasks.discard(task)
            if not task.cancelled():
                task.exception()  # mark retrieved; discarded speculative results may have failed

        t.add_done_callback(done)
        return t

    def _frame(self, frame: np.ndarray) -> None:
        p = self.vad(frame)
        if not self.in_speech:
            self.preroll.append(frame)
            self.start_run = self.start_run + 1 if p >= self.threshold else 0
            if self.start_run >= 2:  # 64 ms of speech: utterance starts
                self.in_speech = True
                self.utt += 1
                self.segment = list(self.preroll)
                self.speech_frames = self.start_run
                self.silence_frames = 0
                self.eager = None
                self.last_partial = ""
                self.frames_at_last_partial = len(self.segment)
                self.on_event("turn_start", "")
            return

        self.segment.append(frame)
        if p >= self.neg_threshold:
            if self.silence_frames:
                self.eager = None  # talking again: the speculative transcript is stale
            self.silence_frames = 0
            if p >= self.threshold:
                self.speech_frames += 1
        else:
            self.silence_frames += 1

        silence_ms = self.silence_frames * FRAME_MS
        too_long = len(self.segment) * FRAME_MS >= self.s.vad_max_speech_s * 1000

        if silence_ms >= self.s.vad_min_silence_ms or too_long:
            self._end_utterance()
            return

        if self.eager is None and silence_ms >= self.s.vad_eager_ms:
            audio, ctx = np.concatenate(self.segment), self._context()
            self.eager = (self._spawn(self.engine.transcribe(audio, partial=False, context=ctx)), audio, ctx)
        elif (self.silence_frames == 0
              and (len(self.segment) - self.frames_at_last_partial) * FRAME_MS >= self.s.asr_partial_interval_ms
              and (self.partial_task is None or self.partial_task.done())
              and self.engine.pending == 0):
            self.frames_at_last_partial = len(self.segment)
            self.partial_task = self._spawn(self._partial(self.utt, np.concatenate(self.segment)))

    def _end_utterance(self) -> None:
        utt, segment, eager = self.utt, self.segment, self.eager
        speech_ms = self.speech_frames * FRAME_MS
        self.in_speech = False
        self.start_run = 0
        self.segment = []
        self.eager = None
        self.preroll.clear()
        if speech_ms < self.s.vad_min_speech_ms:
            self.finalized_utt = utt  # a click or cough: ignore
            return
        if eager is not None:
            task, audio, ctx = eager
        else:
            audio, ctx = np.concatenate(segment), self._context()
            task = self._spawn(self.engine.transcribe(audio, partial=False, context=ctx))
        self._spawn(self._final(utt, task, audio, ctx))

    async def _partial(self, utt: int, audio: np.ndarray) -> None:
        try:
            text = await self.engine.transcribe(audio, partial=True, context=self._context())
        except Exception as e:  # noqa: BLE001
            log.warning("partial ASR failed: %s", e)
            return
        if text and utt == self.utt and self.finalized_utt < utt:
            self.last_partial = text
            self.on_event("partial", text)

    async def _final(self, utt: int, task: asyncio.Task, audio: np.ndarray, ctx: str) -> None:
        try:
            text = await task
        except Exception as e:  # noqa: BLE001
            log.exception("final ASR failed")
            self.on_event("error", f"语音识别出错：{e}")
            return
        finally:
            self.finalized_utt = max(self.finalized_utt, utt)
        if self.rec:
            self.rec.utterance(utt, audio, {"final": text, "last_partial": self.last_partial, "context": ctx})
        if text:
            self.on_event("final", text)
