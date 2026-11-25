"""One browser connection = one game. Bridges the engine to the UI, TTS and push-to-talk.

Server -> browser (JSON): hello, state, log, speech_start/delta/end, audio_start, tts_end,
    request, ptt_text, game_over, error.  Binary: [uint32 speech id][PCM16 mono audio].
Browser -> server (JSON): start, response, playback_done, tts, ptt_start, ptt_end, restart.
    Binary: push-to-talk mic audio (16 kHz PCM16) between ptt_start and ptt_end.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import struct
from collections.abc import AsyncIterator, Awaitable, Callable

from core.services import Services
from core.text import SentenceChunker, clean_for_tts

from .agent import AIController
from .config import LEVELS, WerewolfSettings
from .engine import SPEECH_KINDS, Event, Game, Hooks, Option, Player, deal
from .personas import CASTS, DEFAULT_CAST, HUMAN_COLOR, Cast, Voice
from .prompts import SKIP_LABELS
from .roles import Role
from .scripted import ScriptedController

log = logging.getLogger("werewolf.session")

TERMS = "预言家 女巫 猎人 白痴 狼人 平民 警长 警徽 上警 金水 查杀 银水 悍跳 对跳 归票 站边 抗推 自刀 警徽流"


class HumanController:
    """Decisions come from the browser. 'Let AI decide' delegates to an AI for that one request."""

    def __init__(self, session: WerewolfSession, fallback) -> None:
        self.session = session
        self.fallback = fallback

    async def speak(self, game: Game, player: Player, kind: str, prompt: str) -> AsyncIterator[str]:
        resp = await self.session.request(player, mode="speak", kind=kind, label=SPEECH_KINDS[kind], prompt=prompt)
        if resp.get("auto"):
            async for chunk in self.fallback.speak(game, player, kind, prompt):
                yield chunk
            return
        yield str(resp.get("value") or "").strip()[:500] or "（过）"

    async def choose(
        self, game: Game, player: Player, kind: str, prompt: str, options: list[Option], allow_skip: bool
    ) -> int:
        resp = await self.session.request(
            player, mode="choose", kind=kind, prompt=prompt, allow_skip=allow_skip,
            skip_label=SKIP_LABELS.get(kind, "跳过"),
            options=[{"key": o.key, "label": o.label} for o in options],
            seats=kind not in ("witch_save", "run_sheriff", "speech_order"),
        )
        if resp.get("auto"):
            return await self.fallback.choose(game, player, kind, prompt, options, allow_skip)
        try:
            return int(resp.get("value", 0))
        except (TypeError, ValueError):
            return -1


class WerewolfSession(Hooks):
    def __init__(
        self,
        services: Services,
        settings: WerewolfSettings,
        send_text: Callable[[str], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self.services = services
        self.s = settings
        self._send_text = send_text
        self._send_bytes = send_bytes
        self._lock = asyncio.Lock()
        self.tts = services.new_tts() if services.settings.together_api_key else None
        self.tts_on = self.tts is not None
        self.bad_voices: set[str] = set()

        self.game: Game | None = None
        self.task: asyncio.Task | None = None
        self.human_seat: int | None = None
        self.spectate = False
        self.cast: Cast = CASTS[DEFAULT_CAST]
        self.voices: dict[int, Voice] = {}
        self.colors: dict[int, str] = {}
        self.looks: dict[int, dict] = {}
        self.models: dict[int, str] = {}
        self.revealed: dict[int, str] = {}  # publicly revealed roles (hunter shot, idiot)

        self._req_id = 0
        self._requests: dict[int, asyncio.Future] = {}
        self._speech_id = 0
        self._playback: dict[int, asyncio.Event] = {}
        self.ptt: bytearray | None = None

    # -------------------------------------------------------------- io

    async def send(self, **msg) -> None:
        async with self._lock:
            await self._send_text(json.dumps(msg, ensure_ascii=False))

    async def send_audio(self, sid: int, pcm: bytes) -> None:
        async with self._lock:
            await self._send_bytes(struct.pack("<I", sid) + pcm)

    async def hello(self) -> None:
        await self.send(type="hello", models=sorted({f"{p}:{m}" for p, m in self.s.models}),
                        casts=[{"key": c.key, "title": c.title} for c in CASTS.values()], default_cast=DEFAULT_CAST,
                        levels=[{"key": k, "title": v["title"], "desc": v["desc"]} for k, v in LEVELS.items()],
                        fake_ai=self.s.fake_ai, tts=self.tts is not None, ptt=self.services.asr is not None)

    # ----------------------------------------------------------- inputs

    def on_audio(self, data: bytes) -> None:
        if self.ptt is not None and len(self.ptt) < 16000 * 2 * 60:
            self.ptt += data

    async def handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t in ("start", "restart"):
            await self.start_game(msg)
        elif t == "response":
            fut = self._requests.pop(int(msg.get("id", -1)), None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif t == "playback_done":
            ev = self._playback.get(int(msg.get("id", -1)))
            if ev:
                ev.set()
        elif t == "tts":
            self.tts_on = bool(msg.get("on")) and self.tts is not None
            if not self.tts_on:
                for ev in self._playback.values():
                    ev.set()
        elif t == "ptt_start":
            self.ptt = bytearray()
        elif t == "ptt_end":
            audio, self.ptt = bytes(self.ptt or b""), None
            asyncio.create_task(self._transcribe(audio))

    async def _transcribe(self, audio: bytes) -> None:
        if len(audio) < 16000 * 2 * 0.3:
            await self.send(type="ptt_text", text="", error="录音太短")
            return
        try:
            hotwords = TERMS + " " + " ".join(p.name for p in self.game.players.values()) if self.game else TERMS
            text = await self.services.transcribe_clip(audio, hotwords=hotwords)
            await self.send(type="ptt_text", text=text)
        except Exception as e:  # noqa: BLE001
            log.exception("push-to-talk transcription failed")
            await self.send(type="ptt_text", text="", error=str(e))

    async def request(self, player: Player, **payload) -> dict:
        self._req_id += 1
        rid = self._req_id
        fut = asyncio.get_running_loop().create_future()
        self._requests[rid] = fut
        await self.send(type="request", id=rid, seat=player.seat, **payload)
        try:
            return await fut
        finally:
            self._requests.pop(rid, None)
            with contextlib.suppress(Exception):
                await self.send(type="request_done", id=rid)

    # ------------------------------------------------------------ game

    async def start_game(self, opts: dict) -> None:
        await self.stop_game()
        rng = random.Random()
        role = str(opts.get("role") or "random")
        self.spectate = role == "spectate"
        self.human_seat = None if self.spectate else rng.randint(1, 12)
        human_role = Role(role) if role in {r.value for r in Role} else None
        human_name = (str(opts.get("name") or "").strip() or "玩家")[:8]
        if "tts" in opts:
            self.tts_on = bool(opts["tts"]) and self.tts is not None

        self.cast = CASTS.get(str(opts.get("cast") or ""), CASTS[DEFAULT_CAST])
        personas = list(self.cast.personas)
        rng.shuffle(personas)
        names = [p.name for p in personas]
        if self.human_seat:
            names[self.human_seat - 1] = human_name
        players = deal(names, self.human_seat, human_role, rng)

        level = LEVELS.get(str(opts.get("level") or ""), LEVELS["standard"])
        models = level["models"] or self.s.models
        llms = {} if self.s.fake_ai else {prov: self.services.llm(prov) for prov, _ in models}
        controllers = {}
        ai_index = 0
        self.voices, self.colors, self.looks, self.models, self.revealed = {}, {}, {}, {}, {}
        for p in players:
            persona = personas[p.seat - 1]
            if self.s.fake_ai:
                ai = ScriptedController(rng, delay=self.s.fake_delay_s)
                self.models[p.seat] = "scripted"
            else:
                prov, model = models[ai_index % len(models)]
                ai = AIController(llms[prov], model, persona, self.s, roleplay=self.cast.roleplay)
                self.models[p.seat] = f"{prov}:{model}"
            if p.is_human:
                controllers[p.seat] = HumanController(self, ai)
                self.colors[p.seat] = HUMAN_COLOR
                self.looks[p.seat] = self.cast.human_look
                self.models[p.seat] = "你"
            else:
                ai_index += 1
                controllers[p.seat] = ai
                self.voices[p.seat] = persona.voice
                self.colors[p.seat] = persona.color
                self.looks[p.seat] = persona.look

        self.game = Game(players, controllers, hooks=self, rng=rng)
        await self.send_state()
        self.task = asyncio.create_task(self._run(self.game))

    async def stop_game(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        self.task = None
        for fut in self._requests.values():
            fut.cancel()
        self._requests.clear()
        for ev in self._playback.values():
            ev.set()

    async def _run(self, game: Game) -> None:
        try:
            winner = await game.run()
            await self.send_state()
            await self.send(
                type="game_over", winner=winner,
                players=[{"seat": p.seat, "name": p.name, "role": p.role.value, "role_zh": p.role.zh,
                          "alive": p.alive, "model": self.models.get(p.seat, "")} for p in game.players.values()],
                thoughts=game.thoughts,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("game crashed")
            await self.send(type="error", message=f"游戏出错：{e}")

    async def close(self) -> None:
        await self.stop_game()
        if self.tts:
            await self.tts.aclose()

    # ------------------------------------------------------- what I can see

    def can_see(self, visible_to: frozenset[int] | None) -> bool:
        return self.spectate or visible_to is None or self.human_seat in visible_to

    def audience(self, game: Game, visible_to: frozenset[int] | None) -> str:
        """Who can see a private item, from the viewer's point of view."""
        if visible_to is None:
            return ""
        wolves = {p.seat for p in game.players.values() if p.role.is_wolf}
        if len(visible_to) > 1 and visible_to <= wolves:
            return "狼人可见" if self.spectate else "仅狼队友可见"
        if self.spectate:
            return f"仅 {game.labels(visible_to)} 可见"
        return "仅你可见"

    def snapshot(self) -> dict:
        g = self.game
        me = g.p(self.human_seat) if g and self.human_seat else None
        players = []
        for p in g.players.values():
            role = None
            mark = None
            if g.phase == "end" or self.spectate:
                role = p.role.value
            elif me and (p.seat == me.seat or (me.role.is_wolf and p.role.is_wolf)):
                role = p.role.value
            elif p.seat in self.revealed:
                role = self.revealed[p.seat]
            if me and me.role is Role.SEER and p.seat in g.seer_results:
                mark = "查杀" if g.seer_results[p.seat] else "金水"
            players.append({
                "seat": p.seat, "name": p.name, "alive": p.alive, "is_human": p.is_human,
                "sheriff": g.sheriff == p.seat, "role": role, "mark": mark, "can_vote": p.can_vote,
                "color": self.colors.get(p.seat, "#999"), "look": self.looks.get(p.seat, {}),
            })
        mine = None
        if me:
            mine = {"seat": me.seat, "role": me.role.value, "role_zh": me.role.zh, "alive": me.alive}
            if me.role is Role.WITCH:
                mine["potions"] = {"antidote": g.antidote, "poison": g.poison}
        return {"type": "state", "day": g.day, "phase": g.phase, "players": players, "me": mine,
                "spectate": self.spectate, "tts": self.tts_on, "cast": self.cast.key,
                "judge": {"name": self.cast.judge_name, "look": self.cast.judge_look}}

    async def send_state(self) -> None:
        if self.game:
            await self.send(**self.snapshot())

    # ------------------------------------------------------------- hooks

    async def on_event(self, game: Game, event: Event) -> None:
        if event.data.get("shooter"):
            self.revealed[event.data["shooter"]] = Role.HUNTER.value
        if event.data.get("idiot"):
            self.revealed[event.data["idiot"]] = Role.IDIOT.value
        if not self.can_see(event.visible_to):
            return
        if self.spectate and "role" in event.data:
            return  # spectators see every role on the seat cards already
        data = {k: v for k, v in event.data.items() if k in ("deaths", "sheriff", "winner", "tally", "votes", "phase")}
        await self.send(type="log", kind=event.kind, text=event.text, day=event.day, phase=event.phase,
                        private=event.visible_to is not None, audience=self.audience(game, event.visible_to),
                        data=data)
        await self.send_state()
        if event.data.get("speak") and event.kind == "narration":
            await self._say(None, event.text, self.cast.judge_voice)

    async def present_speech(
        self, game: Game, player: Player, kind: str, chunks: AsyncIterator[str], visible_to: frozenset[int] | None
    ) -> str:
        if not self.can_see(visible_to):
            return "".join([c async for c in chunks])  # e.g. wolf chat when you're not a wolf
        self._speech_id += 1
        sid = self._speech_id
        await self.send(type="speech_start", id=sid, seat=player.seat, kind=kind, label=SPEECH_KINDS[kind],
                        private=visible_to is not None)
        voice = None if player.is_human else self.voices.get(player.seat)
        text = await self._stream_speech(sid, chunks, voice)
        await self.send(type="speech_end", id=sid, seat=player.seat, text=text)
        return text

    async def _say(self, seat: int | None, text: str, voice: Voice) -> None:
        """Read a line aloud (the judge) and wait until it has been played."""
        if not self.tts_on:
            return
        self._speech_id += 1
        sid = self._speech_id

        async def one() -> AsyncIterator[str]:
            yield text

        await self._stream_speech(sid, one(), voice, show=False)

    async def _stream_speech(self, sid: int, chunks: AsyncIterator[str], voice: Voice | None, show: bool = True) -> str:
        text = ""
        stream = await self._open_tts(voice) if (voice and self.tts_on) else None
        pump = asyncio.create_task(self._pump_audio(sid, stream)) if stream else None
        chunker = SentenceChunker()
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                text += chunk
                if show:
                    await self.send(type="speech_delta", id=sid, text=chunk)
                if stream:
                    for piece in chunker.feed(chunk):
                        await self._tts_send(stream, piece)
            if stream:
                for piece in chunker.flush():
                    await self._tts_send(stream, piece)
                with contextlib.suppress(Exception):
                    await stream.finish()
                with contextlib.suppress(Exception):
                    await pump
                await self.send(type="tts_end", id=sid)
                await self._wait_playback(sid, text)
            elif show:
                await asyncio.sleep(self.s.text_pause_s)  # text only: a short beat between speakers
        finally:
            if pump and not pump.done():
                pump.cancel()
            if stream:
                stream.close()
        return text.strip()

    async def _open_tts(self, voice: Voice):
        key = f"{voice.model}/{voice.voice}"
        try:
            if key in self.bad_voices:
                return await self.tts.open()
            return await self.tts.open(voice=voice.voice, model=voice.model, instructions=voice.instructions)
        except Exception as e:  # noqa: BLE001
            log.warning("TTS voice %s failed (%s); using the default voice", key, e)
            self.bad_voices.add(key)
            try:
                return await self.tts.open()
            except Exception as e2:  # noqa: BLE001
                log.warning("TTS unavailable: %s", e2)
                return None

    async def _tts_send(self, stream, piece: str) -> None:
        piece = clean_for_tts(piece)
        if piece:
            with contextlib.suppress(Exception):
                await stream.send_text(piece)

    async def _pump_audio(self, sid: int, stream) -> None:
        first = True
        try:
            async for pcm in stream.audio():
                if first:
                    first = False
                    self._playback[sid] = asyncio.Event()
                    await self.send(type="audio_start", id=sid, sample_rate=self.tts.sample_rate)
                await self.send_audio(sid, pcm)
        except Exception as e:  # noqa: BLE001
            log.warning("TTS stream failed: %s", e)

    async def _wait_playback(self, sid: int, text: str) -> None:
        ev = self._playback.get(sid)
        if ev is None:
            return  # no audio came back
        try:
            await asyncio.wait_for(ev.wait(), timeout=10 + len(text) * 0.4)
        except TimeoutError:
            log.info("speech %d: browser never confirmed playback; moving on", sid)
        finally:
            self._playback.pop(sid, None)
