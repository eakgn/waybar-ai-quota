"""GitHub Copilot opt-in kota sağlayıcısı (`gh api /copilot_internal/user`)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from .. import config as cfg_mod
from ..models import ProviderQuota, QuotaPool
from ..utils import iso_now
from .base import AdapterInfo

COPILOT_LABELS: dict[str, str] = {
    "chat": "Chat",
    "completions": "Completions",
    "premium_interactions": "Premium / AI Credits",
}


def _plan_label(sku: str, plan: str, override: str = "") -> str:
    if override:
        return override
    combined = f"{sku} {plan}".lower()
    if "student" in combined:
        return "Copilot Student"
    if "free" in combined or "limited" in combined:
        return "Copilot Free"
    if plan or sku:
        return "Copilot Pro"
    return "GitHub Copilot"


def _sanitize(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    sku = str(payload.get("access_type_sku") or "")
    plan = str(payload.get("copilot_plan") or "")
    override = str(cfg_mod.get(config, "copilot.plan_label_override", "") or "")
    snapshots = payload.get("quota_snapshots") or {}
    quotas: list[dict[str, Any]] = []
    keys = list(COPILOT_LABELS) + sorted(k for k in snapshots if k not in COPILOT_LABELS)
    for key in keys:
        snap = snapshots.get(key)
        if not isinstance(snap, dict):
            continue
        pct = snap.get("percent_remaining")
        quotas.append(
            {
                "key": key,
                "label": COPILOT_LABELS.get(key, key.replace("_", " ").title()),
                "remaining_pct": round(float(pct), 1) if isinstance(pct, (int, float)) else None,
                "unlimited": bool(snap.get("unlimited")),
                "has_quota": bool(snap.get("has_quota", True)),
            }
        )
    return {
        "plan_label": _plan_label(sku, plan, override),
        "reset_date": payload.get("quota_reset_date"),
        "quotas": quotas,
    }


def _load_cache() -> dict[str, Any] | None:
    path = cfg_mod.provider_cache_path("github_copilot")
    if not path.is_file():
        return None
    try:
        raw = cfg_mod.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        return None
    return raw


def _build_quota(
    data: dict[str, Any],
    fetched_ts: float,
    cached: bool,
    critical_pct: float,
    error: str | None = None,
) -> ProviderQuota:
    pools: list[QuotaPool] = []
    limited_pcts: list[float] = []
    for item in data.get("quotas") or []:
        if not isinstance(item, dict):
            continue
        unlimited = bool(item.get("unlimited"))
        pct = item.get("remaining_pct")
        if not unlimited and item.get("has_quota", True) and isinstance(pct, (int, float)):
            limited_pcts.append(float(pct))
        pools.append(
            QuotaPool(
                key=str(item.get("key") or ""),
                label=str(item.get("label") or ""),
                remaining_pct=None if unlimited else (float(pct) if isinstance(pct, (int, float)) else None),
                unlimited=unlimited,
                critical=bool(not unlimited and isinstance(pct, (int, float)) and float(pct) < critical_pct),
                detail="Sınırsız" if unlimited else "",
            )
        )
    all_unlimited = bool(pools) and all(p.unlimited for p in pools)
    headline = min(limited_pcts) if limited_pcts else None
    return ProviderQuota(
        key="github_copilot",
        label="GitHub Copilot",
        available=bool(pools) or bool(data.get("plan_label")),
        plan_label=data.get("plan_label"),
        headline_pct=headline,
        all_unlimited=all_unlimited,
        critical=bool(headline is not None and headline < critical_pct),
        source="cache" if cached else "api",
        reset_human=str(data.get("reset_date") or "") or None,
        fetched_at=iso_now(fetched_ts) if fetched_ts else None,
        age_seconds=max(0, int(time.time() - fetched_ts)) if fetched_ts else None,
        cached=cached,
        pools=pools,
        error=error,
    )


class GitHubCopilotProviderAdapter:
    info = AdapterInfo(
        key="github_copilot",
        label="GitHub Copilot",
        description="GitHub CLI (`gh`) üzerinden Copilot abonelik kota durumu",
    )

    def detect(self) -> bool:
        return shutil.which("gh") is not None

    def collect(
        self,
        *,
        config: dict[str, Any],
        force_refresh: bool = False,
        offline: bool = False,
        ttl_override: int | None = None,
    ) -> ProviderQuota:
        critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
        default_ttl = int(cfg_mod.get(config, "refresh.copilot_cache_seconds", 600))
        ttl = int(ttl_override) if ttl_override is not None else default_ttl
        cache = _load_cache()
        cache_ts = float(cache.get("fetched_at") or 0) if cache else 0.0
        fresh = bool(cache) and (time.time() - cache_ts) <= ttl

        if cache and fresh and not force_refresh:
            return _build_quota(cache["data"], cache_ts, True, critical_pct)
        if offline:
            if cache:
                return _build_quota(cache["data"], cache_ts, True, critical_pct, error="önbellek bayat")
            return ProviderQuota(key=self.info.key, label=self.info.label, available=False, error="Copilot önbelleği yok")

        gh = str(cfg_mod.get(config, "copilot.gh_binary", "") or "").strip() or shutil.which("gh")
        if not gh:
            if cache:
                return _build_quota(cache["data"], cache_ts, True, critical_pct, error="gh bulunamadı")
            return ProviderQuota(key=self.info.key, label=self.info.label, available=False, error="gh CLI bulunamadı")

        try:
            proc = subprocess.run(
                [gh, "api", "/copilot_internal/user"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            if cache:
                return _build_quota(cache["data"], cache_ts, True, critical_pct, error="gh çalıştırılamadı")
            return ProviderQuota(key=self.info.key, label=self.info.label, available=False, error="gh çalıştırılamadı")

        if proc.returncode != 0:
            err = f"gh api başarısız (kod {proc.returncode})"
            if cache:
                return _build_quota(cache["data"], cache_ts, True, critical_pct, error=err)
            return ProviderQuota(key=self.info.key, label=self.info.label, available=False, error=err)

        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            err = "gh yanıtı geçersiz JSON"
            if cache:
                return _build_quota(cache["data"], cache_ts, True, critical_pct, error=err)
            return ProviderQuota(key=self.info.key, label=self.info.label, available=False, error=err)

        if not isinstance(payload, dict):
            return ProviderQuota(key=self.info.key, label=self.info.label, available=False, error="beklenmeyen yanıt")

        data = _sanitize(payload, config)
        now = time.time()
        try:
            cfg_mod.atomic_write_json(
                cfg_mod.provider_cache_path("github_copilot"),
                {"fetched_at": now, "data": data},
                mode=0o600,
            )
        except OSError:
            pass
        return _build_quota(data, now, False, critical_pct)
