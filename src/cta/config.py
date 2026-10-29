"""Runtime configuration.

Secrets are loaded only from the project-local environment file or the process
environment. Public views expose presence, never the value.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MODE = "mock"
MODES = ("mock", "real")
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
MODEL_KEEPALIVE_SECONDS = 180
DEFAULT_SIM_START = "2026-10-29T00:00:00+08:00"
CHINA_TZ = timezone(timedelta(hours=8))


def find_project_root(start: Path | None = None) -> Path:
    """Locate the repository root by its build file and source package."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "cta").is_dir():
            return candidate
    return Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines without overriding existing variables."""
    target = os.environ
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in target:
            target[key] = value


def parse_instant_with_notes(value: str, *, assume_tz=CHINA_TZ) -> tuple[datetime, list[str]]:
    """Parse an ISO-8601 instant and describe any lenient interpretation."""
    text = value.strip()
    if not text:
        raise ValueError("empty datetime")
    notes: list[str] = []
    if len(text) == 10:
        parsed = datetime.fromisoformat(text).replace(hour=23, minute=59, second=59, tzinfo=assume_tz)
        notes.append("只给到日期，按北京时间当日 23:59:59 解释。")
        return parsed, notes
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=assume_tz)
        notes.append("未带时区，按北京时间解释。")
    return parsed, notes


def parse_instant(value: str, *, assume_tz=CHINA_TZ) -> datetime:
    return parse_instant_with_notes(value, assume_tz=assume_tz)[0]


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Config:
    mode: str
    project_root: Path
    data_root: Path
    mode_dir: Path
    db_path: Path
    exports_dir: Path
    sim_start: datetime
    base_url: str
    model: str
    api_key: str

    @property
    def api_key_present(self) -> bool:
        return bool(self.api_key)

    def model_config_public(self) -> dict:
        return {
            "provider": "deepseek",
            "sdk": "openai",
            "base_url": self.base_url,
            "model": self.model,
            "api_key": "set" if self.api_key_present else "missing",
            "thinking": False,
            "streaming": False,
            "sdk_auto_retries": 0,
            "network_route": "direct",
            "keepalive_expiry_seconds": MODEL_KEEPALIVE_SECONDS,
        }

    def redact(self, text: str) -> str:
        if self.api_key:
            text = text.replace(self.api_key, "***redacted***")
            text = text.replace(json.dumps(self.api_key, ensure_ascii=False)[1:-1], "***redacted***")
        return text

    def redact_data(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_data(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact_data(item) for key, item in value.items()}
        return value


def load_config(mode: str | None = None, data_dir: str | os.PathLike[str] | None = None) -> Config:
    project_root = find_project_root()
    load_env_file(project_root / ".env")

    selected_mode = mode or os.environ.get("CTA_MODE") or DEFAULT_MODE
    if selected_mode not in MODES:
        raise ValueError(f"unsupported mode: {selected_mode}")

    root = Path(data_dir or os.environ.get("CTA_DATA_DIR") or project_root / ".cta-runs").expanduser().resolve()
    mode_dir = root / selected_mode
    sim_start_text = os.environ.get("CTA_SIM_START") or DEFAULT_SIM_START
    try:
        sim_start = parse_instant(sim_start_text)
    except ValueError as exc:  # pragma: no cover - misconfiguration
        raise ValueError(f"invalid CTA_SIM_START: {sim_start_text}") from exc

    return Config(
        mode=selected_mode,
        project_root=project_root,
        data_root=root,
        mode_dir=mode_dir,
        db_path=mode_dir / "tasks.db",
        exports_dir=mode_dir / "exports",
        sim_start=sim_start,
        base_url=os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        model=os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL,
        api_key=os.environ.get("DEEPSEEK_API_KEY") or "",
    )
