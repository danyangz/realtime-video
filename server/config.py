"""Settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v else default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


DEFAULT_SYSTEM_PROMPT = """你是"小星"，一个活泼友好的卡通形象，正在和用户实时视频语音聊天。

说话规则：
- 用自然、口语化的简体中文，像朋友聊天一样。
- 回答要简短，通常一到三句话。用户想深入聊时再展开。
- 你的每个字都会被直接念出来，所以不要用列表、Markdown、表情符号、网址或特殊符号。
- 用户的话来自语音识别，可能有错别字或同音字，请按最合理的意思理解。
- 听不清或不明白时，直接请用户再说一遍。"""


# Default LLM per provider. Both are open-weight (Apache 2.0) Qwen models.
DEFAULT_MODELS = {
    "together": "Qwen/Qwen3.5-9B",
    "openrouter": "qwen/qwen3.6-35b-a3b",
}

BASE_URLS = {
    "together": "https://api.together.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


@dataclass
class Settings:
    together_api_key: str = field(default_factory=lambda: _env("TOGETHER_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))

    # --- LLM ---
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "together").lower())
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 400))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.7))
    # Hybrid models (Qwen3.5/3.6 etc.) think by default; thinking adds seconds of latency.
    llm_disable_thinking: bool = field(default_factory=lambda: _env_bool("LLM_DISABLE_THINKING", True))
    system_prompt: str = field(default_factory=lambda: _env("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT))
    max_history_messages: int = field(default_factory=lambda: _env_int("MAX_HISTORY_MESSAGES", 24))

    # --- STT (Together realtime Whisper) ---
    stt_model: str = field(default_factory=lambda: _env("STT_MODEL", "openai/whisper-large-v3"))
    stt_language: str = field(default_factory=lambda: _env("STT_LANGUAGE", "zh"))
    stt_prompt: str = field(default_factory=lambda: _env("STT_PROMPT", "以下是普通话的日常对话，使用简体中文。"))
    vad_threshold: float = field(default_factory=lambda: _env_float("VAD_THRESHOLD", 0.3))
    # How long you must pause before your turn is considered finished.
    vad_min_silence_ms: int = field(default_factory=lambda: _env_int("VAD_MIN_SILENCE_MS", 600))
    vad_min_speech_ms: int = field(default_factory=lambda: _env_int("VAD_MIN_SPEECH_MS", 250))
    # Server default is 5s, which would cut long sentences into separate "turns".
    vad_max_speech_s: float = field(default_factory=lambda: _env_float("VAD_MAX_SPEECH_S", 30.0))
    vad_speech_pad_ms: int = field(default_factory=lambda: _env_int("VAD_SPEECH_PAD_MS", 250))

    # --- TTS (Together websocket) ---
    tts_model: str = field(default_factory=lambda: _env("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B"))
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "vivian"))
    tts_language: str = field(default_factory=lambda: _env("TTS_LANGUAGE", "zh"))
    tts_sample_rate: int = field(default_factory=lambda: _env_int("TTS_SAMPLE_RATE", 24000))
    tts_instructions: str = field(
        default_factory=lambda: _env("TTS_INSTRUCTIONS", "用自然、亲切、轻松的语气说话，像和朋友聊天。")
    )

    # --- Conversation behaviour ---
    barge_in: bool = field(default_factory=lambda: _env_bool("BARGE_IN", True))
    # Minimum characters of interim transcript before we treat it as the user talking over the avatar.
    barge_in_min_chars: int = field(default_factory=lambda: _env_int("BARGE_IN_MIN_CHARS", 2))

    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))

    def __post_init__(self) -> None:
        if self.llm_provider not in BASE_URLS:
            raise ValueError(f"LLM_PROVIDER must be one of {list(BASE_URLS)}, got {self.llm_provider!r}")
        if not self.llm_model:
            self.llm_model = DEFAULT_MODELS[self.llm_provider]

    @property
    def llm_base_url(self) -> str:
        return BASE_URLS[self.llm_provider]

    @property
    def llm_api_key(self) -> str:
        return self.openrouter_api_key if self.llm_provider == "openrouter" else self.together_api_key

    def problems(self) -> list[str]:
        """Human-readable configuration problems (missing keys etc.)."""
        out = []
        if not self.together_api_key:
            out.append("缺少 TOGETHER_API_KEY（语音识别和 TTS 需要它）")
        if self.llm_provider == "openrouter" and not self.openrouter_api_key:
            out.append("LLM_PROVIDER=openrouter 但缺少 OPENROUTER_API_KEY")
        return out
