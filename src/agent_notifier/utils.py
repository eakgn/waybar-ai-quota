"""Paylaşılan zaman, metin ve salt-okunur SQLite yardımcıları."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_now(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_to_ts(value: Any) -> float | None:
    """ISO-8601 metnini veya epoch (sn/ms) değerini saniyeye çevirir."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num / 1000.0 if num > 1e12 else num
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        num = float(text)
        return num / (1000.0 if num > 1e12 else 1.0)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def clamp_pct(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


def format_duration_tr(seconds: float | None) -> str | None:
    """Saniyeyi kısa Türkçe süreye çevirir: `2s 14dk`, `4g 12s`, `45sn`."""
    if seconds is None:
        return None
    total = int(max(0, seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}g")
    if hours:
        parts.append(f"{hours}s")
    if minutes:
        parts.append(f"{minutes}dk")
    if secs and not days:
        parts.append(f"{secs}sn")
    if not parts:
        parts.append("0sn")
    return " ".join(parts[:2])


def short_model_name(model: str, max_chars: int = 20) -> str:
    name = (model or "").split("#", 1)[0]
    if "/" in name:
        name = name.split("/", 1)[1]
    if len(name) > max_chars > 0:
        name = name[: max_chars - 1] + "…"
    return name or "—"


def opencode_db_path() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_OPENCODE_DB", "").strip() or os.environ.get(
        "WAYBAR_AI_QUOTA_OPENCODE_DB", ""
    ).strip()
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local/share"
    return base / "opencode" / "opencode.db"


def ro_connect(path: Path, timeout: float = 2.0) -> sqlite3.Connection:
    """SQLite veritabanını salt-okunur URI ve `query_only` ile açar."""
    uri = "file:" + quote(str(Path(path).expanduser()), safe="/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = 1")
    return con
