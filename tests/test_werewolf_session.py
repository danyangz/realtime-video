"""The werewolf session (engine <-> browser protocol) with scripted AIs and an auto-playing 'human'."""

import asyncio
import json

from apps.werewolf.config import WerewolfSettings
from apps.werewolf.session import WerewolfSession
from core.config import Settings


class StubServices:
    def __init__(self):
        self.settings = Settings(together_api_key="")  # no TTS
        self.asr = None

    def llm(self, provider=None):
        raise AssertionError("fake AI must not call an LLM")

    def new_tts(self):
        raise AssertionError("no TTS without a key")


def play(role, respond):
    """Run one game; `respond(request) -> response dict` plays for the human."""
    sent = []
    done = asyncio.Event()

    async def main():
        session = None

        async def send_text(s):
            m = json.loads(s)
            sent.append(m)
            if m["type"] == "request":
                asyncio.get_running_loop().call_soon(
                    lambda: asyncio.ensure_future(session.handle({"type": "response", "id": m["id"], **respond(m)})))
            if m["type"] in ("game_over", "error"):
                done.set()

        async def send_bytes(b):
            raise AssertionError("no audio expected")

        session = WerewolfSession(StubServices(), WerewolfSettings(fake_ai=True, fake_delay_s=0, text_pause_s=0, save_games=False), send_text, send_bytes)
        await session.hello()
        await session.handle({"type": "start", "role": role, "name": "测试员", "tts": True})
        await asyncio.wait_for(done.wait(), 60)
        await session.close()
        return session

    session = asyncio.run(main())
    return sent, session


def first_option(req):
    if req["mode"] == "speak":
        return {"value": "我是好人，大家相信我。"}
    return {"value": req["options"][0]["key"]} if req["options"] else {"value": 0}


def test_full_game_as_seer():
    sent, session = play("seer", first_option)
    types = [m["type"] for m in sent]
    assert types[0] == "hello" and "game_over" in types and "error" not in types
    over = next(m for m in sent if m["type"] == "game_over")
    me = next(p for p in over["players"] if p["model"] == "你")
    assert me["role"] == "seer" and me["name"] == "测试员"
    # I only see my own role during the game.
    states = [m for m in sent if m["type"] == "state" and m["phase"] not in ("end",)]
    for st in states:
        known = [p for p in st["players"] if p["role"]]
        assert all(p["is_human"] or p["role"] in ("hunter", "idiot") for p in known)
    # My seer results show up as marks and private logs.
    assert any(m["type"] == "log" and m["private"] and "你查验了" in m["text"] for m in sent)
    # Requests were asked and answered.
    assert any(m["type"] == "request" and m["kind"] == "seer_check" for m in sent)


def test_wolf_sees_teammates_and_wolf_chat():
    sent, _ = play("werewolf", lambda r: {"auto": True})
    st = next(m for m in sent if m["type"] == "state" and m["phase"] == "night")
    wolves = [p for p in st["players"] if p["role"] == "werewolf"]
    assert len(wolves) == 4
    assert any(m["type"] == "speech_start" and m["kind"] == "wolf_chat" for m in sent)


def test_villager_never_sees_wolf_chat_or_others_private_info():
    sent, _ = play("villager", first_option)
    assert not any(m["type"] == "speech_start" and m["kind"] == "wolf_chat" for m in sent)
    priv = [m["text"] for m in sent if m["type"] == "log" and m["private"]]
    assert all("你的身份是【平民】" in t for t in priv), priv


def test_spectator_sees_everything():
    sent, _ = play("spectate", first_option)
    assert not any(m["type"] == "request" for m in sent)
    st = next(m for m in sent if m["type"] == "state")
    assert all(p["role"] for p in st["players"])
    assert any(m["type"] == "speech_start" and m["kind"] == "wolf_chat" for m in sent)
