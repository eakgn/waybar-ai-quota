"""XDG yolları, yapılandırma ve yerel durum (state.json) yönetimi.

Tüm yollar XDG standartlarından dinamik çözülür. Durum dosyası
`~/.local/share/waybar-ai-quota/state.json` altında, 0600 izinleriyle
atomik olarak yazılır. Gizli veri bu dosyada tutulmaz.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - yalnızca çok eski yorumlayıcılar
    tomllib = None  # type: ignore[assignment]

APP_NAME = "waybar-ai-quota"
CALIBRATION_TARGETS = ("flash", "mid", "argon", "premium", "total")

DEFAULT_CONFIG: dict[str, Any] = {
    "refresh": {
        "local_active_seconds": 5,
        "local_idle_seconds": 15,
        "remote_seconds": 600,
        "antigravity_cache_seconds": 600,
        "copilot_cache_seconds": 600,
    },
    "thresholds": {
        "critical_pct": 15,
        "antigravity_primary_pct": 20,
        "recent_activity_seconds": 300,
        "conflict_window_seconds": 90,
    },
    "cloud": {
        "timeout_seconds": 10,
        "antigravity_live_refresh": True,
    },
    "antigravity": {
        "client_id": "",
        "client_secret": "",
    },
    "copilot": {
        "gh_binary": "",
        "plan_label_override": "",
    },
    "notifications": {
        "enabled": True,
        "cooldown_seconds": 1800,
        "app_name": APP_NAME,
    },
    "waybar": {
        "max_model_chars": 22,
        "idle_format": "compact",
    },
    "ui": {
        "gtk_python": "/usr/bin/python3",
    },
}

DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "go_calibration": {"pools": {}, "total": {}},
    "notifications": {},
}


# ---------------------------------------------------------------------------
# Yollar
# ---------------------------------------------------------------------------


def _xdg_dir(env_name: str, fallback: str) -> Path:
    value = os.environ.get(env_name, "").strip()
    base = Path(value) if value else Path.home() / fallback
    return base / APP_NAME


def config_dir() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_CONFIG_DIR", "").strip()
    return Path(override) if override else _xdg_dir("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_DATA_DIR", "").strip()
    return Path(override) if override else _xdg_dir("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    return _xdg_dir("XDG_CACHE_HOME", ".cache")


def config_path() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_CONFIG", "").strip()
    return Path(override) if override else config_dir() / "config.toml"


def state_path() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_STATE", "").strip()
    return Path(override) if override else data_dir() / "state.json"


def copilot_cache_path() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_COPILOT_CACHE", "").strip()
    return Path(override) if override else data_dir() / "copilot_cache.json"


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def atomic_write_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    """Aynı dizinde geçici dosya yazıp `os.replace` ile atomik olarak taşır."""
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
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


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Yapılandırma
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Varsayılanları kullanıcı TOML'u ile birleştirir. Hatalı dosya yok sayılır."""
    config = deepcopy(DEFAULT_CONFIG)
    target = path or config_path()
    if not target.is_file() or tomllib is None:
        return config
    try:
        with target.open("rb") as handle:
            user = tomllib.load(handle)
    except (OSError, ValueError):
        return config
    if isinstance(user, dict):
        return _deep_merge(config, user)
    return config


def get(config: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# Durum (state.json)
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    path = state_path()
    state = deepcopy(DEFAULT_STATE)
    if not path.is_file():
        return state
    try:
        raw = read_json(path)
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    merged = _deep_merge(state, raw)
    merged.setdefault("go_calibration", {}).setdefault("pools", {})
    merged.setdefault("go_calibration", {}).setdefault("total", {})
    merged.setdefault("notifications", {})
    return merged


def save_state(state: dict[str, Any]) -> None:
    atomic_write_json(state_path(), state, mode=0o600)


# ---------------------------------------------------------------------------
# Go kalibrasyonu
# ---------------------------------------------------------------------------


def _calibration_bucket(state: dict[str, Any], target: str) -> dict[str, Any]:
    calibration = state.setdefault("go_calibration", {})
    if target == "total":
        return calibration.setdefault("total", {})
    return calibration.setdefault("pools", {}).setdefault(target, {})


def get_calibration(state: dict[str, Any], target: str) -> dict[str, Any] | None:
    calibration = state.get("go_calibration") or {}
    if target == "total":
        entry = calibration.get("total")
    else:
        entry = (calibration.get("pools") or {}).get(target)
    return entry if isinstance(entry, dict) and entry else None


def set_calibration(
    state: dict[str, Any],
    target: str,
    *,
    remaining_pct: float | None = None,
    offset_pct: float | None = None,
    spent_usd: float | None = None,
) -> dict[str, Any]:
    """Havuz (flash/mid/argon/premium) veya genel (total) kalibrasyonu yazar.

    - `remaining_pct`: kullanıcının konsolda gördüğü kalan yüzde (mutlak override).
    - `offset_pct`: yerel hesaplanan yüzdeden düşülecek sapma.
    - `spent_usd`: yerel hesaplanan harcamaya eklenecek ek tutar.
    """
    if target not in CALIBRATION_TARGETS:
        raise ValueError(f"geçersiz kalibrasyon hedefi: {target}")
    bucket = _calibration_bucket(state, target)
    if remaining_pct is not None:
        bucket["remaining_pct"] = round(max(0.0, min(100.0, float(remaining_pct))), 2)
    if offset_pct is not None:
        bucket["offset_pct"] = round(float(offset_pct), 2)
    if spent_usd is not None:
        bucket["spent_usd"] = round(max(0.0, float(spent_usd)), 4)
    bucket["updated_at"] = int(time.time())
    if not any(key in bucket for key in ("remaining_pct", "offset_pct", "spent_usd")):
        bucket["remaining_pct"] = 100.0
    if target == "total":
        state.setdefault("go_calibration", {})["total"] = bucket
    else:
        state.setdefault("go_calibration", {}).setdefault("pools", {})[target] = bucket
    return bucket


def reset_calibration(state: dict[str, Any], target: str | None = None) -> None:
    """Belirtilen hedefi (veya target=None ise tümünü) sıfırlar."""
    calibration = state.setdefault("go_calibration", {"pools": {}, "total": {}})
    if target is None or target == "all":
        calibration["pools"] = {}
        calibration["total"] = {}
        return
    if target == "total":
        calibration["total"] = {}
        return
    calibration.setdefault("pools", {}).pop(target, None)


def apply_calibration(
    remaining_pct: float,
    spend_usd: float,
    limit_usd: float,
    calibration: dict[str, Any] | None,
) -> tuple[float, float, bool]:
    """Yerel hesaba kullanıcı kalibrasyonunu uygular.

    Dönüş: (kalan_yüzde, düzeltilmiş_harcama, kalibrasyon_uygulandı_mı)
    """
    spend = float(spend_usd)
    pct = float(remaining_pct)
    if not calibration:
        return pct, spend, False
    applied = False
    extra_spend = calibration.get("spent_usd")
    if isinstance(extra_spend, (int, float)) and extra_spend:
        spend += float(extra_spend)
        if limit_usd > 0:
            pct = max(0.0, min(100.0, (limit_usd - spend) / limit_usd * 100.0))
        applied = True
    offset = calibration.get("offset_pct")
    if isinstance(offset, (int, float)) and offset:
        pct = max(0.0, min(100.0, pct - float(offset)))
        applied = True
    override = calibration.get("remaining_pct")
    if isinstance(override, (int, float)):
        pct = max(0.0, min(100.0, float(override)))
        applied = True
    return round(pct, 2), round(spend, 6), applied
