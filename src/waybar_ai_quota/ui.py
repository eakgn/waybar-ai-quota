#!/usr/bin/env python3
"""GTK4 + Libadwaita + Gtk4LayerShell açılır kontrol paneli.

Catppuccin Mocha temalı panel; Waybar modülüne tıklandığında sağ üstte açılır,
tekrar tıklandığında (PID dosyası üzerinden) kapanır. `Escape` ve `✕` da
pencereyi kapatır.

Veri katmanı iki kaynaktan beslenir:

1. ``waybar_ai_quota.collectors`` / ``recommender`` modülleri (tercih edilen).
2. Modüller yoksa/başarısız olursa ``cli.py status --json`` veya doğrudan
   yerel SQLite / Antigravity önbelleği / ``state.json`` okuması.

Bu dosya yalnızca görüntüleme ve kalibrasyon yazar; ağ istekleri arka plan
thread'lerinde yapılır, GTK ana döngüsü asla bloklanmaz.
"""

from __future__ import annotations

import asyncio
import ctypes
import dataclasses
import importlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Gtk4LayerShell: GTK4'ten ÖNCE yüklenmeli.
#
# Fedora'da libgtk4-layer-shell, Gtk.GtkWindow tipine bir GType uzantısı
# kaydeder. Python/GObject sürecinde kütüphane GTK'dan sonra yüklenirse
# sembol/GType çakışması yaşanabilir. RTLD_GLOBAL ile erken yüklemek
# gtk4-layer-shell'in resmi olarak önerdiği kullanımdır.
# ---------------------------------------------------------------------------
_LAYER_SHELL_LIB: Any = None
for _candidate in ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so"):
    try:
        _LAYER_SHELL_LIB = ctypes.CDLL(_candidate, mode=ctypes.RTLD_GLOBAL)
        break
    except OSError:
        continue

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
except (ImportError, ValueError) as exc:  # pragma: no cover - ortam hatası
    sys.stderr.write(
        "waybar-ai-quota: GTK4/PyGObject bulunamadı. "
        "Fedora'da 'python3-gobject' ve 'gtk4' paketlerini kurun.\n"
    )
    raise SystemExit(1) from exc

LAYER_SHELL = None
LAYER_SHELL_ERROR: str | None = None
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LAYER_SHELL
except (ImportError, ValueError) as exc:  # pragma: no cover - ortam hatası
    LAYER_SHELL_ERROR = str(exc)
    LAYER_SHELL = None


# ---------------------------------------------------------------------------
# Sabitler ve XDG yolları
# ---------------------------------------------------------------------------

APP_ID = "org.eakgn.WaybarAiQuota"
APP_TITLE = "🤖 AI Kota & Oturum Merkezi"
APP_VERSION = "0.1.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

HOME = Path.home()
_DATA_DIR_OVERRIDE = os.environ.get("WAYBAR_AI_QUOTA_DATA_DIR", "").strip()
DATA_DIR = (
    Path(_DATA_DIR_OVERRIDE)
    if _DATA_DIR_OVERRIDE
    else (Path(os.environ.get("XDG_DATA_HOME", "").strip() or HOME / ".local/share") / "waybar-ai-quota")
)
STATE_FILE = DATA_DIR / "state.json"
PID_FILE = DATA_DIR / "ui.pid"
COPILOT_CACHE_FILE = DATA_DIR / "copilot_cache.json"

OPENCODE_DB = Path(
    os.environ.get("OPENCODE_DB", "").strip()
    or (Path(os.environ.get("XDG_DATA_HOME", "").strip() or HOME / ".local/share") / "opencode" / "opencode.db")
)
ANTIGRAVITY_ACCOUNTS = Path(
    os.environ.get("ANTIGRAVITY_ACCOUNTS_FILE", "").strip()
    or (Path(os.environ.get("XDG_CONFIG_HOME", "").strip() or HOME / ".config") / "opencode" / "antigravity-accounts.json")
)

# UI sırası: Flash, Pro, Argon, GLM/Kimi
GO_POOL_ORDER = ("flash", "premium", "argon", "mid")
GO_POOL_META: dict[str, dict[str, Any]] = {
    "flash": {"label": "DeepSeek V4.1 Flash", "monthly": 100.0, "tag": "$100/ay"},
    "premium": {"label": "DeepSeek V4 Pro / $15 Havuzu", "monthly": 15.0, "tag": "$15/ay"},
    "argon": {"label": "Gemini 4 Argon", "monthly": 30.0, "tag": "$30/ay"},
    "mid": {"label": "GLM-5.3 / Kimi Code", "monthly": 60.0, "tag": "$60/ay"},
}
GO_WINDOWS = (
    ("5h", "5 Saat", 0.20),
    ("weekly", "Hafta", 0.50),
    ("monthly", "Ay", 1.00),
)
CALIBRATION_CHOICES = (
    ("Flash", "flash"),
    ("Pro", "premium"),
    ("Argon", "argon"),
    ("GLM/Kimi", "mid"),
)
AG_GROUP_LABELS = {
    "claude": "Claude (5s Pencere)",
    "gemini-pro": "Gemini Pro / Argon (Haftalık Rezerv)",
    "gemini-flash": "Gemini Flash",
}


# ---------------------------------------------------------------------------
# Genel yardımcılar
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now()


def _fmt_clock(value: datetime | None = None) -> str:
    return (value or _now()).strftime("%H:%M:%S")


def _fmt_tokens(count: int | float | None) -> str:
    try:
        value = float(count or 0)
    except (TypeError, ValueError):
        return "0"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 1_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return f"{int(value)}"


def _fmt_usd(value: float | None, *, compact: bool = False) -> str:
    if value is None:
        return "—"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    if compact and abs(amount) >= 1000:
        return f"${amount / 1000:.1f}k"
    if abs(amount) < 10:
        return f"${amount:.2f}"
    return f"${amount:.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    number = max(0.0, min(100.0, number))
    if abs(number - round(number)) < 0.05:
        return f"{int(round(number))}%"
    return f"{number:.1f}%"


def _fmt_seconds(seconds: float | int | None) -> str:
    """Örn. ``3s 20dk``, ``12dk``, ``45sn``."""
    if seconds is None:
        return "—"
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}g {hours}s"
    if hours:
        return f"{hours}s {minutes}dk"
    if minutes:
        return f"{minutes}dk"
    return f"{secs}sn"


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        if number > 1e11:  # milisaniye
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromtimestamp(float(text))
        except (OverflowError, OSError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None
    return None


def _countdown_text(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return ""
    delta = (parsed - _now()).total_seconds()
    if delta <= 0:
        return "şimdi"
    return _fmt_seconds(delta)


def _age_text(seconds: Any) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 10:
        return "az önce"
    if total < 60:
        return f"{total}sn önce"
    if total < 3600:
        return f"{total // 60}dk önce"
    if total < 86_400:
        return f"{total // 3600}sa önce"
    return f"{total // 86_400}g önce"


def _pct_class(pct: float | None) -> str:
    if pct is None:
        return "bar-blue"
    if pct < 15:
        return "bar-red"
    if pct < 40:
        return "bar-yellow"
    return "bar-green"


def _display_path(path: str) -> str:
    if not path:
        return "—"
    home = str(HOME)
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _basename(path: str) -> str:
    if not path:
        return "bilinmeyen"
    clean = path.rstrip(os.sep)
    if clean in ("", str(HOME)):
        return "~"
    return os.path.basename(clean) or clean


def _safe_text(value: Any, limit: int = 320) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for attr in ("to_dict", "model_dump", "dict"):
        method = getattr(obj, attr, None)
        if callable(method):
            try:
                data = method()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return dataclasses.asdict(obj)
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return list(value.values())
    return [value]


_MISSING = object()


def _pick(obj: Any, *keys: str, default: Any = None) -> Any:
    if obj is None:
        return default
    data = _as_dict(obj) if not isinstance(obj, dict) else obj
    if isinstance(data, dict):
        lower = {str(k).lower(): v for k, v in data.items()}
        for key in keys:
            if key in data:
                return data[key]
            if key.lower() in lower:
                return lower[key.lower()]
    for key in keys:
        if hasattr(obj, key):
            value = getattr(obj, key)
            if value is not None:
                return value
    return default


def _find_dict(obj: Any, *keys: str) -> dict[str, Any]:
    value = _pick(obj, *keys)
    if isinstance(value, dict):
        return value
    return _as_dict(value) if value is not None else {}


def _name_from_model(raw: Any) -> tuple[str, str, str]:
    """(görünen model, model_id, provider_id) döndürür."""
    if isinstance(raw, dict):
        model_id = str(raw.get("id") or raw.get("model") or "").strip()
        provider = str(raw.get("providerID") or raw.get("provider_id") or "").strip()
        display = str(raw.get("name") or "").strip()
        if not display:
            display = model_id or provider
        return display, model_id, provider
    text = str(raw or "").strip()
    if text.startswith("{"):
        try:
            return _name_from_model(json.loads(text))
        except (ValueError, TypeError):
            pass
    return text, text, ""


def _model_label(display: str, model_id: str, provider: str) -> str:
    text = display or model_id
    if not text:
        return "—"
    if provider and "/" not in text:
        return f"{provider}/{text}"
    return text


# ---------------------------------------------------------------------------
# Kanonik görünüm modelleri
# ---------------------------------------------------------------------------


@dataclass
class Session:
    id: str
    directory: str
    title: str
    agent: str
    model: str
    tokens: int
    cost: float
    active: bool
    updated_at: datetime | None = None
    age_seconds: int | None = None
    is_antigravity: bool = False


@dataclass
class QuotaRow:
    key: str
    name: str
    pct: float | None
    reset_text: str = ""
    locked: bool = False
    detail: str = ""


@dataclass
class GoWindow:
    key: str
    label: str
    pct: float | None
    remaining_usd: float | None = None
    limit_usd: float | None = None


@dataclass
class GoPool:
    key: str
    name: str
    monthly_usd: float
    windows: list[GoWindow] = field(default_factory=list)
    remaining_usd: float | None = None
    calibrated: bool = False
    note: str = ""


@dataclass
class CopilotRow:
    key: str
    name: str
    pct: float | None = None
    remaining: float | None = None
    limit: float | None = None
    unlimited: bool = False


@dataclass
class Copilot:
    plan: str = "GitHub Copilot"
    rows: list[CopilotRow] = field(default_factory=list)
    reset_text: str = ""
    available: bool = True


@dataclass
class Rec:
    rank: int
    model_id: str
    reason: str
    title: str = ""
    badge: str = ""
    remaining_pct: float | None = None
    copy_command: str = ""


@dataclass
class Snapshot:
    sessions: list[Session] = field(default_factory=list)
    antigravity: list[QuotaRow] = field(default_factory=list)
    antigravity_plan: str = "Google AI Pro"
    antigravity_accounts: int = 0
    go_pools: list[GoPool] = field(default_factory=list)
    go_total_pct: float | None = None
    copilot: Copilot | None = None
    easy_recs: list[Rec] = field(default_factory=list)
    hard_recs: list[Rec] = field(default_factory=list)
    conflict: bool = False
    conflict_titles: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=_now)
    source: str = "local"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Modül yanıtı -> kanonik Snapshot
# ---------------------------------------------------------------------------


def _pct_from(value: Any, *, fraction_keys: bool = False) -> float | None:
    """Yüzde alanlarını 0-100 aralığına normalize eder."""
    if isinstance(value, dict):
        for key in ("remaining_pct", "remaining_percent", "percent_remaining", "pct", "percent"):
            if key in value:
                return _pct_from(value[key])
        for key in ("remainingFraction", "remaining_fraction", "remaining_ratio", "fraction"):
            if key in value:
                return _pct_from(value[key], fraction_keys=True)
        if "remaining" in value and "entitlement" in value:
            remaining = _as_number(value.get("remaining"))
            total = _as_number(value.get("entitlement"))
            if remaining is not None and total:
                return max(0.0, min(100.0, remaining / total * 100.0))
        for key in ("used_pct", "used_percent", "percent_used", "utilization", "usage_pct"):
            if key in value:
                used = _pct_from(value[key], fraction_keys=key in {"utilization", "usage_pct"})
                if used is not None:
                    return max(0.0, min(100.0, 100.0 - used))
        return None
    number = _as_number(value)
    if number is None:
        return None
    if fraction_keys or (0.0 < number <= 1.0 and number not in (1.0,) and number != int(number)):
        number *= 100.0
    return max(0.0, min(100.0, number))


def _reset_text_from(obj: Any) -> str:
    human = _pick(obj, "reset_human", "resetText", "reset_text", "reset_in_human")
    if human:
        return _safe_text(human, 80)
    seconds = _pick(obj, "reset_in_seconds", "reset_seconds", "seconds_left", "rate_limit_remaining_seconds")
    if seconds is not None:
        return _fmt_seconds(seconds)
    for key in ("resetTime", "reset_time", "reset_at", "reset", "resets_at", "reset_date"):
        value = _pick(obj, key)
        if value:
            countdown = _countdown_text(value)
            if countdown:
                return countdown
            return _safe_text(value, 80)
    return ""


def _sessions_from(data: dict[str, Any]) -> tuple[list[Session], bool, list[str]]:
    raw_obj = data.get("sessions")
    raw = raw_obj if isinstance(raw_obj, (dict, list)) else _as_dict(raw_obj)
    conflict = bool(data.get("dual_antigravity_conflict"))
    titles: list[str] = []

    if isinstance(raw, dict):
        conflict = bool(raw.get("dual_antigravity_conflict", conflict))
        titles = [str(t) for t in _as_list(raw.get("conflict_sessions")) if t]
        items = _as_list(raw.get("sessions"))
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    sessions: list[Session] = []
    for item in items:
        item_dict = _as_dict(item)
        display, model_id, provider = _name_from_model(
            _pick(item_dict, "model", "model_id", default="")
        )
        provider = str(_pick(item_dict, "provider_id", "providerID", default=provider) or provider)
        model_id = str(_pick(item_dict, "model_id", default=model_id) or model_id)
        label = _model_label(display, model_id, provider)
        tokens = _as_number(_pick(item_dict, "tokens_total", "tokens", "total_tokens"))
        if tokens is None:
            tokens = sum(
                _as_number(_pick(item_dict, key, default=0)) or 0.0
                for key in ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")
            )
        is_ag = bool(_pick(item_dict, "is_antigravity", default=False))
        if not is_ag and "antigravity" in f"{label} {model_id} {provider}".lower():
            is_ag = True
        in_flight = bool(_pick(item_dict, "in_flight", "active", "is_active", "running", default=False))
        recently_active = bool(_pick(item_dict, "recently_active", default=False))
        age = _as_number(_pick(item_dict, "updated_age_seconds", "age_seconds"))
        updated = _parse_dt(_pick(item_dict, "updated_at", "time_updated"))
        if age is None and updated is not None:
            age = max(0.0, (_now() - updated).total_seconds())
        # time_suspended alanı bazı sürümlerde hep dolu/boş olabildiği için
        # "çalışıyor" rozetini son aktiviteyle birlikte değerlendir.
        fresh = age is None or age <= 300
        active = (in_flight or recently_active) and fresh
        sessions.append(
            Session(
                id=str(_pick(item_dict, "id", "session_id", default="") or ""),
                directory=str(_pick(item_dict, "directory", "project_dir", default="") or ""),
                title=str(_pick(item_dict, "title", "name", "slug", default="") or ""),
                agent=str(_pick(item_dict, "agent", default="") or ""),
                model=label,
                tokens=int(tokens or 0),
                cost=float(_as_number(_pick(item_dict, "cost_usd", "cost", default=0)) or 0.0),
                active=active,
                updated_at=updated,
                age_seconds=int(age) if age is not None else None,
                is_antigravity=is_ag,
            )
        )
    sessions.sort(key=lambda s: (not s.active, s.age_seconds if s.age_seconds is not None else 10**9))
    return sessions, conflict, titles


def _antigravity_from(data: dict[str, Any]) -> tuple[list[QuotaRow], str, int]:
    section = data.get("antigravity") or {}
    section_dict = _as_dict(section)
    groups = _as_list(_pick(section_dict, "groups", "quotas", "windows", "models", "buckets"))
    plan = str(_pick(section_dict, "plan", "plan_label", default="Google AI Pro") or "Google AI Pro")
    accounts = int(_as_number(_pick(section_dict, "account_count", "accounts", default=0)) or 0)
    rows: list[QuotaRow] = []

    for group in groups:
        group_dict = _as_dict(group)
        key = str(_pick(group_dict, "key", "id", "model", default="") or "")
        core_label = str(_pick(group_dict, "label", "name", "title", default="") or "")
        label = AG_GROUP_LABELS.get(key, core_label or key or "Kota")
        pct = _pct_from(group_dict)
        if pct is None and "remaining_fraction" in group_dict:
            pct = _pct_from(group_dict.get("remaining_fraction"), fraction_keys=True)
        locked = bool(_pick(group_dict, "rate_limited", "locked", "limited", "is_limited", default=False))
        reset_text = _reset_text_from(group_dict)
        rate_text = _safe_text(_pick(group_dict, "rate_limit_human", default=""), 60)
        detail = ""
        if locked and rate_text:
            detail = f"Rate limit: {rate_text}"
        rows.append(QuotaRow(key=key, name=label, pct=pct, reset_text=reset_text, locked=locked, detail=detail))
    return rows, plan, accounts


def _win_key(mapping: dict[str, Any], aliases: Iterable[str]) -> str | None:
    if not isinstance(mapping, dict):
        return None
    lower = {str(k).lower(): k for k in mapping}
    for alias in aliases:
        if alias in mapping:
            return alias
        if alias.lower() in lower:
            return lower[alias.lower()]
    return None


FIVE_HOUR_ALIASES = ("5h", "5H", "5_hour", "five_hour", "five_hours", "5s", "5hour")
WEEKLY_ALIASES = ("weekly", "week", "7d", "haftalik", "hafta")
MONTHLY_ALIASES = ("monthly", "month", "30d", "aylik", "ay")


def _go_from(data: dict[str, Any]) -> tuple[list[GoPool], float | None]:
    section = _as_dict(data.get("go") or {})
    if not section:
        # Bazı sürümler doğrudan "opencode_go" anahtarı kullanabilir.
        section = _as_dict(data.get("opencode_go") or {})
    raw_pools = _as_list(_pick(section, "pools", "buckets", "models"))
    by_key: dict[str, Any] = {}
    for item in raw_pools:
        item_dict = _as_dict(item)
        key = str(_pick(item_dict, "key", "id", default="") or "").lower()
        if key:
            by_key[key] = item_dict
    if not by_key and isinstance(_pick(section, "pools"), dict):
        by_key = {str(k).lower(): v for k, v in section["pools"].items()}

    window_labels = _as_dict(_pick(section, "window_labels", default={}))
    pools: list[GoPool] = []
    order = list(GO_POOL_ORDER)
    for key in by_key:
        if key not in order:
            order.append(key)

    for key in order:
        item = _as_dict(by_key.get(key))
        meta = GO_POOL_META.get(key, {"label": key, "monthly": 0.0, "tag": ""})
        core_label = str(_pick(item, "label", "name", "title", default="") or "")
        label = meta["label"] if key in GO_POOL_META else (core_label or key)
        limits = _find_dict(item, "limits", "limit", "budgets")
        spend = _find_dict(item, "spend", "spent", "usage")
        remaining_pct_map = _find_dict(item, "remaining", "remaining_pct_map")
        pool_pct = _pct_from(_pick(item, "remaining_pct", "percent_remaining"))
        calibrated = bool(_pick(item, "calibrated", default=False))
        windows: list[GoWindow] = []

        for win_key, default_label, fraction in GO_WINDOWS:
            aliases = {"5h": FIVE_HOUR_ALIASES, "weekly": WEEKLY_ALIASES, "monthly": MONTHLY_ALIASES}[win_key]
            limit_key = _win_key(limits, aliases)
            spend_key = _win_key(spend, aliases)
            rem_key = _win_key(remaining_pct_map, aliases)
            limit = _as_number(limits.get(limit_key)) if limit_key else None
            spent = _as_number(spend.get(spend_key)) if spend_key else None
            if limit is None and meta["monthly"]:
                limit = float(meta["monthly"]) * fraction
            if spent is None:
                spent = 0.0
            rem_usd = max(0.0, limit - spent) if limit is not None else None
            pct = None
            if rem_key:
                pct = _pct_from(remaining_pct_map.get(rem_key))
            if pct is None and limit:
                pct = max(0.0, min(100.0, rem_usd / limit * 100.0)) if rem_usd is not None else None
            if pct is None and pool_pct is not None:
                pct = pool_pct
            win_label = str(window_labels.get(win_key) or default_label)
            windows.append(GoWindow(key=win_key, label=win_label, pct=pct, remaining_usd=rem_usd, limit_usd=limit))

        remaining_usd = None
        monthly = next((w for w in windows if w.key == "monthly"), None)
        if monthly is not None:
            remaining_usd = monthly.remaining_usd
        if remaining_usd is None:
            remaining_usd = _as_number(_pick(item, "remaining_usd", "remaining_total_usd"))
        note = ""
        if calibrated:
            note = "kalibre edildi"
        pools.append(
            GoPool(
                key=key,
                name=label,
                monthly_usd=float(meta.get("monthly") or 0.0),
                windows=windows,
                remaining_usd=remaining_usd,
                calibrated=calibrated,
                note=note,
            )
        )

    total = _as_dict(_pick(section, "total", default={}))
    total_pct = _pct_from(_pick(total, "remaining_pct", "percent_remaining"))
    return pools, total_pct


def _copilot_from(data: dict[str, Any]) -> Copilot | None:
    section = _as_dict(data.get("copilot") or data.get("github_copilot") or {})
    if not section:
        return None
    raw_plan = str(_pick(section, "plan_label", "plan", "copilot_plan", "access_type_sku", default="") or "")
    plan = _copilot_plan_label(raw_plan)
    reset_date = _pick(section, "reset_date", "quota_reset_date", "reset_at")
    reset_text = ""
    if reset_date:
        parsed = _parse_dt(reset_date)
        reset_text = parsed.strftime("%d.%m.%Y") if parsed else _safe_text(reset_date, 40)

    quotas_source = _pick(section, "quotas", "quota_snapshots", "quotaSnapshots", "usage")
    quota_items: list[tuple[str, Any]] = []
    if isinstance(quotas_source, dict):
        quota_items = [(str(name), value) for name, value in quotas_source.items()]
    else:
        quota_items = [("", item) for item in _as_list(quotas_source)]
    rows: list[CopilotRow] = []
    for source_key, item in quota_items:
        item_dict = _as_dict(item)
        key = str(_pick(item_dict, "key", "id", default="") or source_key)
        label = str(_pick(item_dict, "label", "name", "title", default="") or key)
        unlimited = bool(_pick(item_dict, "unlimited", default=False))
        pct = _pct_from(item_dict)
        remaining = _as_number(_pick(item_dict, "remaining"))
        limit = _as_number(_pick(item_dict, "entitlement", "limit", "total"))
        if pct is None and remaining is not None and limit:
            pct = max(0.0, min(100.0, remaining / limit * 100.0))
        if unlimited:
            pct = 100.0
        rows.append(
            CopilotRow(
                key=key,
                name=_copilot_row_label(key, label),
                pct=pct,
                remaining=remaining,
                limit=limit,
                unlimited=unlimited,
            )
        )
    if not rows and not raw_plan:
        return None
    available = bool(_pick(section, "available", default=True))
    return Copilot(plan=plan, rows=rows, reset_text=reset_text, available=available)


def _copilot_plan_label(raw: str) -> str:
    text = (raw or "").lower()
    if "student" in text:
        return "Copilot Student"
    if "business" in text or "enterprise" in text:
        return "Copilot Business"
    if "pro" in text:
        return "Copilot Pro"
    if "free" in text or "individual" in text:
        return "Copilot Free"
    if raw:
        return raw if raw.lower().startswith("copilot") else f"Copilot {raw}"
    return "GitHub Copilot"


def _copilot_row_label(key: str, label: str) -> str:
    text = f"{key} {label}".lower()
    if "premium" in text or "credit" in text:
        return "Premium / AI Credits"
    if "completion" in text:
        return "Completions"
    if "chat" in text:
        return "Chat"
    return label or key


def _recommendations_from(data: dict[str, Any]) -> tuple[list[Rec], list[Rec]]:
    section = _as_dict(data.get("recommendations") or data.get("recommendation") or {})
    easy_raw = _pick(section, "easy_medium", "easy", "kolay", "medium", "simple")
    hard_raw = _pick(section, "hard", "zor", "complex", "difficult", "rescue")
    if not section and isinstance(data.get("recommendations"), list):
        easy_raw, hard_raw = [], data["recommendations"]

    def convert(items: Any) -> list[Rec]:
        out: list[Rec] = []
        for item in _as_list(items):
            item_dict = _as_dict(item)
            if not item_dict:
                continue
            rank = int(_as_number(_pick(item_dict, "rank", "order", "position", default=0)) or 0)
            if rank <= 0:
                rank = len(out) + 1
            model_id = str(_pick(item_dict, "model_id", "model", "id", default="") or "")
            command = str(_pick(item_dict, "copy_command", "command", default="") or "")
            out.append(
                Rec(
                    rank=rank,
                    model_id=model_id,
                    reason=_safe_text(_pick(item_dict, "reason", "gerekce", default=""), 400),
                    title=_safe_text(_pick(item_dict, "title", "name", default=""), 80),
                    badge=_safe_text(_pick(item_dict, "badge", default=""), 40),
                    remaining_pct=_pct_from(_pick(item_dict, "remaining_pct", default=None)),
                    copy_command=command or (f"/model {model_id}" if model_id else ""),
                )
            )
        out.sort(key=lambda r: r.rank)
        return out

    return convert(easy_raw), convert(hard_raw)


def snapshot_from_dict(data: dict[str, Any]) -> Snapshot:
    sessions, conflict, conflict_titles = _sessions_from(data)
    ag_rows, ag_plan, ag_accounts = _antigravity_from(data)
    go_pools, go_total = _go_from(data)
    copilot = _copilot_from(data)
    easy, hard = _recommendations_from(data)
    return Snapshot(
        sessions=sessions,
        antigravity=ag_rows,
        antigravity_plan=ag_plan,
        antigravity_accounts=ag_accounts,
        go_pools=go_pools,
        go_total_pct=go_total,
        copilot=copilot,
        easy_recs=easy,
        hard_recs=hard,
        conflict=conflict,
        conflict_titles=conflict_titles,
        updated_at=_now(),
        source="core",
    )


# ---------------------------------------------------------------------------
# Yerel veri okuyucuları (fallback)
# ---------------------------------------------------------------------------


def _local_sessions(limit: int = 8) -> list[Session]:
    if not OPENCODE_DB.is_file():
        return []
    query = """
        SELECT id, directory, title, slug, agent, model, cost,
               tokens_input, tokens_output, tokens_reasoning,
               tokens_cache_read, tokens_cache_write,
               time_updated, time_suspended
        FROM session_v2
        WHERE time_archived IS NULL
        ORDER BY time_updated DESC
        LIMIT ?
    """
    rows: list[Any] = []
    try:
        uri = f"file:{OPENCODE_DB}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            rows = con.execute(query, (limit,)).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []

    sessions: list[Session] = []
    now_ms = time.time() * 1000.0
    for row in rows:
        (sid, directory, title, slug, agent, model_raw, cost,
         tok_in, tok_out, tok_reason, tok_cr, tok_cw, updated_ms, suspended) = row
        display, model_id, provider = _name_from_model(model_raw)
        label = _model_label(display, model_id, provider)
        tokens = sum(int(v or 0) for v in (tok_in, tok_out, tok_reason, tok_cr, tok_cw))
        updated = _parse_dt(updated_ms)
        age = int(max(0.0, (now_ms - float(updated_ms or now_ms)) / 1000.0))
        active = (age <= 90 and suspended is None) or age <= 20
        is_ag = "antigravity" in f"{label} {model_id} {provider}".lower()
        sessions.append(
            Session(
                id=str(sid or ""),
                directory=str(directory or ""),
                title=str(title or slug or "Adsız oturum"),
                agent=str(agent or "build"),
                model=label,
                tokens=tokens,
                cost=float(cost or 0.0),
                active=bool(active),
                updated_at=updated,
                age_seconds=age,
                is_antigravity=is_ag,
            )
        )
    sessions.sort(key=lambda s: (not s.active, s.age_seconds if s.age_seconds is not None else 10**9))
    return sessions


def _local_antigravity() -> tuple[list[QuotaRow], int]:
    if not ANTIGRAVITY_ACCOUNTS.is_file():
        return [], 0
    try:
        with ANTIGRAVITY_ACCOUNTS.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return [], 0
    accounts = [a for a in _as_list(payload.get("accounts")) if isinstance(a, dict)]
    enabled = [a for a in accounts if a.get("enabled", True)] or accounts
    if not enabled:
        return [], 0
    account = enabled[0]
    cached = _find_dict(account, "cachedQuota", "cached_quota", "quota")
    rate_limits = _find_dict(account, "rateLimitResetTimes", "rate_limit_reset_times")
    rows: list[QuotaRow] = []
    now_ts = time.time()
    for key in ("claude", "gemini-pro", "gemini-flash"):
        item = _find_dict(cached, key)
        if not item:
            continue
        fraction = _as_number(_pick(item, "remainingFraction", "remaining_fraction", "remaining_ratio"))
        if fraction is None:
            pct = _pct_from(item)
        else:
            pct = max(0.0, min(100.0, fraction * 100.0))
        reset_raw = _pick(item, "resetTime", "reset_time", "reset_at")
        reset_text = _countdown_text(reset_raw) or ""
        lock_ts = _as_number(rate_limits.get(key))
        locked = bool(lock_ts and lock_ts > now_ts)
        detail = ""
        if locked and lock_ts:
            detail = f"Rate limit: {_fmt_seconds(lock_ts - now_ts)}"
        rows.append(
            QuotaRow(
                key=key,
                name=AG_GROUP_LABELS.get(key, key),
                pct=pct,
                reset_text=reset_text,
                locked=locked,
                detail=detail,
            )
        )
    return rows, len(accounts)


def _pool_for_model(model_id: str) -> str:
    mid = (model_id or "").lower()
    if not mid:
        return "flash"
    if "glm" in mid:
        return "mid"
    if "kimi" in mid:
        return "premium" if re.search(r"k-?3", mid) else "mid"
    if "argon" in mid:
        return "argon"
    if "flash" in mid:
        return "flash"
    if any(token in mid for token in ("pro", "max", "haiku", "gpt", "grok", "luna")):
        return "premium"
    return "flash"


def _load_calibration_map() -> dict[str, dict[str, Any]]:
    try:
        from waybar_ai_quota import config as core_config  # type: ignore

        state = core_config.load_state()
        pools = _as_dict(_pick(state, "go_calibration", default={}))
        return _as_dict(_pick(pools, "pools", default={}))
    except Exception:
        pass
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        pools = _as_dict(_pick(state, "go_calibration", default={}))
        return _as_dict(_pick(pools, "pools", default={}))
    except (OSError, ValueError):
        return {}


def _local_go_pools() -> tuple[list[GoPool], float | None]:
    """opencode.db mesajlarından son 5s/hafta/ay Go harcamasını hesaplar."""
    calibration = _load_calibration_map()
    spends: dict[str, dict[str, float]] = {
        key: {win: 0.0 for win, _, _ in GO_WINDOWS} for key in GO_POOL_ORDER
    }
    if OPENCODE_DB.is_file():
        now_ms = time.time() * 1000.0
        cutoff = now_ms - 31 * 86_400_000
        boundaries = {
            "5h": now_ms - 5 * 3_600_000,
            "weekly": now_ms - 7 * 86_400_000,
            "monthly": now_ms - 30 * 86_400_000,
        }
        try:
            uri = f"file:{OPENCODE_DB}?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
            try:
                rows = con.execute(
                    "SELECT time_created, data FROM session_message "
                    "WHERE type = 'assistant' AND time_created >= ?",
                    (int(cutoff),),
                ).fetchall()
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            rows = []
        for created, raw in rows:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            model = message.get("model") or {}
            if isinstance(model, str):
                model_id = model
                provider = ""
            else:
                model_id = str(model.get("id") or "")
                provider = str(model.get("providerID") or model.get("provider_id") or "")
            if provider and provider != "opencode-go":
                continue
            if not provider and "opencode-go" not in model_id and "go/" not in model_id:
                # Sağlayıcısız kayıtlarda yalnızca bilinen Go modellerini say.
                if not any(tag in model_id.lower() for tag in ("flash", "glm", "kimi", "argon", "deepseek-v4-pro")):
                    continue
            cost = _as_number(message.get("cost"))
            if cost is None:
                tokens = message.get("tokens") or {}
                cost = _as_number(tokens.get("cost")) if isinstance(tokens, dict) else None
            if not cost:
                continue
            pool = _pool_for_model(model_id)
            if pool not in spends:
                continue
            for win_key, _, _ in GO_WINDOWS:
                if float(created or 0) >= boundaries[win_key]:
                    spends[pool][win_key] += float(cost)

    pools: list[GoPool] = []
    total_remaining = 0.0
    total_limit = 0.0
    for key in GO_POOL_ORDER:
        meta = GO_POOL_META[key]
        cal_entry = calibration.get(key) if isinstance(calibration, dict) else None
        cal_pct = None
        if isinstance(cal_entry, dict):
            cal_pct = _as_number(cal_entry.get("remaining_pct"))
        windows: list[GoWindow] = []
        monthly_remaining = None
        monthly_limit = None
        for win_key, win_label, fraction in GO_WINDOWS:
            limit = float(meta["monthly"]) * fraction
            spent = spends[key][win_key]
            remaining = max(0.0, limit - spent)
            pct = 100.0 if limit <= 0 else max(0.0, min(100.0, remaining / limit * 100.0))
            if win_key == "monthly":
                if cal_pct is not None:
                    pct = cal_pct
                    remaining = limit * (pct / 100.0)
                monthly_remaining = remaining
                monthly_limit = limit
                total_remaining += remaining
                total_limit += limit
            windows.append(
                GoWindow(
                    key=win_key,
                    label=win_label,
                    pct=pct,
                    remaining_usd=remaining,
                    limit_usd=limit,
                )
            )
        pools.append(
            GoPool(
                key=key,
                name=meta["label"],
                monthly_usd=float(meta["monthly"]),
                windows=windows,
                remaining_usd=monthly_remaining,
                calibrated=cal_pct is not None,
                note="kalibre edildi" if cal_pct is not None else "",
            )
        )
    total_pct = (total_remaining / total_limit * 100.0) if total_limit > 0 else None
    return pools, total_pct


def _local_copilot() -> Copilot | None:
    payload: Any = None
    if COPILOT_CACHE_FILE.is_file():
        try:
            with COPILOT_CACHE_FILE.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = None
    if payload is None:
        try:
            with STATE_FILE.open("r", encoding="utf-8") as handle:
                payload = _pick(json.load(handle), "copilot", default=None)
        except (OSError, ValueError):
            payload = None
    if not isinstance(payload, dict):
        return None
    if "payload" in payload and isinstance(payload["payload"], dict):
        payload = payload["payload"]
    return _copilot_from({"copilot": payload})


def _fetch_copilot_remote() -> Copilot | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(
            [gh, "api", "/copilot_internal/user"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(COPILOT_CACHE_FILE, payload)
    except OSError:
        pass
    return _copilot_from({"copilot": payload})


def _atomic_write_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            tmp.chmod(mode)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Öneri motoru fallback'i (çekirdek recommender yoksa)
# ---------------------------------------------------------------------------


def _pool_pct(snapshot: Snapshot, key: str) -> float | None:
    for pool in snapshot.go_pools:
        if pool.key == key:
            monthly = next((w for w in pool.windows if w.key == "monthly"), None)
            if monthly is not None:
                return monthly.pct
            return None
    return None


def _ag_pct(snapshot: Snapshot, key: str) -> float | None:
    for row in snapshot.antigravity:
        if row.key == key:
            return row.pct
    return None


def _build_fallback_recs(snapshot: Snapshot) -> None:
    if snapshot.easy_recs and snapshot.hard_recs:
        return
    flash = _pool_pct(snapshot, "flash")
    mid = _pool_pct(snapshot, "mid")
    argon = _pool_pct(snapshot, "argon")
    premium = _pool_pct(snapshot, "premium")
    ag_claude = _ag_pct(snapshot, "claude")
    ag_pro = _ag_pct(snapshot, "gemini-pro")
    conflict = snapshot.conflict

    def reason_pct(value: float | None) -> str:
        return f"%{value:.0f}" if value is not None else "%—"

    if not snapshot.easy_recs:
        easy: list[Rec] = []
        if flash is None or flash > 15:
            easy.append(
                Rec(
                    rank=1,
                    model_id="opencode-go/deepseek-v4.1-flash",
                    title="Go Flash",
                    badge="Önerilen",
                    reason=f"Go Flash aylık kotası {reason_pct(flash)} müsait; günlük kodlama, keşif ve refactor için en ekonomik seçenek.",
                )
            )
        else:
            easy.append(
                Rec(
                    rank=1,
                    model_id="opencode-go/glm-5.3-flash",
                    title="GLM-5.3 Flash",
                    badge="Önerilen",
                    reason=f"Go Flash kotası {reason_pct(flash)} seviyesinde; $60'lık GLM/Kimi ikincil havuzu günlük işler için yeterli.",
                )
            )
        if mid is None or mid > 15:
            easy.append(
                Rec(
                    rank=2,
                    model_id="opencode-go/kimi-k2.7-code",
                    title="Kimi K2.7 Code",
                    badge="Alternatif",
                    reason=f"GLM/Kimi havuzu {reason_pct(mid)} müsait; uzun kod okuma ve orta zorlukta refactor için güçlü alternatif.",
                )
            )
        else:
            easy.append(
                Rec(
                    rank=2,
                    model_id="github-copilot/gpt-4.1",
                    title="Copilot GPT-4.1",
                    badge="Alternatif",
                    reason="Go ikincil havuzu azaldı; Copilot Student/Free temel sohbet kotası pratik bir yedek.",
                )
            )
        easy.append(
            Rec(
                rank=3,
                model_id="opencode/deepseek-v4.1-flash-free",
                title="Free Fallback",
                badge="Yedek",
                reason="Go ve Copilot havuzları tükendiğinde ücretsiz OpenCode modeliyle kesintisiz devam edin.",
            )
        )
        snapshot.easy_recs = easy

    if not snapshot.hard_recs:
        hard: list[Rec] = []
        if ag_claude is not None and ag_claude > 20 and not conflict:
            hard.append(
                Rec(
                    rank=1,
                    model_id="google/antigravity-claude-opus-5-5-high",
                    title="Antigravity Claude Opus",
                    badge="Önerilen",
                    reason=f"Antigravity Claude kotası {reason_pct(ag_claude)} ve çakışma yok; mimari planlama ile derin hata ayıklama için en güçlü seçenek.",
                )
            )
        elif premium is None or premium > 15:
            hard.append(
                Rec(
                    rank=1,
                    model_id="opencode-go/deepseek-v4-pro#max",
                    title="Go DeepSeek V4 Pro",
                    badge="Önerilen",
                    reason=(
                        "Antigravity Claude kotası düşük veya başka sekmede çalışıyor; "
                        f"Go Pro $15 havuzu {reason_pct(premium)} müsait."
                    ),
                )
            )
        else:
            hard.append(
                Rec(
                    rank=1,
                    model_id="opencode-go/gemini-4-argon",
                    title="Go Gemini 4 Argon",
                    badge="Önerilen",
                    reason=f"Claude ve Go Pro sınırlı; Argon haftalık rezervi {reason_pct(argon)} ile zor işler için devrede.",
                )
            )
        if not conflict and (ag_pro is None or ag_pro > 10):
            hard.append(
                Rec(
                    rank=2,
                    model_id="google/antigravity-gemini-pro",
                    title="Antigravity Gemini Pro",
                    badge="Alternatif",
                    reason=f"Antigravity Gemini Pro rezervi {reason_pct(ag_pro)}; uzun bağlamlı mimari analiz için haftalık rezerv.",
                )
            )
        else:
            hard.append(
                Rec(
                    rank=2,
                    model_id="opencode-go/gemini-4-argon",
                    title="Go Gemini 4 Argon",
                    badge="Alternatif",
                    reason=f"Antigravity çakışması/limiti var; Go Argon havuzu {reason_pct(argon)} ile izole çalışır.",
                )
            )
        hard.append(
            Rec(
                rank=3,
                model_id="github-copilot/claude-sonnet-4",
                title="Copilot Claude Sonnet 4",
                badge="Yedek",
                reason="Go ve Antigravity havuzları tükendiğinde Copilot Premium/AI Credits ile derin hata ayıklamaya devam edin.",
            )
        )
        snapshot.hard_recs = hard

    for rec in snapshot.easy_recs + snapshot.hard_recs:
        if not rec.copy_command:
            rec.copy_command = f"/model {rec.model_id}"


# ---------------------------------------------------------------------------
# Veri köprüsü: çekirdek modüller -> CLI -> yerel önbellek
# ---------------------------------------------------------------------------


class DataBridge:
    """UI'nin tek veri giriş noktası. GTK döngüsünü asla bloklamaz."""

    COLLECTOR_FUNCS = (
        "collect_all",
        "collect_full_status",
        "collect_status",
        "collect_dashboard",
        "get_full_status",
        "get_status",
        "gather_status",
        "build_status",
        "collect",
    )

    def __init__(self) -> None:
        self._collectors_module: Any = None
        self._collectors_failed_at = 0.0
        self._cli_disabled_until = 0.0

    # -- çekirdek ---------------------------------------------------------
    def _import_collectors(self) -> Any:
        if self._collectors_module is not None:
            return self._collectors_module
        if self._collectors_failed_at and time.time() - self._collectors_failed_at < 20:
            return None
        try:
            module = importlib.import_module("waybar_ai_quota.collectors")
            self._collectors_module = module
            self._collectors_failed_at = 0.0
            return module
        except Exception:
            self._collectors_failed_at = time.time()
            return None

    @staticmethod
    def _call_with_force(fn: Any, force: bool) -> Any:
        import inspect

        kwargs: dict[str, Any] = {}
        try:
            signature = inspect.signature(fn)
            parameters = signature.parameters
            if "force_refresh" in parameters:
                kwargs["force_refresh"] = force
            elif "force" in parameters:
                kwargs["force"] = force
            elif "refresh" in parameters:
                kwargs["refresh"] = force
            elif any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                kwargs["force_refresh"] = force
        except (TypeError, ValueError):
            pass
        try:
            result = fn(**kwargs)
        except TypeError:
            result = fn()
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        return result

    def _collect_core(self, force: bool) -> dict[str, Any] | None:
        module = self._import_collectors()
        if module is not None:
            for name in self.COLLECTOR_FUNCS:
                fn = getattr(module, name, None)
                if callable(fn):
                    result = self._call_with_force(fn, force)
                    data = self._coerce_result(result)
                    if data is not None:
                        return self._attach_recommendations(data)
        # cli modülünde ortak bir fonksiyon olabilir.
        try:
            cli = importlib.import_module("waybar_ai_quota.cli")
        except Exception:
            return None
        for name in ("get_full_status", "collect_status", "get_status", "build_status"):
            fn = getattr(cli, name, None)
            if callable(fn):
                try:
                    data = self._coerce_result(self._call_with_force(fn, force))
                except Exception:
                    data = None
                if data is not None:
                    return data
        return None

    @staticmethod
    def _attach_recommendations(data: dict[str, Any]) -> dict[str, Any]:
        """collect_all öneri üretmez; çekirdek recommender varsa burada ekle."""
        if data.get("recommendations") is not None:
            return data
        required = ("sessions", "antigravity", "go", "copilot")
        if not all(key in data for key in required):
            return data
        try:
            recommender = importlib.import_module("waybar_ai_quota.recommender")
            build = getattr(recommender, "build_recommendations", None)
            if callable(build):
                enriched = dict(data)
                enriched["recommendations"] = build(
                    data["sessions"], data["antigravity"], data["go"], data["copilot"]
                )
                return enriched
        except Exception:
            pass
        return data

    @staticmethod
    def _coerce_result(result: Any) -> dict[str, Any] | None:
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        if isinstance(result, (tuple, list)) and not isinstance(result, dict):
            merged: dict[str, Any] = {}
            for part in result:
                part_dict = _as_dict(part)
                if part_dict:
                    merged.update(part_dict)
            return merged or None
        data = _as_dict(result)
        return data or None

    # -- CLI --------------------------------------------------------------
    def _collect_cli(self, force: bool) -> dict[str, Any] | None:
        if not force and time.time() < self._cli_disabled_until:
            return None
        candidates: list[str] = [sys.executable]
        venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
        if venv_python.is_file() and str(venv_python) not in candidates:
            candidates.append(str(venv_python))

        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else str(SRC_DIR)

        for python in candidates:
            base_cmd = [python, "-m", "waybar_ai_quota.cli", "status"]
            variants: list[list[str]] = []
            if force:
                variants.append(base_cmd + ["--refresh"])
            # CLI sürümleri JSON'u varsayılan yazdırabilir veya --json isteyebilir.
            variants.append(base_cmd + ["--json"])
            variants.append(base_cmd)
            seen: set[tuple[str, ...]] = set()
            for command in variants:
                signature = tuple(command)
                if signature in seen:
                    continue
                seen.add(signature)
                try:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        env=env,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue
                if result.returncode != 0 or not result.stdout.strip():
                    continue
                payload = self._parse_json_output(result.stdout)
                if payload is not None:
                    return payload
        self._cli_disabled_until = time.time() + 60
        return None

    @staticmethod
    def _parse_json_output(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                return payload
        except ValueError:
            pass
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(stripped[start : end + 1])
                if isinstance(payload, dict):
                    return payload
            except ValueError:
                return None
        return None

    # -- yerel ------------------------------------------------------------
    def _local_snapshot(self, force_remote: bool = False) -> Snapshot:
        sessions = _local_sessions()
        ag_rows, ag_accounts = _local_antigravity()
        go_pools, go_total = _local_go_pools()
        copilot = _local_copilot()
        if copilot is None and force_remote:
            copilot = _fetch_copilot_remote()

        active_ag = [s for s in sessions if s.active and s.is_antigravity]
        conflict = len(active_ag) >= 2
        return Snapshot(
            sessions=sessions,
            antigravity=ag_rows,
            antigravity_accounts=ag_accounts,
            go_pools=go_pools,
            go_total_pct=go_total,
            copilot=copilot,
            conflict=conflict,
            conflict_titles=[s.title or s.id for s in active_ag],
            updated_at=_now(),
            source="local",
        )

    # -- birleşik ---------------------------------------------------------
    def load(self, force: bool = False, local_only: bool = False) -> Snapshot:
        snapshot: Snapshot | None = None
        core_failed = False

        if not local_only:
            try:
                core_data = self._collect_core(force)
            except Exception:
                core_data = None
                core_failed = True
            if core_data:
                try:
                    snapshot = snapshot_from_dict(core_data)
                except Exception:
                    snapshot = None
                    core_failed = True
            if snapshot is None:
                cli_data = self._collect_cli(force)
                if cli_data:
                    try:
                        snapshot = snapshot_from_dict(cli_data)
                        snapshot.source = "cli"
                    except Exception:
                        snapshot = None

        if snapshot is None:
            snapshot = self._local_snapshot(force_remote=force and not local_only)
            snapshot.source = "local"
            if core_failed or (not local_only):
                snapshot.warnings.append("Canlı veri kaynağı kullanılamadı; yerel önbellek gösteriliyor.")
            return self._finalize(snapshot)

        # Eksik bölümleri yerel verilerle doldur.
        if not snapshot.sessions:
            snapshot.sessions = _local_sessions()
        if not snapshot.antigravity:
            rows, accounts = _local_antigravity()
            snapshot.antigravity = rows
            if accounts:
                snapshot.antigravity_accounts = accounts
        if not snapshot.go_pools:
            pools, total = _local_go_pools()
            snapshot.go_pools = pools
            snapshot.go_total_pct = total
        if snapshot.copilot is None or not snapshot.copilot.rows:
            local_copilot = _local_copilot()
            if local_copilot is not None:
                snapshot.copilot = local_copilot

        return self._finalize(snapshot)

    def _finalize(self, snapshot: Snapshot) -> Snapshot:
        if not snapshot.conflict:
            active_ag = [s for s in snapshot.sessions if s.active and s.is_antigravity]
            if len(active_ag) >= 2:
                snapshot.conflict = True
                snapshot.conflict_titles = [s.title or s.id for s in active_ag]
        _build_fallback_recs(snapshot)
        snapshot.updated_at = _now()
        return snapshot

    # -- kalibrasyon ------------------------------------------------------
    def save_calibration(self, target: str, pct: float | None) -> bool:
        saved = False
        try:
            from waybar_ai_quota import config as core_config  # type: ignore

            state = core_config.load_state()
            if pct is None:
                core_config.reset_calibration(state, target)
            else:
                core_config.set_calibration(state, target, remaining_pct=float(pct))
            core_config.save_state(state)
            saved = True
        except Exception:
            saved = self._save_calibration_generic(target, pct)
        signal_waybar()
        return saved

    @staticmethod
    def _save_calibration_generic(target: str, pct: float | None) -> bool:
        try:
            state: dict[str, Any] = {}
            if STATE_FILE.is_file():
                with STATE_FILE.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    state = loaded
            calibration = state.setdefault("go_calibration", {})
            pools = calibration.setdefault("pools", {})
            if pct is None:
                pools.pop(target, None)
            else:
                pools[target] = {
                    "remaining_pct": round(max(0.0, min(100.0, float(pct))), 2),
                    "updated_at": int(time.time()),
                }
            _atomic_write_json(STATE_FILE, state)
            return True
        except (OSError, ValueError, TypeError):
            return False


def signal_waybar() -> None:
    """Waybar'ı önbelleği bırakıp yeniden çizmeye zorlar."""
    try:
        subprocess.run(
            ["pkill", "-RTMIN+8", "waybar"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _copy_to_clipboard(text: str) -> bool:
    try:
        result = subprocess.run(
            ["wl-copy", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        if result.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# CSS — Catppuccin Mocha
# ---------------------------------------------------------------------------

CSS = """
window.aiq-window {
    background-color: transparent;
    box-shadow: none;
}

.aiq-root {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 16px;
    box-shadow: 0 14px 40px rgba(0, 0, 0, 0.45);
}

.aiq-header {
    padding: 14px 16px 10px 16px;
    border-bottom: 1px solid #313244;
}

.aiq-title {
    font-size: 15px;
    font-weight: 800;
    color: #cdd6f4;
}

.aiq-sub {
    font-size: 11px;
    color: #9399b2;
}

.aiq-body {
    padding: 10px 12px 14px 12px;
}

.aiq-section {
    font-size: 13px;
    font-weight: 800;
    color: #cdd6f4;
    margin: 12px 2px 2px 2px;
}

.aiq-section-sub {
    font-size: 11px;
    color: #9399b2;
    margin: 0 2px 2px 2px;
}

.aiq-card {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 12px;
    padding: 10px 12px;
    margin: 5px 0;
}

.aiq-card-title {
    font-size: 12.5px;
    font-weight: 800;
    color: #cdd6f4;
}

.aiq-conflict {
    background-color: rgba(243, 139, 168, 0.13);
    border: 1px solid #f38ba8;
    border-radius: 12px;
    padding: 10px 12px;
    margin: 6px 0;
}

.aiq-conflict-title {
    font-size: 12.5px;
    font-weight: 800;
    color: #f38ba8;
}

.aiq-conflict-body {
    font-size: 11px;
    color: #f5c2e7;
}

.aiq-badge {
    background-color: #313244;
    color: #cdd6f4;
    border-radius: 6px;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 700;
}

.aiq-badge.blue { background-color: rgba(137, 180, 250, 0.18); color: #89b4fa; }
.aiq-badge.green { background-color: rgba(166, 227, 161, 0.18); color: #a6e3a1; }
.aiq-badge.yellow { background-color: rgba(249, 226, 175, 0.18); color: #f9e2af; }
.aiq-badge.red { background-color: rgba(243, 139, 168, 0.18); color: #f38ba8; }
.aiq-badge.purple { background-color: rgba(203, 166, 247, 0.18); color: #cba6f7; }

button.aiq-btn {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 700;
    min-height: 24px;
    box-shadow: none;
}

button.aiq-btn:hover { background-color: #45475a; }

button.aiq-btn.accent {
    background-color: rgba(137, 180, 250, 0.16);
    color: #89b4fa;
    border-color: rgba(137, 180, 250, 0.45);
}

button.aiq-btn.danger {
    background-color: rgba(243, 139, 168, 0.12);
    color: #f38ba8;
    border-color: rgba(243, 139, 168, 0.35);
}

button.aiq-btn.success {
    background-color: rgba(166, 227, 161, 0.16);
    color: #a6e3a1;
    border-color: rgba(166, 227, 161, 0.40);
}

button.aiq-btn.small {
    padding: 2px 8px;
    font-size: 10.5px;
    min-height: 22px;
}

button.aiq-close {
    min-width: 26px;
    min-height: 26px;
    padding: 0;
    border-radius: 8px;
}

progressbar.aiq-bar {
    min-height: 6px;
    min-width: 0;
}

progressbar.aiq-bar trough {
    background-color: #313244;
    border-radius: 4px;
    min-height: 6px;
    min-width: 0;
    border: none;
}

progressbar.aiq-bar trough progress {
    background-color: #89b4fa;
    border-radius: 4px;
    min-height: 6px;
    border: none;
}

progressbar.aiq-bar.bar-green trough progress { background-color: #a6e3a1; }
progressbar.aiq-bar.bar-yellow trough progress { background-color: #f9e2af; }
progressbar.aiq-bar.bar-red trough progress { background-color: #f38ba8; }
progressbar.aiq-bar.bar-blue trough progress { background-color: #89b4fa; }
progressbar.aiq-bar.bar-purple trough progress { background-color: #cba6f7; }

progressbar.aiq-bar text { font-size: 0; color: transparent; }

.aiq-quota-name { font-size: 12px; font-weight: 700; color: #cdd6f4; }
.aiq-quota-meta { font-size: 10.5px; color: #9399b2; }
.aiq-quota-pct {
    font-family: "JetBrains Mono", "Cantarell", monospace;
    font-size: 12px;
    font-weight: 800;
}

.aiq-session {
    border-top: 1px solid rgba(49, 50, 68, 0.70);
    padding-top: 8px;
    margin-top: 8px;
}

.aiq-session:first-child {
    border-top: none;
    padding-top: 0;
    margin-top: 0;
}

.aiq-session-title { font-size: 12.5px; font-weight: 700; color: #cdd6f4; }
.aiq-session-project {
    font-family: "JetBrains Mono", "Cantarell", monospace;
    font-size: 10.5px;
    color: #89b4fa;
}
.aiq-session-meta { font-size: 10.5px; color: #9399b2; }

.aiq-rec {
    border-top: 1px solid rgba(49, 50, 68, 0.70);
    padding-top: 8px;
    margin-top: 8px;
}

.aiq-rec:first-child {
    border-top: none;
    padding-top: 0;
    margin-top: 0;
}

.aiq-rec-model {
    font-family: "JetBrains Mono", "Cantarell", monospace;
    font-size: 11.5px;
    font-weight: 700;
    color: #89b4fa;
}

.aiq-rec-reason { font-size: 11px; color: #a6adc8; }

.aiq-mono { font-family: "JetBrains Mono", "Cantarell", monospace; }
.aiq-green { color: #a6e3a1; }
.aiq-yellow { color: #f9e2af; }
.aiq-red { color: #f38ba8; }
.aiq-blue { color: #89b4fa; }
.aiq-purple { color: #cba6f7; }
.aiq-muted { color: #9399b2; }
.aiq-dim { color: #6c7086; }
.aiq-caption { font-size: 10px; color: #6c7086; }

dropdown > button {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 7px;
    padding: 2px 8px;
    font-size: 11px;
    min-height: 24px;
}

spinbutton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 7px;
}

spinbutton text {
    color: #cdd6f4;
    min-height: 22px;
}

scrollbar { background: transparent; }
scrollbar trough { background: transparent; }
scrollbar slider {
    background-color: #45475a;
    border-radius: 5px;
    min-width: 6px;
    min-height: 28px;
}

toast {
    background-color: #313244;
    color: #cdd6f4;
}
"""

_CSS_PROVIDER: Any = None


def install_css() -> None:
    global _CSS_PROVIDER
    if _CSS_PROVIDER is not None:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_string(CSS)
    except AttributeError:  # GTK < 4.12
        provider.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_PROVIDER = provider


# ---------------------------------------------------------------------------
# Widget yardımcıları
# ---------------------------------------------------------------------------


def _label(
    text: str,
    css: str | None = None,
    *,
    xalign: float = 0.0,
    wrap: bool = False,
    ellipsize: bool = False,
    hexpand: bool = False,
    max_chars: int | None = None,
) -> Any:
    label = Gtk.Label(label=text)
    label.set_xalign(xalign)
    if wrap:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        label.set_ellipsize(Pango.EllipsizeMode.END)
    if max_chars:
        label.set_max_width_chars(max_chars)
    label.set_hexpand(hexpand)
    if css:
        for name in css.split():
            label.add_css_class(name)
    return label


def _badge(text: str, color: str = "") -> Any:
    label = Gtk.Label(label=text)
    label.add_css_class("aiq-badge")
    if color:
        label.add_css_class(color)
    return label


def _bar(pct: float | None, color: str | None = None, width: int = 140) -> Any:
    bar = Gtk.ProgressBar()
    bar.add_css_class("aiq-bar")
    bar.add_css_class(color or _pct_class(pct))
    try:
        value = max(0.0, min(100.0, float(pct))) if pct is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    bar.set_fraction(value / 100.0)
    bar.set_show_text(False)
    bar.set_size_request(width, 6)
    bar.set_hexpand(False)
    return bar


def _spacer(hexpand: bool = True) -> Any:
    box = Gtk.Box()
    box.set_hexpand(hexpand)
    return box


def _clear(box: Any) -> None:
    while True:
        child = box.get_first_child()
        if child is None:
            break
        box.remove(child)


# ---------------------------------------------------------------------------
# Panel penceresi
# ---------------------------------------------------------------------------


class QuotaPanel(Adw.ApplicationWindow):
    def __init__(self, application: Any, bridge: DataBridge) -> None:
        super().__init__(application=application)
        self._bridge = bridge
        self._snapshot: Snapshot | None = None
        self._signature: str = ""
        self._alive = True
        self._refreshing = False
        self._calib_target = "flash"
        self._calib_expanded = False

        self.set_title(APP_TITLE)
        self.set_decorated(LAYER_SHELL is None)
        self.set_resizable(False)
        self.add_css_class("aiq-window")

        self._build_shell()
        self._install_layer_shell()
        self._bind_shortcuts()

        # İlk açılış yerel veriyle anında dolsun; canlı katman arkada gelsin.
        try:
            initial = self._bridge.load(local_only=True)
        except Exception:
            initial = Snapshot(warnings=["Yerel veri okunamadı."])
        self._render(initial, force=True)
        self._update_header(initial)

        GLib.timeout_add(120, self._initial_refresh)
        GLib.timeout_add_seconds(5, self._auto_refresh_tick)

        self.connect("close-request", self._on_close_request)

    # -- yaşam döngüsü ----------------------------------------------------
    def _install_layer_shell(self) -> None:
        if LAYER_SHELL is None:
            return
        try:
            if not LAYER_SHELL.is_supported():
                return
        except Exception:
            pass
        try:
            LAYER_SHELL.init_for_window(self)
            LAYER_SHELL.set_namespace(self, "waybar-ai-quota")
            LAYER_SHELL.set_layer(self, LAYER_SHELL.Layer.OVERLAY)
            LAYER_SHELL.set_anchor(self, LAYER_SHELL.Edge.TOP, True)
            LAYER_SHELL.set_anchor(self, LAYER_SHELL.Edge.RIGHT, True)
            LAYER_SHELL.set_margin(self, LAYER_SHELL.Edge.TOP, 8)
            LAYER_SHELL.set_margin(self, LAYER_SHELL.Edge.RIGHT, 12)
            LAYER_SHELL.set_keyboard_mode(self, LAYER_SHELL.KeyboardMode.ON_DEMAND)
            LAYER_SHELL.set_exclusive_zone(self, 0)
        except Exception:
            pass

    def _bind_shortcuts(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(self, _controller: Any, keyval: int, _keycode: int, _state: Any) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close_panel()
            return True
        return False

    def _on_close_request(self, *_args: Any) -> bool:
        self._alive = False
        remove_pid_file()
        return False

    def close_panel(self) -> None:
        self._alive = False
        remove_pid_file()
        application = self.get_application()
        if application is not None:
            application.quit()

    def _initial_refresh(self) -> bool:
        self._start_refresh(force=False, manual=False)
        return False

    def _auto_refresh_tick(self) -> bool:
        if not self._alive:
            return False
        if not self._refreshing:
            self._start_refresh(force=False, manual=False)
        return True

    # -- iskelet ----------------------------------------------------------
    def _build_shell(self) -> None:
        width = 460
        height = 700
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                monitors = display.get_monitors()
                if monitors.get_n_items() > 0:
                    geometry = monitors.get_item(0).get_geometry()
                    height = max(320, min(760, geometry.height - 80))
        except Exception:
            pass
        self.set_default_size(width, height)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("aiq-root")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("aiq-header")

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        self._title = _label(APP_TITLE, "aiq-title", ellipsize=True, max_chars=34)
        self._subtitle = _label("Son yenileme: --:--:--", "aiq-sub")
        titles.append(self._title)
        titles.append(self._subtitle)
        header.append(titles)

        self._refresh_btn = Gtk.Button(label="🔄 Yenile")
        self._refresh_btn.add_css_class("aiq-btn")
        self._refresh_btn.add_css_class("accent")
        self._refresh_btn.set_tooltip_text("Tüm uzak API'leri ve yerel DB'yi anında yenile")
        self._refresh_btn.connect("clicked", self._on_manual_refresh)
        header.append(self._refresh_btn)

        close_btn = Gtk.Button(label="✕ Kapat")
        close_btn.add_css_class("aiq-btn")
        close_btn.add_css_class("danger")
        close_btn.set_tooltip_text("Kapat (Escape)")
        close_btn.connect("clicked", lambda *_: self.close_panel())
        header.append(close_btn)

        root.append(header)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.add_css_class("aiq-scroll")
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_propagate_natural_height(True)
        self._scrolled.set_max_content_height(760)
        self._scrolled.set_min_content_height(220)
        self._scrolled.set_vexpand(True)
        self._scrolled.set_kinetic_scrolling(True)

        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._body.add_css_class("aiq-body")
        self._scrolled.set_child(self._body)
        root.append(self._scrolled)

        overlay = Adw.ToastOverlay()
        overlay.set_child(root)
        self._toasts = overlay
        self.set_content(overlay)

    # -- yenileme ---------------------------------------------------------
    def _on_manual_refresh(self, *_args: Any) -> None:
        if self._refreshing:
            self._toast("Yenileme zaten sürüyor…")
            return
        self._refresh_btn.set_label("⏳ Yenileniyor...")
        self._refresh_btn.set_sensitive(False)
        self._subtitle.set_text("Yenileniyor...")
        self._start_refresh(force=True, manual=True)

    def _start_refresh(self, force: bool, manual: bool) -> None:
        if self._refreshing or not self._alive:
            return
        self._refreshing = True

        def worker() -> None:
            try:
                snapshot = self._bridge.load(force=force)
            except Exception:
                snapshot = None
            GLib.idle_add(self._on_refreshed, snapshot, manual)

        threading.Thread(target=worker, name="aiq-refresh", daemon=True).start()

    def _on_refreshed(self, snapshot: Snapshot | None, manual: bool) -> bool:
        if not self._alive:
            return False
        if snapshot is not None:
            self._snapshot = snapshot
            self._render(snapshot, force=manual)
            self._update_header(snapshot)
        if manual:
            self._refresh_btn.set_label("🔄 Yenile")
            self._refresh_btn.set_sensitive(True)
            signal_waybar()
            self._toast("Kota verileri yenilendi")
            if snapshot is not None:
                self._subtitle.set_text(f"Son yenileme: {_fmt_clock(snapshot.updated_at)}")
            else:
                self._subtitle.set_text("Yenileme başarısız · yerel veri korunuyor")
        self._refreshing = False
        return False

    def _update_header(self, snapshot: Snapshot) -> None:
        self._subtitle.set_text(f"Son yenileme: {_fmt_clock(snapshot.updated_at)}")

    def _toast(self, message: str) -> None:
        try:
            self._toasts.add_toast(Adw.Toast.new(message))
        except Exception:
            pass

    # -- render -----------------------------------------------------------
    @staticmethod
    def _snapshot_signature(snapshot: Snapshot) -> str:
        return repr(
            (
                snapshot.sessions,
                snapshot.antigravity,
                snapshot.antigravity_accounts,
                snapshot.go_pools,
                snapshot.go_total_pct,
                snapshot.copilot,
                snapshot.easy_recs,
                snapshot.hard_recs,
                snapshot.conflict,
            )
        )

    def _render(self, snapshot: Snapshot, force: bool = False) -> None:
        signature = self._snapshot_signature(snapshot)
        if not force and signature == self._signature:
            self._snapshot = snapshot
            return
        # Kullanıcı kalibrasyon kutusunda yazarken 5 saniyelik yenileme
        # kutuyu yeniden kurmasın; daraltılınca bir sonraki turda tazelenir.
        if self._calib_expanded and not force:
            self._snapshot = snapshot
            return

        self._signature = signature
        self._snapshot = snapshot

        adjustment = self._scrolled.get_vadjustment()
        position = adjustment.get_value() if adjustment is not None else 0.0

        _clear(self._body)
        if snapshot.conflict:
            self._body.append(self._build_conflict(snapshot))
        self._body.append(self._build_sessions(snapshot))

        self._body.append(self._section_header("📊 Abonelik Kotaları", "Katmanlı yerel + canlı kota takibi"))
        self._body.append(self._build_antigravity(snapshot))
        self._body.append(self._build_go(snapshot))
        self._body.append(self._build_copilot(snapshot))

        self._body.append(self._section_header("🧭 Akıllı Model Öneri Motoru", "Görev zorluğuna göre kalan kotaya uygun geçiş"))
        self._body.append(self._build_recommendations(snapshot))

        if snapshot.warnings:
            footer = _label(" • ".join(snapshot.warnings), "aiq-caption", wrap=True)
            footer.set_margin_top(8)
            self._body.append(footer)

        if adjustment is not None:
            GLib.timeout_add(30, self._restore_scroll, adjustment, position)

    def _restore_scroll(self, adjustment: Any, position: float) -> bool:
        if not self._alive:
            return False
        upper = adjustment.get_upper()
        page = adjustment.get_page_size()
        adjustment.set_value(max(0.0, min(position, max(0.0, upper - page))))
        return False

    def _section_header(self, title: str, subtitle: str = "") -> Any:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.append(_label(title, "aiq-section"))
        if subtitle:
            box.append(_label(subtitle, "aiq-section-sub", wrap=True))
        return box

    def _card(self, title: str, icon: str = "") -> tuple[Any, Any]:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        card.add_css_class("aiq-card")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_label = _label(f"{icon} {title}".strip(), "aiq-card-title", hexpand=True, ellipsize=True)
        head.append(title_label)
        card.append(head)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.append(content)
        return card, content

    # -- çakışma ----------------------------------------------------------
    def _build_conflict(self, snapshot: Snapshot) -> Any:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.add_css_class("aiq-conflict")
        card.append(_label("⚠️ Çift Antigravity Oturumu Tespit Edildi!", "aiq-conflict-title"))
        card.append(
            _label(
                "Kota yanmasını önlemek için tek sekmede Antigravity kullanın.",
                "aiq-conflict-body",
                wrap=True,
            )
        )
        if snapshot.conflict_titles:
            names = ", ".join(_safe_text(t, 48) for t in snapshot.conflict_titles[:2])
            card.append(_label(f"Aktif: {names}", "aiq-caption", wrap=True))
        return card

    # -- oturumlar --------------------------------------------------------
    def _build_sessions(self, snapshot: Snapshot) -> Any:
        card, content = self._card("Aktif / Son Çalışan Oturumlar", "⚡")
        head = card.get_first_child()
        if head is not None and isinstance(head, Gtk.Box):
            head.append(_badge(f"{len(snapshot.sessions)} oturum", "blue"))

        if not snapshot.sessions:
            content.append(_label("Aktif veya yakın zamanda kullanılmış OpenCode oturumu yok.", "aiq-muted"))
            return card

        for session in snapshot.sessions[:4]:
            content.append(self._session_row(session))
        return card

    def _session_row(self, session: Session) -> Any:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row.add_css_class("aiq-session")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status = "🟢 Çalışıyor" if session.active else "⚪ Boşta"
        status_label = _label(status, "aiq-green" if session.active else "aiq-muted")
        top.append(status_label)
        project_label = _label(f"📁 {_basename(session.directory)}", "aiq-session-project", ellipsize=True, hexpand=True)
        project_label.set_tooltip_text(_display_path(session.directory))
        top.append(project_label)
        age = _age_text(session.age_seconds)
        if age:
            top.append(_label(age, "aiq-caption"))
        row.append(top)

        row.append(_label(session.title or "Adsız oturum", "aiq-session-title", wrap=True, ellipsize=True, max_chars=46))

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        agent = (session.agent or "build").lower()
        agent_color = {
            "build": "blue",
            "plan": "purple",
            "explore": "green",
            "rescue": "red",
            "general": "blue",
        }.get(agent, "yellow")
        bottom.append(_badge(agent, agent_color))
        model_label = _label(session.model, "aiq-session-meta aiq-mono", ellipsize=True, hexpand=True, max_chars=18)
        model_label.set_tooltip_text(session.model)
        bottom.append(model_label)
        bottom.append(_label(f"{_fmt_tokens(session.tokens)} tok", "aiq-session-meta aiq-mono"))
        bottom.append(_label(_fmt_usd(session.cost), "aiq-session-meta aiq-mono aiq-green"))
        row.append(bottom)
        return row

    # -- Antigravity ------------------------------------------------------
    def _build_antigravity(self, snapshot: Snapshot) -> Any:
        card, content = self._card("Antigravity", "🟣")
        head = card.get_first_child()
        if head is not None and isinstance(head, Gtk.Box):
            head.append(_badge(snapshot.antigravity_plan, "purple"))
            if snapshot.antigravity_accounts:
                head.append(_badge(f"{snapshot.antigravity_accounts} hesap", "blue"))

        if not snapshot.antigravity:
            content.append(_label("Antigravity kota verisi bulunamadı. Yenile'yi deneyin.", "aiq-muted"))
            return card

        for row in snapshot.antigravity:
            content.append(self._quota_row(row))
        return card

    def _quota_row(self, row: QuotaRow) -> Any:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        left.set_hexpand(True)
        left.append(_label(row.name, "aiq-quota-name", wrap=True))
        meta_parts: list[str] = []
        if row.reset_text:
            meta_parts.append(f"Sıfırlanma: {row.reset_text}")
        if row.locked:
            meta_parts.append(f"🔒 {row.detail or 'Rate limit'}")
        left.append(_label(" · ".join(meta_parts) if meta_parts else "Kota verisi hazır", "aiq-quota-meta", wrap=True))

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        right.set_size_request(150, -1)
        pct_color = "aiq-red" if row.locked else (
            "aiq-red" if (row.pct or 0) < 15 else "aiq-yellow" if (row.pct or 0) < 40 else "aiq-green"
        )
        right.append(_label(_fmt_pct(row.pct), f"aiq-quota-pct {pct_color}", xalign=1.0))
        right.append(_bar(row.pct, width=150))

        box.append(left)
        box.append(right)
        return box

    # -- OpenCode Go ------------------------------------------------------
    def _build_go(self, snapshot: Snapshot) -> Any:
        card, content = self._card("OpenCode Go", "⚡")
        head = card.get_first_child()
        if head is not None and isinstance(head, Gtk.Box):
            head.append(_badge("$10/ay · 3 Pencere", "green"))
            if snapshot.go_total_pct is not None:
                total_color = "red" if snapshot.go_total_pct < 15 else "yellow" if snapshot.go_total_pct < 40 else "blue"
                head.append(_badge(f"Toplam {_fmt_pct(snapshot.go_total_pct)}", total_color))

        if not snapshot.go_pools:
            content.append(_label("Go kullanım verisi bulunamadı. Yenile'yi deneyin.", "aiq-muted"))
        else:
            content.append(self._go_grid(snapshot.go_pools))
            content.append(
                _label(
                    "Limit dilimleri: 5 saat = aylık %20 · haftalık = %50 · aylık = %100",
                    "aiq-caption",
                    wrap=True,
                )
            )

        content.append(self._calibration_box(snapshot))
        return card

    def _go_grid(self, pools: list[GoPool]) -> Any:
        grid = Gtk.Grid()
        grid.set_column_spacing(6)
        grid.set_row_spacing(8)

        grid.attach(_label("Havuz", "aiq-caption"), 0, 0, 1, 1)
        for index, (_key, label, _fraction) in enumerate(GO_WINDOWS, start=1):
            cell = _label(label, "aiq-caption", xalign=0.5)
            cell.set_size_request(58, -1)
            grid.attach(cell, index, 0, 1, 1)

        for row_index, pool in enumerate(pools, start=1):
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            left.set_hexpand(True)
            left.set_size_request(164, -1)
            name = _label(pool.name, "aiq-quota-name", wrap=True, max_chars=22)
            left.append(name)
            sub = f"Kalan {_fmt_usd(pool.remaining_usd)} / {_fmt_usd(pool.monthly_usd)}"
            if pool.note:
                sub += f" · {pool.note}"
            left.append(_label(sub, "aiq-caption", wrap=True))
            grid.attach(left, 0, row_index, 1, 1)

            for col_index, window in enumerate(pool.windows, start=1):
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                cell.set_size_request(58, -1)
                color = "aiq-red" if (window.pct or 0) < 15 else "aiq-yellow" if (window.pct or 0) < 40 else "aiq-green"
                cell.append(_label(_fmt_pct(window.pct), f"aiq-quota-pct {color}", xalign=0.5))
                cell.append(_bar(window.pct, width=56))
                if window.remaining_usd is not None:
                    cell.append(_label(_fmt_usd(window.remaining_usd), "aiq-caption", xalign=0.5))
                grid.attach(cell, col_index, row_index, 1, 1)

        return grid

    def _pool_by_key(self, snapshot: Snapshot, key: str) -> GoPool | None:
        for pool in snapshot.go_pools:
            if pool.key == key:
                return pool
        return None

    def _calibration_box(self, snapshot: Snapshot) -> Any:
        expander = Gtk.Expander(label="⚖️ Konsol Kalibrasyonu")
        expander.set_expanded(self._calib_expanded)
        expander.connect("notify::expanded", self._on_calibration_expanded)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(6)
        inner.append(
            _label(
                "opencode.ai/console ekranındaki gerçek kalan yüzdeyi girin; anında state.json ve Waybar güncellenir.",
                "aiq-caption",
                wrap=True,
            )
        )

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        names = [name for name, _key in CALIBRATION_CHOICES]
        dropdown = Gtk.DropDown.new_from_strings(names)
        selected = next(
            (index for index, (_name, key) in enumerate(CALIBRATION_CHOICES) if key == self._calib_target),
            0,
        )
        dropdown.set_selected(selected)
        dropdown.connect("notify::selected", self._on_calibration_target_changed)
        controls.append(dropdown)

        spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        spin.set_numeric(True)
        spin.set_width_chars(4)
        current_pool = self._pool_by_key(snapshot, self._calib_target)
        current_pct = 100.0
        if current_pool is not None:
            monthly = next((w for w in current_pool.windows if w.key == "monthly"), None)
            if monthly is not None and monthly.pct is not None:
                current_pct = monthly.pct
        spin.set_value(round(current_pct))
        self._calib_spin = spin
        controls.append(spin)

        save_btn = Gtk.Button(label="Kaydet")
        save_btn.add_css_class("aiq-btn")
        save_btn.add_css_class("accent")
        save_btn.connect("clicked", self._on_calibration_save)
        controls.append(save_btn)

        reset_btn = Gtk.Button(label="Sıfırla")
        reset_btn.add_css_class("aiq-btn")
        reset_btn.add_css_class("danger")
        reset_btn.connect("clicked", self._on_calibration_reset)
        controls.append(reset_btn)

        inner.append(controls)
        self._calib_status = _label("", "aiq-caption", wrap=True)
        inner.append(self._calib_status)

        expander.set_child(inner)
        return expander

    def _on_calibration_expanded(self, expander: Any, _param: Any) -> None:
        self._calib_expanded = bool(expander.get_expanded())

    def _on_calibration_target_changed(self, dropdown: Any, _param: Any) -> None:
        index = dropdown.get_selected()
        if 0 <= index < len(CALIBRATION_CHOICES):
            self._calib_target = CALIBRATION_CHOICES[index][1]
            if self._snapshot is not None:
                pool = self._pool_by_key(self._snapshot, self._calib_target)
                if pool is not None and hasattr(self, "_calib_spin"):
                    monthly = next((w for w in pool.windows if w.key == "monthly"), None)
                    if monthly is not None and monthly.pct is not None:
                        self._calib_spin.set_value(round(monthly.pct))

    def _on_calibration_save(self, *_args: Any) -> None:
        value = self._calib_spin.get_value()
        ok = self._bridge.save_calibration(self._calib_target, value)
        self._apply_calibration_to_snapshot(self._calib_target, value)
        self._render(self._snapshot or Snapshot(), force=True)
        self._calib_status.set_text("✓ Kaydedildi — ekran ve Waybar yenilendi." if ok else "✗ Kalibrasyon kaydedilemedi.")
        self._toast("Kalibrasyon kaydedildi" if ok else "Kalibrasyon kaydedilemedi")
        self._start_refresh(force=False, manual=False)

    def _on_calibration_reset(self, *_args: Any) -> None:
        ok = self._bridge.save_calibration(self._calib_target, None)
        self._apply_calibration_to_snapshot(self._calib_target, None)
        self._render(self._snapshot or Snapshot(), force=True)
        self._calib_status.set_text("✓ Kalibrasyon sıfırlandı." if ok else "✗ Kalibrasyon sıfırlanamadı.")
        self._toast("Kalibrasyon sıfırlandı" if ok else "Kalibrasyon sıfırlanamadı")
        self._start_refresh(force=False, manual=False)

    def _apply_calibration_to_snapshot(self, target: str, value: float | None) -> None:
        if self._snapshot is None:
            return
        for pool in self._snapshot.go_pools:
            if pool.key != target:
                continue
            pool.calibrated = value is not None
            pool.note = "kalibre edildi" if value is not None else ""
            if value is None:
                continue
            for window in pool.windows:
                window.pct = max(0.0, min(100.0, float(value)))
                if window.limit_usd is not None:
                    window.remaining_usd = window.limit_usd * (window.pct / 100.0)
                if window.key == "monthly":
                    pool.remaining_usd = window.remaining_usd
        self._signature = ""

    # -- Copilot ----------------------------------------------------------
    def _build_copilot(self, snapshot: Snapshot) -> Any:
        card, content = self._card("GitHub Copilot", "🐙")
        head = card.get_first_child()
        copilot = snapshot.copilot
        if head is not None and isinstance(head, Gtk.Box):
            head.append(_badge(copilot.plan if copilot else "Copilot", "blue"))

        if copilot is None:
            content.append(_label("Copilot verisi bulunamadı. 🔄 Yenile canlı sorgular.", "aiq-muted"))
            return card

        if copilot.reset_text:
            content.append(_label(f"Sıfırlanma: {copilot.reset_text}", "aiq-caption"))

        if not copilot.rows:
            content.append(_label(copilot.plan + " · kota ayrıntısı yok (sınırsız temel havuz olabilir).", "aiq-muted", wrap=True))
            return card

        for row in copilot.rows:
            content.append(self._copilot_row(row))
        return card

    def _copilot_row(self, row: CopilotRow) -> Any:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        left.set_hexpand(True)
        left.append(_label(row.name, "aiq-quota-name"))
        no_quota = not row.unlimited and (row.limit is None or row.limit <= 0)
        if row.unlimited:
            meta = "Sınırsız kullanım"
        elif no_quota:
            meta = "Bu planda tanımlı kota yok"
        elif row.limit is not None:
            meta = f"Kalan {_fmt_tokens(row.remaining or 0)} / {_fmt_tokens(row.limit)}"
        else:
            meta = "Kota bilgisi yok"
        left.append(_label(meta, "aiq-quota-meta"))

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        right.set_size_request(140, -1)
        if row.unlimited:
            pct_text = "∞"
        elif no_quota:
            pct_text = "—"
        else:
            pct_text = _fmt_pct(row.pct)
        color = "aiq-green" if row.unlimited else "aiq-dim" if no_quota else (
            "aiq-red" if (row.pct or 0) < 15 else "aiq-yellow" if (row.pct or 0) < 40 else "aiq-green"
        )
        right.append(_label(pct_text, f"aiq-quota-pct {color}", xalign=1.0))
        right.append(_bar(100.0 if row.unlimited else 0.0 if no_quota else row.pct, width=140))

        box.append(left)
        box.append(right)
        return box

    # -- öneriler ---------------------------------------------------------
    def _build_recommendations(self, snapshot: Snapshot) -> Any:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wrapper.append(self._recommendation_group("🟢 Kolay & Orta İşler", "Günlük kodlama, keşif, refactor", snapshot.easy_recs))
        wrapper.append(self._recommendation_group("🔥 Zor İşler", "Mimari planlama, derin hata ayıklama", snapshot.hard_recs))
        return wrapper

    def _recommendation_group(self, title: str, subtitle: str, recs: list[Rec]) -> Any:
        card, content = self._card(title)
        if subtitle:
            content.append(_label(subtitle, "aiq-caption", wrap=True))

        if not recs:
            content.append(_label("Öneri üretilemedi; kota verileri bekleniyor.", "aiq-muted"))
            return card

        for rec in recs[:3]:
            content.append(self._recommendation_row(rec))
        return card

    def _recommendation_row(self, rec: Rec) -> Any:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row.add_css_class("aiq-rec")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        badge_text = "Önerilen" if rec.rank == 1 else "Alternatif" if rec.rank == 2 else "Yedek"
        badge_color = "green" if rec.rank == 1 else "blue" if rec.rank == 2 else "purple"
        top.append(_badge(f"#{rec.rank} {badge_text}", badge_color))

        model_label = _label(rec.model_id or "—", "aiq-rec-model", ellipsize=True, hexpand=True, max_chars=20)
        model_label.set_tooltip_text(rec.model_id)
        top.append(model_label)

        copy_btn = Gtk.Button(label="📋 Kopyala")
        copy_btn.add_css_class("aiq-btn")
        copy_btn.add_css_class("small")
        copy_btn.set_tooltip_text(f"Panoya kopyala: {rec.copy_command or ('/model ' + rec.model_id)}")
        copy_btn.connect("clicked", self._on_copy_clicked, rec)
        top.append(copy_btn)
        row.append(top)

        if rec.title:
            row.append(_label(rec.title, "aiq-session-meta"))
        if rec.reason:
            row.append(_label(rec.reason, "aiq-rec-reason", wrap=True))
        if rec.remaining_pct is not None:
            row.append(_label(f"Kalan kota: {_fmt_pct(rec.remaining_pct)}", "aiq-caption"))
        return row

    def _on_copy_clicked(self, button: Any, rec: Rec) -> None:
        command = rec.copy_command or f"/model {rec.model_id}"
        if _copy_to_clipboard(command):
            button.set_label("✓ Kopyalandı")
            button.add_css_class("success")
            self._toast(f"Kopyalandı: {command}")

            def restore() -> bool:
                if button.get_root() is not None:
                    button.set_label("📋 Kopyala")
                    button.remove_css_class("success")
                return False

            GLib.timeout_add(1500, restore)
        else:
            button.set_label("✗ Kopyalanamadı")

            def restore_error() -> bool:
                if button.get_root() is not None:
                    button.set_label("📋 Kopyala")
                return False

            GLib.timeout_add(1500, restore_error)


# ---------------------------------------------------------------------------
# Uygulama ve PID (tek örnek / toggle)
# ---------------------------------------------------------------------------


class QuotaApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._panel: QuotaPanel | None = None
        self._bridge = DataBridge()

    def do_activate(self) -> None:
        if self._panel is None:
            self._panel = QuotaPanel(self, self._bridge)
        self._panel.present()


def read_pid_file() -> int | None:
    try:
        text = PID_FILE.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_pid_file() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            DATA_DIR.chmod(0o700)
        except OSError:
            pass
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        try:
            PID_FILE.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass


def remove_pid_file() -> None:
    try:
        if read_pid_file() == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def toggle_existing_instance() -> bool:
    """Çalışan bir panel varsa SIGTERM gönderir ve False döndürür."""
    pid = read_pid_file()
    if pid and pid != os.getpid():
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            return False
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def _install_gtk_signals(application: Any) -> None:
    def quit_handler(*_args: Any) -> bool:
        application.quit()
        return False

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, quit_handler)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    # Launcher normalde toggle'ı yapar; doğrudan çalıştırmada da güvenli olsun.
    if "--close" in args:
        pid = read_pid_file()
        if pid and _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        else:
            try:
                PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
        return 0

    if not toggle_existing_instance():
        return 0

    display = Gdk.Display.get_default()
    if display is None:
        sys.stderr.write(
            "waybar-ai-quota: Wayland/GDK görüntüsü bulunamadı. "
            "Sway/Wayland oturumunda çalıştırın.\n"
        )
        return 1

    install_css()
    try:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    except Exception:
        pass

    write_pid_file()
    application = QuotaApplication()
    _install_gtk_signals(application)
    application.connect("shutdown", lambda *_: remove_pid_file())

    if LAYER_SHELL is None:
        sys.stderr.write(
            "waybar-ai-quota: Gtk4LayerShell bulunamadı; normal pencere olarak açılıyor.\n"
        )

    try:
        return int(application.run([sys.argv[0]]) or 0)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    raise SystemExit(main())
