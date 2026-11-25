from pathlib import Path

from core.app import AppSpec

from .routes import router

APP = AppSpec(
    slug="werewolf",
    title="狼人杀",
    description="和 11 个 AI 玩一局 12 人标准狼人杀（预女猎白），AI 发言会用不同的声音念出来。",
    router=router,
    static_dir=Path(__file__).parent / "static",
    emoji="🐺",
)
