"""App registry.

To add an app: create apps/<name>/ whose __init__.py defines `APP = AppSpec(...)`
(see core/app.py), then list it below. The server mounts it at /<slug>/.
"""

from core.app import AppSpec

from .video_chat import APP as VIDEO_CHAT
from .werewolf import APP as WEREWOLF

APPS: list[AppSpec] = [WEREWOLF, VIDEO_CHAT]
