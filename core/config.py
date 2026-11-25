"""Shared settings for all apps, read from environment variables (and an optional .env file).

App-specific settings live next to each app (e.g. apps/video_chat/config.py) and can use
the env helpers below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    v = env(name)
    return int(v) if v else default


def env_float(name: str, default: float) -> float:
    v = env(name)
    return float(v) if v else default


def env_bool(name: str, default: bool) -> bool:
    v = env(name).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _default_stt_engine() -> str:
    """Local Qwen3-ASR on Apple Silicon when its packages are installed, otherwise Together."""
    import importlib.util
    import platform

    if platform.system() == "Darwin" and platform.machine() == "arm64" and importlib.util.find_spec("mlx_qwen3_asr"):
        return "local"
    return "together"


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
    together_api_key: str = field(default_factory=lambda: env("TOGETHER_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: env("OPENROUTER_API_KEY"))

    # --- LLM (defaults; apps may pick other models per call) ---
    llm_provider: str = field(default_factory=lambda: env("LLM_PROVIDER", "together").lower())
    llm_model: str = field(default_factory=lambda: env("LLM_MODEL"))
    llm_max_tokens: int = field(default_factory=lambda: env_int("LLM_MAX_TOKENS", 400))
    llm_temperature: float = field(default_factory=lambda: env_float("LLM_TEMPERATURE", 0.7))
    # Hybrid models (Qwen3.5/3.6 etc.) think by default; thinking adds seconds of latency.
    llm_disable_thinking: bool = field(default_factory=lambda: env_bool("LLM_DISABLE_THINKING", True))

    # --- STT ---
    # "local": Silero VAD + Qwen3-ASR on this Mac (Apple Silicon); "together": Together realtime Whisper.
    stt_engine: str = field(default_factory=lambda: env("STT_ENGINE", _default_stt_engine()).lower())
    # Local engine: accurate model for the final transcript, small fast one for live captions.
    asr_model: str = field(default_factory=lambda: env("ASR_MODEL", "mlx-community/Qwen3-ASR-1.7B-8bit"))
    asr_partial_model: str = field(default_factory=lambda: env("ASR_PARTIAL_MODEL", "mlx-community/Qwen3-ASR-0.6B-8bit"))
    # Extra space-separated names/terms Qwen3-ASR should favour, added to each app's own hotwords.
    asr_hotwords: str = field(default_factory=lambda: env("ASR_HOTWORDS", ""))
    # Also give the ASR the last thing the app said as context, so follow-up words match the topic.
    asr_dialog_context: bool = field(default_factory=lambda: env_bool("ASR_DIALOG_CONTEXT", True))
    asr_partial_interval_ms: int = field(default_factory=lambda: env_int("ASR_PARTIAL_INTERVAL_MS", 700))
    local_vad_threshold: float = field(default_factory=lambda: env_float("LOCAL_VAD_THRESHOLD", 0.5))
    # Start the accurate transcription speculatively after this much silence.
    vad_eager_ms: int = field(default_factory=lambda: env_int("VAD_EAGER_MS", 250))
    # Save mic audio + each utterance + transcripts under out/debug/ for diagnosing recognition.
    debug_audio: bool = field(default_factory=lambda: env_bool("DEBUG_AUDIO", False))
    debug_dir: Path = ROOT / "out" / "debug"
    # Together engine:
    stt_model: str = field(default_factory=lambda: env("STT_MODEL", "openai/whisper-large-v3"))
    stt_language: str = field(default_factory=lambda: env("STT_LANGUAGE", "zh"))
    stt_prompt: str = field(default_factory=lambda: env("STT_PROMPT", "以下是普通话的日常对话，使用简体中文。"))
    vad_threshold: float = field(default_factory=lambda: env_float("VAD_THRESHOLD", 0.3))
    # How long you must pause before your turn is considered finished.
    vad_min_silence_ms: int = field(default_factory=lambda: env_int("VAD_MIN_SILENCE_MS", 600))
    vad_min_speech_ms: int = field(default_factory=lambda: env_int("VAD_MIN_SPEECH_MS", 250))
    # Server default is 5s, which would cut long sentences into separate "turns".
    vad_max_speech_s: float = field(default_factory=lambda: env_float("VAD_MAX_SPEECH_S", 30.0))
    vad_speech_pad_ms: int = field(default_factory=lambda: env_int("VAD_SPEECH_PAD_MS", 250))

    # --- TTS (Together websocket) ---
    tts_model: str = field(default_factory=lambda: env("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B"))
    tts_voice: str = field(default_factory=lambda: env("TTS_VOICE", "vivian"))
    tts_language: str = field(default_factory=lambda: env("TTS_LANGUAGE", "zh"))
    tts_sample_rate: int = field(default_factory=lambda: env_int("TTS_SAMPLE_RATE", 24000))
    tts_instructions: str = field(
        default_factory=lambda: env("TTS_INSTRUCTIONS", "用自然、亲切、轻松的语气说话，像和朋友聊天。")
    )

    host: str = field(default_factory=lambda: env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: env_int("PORT", 8000))

    def __post_init__(self) -> None:
        if self.llm_provider not in BASE_URLS:
            raise ValueError(f"LLM_PROVIDER must be one of {list(BASE_URLS)}, got {self.llm_provider!r}")
        if self.stt_engine not in ("local", "together"):
            raise ValueError(f"STT_ENGINE must be 'local' or 'together', got {self.stt_engine!r}")
        if not self.llm_model:
            self.llm_model = DEFAULT_MODELS[self.llm_provider]

    @property
    def llm_base_url(self) -> str:
        return BASE_URLS[self.llm_provider]

    @property
    def llm_api_key(self) -> str:
        return self.api_key_for(self.llm_provider)

    def api_key_for(self, provider: str) -> str:
        return self.openrouter_api_key if provider == "openrouter" else self.together_api_key

    def problems(self) -> list[str]:
        """Human-readable configuration problems (missing keys etc.)."""
        out = []
        if not self.together_api_key:
            out.append("缺少 TOGETHER_API_KEY（TTS 和默认的 LLM 需要它）")
        if self.llm_provider == "openrouter" and not self.openrouter_api_key:
            out.append("LLM_PROVIDER=openrouter 但缺少 OPENROUTER_API_KEY")
        return out
