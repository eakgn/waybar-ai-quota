"""XDG yolları, opt-in yapılandırma (config.toml) ve yerel durum (state.json).

Tüm yollar XDG standartlarından dinamik çözülür; gizli kimlik bilgileri bu
dosyalarda saklanmaz. Yazımlar atomik ve 0600 izinli yapılır.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

APP_NAME = "agent-notifier"

KNOWN_HARNESSES: tuple[str, ...] = ("opencode", "antigravity")
KNOWN_PROVIDERS: tuple[str, ...] = ("opencode_go", "github_copilot")

DEFAULT_CONFIG: dict[str, Any] = {
    "harnesses": {
        "opencode": True,
        "antigravity": True,
    },
    "providers": {
        "opencode_go": True,
        "github_copilot": False,
    },
    "refresh": {
        "local_active_seconds": 3,
        "local_idle_seconds": 10,
        "remote_seconds": 600,
        "remote_active_seconds": 30,
        "opencode_go_cache_seconds": 600,
        "copilot_cache_seconds": 600,
    },
    "thresholds": {
        "critical_pct": 15,
        "recent_activity_seconds": 300,
        "active_window_minutes": 90,
        "recent_history_hours": 24,
        "stale_running_seconds": 3600,
        "event_stale_seconds": 1800,
    },
    "cloud": {
        "timeout_seconds": 10,
        "opencode_go_live_api": True,
    },
    "copilot": {
        "gh_binary": "",
        "plan_label_override": "",
    },
    "notifications": {
        "enabled": True,
        "cooldown_seconds": 300,
        "notify_on_done": True,
        "notify_on_waiting": True,
        "app_name": APP_NAME,
    },
    "bar": {
        "max_model_chars": 20,
        "idle_format": "compact",
    },
    "ui": {
        "gtk_python": "/usr/bin/python3",
        "anchor": "top-right",
        "ack_done_on_open": True,
    },
}

DEFAULT_STATE: dict[str, Any] = {
    "version": 2,
    "seen_done": {},
    "notifications": {},
    "last_seen_states": {},
}


# ---------------------------------------------------------------------------
# XDG Yolları
# ---------------------------------------------------------------------------


def _xdg_dir(env_name: str, fallback: str) -> Path:
    value = os.environ.get(env_name, "").strip()
    base = Path(value) if value else Path.home() / fallback
    return base / APP_NAME


def config_dir() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_CONFIG_DIR", "").strip()
    return Path(override) if override else _xdg_dir("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_DATA_DIR", "").strip()
    return Path(override) if override else _xdg_dir("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_STATE_DIR", "").strip()
    return Path(override) if override else _xdg_dir("XDG_STATE_HOME", ".local/state")


def cache_dir() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_CACHE_DIR", "").strip()
    return Path(override) if override else _xdg_dir("XDG_CACHE_HOME", ".cache")


def config_path() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_CONFIG", "").strip()
    return Path(override) if override else config_dir() / "config.toml"


def state_path() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_STATE", "").strip()
    return Path(override) if override else state_dir() / "state.json"


def events_path() -> Path:
    override = os.environ.get("AGENT_NOTIFIER_EVENTS", "").strip()
    return Path(override) if override else state_dir() / "events.json"


def provider_cache_path(provider_key: str) -> Path:
    override = os.environ.get(f"AGENT_NOTIFIER_CACHE_{provider_key.upper()}", "").strip()
    if override:
        return Path(override)
    return cache_dir() / f"provider_{provider_key}.json"


def icon_theme_dir() -> Path:
    return data_dir() / "icons"


# ---------------------------------------------------------------------------
# Dosya Yardımcıları
# ---------------------------------------------------------------------------


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Aynı dizinde geçici dosya yazıp `os.replace` ile atomik taşır."""
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
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


def atomic_write_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text, mode=mode)


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
# Yapılandırma Okuma & Yazma (TOML)
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Varsayılanları kullanıcı TOML dosyası ile birleştirir."""
    config = deepcopy(DEFAULT_CONFIG)
    target = path or config_path()
    if not target.is_file():
        return config
    try:
        with target.open("rb") as handle:
            user = tomllib.load(handle)
    except (OSError, ValueError):
        return config
    if isinstance(user, dict):
        return _deep_merge(config, user)
    return config


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return f'"{value}"'


def dump_toml(config: dict[str, Any]) -> str:
    """İki seviyeli yapılandırma sözlüğünü temiz TOML metnine dönüştürür."""
    lines: list[str] = [
        "# agent-notifier yapılandırması",
        "# Dashboard ayarlarından veya `agent-notifier setup` ile güncellenebilir.",
        "",
    ]
    for section, values in config.items():
        if not isinstance(values, dict):
            lines.append(f"{section} = {_toml_value(values)}")
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            if value is None:
                continue
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_config(config: dict[str, Any], path: Path | None = None) -> Path:
    target = path or config_path()
    merged = _deep_merge(DEFAULT_CONFIG, config)
    atomic_write_text(target, dump_toml(merged), mode=0o600)
    return target


def get(config: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def enabled_harnesses(config: dict[str, Any]) -> list[str]:
    section = config.get("harnesses") or {}
    if not isinstance(section, dict):
        return []
    return [key for key, enabled in section.items() if bool(enabled)]


def enabled_providers(config: dict[str, Any]) -> list[str]:
    section = config.get("providers") or {}
    if not isinstance(section, dict):
        return []
    return [key for key, enabled in section.items() if bool(enabled)]


def set_toggle(config: dict[str, Any], section: str, key: str, enabled: bool) -> dict[str, Any]:
    """Bir harness veya provider'ı açar/kapatır ve güncellenmiş config döndürür."""
    updated = deepcopy(config)
    updated.setdefault(section, {})[key] = bool(enabled)
    return updated


# ---------------------------------------------------------------------------
# Yerel Durum (state.json)
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    target = state_path()
    state = deepcopy(DEFAULT_STATE)
    if not target.is_file():
        return state
    try:
        raw = read_json(target)
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    merged = _deep_merge(state, raw)
    merged.setdefault("seen_done", {})
    merged.setdefault("notifications", {})
    merged.setdefault("last_seen_states", {})
    return merged


def save_state(state: dict[str, Any]) -> None:
    atomic_write_json(state_path(), state, mode=0o600)
