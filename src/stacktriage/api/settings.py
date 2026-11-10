"""Runtime configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/stacktriage.db"
    api_key: str | None = None
    env: str = "development"  # "production" refuses to start without an API key
    max_upload_bytes: int = 25 * 1024 * 1024
    max_events: int = 200_000
    static_dir: str | None = None
    cors_origins: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        origins = tuple(o.strip() for o in e.get("STACKTRIAGE_CORS_ORIGINS", "").split(",") if o.strip())
        static = e.get("STACKTRIAGE_STATIC_DIR")
        if static is None and Path("frontend/dist/index.html").exists():
            static = "frontend/dist"
        return cls(
            db_path=e.get("STACKTRIAGE_DB", cls.db_path),
            api_key=e.get("STACKTRIAGE_API_KEY") or None,
            env=e.get("STACKTRIAGE_ENV", "development"),
            max_upload_bytes=int(float(e.get("STACKTRIAGE_MAX_UPLOAD_MB", "25")) * 1024 * 1024),
            max_events=int(e.get("STACKTRIAGE_MAX_EVENTS", "200000")),
            static_dir=static,
            cors_origins=origins,
        )

    def validate(self) -> None:
        if self.env == "production" and not self.api_key:
            raise RuntimeError("STACKTRIAGE_ENV=production requires STACKTRIAGE_API_KEY to be set")
        if self.api_key is not None and len(self.api_key) < 16:
            raise RuntimeError("STACKTRIAGE_API_KEY must be at least 16 characters")
