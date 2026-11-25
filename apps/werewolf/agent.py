"""AI player: an open-weight LLM behind the engine's Controller interface."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from core.llm import LLM

from . import prompts
from .config import WerewolfSettings
from .engine import Game, Option, Player
from .personas import Persona

log = logging.getLogger("werewolf.agent")

SPEECH_MARK = re.compile(r"【\s*发言\s*】[:：]?\s*")
THOUGHT_MARK = re.compile(r"【\s*思考\s*】[:：]?\s*")
CHOICE_MARK = re.compile(r"【\s*选择\s*】[:：]?\s*(-?\d+)")
NEXT_TAG = re.compile(r"【\s*(思考|选择|发言)\s*】")


def clean_speech(text: str) -> str:
    t = text.strip().strip("“”\"'「」").replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", t).strip()


class SpeechParser:
    """Streams only the part after 【发言】; everything before it is the private thought."""

    def __init__(self) -> None:
        self.buf = ""  # text before 【发言】
        self.raw = ""  # text after 【发言】
        self.emitted = 0
        self.started = False
        self.stopped = False
        self.thought = ""

    @property
    def speech(self) -> str:
        return self.raw[: self.emitted]

    def feed(self, delta: str) -> str:
        if self.stopped:
            return ""
        if not self.started:
            self.buf += delta
            m = SPEECH_MARK.search(self.buf)
            if not m:
                return ""
            self.started = True
            self.thought = THOUGHT_MARK.sub("", self.buf[: m.start()]).strip()
            delta = self.buf[m.end():]
        self.raw += delta
        stop = NEXT_TAG.search(self.raw)
        if stop:  # the model started another section after the speech
            self.stopped = True
            end = stop.start()
        else:
            end = len(self.raw)
            open_bracket = self.raw.rfind("【")
            if open_bracket != -1 and "】" not in self.raw[open_bracket:]:
                end = open_bracket  # might be the start of a tag: hold it back
        new = self.raw[self.emitted:end] if end > self.emitted else ""
        self.emitted = max(self.emitted, end)
        return new

    def finish(self) -> str:
        """Call at the end of the stream; returns any text still held back."""
        if self.started:
            if self.stopped:
                return ""
            new = self.raw[self.emitted:]
            self.emitted = len(self.raw)
            return new
        # 【发言】 never appeared: treat the reply (minus a thought tag) as the speech.
        self.raw = THOUGHT_MARK.sub("", self.buf).strip()
        self.emitted = len(self.raw)
        return self.raw


def parse_choice(text: str, options: list[Option], allow_skip: bool) -> tuple[int | None, str]:
    valid = {o.key for o in options} | ({0} if allow_skip else set())
    thought = THOUGHT_MARK.sub("", CHOICE_MARK.split(text)[0]).strip()
    m = CHOICE_MARK.findall(text)
    if m:
        key = int(m[-1])
        return (key if key in valid else None), thought
    # No tag: accept a lone number at the end.
    tail = re.findall(r"(\d+)\s*$", text.strip())
    if tail and int(tail[0]) in valid:
        return int(tail[0]), thought
    return None, thought


class AIController:
    def __init__(
        self, llm: LLM, model: str, persona: Persona, settings: WerewolfSettings, roleplay: str = ""
    ) -> None:
        self.llm = llm
        self.model = model
        self.persona = persona
        self.roleplay = roleplay
        self.s = settings
        self._system: str | None = None

    @property
    def label(self) -> str:
        return f"{self.llm.provider}:{self.model}"

    def _messages(self, game: Game, me: Player, task: str) -> list[dict]:
        if self._system is None:
            self._system = prompts.system_prompt(game, me, self.persona, self.s.speech_max_chars, self.roleplay)
        return [
            {"role": "system", "content": self._system},
            {"role": "user", "content": prompts.user_message(game, me, task)},
        ]

    def _record(self, game: Game, me: Player, kind: str, thought: str, output: str) -> None:
        game.thoughts.append({
            "seat": me.seat, "day": game.day, "phase": game.phase, "kind": kind,
            "label": prompts.kind_label(kind), "thought": thought, "output": output,
        })

    async def speak(self, game: Game, player: Player, kind: str, prompt: str) -> AsyncIterator[str]:
        messages = self._messages(game, player, prompts.speech_task(kind, self.s.speech_max_chars))
        parser = SpeechParser()
        first = True
        async for delta in self.llm.stream(
            messages, model=self.model, max_tokens=700, temperature=self.s.temperature,
            disable_thinking=self.s.disable_thinking,
        ):
            out = parser.feed(delta)
            if first:
                out = out.lstrip(" \n“\"「")  # no leading quote marks
            if out:
                first = False
                yield out.replace("*", "")
        rest = parser.finish()
        if first:
            rest = rest.lstrip(" \n“\"「")
        if rest.strip():
            yield rest.replace("*", "")
        self._record(game, player, kind, parser.thought, clean_speech(parser.speech))

    async def choose(
        self, game: Game, player: Player, kind: str, prompt: str, options: list[Option], allow_skip: bool
    ) -> int:
        messages = self._messages(game, player, prompts.choice_task(kind, prompt, options, allow_skip))
        text = ""
        for attempt in range(2):
            text = await self.llm.complete(
                messages, model=self.model, max_tokens=400, temperature=0.4,
                disable_thinking=self.s.disable_thinking,
            )
            key, thought = parse_choice(text, options, allow_skip)
            if key is not None:
                label = next((o.label for o in options if o.key == key), prompts.SKIP_LABELS.get(kind, "跳过"))
                self._record(game, player, kind, thought, label)
                return key
            log.info("seat %d gave no valid choice for %s (attempt %d): %r", player.seat, kind, attempt, text[-200:])
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "你的回答里没有有效的选择。请只输出【选择】加上一个可选项的数字。"},
            ]
        raise ValueError(f"no valid choice from model: {text[-200:]!r}")
