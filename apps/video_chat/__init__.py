from pathlib import Path

from core.app import AppSpec

from .routes import router

APP = AppSpec(
    slug="video-chat",
    title="和小星视频聊天",
    description="和卡通形象实时语音聊天，可以随时打断她。",
    router=router,
    static_dir=Path(__file__).parent / "static",
    emoji="🌟",
)
