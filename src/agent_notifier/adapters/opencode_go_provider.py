"""OpenCode Go kota sağlayıcısı (yalnız gerçek resmî API — sıfır sahte tahmin).

Resmî uç (`OPENCODE_QUOTA_API_NOTES.md`):
  GET https://opencode.ai/zen/go/v1/usage
  Headers: Authorization: Bearer <token>, x-opencode-org-id: <workspaceID>

İlkeler (Red-Team R2):
- Yerel `opencode.db` harcaması sunucu penceresi (`resetsAt`) ve dinamik plan
  limitleriyle birebir örtüşmediği için kota yüzdesi uydurmakta KULLANILMAZ.
- Canlı API başarılıysa gerçek yüzdeler (`rolling`, `weekly`, `monthly`) gösterilir.
- Geçici API kesintisinde son 30 dk içindeki gerçek API önbelleği açıkça
  "bayat önbellek" notuyla sunulur; taze önbellek yoksa açıkça "Canlı API hatası"
  bildirilir.
- Token, e-posta ve orgID asla loga, önbelleğe veya çıktıya yazılmaz.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import config as cfg_mod
from ..models import ProviderQuota, QuotaWindow
from ..utils import clamp_pct, format_duration_tr, iso_now, iso_to_ts, now_ms, opencode_db_path, ro_connect
from .base import AdapterInfo

GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
MAX_STALE_CACHE_SECONDS = 1800  # 30 dk'dan eski önbellek gerçek veri gibi sunulmaz

WINDOW_LABELS: dict[str, str] = {
    "rolling": "5 Saatlik",
    "weekly": "Haftalık",
    "monthly": "Aylık",
}


def _extract_token_from_value(raw_val: Any) -> str | None:
    if not raw_val:
        return None
    text = str(raw_val).strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        if isinstance(parsed, dict):
            for key in ("access", "access_token", "accessToken", "key", "apiKey", "api_key", "token"):
                cand = parsed.get(key)
                if isinstance(cand, str) and cand.strip():
                    return cand.strip()
        return None
    return text


def _read_go_credentials(db_path: Path) -> tuple[str | None, str | None]:
    """`opencode.db` içinden yalnızca bellek içi kullanım için token + orgID okur.

    Desteklenen gerçek OpenCode v2 tabloları:
    - `credential` (`integration_id`/`connector_id`/`label`, `value`, `active`)
    - `control_account` (`access_token`, `active`, `time_updated`)
    - `workspace` (`id`, `provider`, `last_used_at`)
    """
    if not db_path.is_file():
        return None, None
    try:
        con = ro_connect(db_path)
    except sqlite3.Error:
        return None, None
    token: str | None = None
    org_id: str | None = None
    try:
        tables = {
            str(r["name"])
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        if "credential" in tables:
            cols = {
                str(r["name"]) for r in con.execute("PRAGMA table_info(credential)").fetchall()
            }
            order = (
                "ORDER BY COALESCE(active, 0) DESC, time_updated DESC"
                if "active" in cols and "time_updated" in cols
                else ""
            )
            rows = con.execute(f"SELECT * FROM credential {order}").fetchall()
            for needle in ("opencode-go", "opencode"):
                for row in rows:
                    row_cols = row.keys()
                    meta = " ".join(
                        str(row[c] or "")
                        for c in ("integration_id", "connector_id", "label", "provider", "id")
                        if c in row_cols
                    ).lower()
                    if needle not in meta:
                        continue
                    for val_col in ("value", "data", "secret", "credentials"):
                        if val_col in row_cols and row[val_col]:
                            candidate = _extract_token_from_value(row[val_col])
                            if candidate:
                                token = candidate
                                break
                    if token:
                        break
                if token:
                    break

        if not token and "control_account" in tables:
            cols = {
                str(r["name"])
                for r in con.execute("PRAGMA table_info(control_account)").fetchall()
            }
            if "access_token" in cols:
                where = "WHERE active = 1" if "active" in cols else ""
                order = "ORDER BY time_updated DESC" if "time_updated" in cols else ""
                row = con.execute(
                    f"SELECT access_token FROM control_account {where} {order} LIMIT 1"
                ).fetchone()
                if row and row["access_token"]:
                    cand = str(row["access_token"]).strip()
                    if cand:
                        token = cand

        if "workspace" in tables:
            cols = {
                str(r["name"]) for r in con.execute("PRAGMA table_info(workspace)").fetchall()
            }
            if "id" in cols:
                order = "ORDER BY last_used_at DESC" if "last_used_at" in cols else ""
                rows = con.execute(f"SELECT * FROM workspace {order} LIMIT 10").fetchall()
                for row in rows:
                    if "provider" in row.keys() and "opencode" in str(row["provider"] or "").lower():
                        org_id = str(row["id"]).strip()
                        break
                if not org_id and rows and rows[0]["id"]:
                    org_id = str(rows[0]["id"]).strip()
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return token, org_id


def _fetch_live_usage(db_path: Path, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    """Resmî `/zen/go/v1/usage` çağrısı yapar; yalnız temiz yüzdeleri döndürür."""
    token, org_id = _read_go_credentials(db_path)
    if not token:
        return None, "Canlı API hatası (OpenCode Go kimlik bilgisi bulunamadı)"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "agent-notifier/0.2.0",
    }
    if org_id:
        headers["x-opencode-org-id"] = org_id
    req = Request(GO_USAGE_URL, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - sabit resmî https ucu
            raw = resp.read()
    except HTTPError as exc:
        if exc.code == 401:
            return None, "Canlı API hatası (HTTP 401 — oturum/token yenilenmeli)"
        if exc.code == 403:
            return None, "Canlı API hatası (HTTP 403 — Go aboneliği yok veya erişim reddedildi)"
        return None, f"Canlı API hatası (HTTP {exc.code})"
    except (URLError, TimeoutError, OSError):
        return None, "Canlı API hatası (ağ erişilemiyor)"

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "Canlı API hatası (geçersiz JSON)"

    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None, "Canlı API hatası (beklenmeyen yanıt biçimi)"

    sanitized: dict[str, dict[str, Any]] = {}
    for key in ("rolling", "weekly", "monthly"):
        entry = usage.get(key)
        if not isinstance(entry, dict):
            continue
        status_str = str(entry.get("status") or "ok").lower()
        rate_limited = status_str == "rate-limited"
        used = entry.get("percent")
        used_pct = clamp_pct(float(used)) if isinstance(used, (int, float)) else None
        rem_pct = 0.0 if rate_limited else (clamp_pct(100.0 - used_pct) if used_pct is not None else None)
        sanitized[key] = {
            "rate_limited": rate_limited,
            "used_pct": used_pct,
            "remaining_pct": rem_pct,
            "resets_at": entry.get("resetsAt") or entry.get("resetAt"),
        }
    return (sanitized, None) if sanitized else (None, "Canlı API hatası (boş kullanım verisi)")


def _local_5h_spend_usd(db_path: Path) -> float | None:
    """Bilgilendirme amaçlı son 5 saatlik yerel `opencode-go` harcaması (yüzde üretmez)."""
    if not db_path.is_file():
        return None
    since = now_ms() - 5 * 3600 * 1000
    try:
        con = ro_connect(db_path)
    except sqlite3.Error:
        return None
    total = 0.0
    try:
        rows = con.execute(
            """
            SELECT data FROM session_message
            WHERE type = 'assistant' AND time_created >= ?
            ORDER BY time_created DESC
            LIMIT 5000
            """,
            (since,),
        ).fetchall()
        for row in rows:
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            model = data.get("model") or {}
            if isinstance(model, dict) and str(model.get("providerID") or "") == "opencode-go":
                cost = data.get("cost")
                if isinstance(cost, (int, float)) and float(cost) > 0:
                    total += float(cost)
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return round(total, 4)


def _load_api_cache() -> dict[str, Any] | None:
    path = cfg_mod.provider_cache_path("opencode_go")
    if not path.is_file():
        return None
    try:
        raw = cfg_mod.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        return None
    return raw


class OpenCodeGoProviderAdapter:
    info = AdapterInfo(
        key="opencode_go",
        label="OpenCode Go",
        description="OpenCode Go resmî canlı kota API (/zen/go/v1/usage)",
    )

    def detect(self) -> bool:
        return opencode_db_path().is_file()

    def collect(
        self,
        *,
        config: dict[str, Any],
        force_refresh: bool = False,
        offline: bool = False,
        ttl_override: int | None = None,
    ) -> ProviderQuota:
        critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
        default_ttl = int(cfg_mod.get(config, "refresh.opencode_go_cache_seconds", 600))
        ttl = int(ttl_override) if ttl_override is not None else default_ttl
        live_enabled = bool(cfg_mod.get(config, "cloud.opencode_go_live_api", True))
        timeout = float(cfg_mod.get(config, "cloud.timeout_seconds", 10))
        db_path = opencode_db_path()
        now = time.time()

        api_data: dict[str, Any] | None = None
        api_fetched_ts: float | None = None
        api_cached = False
        api_error: str | None = None

        cached = _load_api_cache()
        cache_ts = float(cached.get("fetched_at") or 0) if cached else 0.0
        cache_age = (now - cache_ts) if cached else float("inf")

        if cached and cache_age <= ttl and not force_refresh:
            api_data = cached["data"]
            api_fetched_ts = cache_ts
            api_cached = True

        if api_data is None and live_enabled and not offline and db_path.is_file():
            live, err = _fetch_live_usage(db_path, timeout)
            if live:
                api_data = live
                api_fetched_ts = now
                api_cached = False
                try:
                    cfg_mod.atomic_write_json(
                        cfg_mod.provider_cache_path("opencode_go"),
                        {"fetched_at": now, "data": live},
                        mode=0o600,
                    )
                except OSError:
                    pass
            else:
                api_error = err
                # Yalnız yakın tarihli (<= 30 dk) gerçek API önbelleğini bayat etiketiyle kullan
                if cached and cache_age <= MAX_STALE_CACHE_SECONDS:
                    api_data = cached["data"]
                    api_fetched_ts = cache_ts
                    api_cached = True
        elif api_data is None and offline:
            if cached and cache_age <= MAX_STALE_CACHE_SECONDS:
                api_data = cached["data"]
                api_fetched_ts = cache_ts
                api_cached = True
            else:
                api_error = "Canlı API hatası (güncel önbellek yok)"
        elif api_data is None and not db_path.is_file():
            api_error = "Canlı API hatası (opencode.db bulunamadı)"
        elif api_data is None and not live_enabled:
            api_error = "Canlı API kapalı (`cloud.opencode_go_live_api = false`)"

        local_5h = _local_5h_spend_usd(db_path)

        if not api_data:
            return ProviderQuota(
                key=self.info.key,
                label=self.info.label,
                available=False,
                source="unavailable",
                local_spend_5h_usd=local_5h,
                error=api_error or "Canlı API hatası",
            )

        windows: list[QuotaWindow] = []
        for w_key in ("rolling", "weekly", "monthly"):
            entry = api_data.get(w_key)
            if not isinstance(entry, dict):
                continue
            resets_at = entry.get("resets_at")
            reset_ts = iso_to_ts(resets_at)
            reset_in = max(0, int(reset_ts - now)) if reset_ts else None
            windows.append(
                QuotaWindow(
                    key=w_key,
                    label=WINDOW_LABELS[w_key],
                    remaining_pct=entry.get("remaining_pct"),
                    used_pct=entry.get("used_pct"),
                    rate_limited=bool(entry.get("rate_limited")),
                    reset_at=str(resets_at) if resets_at else None,
                    reset_in_seconds=reset_in,
                    reset_human=format_duration_tr(reset_in),
                )
            )

        known_pcts = [
            0.0 if w.rate_limited else w.remaining_pct
            for w in windows
            if w.rate_limited or isinstance(w.remaining_pct, (int, float))
        ]
        headline = min(known_pcts) if known_pcts else None
        any_limited = any(w.rate_limited for w in windows)
        earliest_reset = next((w.reset_human for w in windows if w.reset_human), None)
        age_sec = max(0, int(now - api_fetched_ts)) if api_fetched_ts else 0

        plan_note = "Go Planı"
        if api_error and api_cached:
            plan_note = f"Go Planı (Bayat · {format_duration_tr(age_sec) or '—'})"

        return ProviderQuota(
            key=self.info.key,
            label=self.info.label,
            available=True,
            plan_label=plan_note,
            headline_pct=headline,
            critical=bool(any_limited or (headline is not None and headline < critical_pct)),
            rate_limited=any_limited,
            source="cache" if api_cached else "api",
            reset_human=earliest_reset,
            local_spend_5h_usd=local_5h,
            fetched_at=iso_now(api_fetched_ts) if api_fetched_ts else iso_now(now),
            age_seconds=age_sec,
            cached=api_cached,
            pools=[],
            windows=windows,
            error=api_error,
        )
