"""
Runtime configuration. All env vars are read here, defaults documented.

The only HARD requirement to start is nothing — even FRIGATE_URL has a default,
and PHANTOM_MODE=1 boots without any external dependency.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    return v.strip() in ("1", "true", "True", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    # Operation mode
    phantom_mode: bool

    # Frigate
    frigate_url: str
    cameras_filter: list[str]  # empty = all

    # Claude
    anthropic_api_key: str  # empty string = Claude layer disabled
    model: str
    token_budget: int

    # Scheduling
    interval_seconds: int

    # ZMA optional status webhook
    zma_webhook_url: str

    # Dashboard
    dashboard_host: str
    dashboard_port: int

    # Data location
    data_dir: Path
    db_path: Path

    @property
    def claude_enabled(self) -> bool:
        """Claude layer is opt-in via API key presence — phantom forces off."""
        return bool(self.anthropic_api_key) and not self.phantom_mode


def load() -> Config:
    data_dir = Path(os.environ.get("OPTIMIZER_DATA_DIR", "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        phantom_mode=_env_bool("PHANTOM_MODE"),
        frigate_url=os.environ.get("FRIGATE_URL", "http://192.168.50.6:5000").rstrip("/"),
        cameras_filter=_env_list("OPTIMIZER_CAMERAS"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        model=os.environ.get("OPTIMIZER_MODEL", "claude-haiku-4-5").strip(),
        token_budget=_env_int("OPTIMIZER_TOKEN_BUDGET", 20000),
        interval_seconds=_env_int("OPTIMIZER_INTERVAL_SECONDS", 86400),
        zma_webhook_url=os.environ.get("ZMA_WEBHOOK_URL", "").strip(),
        dashboard_host=os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=_env_int("DASHBOARD_PORT", 5004),
        data_dir=data_dir,
        db_path=Path(os.environ.get("OPTIMIZER_DB_PATH", data_dir / "optimizer.db")),
    )
