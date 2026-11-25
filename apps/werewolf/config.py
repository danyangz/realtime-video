"""Settings specific to the werewolf app (env vars prefixed WEREWOLF_)."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.config import env, env_bool, env_float, env_int

# Open-weight models only. "provider:model"; several (comma-separated) are assigned to AI
# players round-robin, e.g. to see which model plays best.
DEFAULT_MODELS = "together:deepseek-ai/DeepSeek-V4.1-Flash"

# "AI 水平" on the start screen. Scores are from scripts/werewolf_eval.py (9 situations with a
# clear best move); "standard" can be overridden with WEREWOLF_MODELS.
LEVELS = {
    "standard": {"title": "标准", "desc": "DeepSeek-V4.1-Flash：反应快、费用低", "models": None},
    "strong": {"title": "高手", "desc": "Kimi-K3：更会玩，每步多想几秒，费用约 10 倍",
               "models": [("together", "moonshotai/Kimi-K3")]},
}


def _models() -> list[tuple[str, str]]:
    out = []
    for spec in env("WEREWOLF_MODELS", DEFAULT_MODELS).split(","):
        spec = spec.strip()
        if not spec:
            continue
        provider, _, model = spec.partition(":")
        if not model:  # bare model name: default provider
            provider, model = env("LLM_PROVIDER", "together"), provider
        out.append((provider.lower(), model))
    return out


@dataclass
class WerewolfSettings:
    models: list[tuple[str, str]] = field(default_factory=_models)
    speech_max_chars: int = field(default_factory=lambda: env_int("WEREWOLF_SPEECH_CHARS", 150))
    temperature: float = field(default_factory=lambda: env_float("WEREWOLF_TEMPERATURE", 0.8))
    disable_thinking: bool = field(default_factory=lambda: env_bool("WEREWOLF_DISABLE_THINKING", True))
    # Scripted bots instead of LLMs: for UI work and demos without API calls.
    fake_ai: bool = field(default_factory=lambda: env_bool("WEREWOLF_FAKE_AI", False))
    fake_delay_s: float = 0.05  # scripted bots "type" this slowly
    text_pause_s: float = 0.6  # pause between speakers when voice is off
