"""Kritik kota ve çakışma bildirimleri (`notify-send`).

Aynı uyarı için `state.json` üzerinden cooldown uygulanır; böylece Waybar'ın
sık yenileme döngüsü masaüstünü bildirim yağmuruna tutmaz.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any

from . import config as cfg_mod
from . import models as m


def _format_pct(value: float) -> str:
    return f"%{value:.0f}"


def collect_alerts(status: m.FullStatus, critical_pct: float) -> list[dict[str, str]]:
    """Gönderilmesi gereken uyarıları (anahtar + başlık + gövde) üretir."""
    alerts: list[dict[str, str]] = []

    for group in status.antigravity.groups:
        if group.rate_limited:
            alerts.append(
                {
                    "key": f"ag-lock:{group.key}",
                    "title": "Antigravity rate-limit kilidi",
                    "body": (
                        f"{group.label}: {group.rate_limit_human or 'kısa süre'} sonra açılacak "
                        f"(kalan {_format_pct(group.remaining_pct)})."
                    ),
                    "urgency": "normal",
                }
            )
        elif group.remaining_pct < critical_pct:
            alerts.append(
                {
                    "key": f"ag-critical:{group.key}",
                    "title": "Antigravity kotası kritik",
                    "body": (
                        f"{group.label}: {_format_pct(group.remaining_pct)} kaldı"
                        + (f", sıfırlanma {group.reset_human}" if group.reset_human else "")
                        + "."
                    ),
                    "urgency": "critical",
                }
            )

    for pool in status.go.pools:
        if pool.remaining_pct < critical_pct:
            alerts.append(
                {
                    "key": f"go-critical:{pool.key}",
                    "title": "OpenCode Go kotası kritik",
                    "body": (
                        f"{pool.label}: {_format_pct(pool.remaining_pct)} kaldı "
                        f"(5s ${pool.spend.get('5h', 0):.2f}/${pool.limits.get('5h', 0):.2f})."
                    ),
                    "urgency": "critical",
                }
            )

    for quota in status.copilot.quotas:
        pct = quota.percent_remaining
        if quota.unlimited or not quota.has_quota or not isinstance(pct, (int, float)):
            continue
        if pct < critical_pct:
            alerts.append(
                {
                    "key": f"gh-critical:{quota.key}",
                    "title": "GitHub Copilot kotası kritik",
                    "body": f"{quota.label}: {_format_pct(pct)} kaldı.",
                    "urgency": "normal",
                }
            )

    if status.sessions.dual_antigravity_conflict:
        alerts.append(
            {
                "key": "antigravity-conflict",
                "title": "Çift Antigravity çakışması",
                "body": (
                    f"{len(status.sessions.conflict_sessions)} oturum aynı anda Antigravity kullanıyor. "
                    "Diğer sekmeyi kapatmadan yeni Antigravity işi başlatma."
                ),
                "urgency": "critical",
            }
        )
    return alerts


def _can_notify() -> bool:
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    return shutil.which("notify-send") is not None


def maybe_notify(
    status: m.FullStatus,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> list[str]:
    """Cooldown'ı geçen uyarıları gönderir; gönderilen anahtarları döndürür."""
    config = config or cfg_mod.load_config()
    if not bool(cfg_mod.get(config, "notifications.enabled", True)):
        return []
    state = state if state is not None else cfg_mod.load_state()
    cooldown = int(cfg_mod.get(config, "notifications.cooldown_seconds", 1800))
    critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
    app_name = str(cfg_mod.get(config, "notifications.app_name", "waybar-ai-quota"))

    alerts = collect_alerts(status, critical_pct)
    if not alerts:
        return []
    if not _can_notify():
        return []

    history = state.setdefault("notifications", {})
    if not isinstance(history, dict):
        history = {}
        state["notifications"] = history
    now = time.time()
    sent: list[str] = []

    for alert in alerts:
        key = alert["key"]
        last = history.get(key)
        if isinstance(last, (int, float)) and (now - float(last)) < cooldown:
            continue
        command = [
            "notify-send",
            "-a",
            app_name,
            "-u",
            alert.get("urgency", "normal"),
            "-i",
            "dialog-warning",
            "-t",
            "10000",
            alert["title"],
            alert["body"][:400],
        ]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            history[key] = now
            sent.append(key)

    if sent:
        try:
            cfg_mod.save_state(state)
        except OSError:
            pass
    return sent
