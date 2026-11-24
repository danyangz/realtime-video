"""One browser connection = one conversation.

Flow per user turn:
    mic PCM ──▶ STT ──final transcript──▶ LLM (streamed) ──sentences──▶ TTS ──PCM──▶ browser

Turn-taking rules:
  * A final transcript starts an assistant turn.
  * If the user keeps talking before any audio has been sent ("thinking" phase), the
    turn is cancelled and the next final transcript is merged with the earlier one.
  * If the user talks while the avatar is speaking (barge-in), the avatar stops, and
    the part of the reply generated so far is kept in history marked as interrupted.

Wire protocol to the browser:
  * binary frames from browser: PCM16 mono 16 kHz mic audio
  * binary frames to browser:   4-byte little-endian turn id + PCM16 mono TTS audio
  * text frames both ways:      JSON events (see `_send` calls / web/app.js)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import Callable
from typing import Protocol

from .config import Settings
from .text import SentenceChunker, clean_for_tts, looks_like_echo, normalize_transcript, speakable_len

log = logging.getLogger("session")


class Transport(Protocol):
    async def send_text(self, data: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


class Session:
    def __init__(
        self,
        settings: Settings,
        transport: Transport,
        llm,
        tts,
        stt_factory: Callable[[Callable[[str, str], None]], object],
    ) -> None:
        self.s = settings
        self.transport = transport
        self.llm = llm
        self.tts = tts
        self.stt_factory = stt_factory

        self.history: list[dict] = []
        self.pending_user = ""  # finalized user speech not yet answered
        self.turn_id = 0
        self.turn_task: asyncio.Task | None = None
        self.phase = "idle"  # idle | thinking | speaking
        self.reply_so_far = ""  # assistant text of the current turn
        self.last_reply = ""  # for echo detection right after the avatar stops
        self.last_reply_end = 0.0

        self.events: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self.stt = None
        self.stt_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------------ io

    async def _send(self, **event) -> None:
        async with self._send_lock:
            await self.transport.send_text(json.dumps(event, ensure_ascii=False))

    async def _send_audio(self, turn: int, pcm: bytes) -> None:
        async with self._send_lock:
            await self.transport.send_bytes(struct.pack("<I", turn) + pcm)

    async def _set_phase(self, phase: str) -> None:
        self.phase = phase
        await self._send(type="state", value={"idle": "listening"}.get(phase, phase), turn=self.turn_id)

    # ----------------------------------------------------------- inputs

    def on_audio(self, pcm16: bytes) -> None:
        if self.stt is not None:
            self.stt.push_audio(pcm16)

    def on_client_message(self, msg: dict) -> None:
        self.events.put_nowait(("client", msg))

    def _on_stt_event(self, kind: str, text: str) -> None:
        self.events.put_nowait(("stt", (kind, text)))

    # ------------------------------------------------------------ loop

    async def run(self) -> None:
        """Process events until cancelled."""
        await self._send(
            type="hello",
            llm=self.llm.label,
            stt=self.s.stt_model,
            tts=f"{self.s.tts_model} / {self.s.tts_voice}",
            sample_rate=self.tts.sample_rate,
        )
        try:
            while True:
                source, payload = await self.events.get()
                try:
                    if source == "client":
                        await self._handle_client(payload)  # type: ignore[arg-type]
                    else:
                        kind, text = payload  # type: ignore[misc]
                        await self._handle_stt(kind, text)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - keep the session alive
                    log.exception("error handling %s event", source)
                    await self._send(type="error", message=str(e))
        finally:
            await self.close()

    async def close(self) -> None:
        await self._cancel_turn(notify=False)
        if self.stt_task:
            self.stt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.stt_task
            self.stt_task = None
            self.stt = None

    async def _handle_client(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "start":
            if self.stt_task is None:
                self.stt = self.stt_factory(self._on_stt_event)
                self.stt_task = asyncio.create_task(self.stt.run())
            self.tts.warm()
            await self._set_phase("idle")
        elif t == "stop":
            await self.close()
            await self._set_phase("idle")
        elif t == "text":
            # Typed message: behaves like a finished spoken utterance.
            text = (msg.get("text") or "").strip()
            if text:
                await self._user_final(text, typed=True)
        elif t == "playback_done":
            if msg.get("turn") == self.turn_id and self.phase == "speaking":
                self.last_reply_end = time.monotonic()
                await self._set_phase("idle")
        elif t == "interrupt":
            await self._barge_in("button")
        elif t == "reset":
            await self._cancel_turn()
            self.history.clear()
            self.pending_user = ""
            await self._set_phase("idle")

    async def _handle_stt(self, kind: str, text: str) -> None:
        if kind == "ready":
            await self._send(type="stt_ready")
        elif kind == "error":
            await self._send(type="error", message=text)
        elif kind == "partial":
            heard = normalize_transcript(text)
            if not heard:
                return
            if self._is_echo(heard):
                return
            await self._send(type="user_partial", text=heard)
            if speakable_len(heard) >= self.s.barge_in_min_chars:
                await self._user_speaking()
        elif kind == "final":
            heard = normalize_transcript(text)
            if heard and not self._is_echo(heard):
                await self._user_final(heard)
        elif kind == "turn_start":
            pass  # interim transcripts already tell us the user is talking

    # ------------------------------------------------------- turn logic

    def _is_echo(self, heard: str) -> bool:
        """The mic picked up the avatar's own voice (no headphones / weak echo cancellation)."""
        if self.phase == "speaking":
            return looks_like_echo(heard, self.reply_so_far)
        if self.last_reply and time.monotonic() - self.last_reply_end < 2.0:
            return looks_like_echo(heard, self.last_reply)
        return False

    async def _user_speaking(self) -> None:
        if self.phase == "thinking":
            # The user hadn't finished; drop the half-started reply and wait for the rest.
            await self._cancel_turn()
            await self._set_phase("idle")
        elif self.phase == "speaking" and self.s.barge_in:
            await self._barge_in("voice")

    async def _barge_in(self, reason: str) -> None:
        if self.phase not in ("thinking", "speaking"):
            return
        log.info("barge-in (%s) during %s", reason, self.phase)
        await self._cancel_turn()
        await self._set_phase("idle")

    async def _user_final(self, text: str, typed: bool = False) -> None:
        await self._send(type="user_final", text=text, typed=typed)
        if self.phase in ("thinking", "speaking"):
            await self._cancel_turn()
        self.pending_user = f"{self.pending_user} {text}".strip() if self.pending_user else text
        await self._start_turn()

    async def _start_turn(self) -> None:
        self.turn_id += 1
        user_text = self.pending_user
        self.reply_so_far = ""
        await self._set_phase("thinking")
        self.turn_task = asyncio.create_task(self._run_turn(self.turn_id, user_text))

    async def _cancel_turn(self, notify: bool = True) -> None:
        task, self.turn_task = self.turn_task, None
        if task is None:
            return
        was_speaking = self.phase == "speaking"
        finished = task.done()  # generation already complete; browser may still be playing the tail
        if not finished:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if was_speaking and self.reply_so_far and not finished:
            # Keep what the avatar had started saying, so the model knows it was cut off.
            self.history.append({"role": "assistant", "content": self.reply_so_far + "……（被用户打断）"})
            self.last_reply = self.reply_so_far
            self.last_reply_end = time.monotonic()
        if notify:
            await self._send(type="interrupt", turn=self.turn_id)

    def _messages(self, user_text: str) -> list[dict]:
        hist = self.history[-self.s.max_history_messages :]
        return [{"role": "system", "content": self.s.system_prompt}, *hist, {"role": "user", "content": user_text}]

    async def _run_turn(self, turn: int, user_text: str) -> None:
        t0 = time.monotonic()
        marks: dict[str, float] = {}
        tts = None
        tts_open: asyncio.Task | None = None
        audio_task: asyncio.Task | None = None
        committed_user = False

        async def commit_user() -> None:
            nonlocal committed_user
            if not committed_user:
                committed_user = True
                self.history.append({"role": "user", "content": user_text})
                self.pending_user = ""

        async def pump_audio() -> None:
            first = True
            async for pcm in tts.audio():
                if first:
                    first = False
                    marks["first_audio"] = time.monotonic()
                    await commit_user()
                    self.phase = "speaking"
                    await self._send(type="state", value="speaking", turn=turn)
                    await self._send(type="audio_start", turn=turn, sample_rate=self.tts.sample_rate)
                await self._send_audio(turn, pcm)

        try:
            tts_open = asyncio.create_task(self.tts.open())
            chunker = SentenceChunker()
            reply = ""
            pieces_sent = 0

            async def speak(piece: str) -> None:
                nonlocal pieces_sent, tts, audio_task
                piece = clean_for_tts(piece)
                if not piece:
                    return
                if tts is None:
                    tts = await tts_open
                    audio_task = asyncio.create_task(pump_audio())
                if pieces_sent == 0:
                    marks["first_tts_send"] = time.monotonic()
                await tts.send_text(piece)
                pieces_sent += 1

            async for delta in self.llm.stream(self._messages(user_text)):
                if "first_token" not in marks:
                    marks["first_token"] = time.monotonic()
                reply += delta
                self.reply_so_far = reply
                await self._send(type="assistant_delta", turn=turn, text=delta)
                for piece in chunker.feed(delta):
                    await speak(piece)
            for piece in chunker.flush():
                await speak(piece)

            if tts is None:
                # Nothing speakable came back.
                await commit_user()
                if reply.strip():
                    self.history.append({"role": "assistant", "content": reply})
                await self._send(type="assistant_done", turn=turn, text=reply)
                await self._set_phase("idle")
                return

            await tts.finish()
            await audio_task
            await commit_user()
            self.history.append({"role": "assistant", "content": reply})
            self.last_reply = reply
            await self._send(type="assistant_done", turn=turn, text=reply)
            await self._send(type="tts_end", turn=turn)

            def ms(a: str, b: str | None = None) -> int | None:
                if a not in marks:
                    return None
                start = marks[b] if b else t0
                return round((marks[a] - start) * 1000)

            metrics = {
                "llm_first_token_ms": ms("first_token"),
                "tts_first_audio_ms": ms("first_audio", "first_tts_send") if "first_tts_send" in marks else None,
                "total_ms": ms("first_audio"),
            }
            log.info("turn %d metrics %s", turn, metrics)
            await self._send(type="metrics", turn=turn, **metrics)
            if self.phase != "speaking":
                await self._set_phase("idle")
            # Otherwise stay "speaking" until the browser reports playback_done.
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("turn %d failed", turn)
            if not committed_user:
                self.pending_user = ""  # don't keep retrying a request that errors
            await self._send(type="error", message=f"生成回复失败：{e}")
            await self._send(type="interrupt", turn=turn)
            await self._set_phase("idle")
        finally:
            if audio_task and not audio_task.done():
                audio_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await audio_task
            if tts is not None:
                tts.close()
            elif tts_open is not None:
                # The TTS socket was still connecting (or never used): don't leak it.
                if tts_open.done() and not tts_open.cancelled() and tts_open.exception() is None:
                    tts_open.result().close()
                else:
                    tts_open.cancel()
