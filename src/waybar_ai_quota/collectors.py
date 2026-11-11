"""Salt-okunur veri toplayıcılar.

Gizlilik kuralları (SPEC.md §4):
- `refreshToken`, `email`, `projectId`, API anahtarları ve kişisel yollar
  hiçbir zaman loglanmaz veya döndürülen sözlüklere eklenmez.
- `opencode.db` daima `file:...?mode=ro` URI'si ve kısa timeout ile açılır.
- `antigravity-accounts.json` yalnızca okunur; canlı yenileme sırasında
  atomik olarak güncellenir ve dosya izinleri korunur.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import config as cfg_mod
from . import models as m

# ---------------------------------------------------------------------------
# Yollar (test/özelleştirme için ortam değişkeniyle geçersiz kılınabilir)
# ---------------------------------------------------------------------------


def opencode_db_path() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_OPENCODE_DB", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local/share/opencode/opencode.db"


def antigravity_accounts_path() -> Path:
    override = os.environ.get("WAYBAR_AI_QUOTA_ACCOUNTS", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config/opencode/antigravity-accounts.json"


# ---------------------------------------------------------------------------
# Zaman / biçimlendirme yardımcıları
# ---------------------------------------------------------------------------

WINDOWS: dict[str, int] = {
    "5h": 5 * 3600 * 1000,
    "weekly": 7 * 24 * 3600 * 1000,
    "monthly": 30 * 24 * 3600 * 1000,
}

WINDOW_LABELS: dict[str, str] = {
    "5h": "5 saatlik",
    "weekly": "Haftalık",
    "monthly": "Aylık",
}

AG_GROUP_LABELS: dict[str, str] = {
    "claude": "Claude (Opus/Sonnet)",
    "gemini-pro": "Gemini Pro (Argon)",
    "gemini-flash": "Gemini Flash",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_now(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_to_ts(value: Any) -> float | None:
    """ISO-8601 veya epoch (sn/ms) değerini epoch saniyeye çevirir."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        return value / 1000.0 if value > 1e12 else value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text) / (1000.0 if float(text) > 1e12 else 1.0)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def format_duration_tr(seconds: float | None) -> str | None:
    """Saniyeyi Türkçe kısa süreye çevirir: `2s 14dk`, `4g 12s`, `45sn`."""
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


def short_model_name(model: str, max_chars: int = 22) -> str:
    """`provider/model#variant` -> bar için kısa ad."""
    name = (model or "").split("#", 1)[0]
    if "/" in name:
        name = name.split("/", 1)[1]
    name = name.replace("antigravity-", "")
    if len(name) > max_chars > 0:
        name = name[: max_chars - 1] + "…"
    return name or "boşta"


def _clamp_pct(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


def _ro_connect(path: Path) -> sqlite3.Connection:
    """SQLite'ı salt-okunur URI ve 2 sn timeout ile açar."""
    uri = "file:" + quote(str(Path(path).expanduser()), safe="/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=2.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = 1")
    return con


# ---------------------------------------------------------------------------
# OpenCode oturumları
# ---------------------------------------------------------------------------


def _parse_model(raw: Any) -> dict[str, str | None]:
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {"model_id": raw, "provider_id": ""}
    if not isinstance(raw, dict):
        return {}
    provider = raw.get("providerID") or raw.get("providerId") or raw.get("provider") or ""
    model_id = raw.get("id") or raw.get("modelID") or raw.get("modelId") or ""
    return {
        "model_id": str(model_id or ""),
        "provider_id": str(provider or ""),
        "variant": str(raw["variant"]) if raw.get("variant") else None,
    }


def _last_assistant_models(con: sqlite3.Connection, session_ids: list[str]) -> dict[str, dict[str, str | None]]:
    """Model alanı boş oturumlar için son assistant mesajındaki modeli bulur."""
    found: dict[str, dict[str, str | None]] = {}
    if not session_ids:
        return found
    placeholders = ",".join("?" for _ in session_ids)
    query = (
        "SELECT session_id, data FROM session_message "
        f"WHERE type = 'assistant' AND session_id IN ({placeholders}) "
        "ORDER BY seq DESC"
    )
    try:
        for row in con.execute(query, session_ids):
            sid = row["session_id"]
            if sid in found:
                continue
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("model"):
                found[sid] = _parse_model(data["model"])
    except sqlite3.Error:
        return found
    return found


def collect_sessions(config: dict[str, Any] | None = None) -> m.SessionStatus:
    """`opencode.db` üzerinden arşivlenmemiş oturumları okur."""
    config = config or cfg_mod.load_config()
    status = m.SessionStatus()
    db_path = opencode_db_path()
    if not db_path.is_file():
        status.error = "opencode.db bulunamadı"
        return status

    recent_seconds = int(cfg_mod.get(config, "thresholds.recent_activity_seconds", 300))
    conflict_seconds = int(cfg_mod.get(config, "thresholds.conflict_window_seconds", 90))
    now = now_ms()

    try:
        con = _ro_connect(db_path)
    except sqlite3.Error as exc:
        status.error = f"opencode.db açılamadı: {exc}"
        return status

    try:
        rows = con.execute(
            """
            SELECT id, project_id, directory, title, agent, model, cost,
                   tokens_input, tokens_output, tokens_cache_read,
                   time_created, time_updated, time_suspended, time_archived
            FROM session_v2
            WHERE time_archived IS NULL
            ORDER BY time_updated DESC
            LIMIT 50
            """
        ).fetchall()
    except sqlite3.Error as exc:
        con.close()
        status.error = f"session_v2 okunamadı: {exc}"
        return status

    pending = [row["id"] for row in rows if not row["model"]]
    fallback_models = _last_assistant_models(con, pending)
    con.close()

    sessions: list[m.SessionInfo] = []
    for row in rows:
        parsed = _parse_model(row["model"]) or fallback_models.get(row["id"], {})
        model_id = str(parsed.get("model_id") or "")
        provider_id = str(parsed.get("provider_id") or "")
        variant = parsed.get("variant")
        model_full = f"{provider_id}/{model_id}" if provider_id else model_id
        directory = str(row["directory"] or "")
        title = str(row["title"] or "")
        updated = int(row["time_updated"] or 0)
        age_seconds = max(0, int((now - updated) / 1000)) if updated else None
        in_flight = row["time_suspended"] is not None
        recently_active = bool(age_seconds is not None and age_seconds <= recent_seconds)
        is_antigravity = "antigravity" in f"{provider_id}/{model_id}".lower()
        tokens_total = int(row["tokens_input"] or 0) + int(row["tokens_output"] or 0) + int(
            row["tokens_cache_read"] or 0
        )
        sessions.append(
            m.SessionInfo(
                id=str(row["id"]),
                directory=directory,
                project=Path(directory).name if directory else "",
                title=title,
                agent=str(row["agent"] or ""),
                model=model_full,
                model_id=model_id,
                provider_id=provider_id,
                variant=variant,
                tokens_total=tokens_total,
                tokens_input=int(row["tokens_input"] or 0),
                tokens_output=int(row["tokens_output"] or 0),
                tokens_cache_read=int(row["tokens_cache_read"] or 0),
                cost_usd=round(float(row["cost"] or 0.0), 6),
                in_flight=in_flight,
                recently_active=recently_active,
                updated_at=iso_now(updated / 1000) if updated else None,
                updated_age_seconds=age_seconds,
                is_antigravity=is_antigravity,
            )
        )

    status.available = True
    status.sessions = sessions
    status.active_count = sum(1 for item in sessions if item.in_flight)
    status.recent_count = sum(1 for item in sessions if item.recently_active)

    conflict: list[m.SessionInfo] = []
    for item in sessions:
        if not item.is_antigravity:
            continue
        age = item.updated_age_seconds or 0
        if item.in_flight or age <= conflict_seconds:
            conflict.append(item)
    if len(conflict) >= 2:
        status.dual_antigravity_conflict = True
        status.conflict_sessions = [
            f"{item.project or item.id[:8]} · {item.model or 'antigravity'}" for item in conflict
        ]
    return status


# ---------------------------------------------------------------------------
# Antigravity
# ---------------------------------------------------------------------------

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
AG_ENDPOINTS = (
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
)
AG_PROD_ENDPOINT = "https://cloudcode-pa.googleapis.com"
AG_USER_AGENT = "antigravity/hub/2.24.0 (aidev_client; os_type=darwin; arch=arm64; cl=1000335682)"
AG_CACHE_MAX_AGE = 600

_oauth_cache: tuple[str, str] | None = None


def _read_accounts_file() -> dict[str, Any]:
    path = antigravity_accounts_path()
    if not path.is_file():
        raise FileNotFoundError("antigravity-accounts.json bulunamadı")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("antigravity-accounts.json geçersiz")
    return data


def _enabled_accounts(data: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    accounts = data.get("accounts") or []
    return [
        (index, account)
        for index, account in enumerate(accounts)
        if isinstance(account, dict) and account.get("enabled", True) is not False
    ]


def _normalize_fraction(value: Any) -> float:
    if not isinstance(value, (int, float)) or value != value:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _classify_ag_group(name: str) -> str | None:
    combined = name.lower()
    if "claude" in combined:
        return "claude"
    if "gemini" not in combined:
        return None
    return "gemini-flash" if "flash" in combined else "gemini-pro"


def _aggregate_cache(data: dict[str, Any], now: int) -> tuple[dict[str, dict[str, Any]], int, int]:
    """cachedQuota alanlarını grup bazında birleştirir (en düşük kalan yüzde)."""
    groups: dict[str, dict[str, Any]] = {}
    lock_seconds: dict[str, int] = {}
    updated_at = 0
    count = 0

    for _index, account in _enabled_accounts(data):
        count += 1
        account_updated = int(account.get("cachedQuotaUpdatedAt") or 0)
        if 0 < account_updated < 1_000_000_000_000:  # saniye -> milisaniye
            account_updated *= 1000
        updated_at = max(updated_at, account_updated)
        for key, info in (account.get("cachedQuota") or {}).items():
            if key not in AG_GROUP_LABELS or not isinstance(info, dict):
                continue
            fraction = _normalize_fraction(info.get("remainingFraction"))
            reset_ts = _iso_to_ts(info.get("remainingFractionResetTime")) or _iso_to_ts(info.get("resetTime"))
            entry = groups.get(key)
            if entry is None or fraction < entry["remainingFraction"]:
                groups[key] = {
                    "remainingFraction": fraction,
                    "reset_ts": reset_ts,
                    "reset_time": info.get("remainingFractionResetTime") or info.get("resetTime"),
                    "modelCount": int(info.get("modelCount") or 0),
                }
            elif entry is not None and reset_ts and (
                entry.get("reset_ts") is None or reset_ts < entry["reset_ts"]
            ):
                entry["reset_ts"] = reset_ts
                entry["reset_time"] = info.get("remainingFractionResetTime") or info.get("resetTime")

        locks = account.get("rateLimitResetTimes") or {}
        if isinstance(locks, dict):
            for key, raw_value in locks.items():
                ts = _iso_to_ts(raw_value)
                if ts is None or ts * 1000 <= now:
                    continue
                remaining = int(ts - now / 1000)
                if remaining <= 0:
                    continue
                family = "claude" if str(key).startswith("claude") else "gemini"
                lock_seconds[family] = max(lock_seconds.get(family, 0), remaining)

    for key, entry in groups.items():
        family = "claude" if key == "claude" else "gemini"
        entry["rate_limit_remaining_seconds"] = lock_seconds.get(family) or None
    return groups, updated_at, count


def _group_status(key: str, entry: dict[str, Any], critical_pct: float, now: int) -> m.AntigravityGroup:
    fraction = _normalize_fraction(entry.get("remainingFraction", entry.get("remaining_fraction")))
    pct = _clamp_pct(fraction * 100.0)
    reset_ts = entry.get("reset_ts")
    reset_seconds = None
    if isinstance(reset_ts, (int, float)):
        reset_seconds = max(0, int(reset_ts - now / 1000))
    lock_seconds = entry.get("rate_limit_remaining_seconds")
    return m.AntigravityGroup(
        key=key,
        label=AG_GROUP_LABELS.get(key, key),
        remaining_pct=pct,
        remaining_fraction=round(fraction, 4),
        reset_time=entry.get("reset_time"),
        reset_in_seconds=reset_seconds,
        reset_human=format_duration_tr(reset_seconds),
        rate_limited=bool(lock_seconds),
        rate_limit_remaining_seconds=lock_seconds,
        rate_limit_human=format_duration_tr(lock_seconds),
        model_count=int(entry.get("modelCount") or entry.get("model_count") or 0),
        critical=pct < critical_pct,
    )


def _discover_oauth_client(config: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """OAuth client_id/secret'i ortam, yapılandırma veya eklenti dosyasından çözer.

    Depoda sabit anahtar tutulmaz; değerler çalışma anında okunur.
    """
    global _oauth_cache
    config = config or cfg_mod.load_config()
    client_id = (os.environ.get("WAYBAR_AI_QUOTA_ANTIGRAVITY_CLIENT_ID") or "").strip() or str(
        cfg_mod.get(config, "antigravity.client_id", "") or ""
    ).strip()
    client_secret = (os.environ.get("WAYBAR_AI_QUOTA_ANTIGRAVITY_CLIENT_SECRET") or "").strip() or str(
        cfg_mod.get(config, "antigravity.client_secret", "") or ""
    ).strip()
    if client_id and client_secret:
        return client_id, client_secret
    if _oauth_cache:
        return _oauth_cache

    cache_root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "opencode/npm"
    patterns = [
        "opencode-antigravity-auth*/**/dist/src/constants.js",
        "opencode-google-antigravity-auth*/**/dist/src/constants.js",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        try:
            candidates.extend(sorted(cache_root.glob(pattern)))
        except OSError:
            continue
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        id_match = re.search(r'ANTIGRAVITY_CLIENT_ID\s*=\s*"([^"]+)"', text)
        secret_match = re.search(r'ANTIGRAVITY_CLIENT_SECRET\s*=\s*"([^"]+)"', text)
        if id_match and secret_match:
            _oauth_cache = (id_match.group(1), secret_match.group(1))
            return _oauth_cache
    return None


def _http_json(
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - sabit https adresleri
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("ağ hatası") from None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("geçersiz JSON yanıtı") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("beklenmeyen yanıt biçimi")
    return parsed


def _refresh_access_token(refresh_token: str, client_id: str, client_secret: str, timeout: float) -> dict[str, Any]:
    body = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = Request(
        OAUTH_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"token yenileme başarısız (HTTP {exc.code})") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("token yenileme ağ hatası") from None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("token yanıtı geçersiz") from None
    if not isinstance(parsed, dict) or not parsed.get("access_token"):
        raise RuntimeError("token yanıtında access_token yok")
    return parsed


def _aggregate_live_models(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    models = payload.get("models")
    if not isinstance(models, dict):
        return groups
    for model_name, entry in models.items():
        if not isinstance(entry, dict) or entry.get("disabled"):
            continue
        group = _classify_ag_group(f"{model_name} {entry.get('displayName') or ''}")
        if not group:
            continue
        quota = entry.get("quotaInfo") or {}
        fraction = _normalize_fraction(quota.get("remainingFraction"))
        reset_time = quota.get("resetTime")
        reset_ts = _iso_to_ts(reset_time)
        existing = groups.get(group)
        if existing is None:
            groups[group] = {
                "remainingFraction": fraction,
                "reset_ts": reset_ts,
                "reset_time": reset_time,
                "modelCount": 1,
            }
            continue
        existing["modelCount"] += 1
        if fraction < existing["remainingFraction"]:
            existing.update(
                {
                    "remainingFraction": fraction,
                    "reset_ts": reset_ts,
                    "reset_time": reset_time,
                }
            )
    return groups


def _merge_gemini_cli_buckets(groups: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    """Gemini CLI `retrieveUserQuota` yanıtını eksik gemini grupları için kullanır."""
    buckets = payload.get("buckets")
    if not isinstance(buckets, list):
        return
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        model_id = str(bucket.get("modelId") or "")
        if not model_id.startswith("gemini"):
            continue
        group = _classify_ag_group(model_id)
        if not group:
            continue
        fraction = _normalize_fraction(bucket.get("remainingFraction"))
        reset_time = bucket.get("resetTime")
        reset_ts = _iso_to_ts(reset_time)
        existing = groups.get(group)
        if existing is None:
            groups[group] = {
                "remainingFraction": fraction,
                "reset_ts": reset_ts,
                "reset_time": reset_time,
                "modelCount": 1,
            }
            continue
        existing["modelCount"] += 1
        if fraction < existing["remainingFraction"]:
            existing.update(
                {
                    "remainingFraction": fraction,
                    "reset_ts": reset_ts,
                    "reset_time": reset_time,
                }
            )


def refresh_antigravity_quota(
    config: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Canlı kota yenilemesi; `antigravity-accounts.json` içindeki cache'i atomik günceller.

    Dönüş: {"groups": {...}, "updated_at": epoch_ms, "refreshed": bool, "error": str|None}
    Hata durumunda mevcut cachedQuota korunur.
    """
    config = config or cfg_mod.load_config()
    timeout = timeout or float(cfg_mod.get(config, "cloud.timeout_seconds", 10))
    result: dict[str, Any] = {"groups": {}, "updated_at": 0, "refreshed": False, "error": None}

    try:
        data = _read_accounts_file()
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
        return result

    accounts = _enabled_accounts(data)
    if not accounts:
        result["error"] = "etkin Antigravity hesabı yok"
        return result

    client = _discover_oauth_client(config)
    if client is None:
        result["error"] = "OAuth istemci bilgisi bulunamadı"
        return result
    client_id, client_secret = client

    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    changed = False

    for index, account in accounts:
        refresh_token = str(account.get("refreshToken") or "")
        if not refresh_token:
            errors.append("refresh token yok")
            continue
        project_id = str(account.get("managedProjectId") or account.get("projectId") or "")
        try:
            token = _refresh_access_token(refresh_token, client_id, client_secret, timeout)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue

        access_token = str(token["access_token"])
        rotated = token.get("refresh_token")
        if isinstance(rotated, str) and rotated and rotated != refresh_token:
            account["refreshToken"] = rotated
            changed = True

        groups: dict[str, dict[str, Any]] = {}
        body = {"project": project_id} if project_id else {}
        for endpoint in AG_ENDPOINTS:
            try:
                payload = _http_json(
                    f"{endpoint}/v1internal:fetchAvailableModels",
                    body,
                    {
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "User-Agent": AG_USER_AGENT,
                    },
                    timeout,
                )
            except RuntimeError:
                continue
            groups = _aggregate_live_models(payload)
            if groups:
                break
        try:
            cli_payload = _http_json(
                f"{AG_PROD_ENDPOINT}/v1internal:retrieveUserQuota",
                body,
                {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "User-Agent": AG_USER_AGENT,
                },
                timeout,
            )
            _merge_gemini_cli_buckets(groups, cli_payload)
        except RuntimeError:
            pass

        if not groups:
            errors.append("kota verisi alınamadı")
            continue

        serializable = {
            key: {
                "remainingFraction": round(value["remainingFraction"], 6),
                **(
                    {"resetTime": value["reset_time"]}
                    if value.get("reset_time")
                    else {}
                ),
                **(
                    {"remainingFractionResetTime": value["reset_time"]}
                    if value.get("reset_time")
                    else {}
                ),
                "modelCount": value.get("modelCount", 0),
            }
            for key, value in groups.items()
        }
        account["cachedQuota"] = serializable
        account["cachedQuotaUpdatedAt"] = now_ms()
        changed = True
        for key, value in groups.items():
            current = merged.get(key)
            if current is None or value["remainingFraction"] < current["remainingFraction"]:
                merged[key] = value
            elif current and value.get("reset_ts") and (
                current.get("reset_ts") is None or value["reset_ts"] < current["reset_ts"]
            ):
                current["reset_ts"] = value["reset_ts"]
                current["reset_time"] = value.get("reset_time")

    if changed:
        try:
            cfg_mod.atomic_write_json(antigravity_accounts_path(), data, mode=0o600)
        except OSError:
            result["error"] = "antigravity-accounts.json yazılamadı"
            return result

    result["groups"] = merged
    result["updated_at"] = now_ms() if merged else 0
    result["refreshed"] = bool(merged)
    if errors and not merged:
        result["error"] = "; ".join(sorted(set(errors)))
    elif not merged:
        result["error"] = "kota verisi alınamadı"
    return result


def collect_antigravity(
    force_refresh: bool = False,
    config: dict[str, Any] | None = None,
    allow_live: bool = True,
) -> m.AntigravityStatus:
    config = config or cfg_mod.load_config()
    status = m.AntigravityStatus()
    critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
    cache_seconds = int(cfg_mod.get(config, "refresh.antigravity_cache_seconds", AG_CACHE_MAX_AGE))
    live_enabled = bool(cfg_mod.get(config, "cloud.antigravity_live_refresh", True))
    now = now_ms()

    try:
        data = _read_accounts_file()
    except (OSError, ValueError) as exc:
        status.error = str(exc)
        return status

    groups, updated_at, count = _aggregate_cache(data, now)
    age_seconds = None
    if updated_at:
        age_seconds = max(0, int((now - updated_at) / 1000))
    status.account_count = count
    stale = age_seconds is None or age_seconds > cache_seconds

    if live_enabled and allow_live and (force_refresh or stale):
        refreshed = refresh_antigravity_quota(config)
        status.refreshed = bool(refreshed.get("refreshed"))
        if refreshed.get("refreshed"):
            try:
                data = _read_accounts_file()
                groups, updated_at, count = _aggregate_cache(data, now)
                age_seconds = max(0, int((now - updated_at) / 1000))
                status.account_count = count
            except (OSError, ValueError):
                pass
        elif refreshed.get("error"):
            status.error = str(refreshed["error"])

    if not groups:
        if not status.error:
            status.error = "Antigravity kota verisi yok"
        return status

    status.available = True
    status.updated_ts = updated_at or None
    status.updated_at = iso_now(updated_at / 1000) if updated_at else None
    status.age_seconds = age_seconds
    status.stale = bool(stale and not status.refreshed)
    for key in ("claude", "gemini-pro", "gemini-flash"):
        entry = groups.get(key)
        if entry:
            status.groups.append(_group_status(key, entry, critical_pct, now))
    return status


# ---------------------------------------------------------------------------
# OpenCode Go
# ---------------------------------------------------------------------------

GO_POOLS: tuple[dict[str, Any], ...] = (
    {
        "key": "flash",
        "label": "DeepSeek V4.1 Flash",
        "models": ("deepseek-v4.1-flash",),
        "limits": {"5h": 20.0, "weekly": 50.0, "monthly": 100.0},
    },
    {
        "key": "mid",
        "label": "GLM 5.3 Flash / Kimi K2.7 Code",
        "models": ("glm-5.3-flash", "kimi-k2.7-code"),
        "limits": {"5h": 12.0, "weekly": 30.0, "monthly": 60.0},
    },
    {
        "key": "argon",
        "label": "Gemini 4 Argon",
        "models": ("gemini-4-argon",),
        "limits": {"5h": 6.0, "weekly": 15.0, "monthly": 30.0},
    },
    {
        "key": "premium",
        "label": "DeepSeek V4 Pro / Claude Haiku 5.5 / GPT 6 Luna / Grok 4.7 / Kimi K3",
        "models": (
            "deepseek-v4-pro",
            "claude-haiku-5.5",
            "gpt-6-luna",
            "grok-4.7",
            "kimi-k3",
        ),
        "limits": {"5h": 3.0, "weekly": 7.5, "monthly": 15.0},
    },
)

_POOL_INDEX: dict[str, dict[str, Any]] = {}
for _pool in GO_POOLS:
    for _model in _pool["models"]:
        _POOL_INDEX[_model] = _pool
_MATCH_ORDER = sorted(_POOL_INDEX, key=len, reverse=True)


def _normalize_go_model(model_id: str) -> str:
    return (model_id or "").split("#", 1)[0].strip().lower()


def _pool_for_model(model_id: str) -> dict[str, Any] | None:
    normalized = _normalize_go_model(model_id)
    for candidate in _MATCH_ORDER:
        if normalized == candidate or normalized.startswith(candidate + "-"):
            return _POOL_INDEX[candidate]
    return None


def _go_spend(db_path: Path) -> tuple[dict[str, dict[str, float]], dict[str, float], str | None]:
    """Model havuzu ve genel toplam için pencere bazlı harcama (USD)."""
    now = now_ms()
    starts = {key: now - span for key, span in WINDOWS.items()}
    month_start = starts["monthly"]
    pool_spend: dict[str, dict[str, float]] = {
        pool["key"]: {key: 0.0 for key in WINDOWS} for pool in GO_POOLS
    }
    total_spend: dict[str, float] = {key: 0.0 for key in WINDOWS}

    try:
        con = _ro_connect(db_path)
    except sqlite3.Error as exc:
        return pool_spend, total_spend, f"opencode.db açılamadı: {exc}"

    message_costs: dict[str, float] = {}
    try:
        rows = con.execute(
            """
            SELECT session_id, time_created, data
            FROM session_message
            WHERE type = 'assistant' AND time_created >= ?
            ORDER BY time_created DESC
            LIMIT 20000
            """,
            (month_start,),
        ).fetchall()
        for row in rows:
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            model = data.get("model") or {}
            if not isinstance(model, dict) or str(model.get("providerID") or "") != "opencode-go":
                continue
            cost = data.get("cost")
            if not isinstance(cost, (int, float)):
                continue
            cost = float(cost)
            if cost <= 0:
                continue
            pool = _pool_for_model(str(model.get("id") or ""))
            created = int(row["time_created"] or 0)
            message_costs[row["session_id"]] = message_costs.get(row["session_id"], 0.0) + cost
            for key, start in starts.items():
                if created >= start:
                    total_spend[key] += cost
                    if pool:
                        pool_spend[pool["key"]][key] += cost

        sessions = con.execute(
            """
            SELECT id, model, cost, time_updated
            FROM session_v2
            WHERE time_updated >= ? AND cost > 0
            """,
            (month_start,),
        ).fetchall()
    except sqlite3.Error as exc:
        con.close()
        return pool_spend, total_spend, f"Go harcaması okunamadı: {exc}"
    con.close()

    for session in sessions:
        total = float(session["cost"] or 0.0)
        residual = total - message_costs.get(session["id"], 0.0)
        if residual <= 0:
            continue
        parsed = _parse_model(session["model"])
        if str(parsed.get("provider_id") or "") != "opencode-go":
            continue
        pool = _pool_for_model(str(parsed.get("model_id") or ""))
        updated = int(session["time_updated"] or 0)
        for key, start in starts.items():
            if updated >= start:
                total_spend[key] += residual
                if pool:
                    pool_spend[pool["key"]][key] += residual

    return pool_spend, total_spend, None


def _window_pct(spend: float, limit: float) -> float:
    if limit <= 0:
        return 0.0
    return _clamp_pct((limit - spend) / limit * 100.0)


def collect_opencode_go(
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> m.GoStatus:
    config = config or cfg_mod.load_config()
    state = state if state is not None else cfg_mod.load_state()
    status = m.GoStatus(available=False, window_labels=dict(WINDOW_LABELS))
    db_path = opencode_db_path()
    if not db_path.is_file():
        status.error = "opencode.db bulunamadı"
        return status

    pool_spend, total_spend, error = _go_spend(db_path)
    if error:
        status.error = error
        return status

    for pool in GO_POOLS:
        calibration = cfg_mod.get_calibration(state, pool["key"])
        limits = dict(pool["limits"])
        spend = {key: round(pool_spend[pool["key"]][key], 6) for key in WINDOWS}
        remaining: dict[str, float] = {}
        calibrated = False
        adjusted_spend = dict(spend)
        for key, limit in limits.items():
            pct, adj_spend, used = cfg_mod.apply_calibration(
                _window_pct(spend[key], limit), spend[key], limit, calibration
            )
            remaining[key] = pct
            adjusted_spend[key] = round(adj_spend, 6)
            calibrated = calibrated or used
        headline = min(remaining.values()) if remaining else 100.0
        if calibration and isinstance(calibration.get("remaining_pct"), (int, float)):
            headline = _clamp_pct(float(calibration["remaining_pct"]))
        status.pools.append(
            m.GoPoolStatus(
                key=pool["key"],
                label=pool["label"],
                models=list(pool["models"]),
                limits=limits,
                spend=adjusted_spend,
                remaining=remaining,
                remaining_pct=_clamp_pct(headline),
                calibrated=calibrated,
                calibration=calibration,
            )
        )

    total_limits = {
        key: round(sum(pool["limits"][key] for pool in GO_POOLS), 2) for key in WINDOWS
    }
    total_cal = cfg_mod.get_calibration(state, "total")
    total_remaining: dict[str, float] = {}
    adjusted_total = {key: round(total_spend[key], 6) for key in WINDOWS}
    total_calibrated = False
    for key, limit in total_limits.items():
        pct, adj_spend, used = cfg_mod.apply_calibration(
            _window_pct(total_spend[key], limit), total_spend[key], limit, total_cal
        )
        total_remaining[key] = pct
        adjusted_total[key] = round(adj_spend, 6)
        total_calibrated = total_calibrated or used
    total_headline = min(total_remaining.values()) if total_remaining else 100.0
    if total_cal and isinstance(total_cal.get("remaining_pct"), (int, float)):
        total_headline = _clamp_pct(float(total_cal["remaining_pct"]))

    status.available = True
    status.total = m.GoTotalStatus(
        limits=total_limits,
        spend=adjusted_total,
        remaining=total_remaining,
        remaining_pct=_clamp_pct(total_headline),
        calibrated=total_calibrated,
    )
    return status


# ---------------------------------------------------------------------------
# GitHub Copilot
# ---------------------------------------------------------------------------

COPILOT_QUOTA_LABELS: dict[str, str] = {
    "chat": "Chat",
    "completions": "Completions",
    "premium_interactions": "Premium Interactions / AI Credits",
}


def _copilot_plan_label(sku: str, plan: str, override: str = "") -> str | None:
    if override:
        return override
    combined = f"{sku} {plan}".lower()
    if "student" in combined:
        return "Copilot Student"
    if "free" in combined or "limited" in combined:
        return "Copilot Free"
    if plan or sku:
        return "Copilot Pro"
    return None


def _copilot_reset_at(snapshot: dict[str, Any]) -> str | None:
    raw = snapshot.get("quota_reset_at")
    if isinstance(raw, (int, float)) and raw > 0:
        value = float(raw) / (1000.0 if float(raw) > 1e12 else 1.0)
        return iso_now(value)
    return None


def _sanitize_copilot(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """API yanıtından yalnızca güvenli, kişisel olmayan alanları ayıklar."""
    sku = str(payload.get("access_type_sku") or "")
    plan = str(payload.get("copilot_plan") or "")
    override = str(cfg_mod.get(config, "copilot.plan_label_override", "") or "")
    snapshots = payload.get("quota_snapshots") or {}
    quotas: list[dict[str, Any]] = []
    order = list(COPILOT_QUOTA_LABELS)
    keys = order + sorted(key for key in snapshots if key not in COPILOT_QUOTA_LABELS)
    for key in keys:
        snapshot = snapshots.get(key)
        if not isinstance(snapshot, dict):
            continue
        percent = snapshot.get("percent_remaining")
        quotas.append(
            {
                "key": key,
                "label": COPILOT_QUOTA_LABELS.get(key, key.replace("_", " ").title()),
                "percent_remaining": round(float(percent), 1)
                if isinstance(percent, (int, float))
                else None,
                "remaining": snapshot.get("remaining", snapshot.get("quota_remaining")),
                "entitlement": snapshot.get("entitlement"),
                "unlimited": bool(snapshot.get("unlimited")),
                "has_quota": bool(snapshot.get("has_quota", True)),
                "reset_at": _copilot_reset_at(snapshot),
            }
        )
    return {
        "plan": plan or None,
        "plan_label": _copilot_plan_label(sku, plan, override),
        "reset_date": payload.get("quota_reset_date"),
        "quotas": quotas,
    }


def _load_copilot_cache() -> dict[str, Any] | None:
    path = cfg_mod.copilot_cache_path()
    if not path.is_file():
        return None
    try:
        raw = cfg_mod.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        return None
    return raw


def _copilot_status_from_data(
    data: dict[str, Any],
    fetched_ts: float,
    cached: bool,
    error: str | None = None,
) -> m.CopilotStatus:
    quotas = [m.CopilotQuota(**item) for item in data.get("quotas") or [] if isinstance(item, dict)]
    return m.CopilotStatus(
        available=bool(quotas) or bool(data.get("plan_label")),
        plan=data.get("plan"),
        plan_label=data.get("plan_label"),
        reset_date=data.get("reset_date"),
        quotas=quotas,
        fetched_at=iso_now(fetched_ts) if fetched_ts else None,
        age_seconds=max(0, int(time.time() - fetched_ts)) if fetched_ts else None,
        cached=cached,
        error=error,
    )


def collect_github_copilot(
    force_refresh: bool = False,
    config: dict[str, Any] | None = None,
    offline: bool = False,
) -> m.CopilotStatus:
    config = config or cfg_mod.load_config()
    ttl = int(cfg_mod.get(config, "refresh.copilot_cache_seconds", 600))
    cache = _load_copilot_cache()
    cache_ts = float(cache.get("fetched_at") or 0) if cache else 0.0
    cache_fresh = bool(cache) and (time.time() - cache_ts) <= ttl

    if cache and cache_fresh and not force_refresh:
        return _copilot_status_from_data(cache["data"], cache_ts, cached=True)
    if offline:
        if cache:
            return _copilot_status_from_data(cache["data"], cache_ts, cached=True, error="önbellek bayat")
        return m.CopilotStatus(available=False, error="Copilot önbelleği yok")

    gh = str(cfg_mod.get(config, "copilot.gh_binary", "") or "").strip() or shutil.which("gh")
    if not gh:
        if cache:
            return _copilot_status_from_data(cache["data"], cache_ts, cached=True, error="gh bulunamadı")
        return m.CopilotStatus(available=False, error="gh bulunamadı")

    try:
        completed = subprocess.run(
            [gh, "api", "/copilot_internal/user"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        if cache:
            return _copilot_status_from_data(cache["data"], cache_ts, cached=True, error="gh çalıştırılamadı")
        return m.CopilotStatus(available=False, error="gh çalıştırılamadı")

    if completed.returncode != 0:
        if cache:
            return _copilot_status_from_data(
                cache["data"], cache_ts, cached=True, error=f"gh api başarısız (kod {completed.returncode})"
            )
        return m.CopilotStatus(available=False, error=f"gh api başarısız (kod {completed.returncode})")

    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        if cache:
            return _copilot_status_from_data(cache["data"], cache_ts, cached=True, error="gh yanıtı geçersiz")
        return m.CopilotStatus(available=False, error="gh yanıtı geçersiz")
    if not isinstance(payload, dict):
        return m.CopilotStatus(available=False, error="gh yanıtı beklenmeyen biçimde")

    data = _sanitize_copilot(payload, config)
    fetched_ts = time.time()
    try:
        cfg_mod.atomic_write_json(
            cfg_mod.copilot_cache_path(),
            {"fetched_at": fetched_ts, "data": data},
            mode=0o600,
        )
    except OSError:
        pass
    return _copilot_status_from_data(data, fetched_ts, cached=False)


# ---------------------------------------------------------------------------
# Birleşik toplama
# ---------------------------------------------------------------------------


def collect_all(
    force_refresh: bool = False,
    config: dict[str, Any] | None = None,
    offline: bool = False,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tüm durumu toplar; öneri motoru CLI katmanında eklenir."""
    config = config or cfg_mod.load_config()
    state = state if state is not None else cfg_mod.load_state()
    return {
        "generated_at": iso_now(),
        "generated_ts": now_ms(),
        "sessions": collect_sessions(config),
        "antigravity": collect_antigravity(
            force_refresh=force_refresh, config=config, allow_live=not offline
        ),
        "go": collect_opencode_go(config=config, state=state),
        "copilot": collect_github_copilot(
            force_refresh=force_refresh, config=config, offline=offline
        ),
    }
