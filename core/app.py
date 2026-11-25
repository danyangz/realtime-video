"""What an app provides to the server. See apps/__init__.py for the registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter


@dataclass(frozen=True)
class AppSpec:
    slug: str  # URL prefix: the app lives at /<slug>/
    title: str
    description: str
    router: APIRouter  # routes (e.g. /ws), mounted under /<slug>
    static_dir: Path  # index.html and assets, served at /<slug>/
    emoji: str = ""
