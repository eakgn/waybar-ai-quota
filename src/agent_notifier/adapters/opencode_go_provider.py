"""OpenCode Go kota sağlayıcısı (hibrit: resmî API + yerel opencode.db fallback).

Resmî uç (OPENCODE_QUOTA_API_NOTES.md):
  GET https://opencode.ai/zen/go/v1/usage
  Headers: Authorization: Bearer <token>, x-opencode-org-id: <workspaceID>

Gizlilik:
- Kimlik bilgisi yalnız bellek içinde `opencode.db` salt-okunur sorgulanarak okunur;
  asla önbelleğe, loglara veya çıktı modellerine yazılmaz.
- Ağ/kimlik başarısız olursa sessizce yerel `opencode.db` harcama hesabına düşer.
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
from ..models import ProviderQuota, QuotaPool, QuotaWindow
from ..utils import clamp_pct, format_duration_tr, iso_now, iso_to_ts, now_ms, opencode_db_path, ro_connect
from .base import AdapterInfo

GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"

WINDOWS_MS: dict[str, int] = {
    "rolling": 5 * 3600 * 1000,
    "weekly": 7 * 24 * 3600 * 1000,
    "monthly": 30 * 24 * 3600 * 1000,
}

WINDOW_LABELS: dict[str, str] = {
    "rolling": "5 Saatlik",
    "weekly": "Haftalık",
    "monthly": "Aylık",
}

GO_POOLS: tuple[dict[str, Any], ...] = (
    {
        "key": "flash",
        "label": "DeepSeek V4.1 Flash",
        "models": ("deepseek-v4.1-flash",),
        "limits": {"rolling": 20.0, "weekly": 50.0, "monthly": 100.0},
    },
    {
        "key": "mid",
        "label": "GLM 5.3 / Kimi Code",
        "models": ("glm-5.3-flash", "kimi-k2.7-code"),
        "limits": {"rolling": 12.0, "weekly": 30.0, "monthly": 60.0},
    },
    {
        "key": "argon",
        "label": "Gemini 4 Argon",
        "models": ("gemini-4-argon",),
        "limits": {"rolling": 6.0, "weekly": 15.0, "monthly": 30.0},
    },
    {
        "key": "premium",
        "label": "Pro / Premium ($15)",
        "models": ("deepseek-v4-pro", "claude-haiku-5.5", "gpt-6-luna", "grok-4.7", "kimi-k3"),
        "limits": {"rolling": 3.0, "weekly": 7.5, "monthly": 15.0},
    },
)

_POOL_INDEX: dict[str, dict[str, Any]] = {}
for _p in GO_POOLS:
    for _m in _p["models"]:
        _POOL_INDEX[_m] = _p
_MATCH_ORDER = sorted(_POOL_INDEX, key=len, reverse=True)


def _pool_for_model(model_id: str) -> dict[str, Any] | None:
    normalized = (model_id or "").split("#", 1)[0].strip().lower()
    for candidate in _MATCH_ORDER:
        if normalized == candidate or normalized.startswith(candidate + "-"):
            return _POOL_INDEX[candidate]
    return None


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

    Token, e-posta ve orgID asla loga, önbelleğe veya çıktıya yazılmaz.
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

        # 1. Öncelik: `credential` tablosunda opencode-go / opencode kaydı (active öncelikli)
        if "credential" in tables:
            cols = {
                str(r["name"]) for r in con.execute("PRAGMA table_info(credential)").fetchall()
            }
            order = "ORDER BY COALESCE(active, 0) DESC, time_updated DESC" if "active" in cols and "time_updated" in cols else ""
            rows = con.execute(f"SELECT * FROM credential {order}").fetchall()
            # Önce açıkça `opencode-go`, sonra genel `opencode`
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

        # 2. Yedek: `control_account` tablosunda aktif OAuth access_token
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

        # 3. `workspace` tablosundan en son kullanılan org/workspace kimliği
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
        return None, None
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
        if exc.code == 403:
            return None, "Go aboneliği etkin değil (HTTP 403)"
        return None, f"Go API HTTP {exc.code}"
    except (URLError, TimeoutError, OSError):
        return None, "Go API ağ hatası"

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "Go API geçersiz JSON"

    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None, "Go API beklenmeyen biçim"

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
    return sanitized if sanitized else None, None


def _local_go_spend(db_path: Path) -> tuple[dict[str, dict[str, float]], dict[str, float], str | None]:
    now = now_ms()
    starts = {k: now - span for k, span in WINDOWS_MS.items()}
    month_start = starts["monthly"]
    pool_spend = {p["key"]: {k: 0.0 for k in WINDOWS_MS} for p in GO_POOLS}
    total_spend = {k: 0.0 for k in WINDOWS_MS}

    try:
        con = ro_connect(db_path)
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
            if not isinstance(cost, (int, float)) or float(cost) <= 0:
                continue
            cost_f = float(cost)
            pool = _pool_for_model(str(model.get("id") or ""))
            created = int(row["time_created"] or 0)
            message_costs[row["session_id"]] = message_costs.get(row["session_id"], 0.0) + cost_f
            for key, start in starts.items():
                if created >= start:
                    total_spend[key] += cost_f
                    if pool:
                        pool_spend[pool["key"]][key] += cost_f

        sessions = con.execute(
            "SELECT id, model, cost, time_updated FROM session_v2 WHERE time_updated >= ? AND cost > 0",
            (month_start,),
        ).fetchall()
    except sqlite3.Error as exc:
        con.close()
        return pool_spend, total_spend, f"Go harcaması okunamadı: {exc}"
    con.close()

    for session in sessions:
        residual = float(session["cost"] or 0.0) - message_costs.get(session["id"], 0.0)
        if residual <= 0:
            continue
        raw_model = session["model"]
        prov, mod = "", ""
        if isinstance(raw_model, str):
            try:
                parsed = json.loads(raw_model)
                if isinstance(parsed, dict):
                    prov = str(parsed.get("providerID") or parsed.get("provider") or "")
                    mod = str(parsed.get("id") or parsed.get("modelID") or "")
            except ValueError:
                pass
        if prov != "opencode-go":
            continue
        pool = _pool_for_model(mod)
        updated = int(session["time_updated"] or 0)
        for key, start in starts.items():
            if updated >= start:
                total_spend[key] += residual
                if pool:
                    pool_spend[pool["key"]][key] += residual

    return pool_spend, total_spend, None


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
        description="OpenCode Go resmî kota API (/zen/go/v1/usage) + yerel DB havuz kırılımı",
    )

    def detect(self) -> bool:
        return opencode_db_path().is_file()

    def collect(
        self,
        *,
        config: dict[str, Any],
        force_refresh: bool = False,
        offline: bool = False,
    ) -> ProviderQuota:
        critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
        ttl = int(cfg_mod.get(config, "refresh.opencode_go_cache_seconds", 600))
        live_enabled = bool(cfg_mod.get(config, "cloud.opencode_go_live_api", True))
        timeout = float(cfg_mod.get(config, "cloud.timeout_seconds", 10))
        db_path = opencode_db_path()
        now = time.time()

        # 1. Resmî canlı API (önbellekli)
        api_data: dict[str, Any] | None = None
        api_fetched_ts: float | None = None
        api_cached = False
        api_error: str | None = None

        cached = _load_api_cache()
        if cached:
            fetched_ts = float(cached.get("fetched_at") or 0)
            if (now - fetched_ts) <= ttl and not force_refresh:
                api_data = cached["data"]
                api_fetched_ts = fetched_ts
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
                if cached:
                    api_data = cached["data"]
                    api_fetched_ts = float(cached.get("fetched_at") or 0)
                    api_cached = True
        elif api_data is None and cached:
            api_data = cached["data"]
            api_fetched_ts = float(cached.get("fetched_at") or 0)
            api_cached = True

        # 2. Yerel opencode.db havuz kırılımı & offline fallback
        pools: list[QuotaPool] = []
        local_windows: list[QuotaWindow] = []
        local_error: str | None = None

        if db_path.is_file():
            pool_spend, total_spend, local_error = _local_go_spend(db_path)
            if not local_error:
                for p in GO_POOLS:
                    w_list: list[QuotaWindow] = []
                    rem_values: list[float] = []
                    for w_key, limit in p["limits"].items():
                        spent = round(pool_spend[p["key"]][w_key], 4)
                        pct = clamp_pct((limit - spent) / limit * 100.0) if limit > 0 else 0.0
                        rem_values.append(pct)
                        w_list.append(
                            QuotaWindow(
                                key=w_key,
                                label=WINDOW_LABELS[w_key],
                                remaining_pct=pct,
                                used_pct=clamp_pct(100.0 - pct),
                                spent_usd=spent,
                                limit_usd=limit,
                            )
                        )
                    pool_pct = min(rem_values) if rem_values else 100.0
                    pools.append(
                        QuotaPool(
                            key=p["key"],
                            label=p["label"],
                            remaining_pct=pool_pct,
                            critical=pool_pct < critical_pct,
                            detail=f"5s ${pool_spend[p['key']]['rolling']:.2f}/${p['limits']['rolling']:.0f}",
                            windows=w_list,
                        )
                    )

                for w_key in ("rolling", "weekly", "monthly"):
                    total_limit = sum(p["limits"][w_key] for p in GO_POOLS)
                    spent = round(total_spend[w_key], 4)
                    pct = clamp_pct((total_limit - spent) / total_limit * 100.0) if total_limit > 0 else 0.0
                    local_windows.append(
                        QuotaWindow(
                            key=w_key,
                            label=WINDOW_LABELS[w_key],
                            remaining_pct=pct,
                            used_pct=clamp_pct(100.0 - pct),
                            spent_usd=spent,
                            limit_usd=round(total_limit, 2),
                        )
                    )
        else:
            local_error = "opencode.db bulunamadı"

        # 3. Canlı API pencerelerini önceliklendir, yoksa yerel pencereleri kullan
        windows: list[QuotaWindow] = []
        if api_data:
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
            source = "hybrid" if pools else "api"
        else:
            windows = local_windows
            source = "local"

        if not windows and not pools:
            return ProviderQuota(
                key=self.info.key,
                label=self.info.label,
                available=False,
                error=api_error or local_error or "OpenCode Go verisi yok",
            )

        known_pcts = [
            0.0 if w.rate_limited else w.remaining_pct
            for w in windows
            if w.rate_limited or isinstance(w.remaining_pct, (int, float))
        ]
        headline = min(known_pcts) if known_pcts else None
        any_limited = any(w.rate_limited for w in windows)
        earliest_reset = next((w.reset_human for w in windows if w.reset_human), None)

        return ProviderQuota(
            key=self.info.key,
            label=self.info.label,
            available=True,
            plan_label="Go Planı" + (" (Canlı API)" if api_data else " (Yerel DB)"),
            headline_pct=headline,
            critical=bool(any_limited or (headline is not None and headline < critical_pct)),
            rate_limited=any_limited,
            source=source,
            reset_human=earliest_reset,
            fetched_at=iso_now(api_fetched_ts) if api_fetched_ts else iso_now(now),
            age_seconds=max(0, int(now - api_fetched_ts)) if api_fetched_ts else 0,
            cached=api_cached,
            pools=pools,
            windows=windows,
            error=api_error if not api_data else None,
        )
