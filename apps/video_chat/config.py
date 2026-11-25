"""Settings specific to the video chat app."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.config import env, env_bool, env_int

DEFAULT_SYSTEM_PROMPT = """你是"小星"，一个活泼友好的卡通形象，正在和用户实时视频语音聊天。

说话规则：
- 用自然、口语化的简体中文，像朋友聊天一样。
- 回答要简短，通常一到三句话。用户想深入聊时再展开。
- 你的每个字都会被直接念出来，所以不要用列表、Markdown、表情符号、网址或特殊符号。
- 用户的话来自语音识别，可能有错别字或同音字，请按最合理的意思理解。
- 听不清或不明白时，直接请用户再说一遍。"""

# Names the speech recognizer should favour in this app.
HOTWORDS = "小星"


@dataclass
class ChatSettings:
    system_prompt: str = field(default_factory=lambda: env("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT))
    max_history_messages: int = field(default_factory=lambda: env_int("MAX_HISTORY_MESSAGES", 24))
    # Whether you can interrupt the avatar by talking over it.
    barge_in: bool = field(default_factory=lambda: env_bool("BARGE_IN", True))
    # Minimum characters of interim transcript before we treat it as the user talking over the avatar.
    barge_in_min_chars: int = field(default_factory=lambda: env_int("BARGE_IN_MIN_CHARS", 2))
