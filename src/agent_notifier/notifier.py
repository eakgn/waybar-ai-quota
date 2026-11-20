"""Masaüstü bildirimleri (`notify-send`) ve cooldown yönetimi."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any

from . import config as cfg_mod
from .models import SessionNode, SessionState, Snapshot


def _iter_nodes(roots: list[SessionNode]) -> list[SessionNode]:
    out: list[SessionNode] = []
    for root in roots:
        out.append(root)
        if root.children:
            out.extend(_iter_nodes(root.children))
    return out


def collect_alerts(snapshot: Snapshot, config: dict[str, Any]) -> list[dict[str, str]]:
    """Gönderilmesi gereken bildirimleri üretir."""
    alerts: list[dict[str, str]] = []
    notify_waiting = bool(cfg_mod.get(config, "notifications.notify_on_waiting", True))
    notify_done = bool(cfg_mod.get(config, "notifications.notify_on_done", True))
    critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))

    for node in _iter_nodes(snapshot.sessions):
        who = node.project or node.title or node.id[:8]
        role = f" ({node.agent})" if node.agent else ""
        if node.state == SessionState.WAITING_INPUT and notify_waiting:
            body = node.message or f"{node.harness}: {who}{role} onay veya cevap bekliyor."
            alerts.append(
                {
                    "key": f"waiting:{node.harness}:{node.id}",
                    "title": f"Soru / Onay Bekliyor · {who}",
                    "body": body,
                    "urgency": "critical",
                    "icon": "dialog-question",
                }
            )
        elif node.state in (SessionState.ERROR, SessionState.RATE_LIMITED):
            title = "Kota Kilidi" if node.state == SessionState.RATE_LIMITED else "Oturum Hatası"
            body = node.message or f"{node.harness}: {who}{role} durdu."
            alerts.append(
                {
                    "key": f"err:{node.harness}:{node.id}:{node.state.value}",
                    "title": f"{title} · {who}",
                    "body": body,
                    "urgency": "critical",
                    "icon": "dialog-error",
                }
            )
        elif node.state == SessionState.DONE_UNSEEN and notify_done and not node.is_subagent:
            alerts.append(
                {
                    "key": f"done:{node.harness}:{node.id}",
                    "title": f"Oturum Tamamlandı · {who}",
                    "body": node.message or f"{node.harness}: {node.title or who} işini bitirdi.",
                    "urgency": "normal",
                    "icon": "emblem-ok-symbolic",
                }
            )

    for prov in snapshot.providers:
        if not prov.available:
            continue
        if prov.rate_limited:
            alerts.append(
                {
                    "key": f"quota-lock:{prov.key}",
                    "title": f"{prov.label} kota kilidi",
                    "body": (
                        f"Sıfırlanma: {prov.reset_human}"
                        if prov.reset_human
                        else "Kota penceresi doldu."
                    ),
                    "urgency": "normal",
                    "icon": "dialog-warning",
                }
            )
        elif isinstance(prov.headline_pct, (int, float)) and prov.headline_pct < critical_pct:
            alerts.append(
                {
                    "key": f"quota-critical:{prov.key}",
                    "title": f"{prov.label} kotası kritik",
                    "body": f"Kalan: %{prov.headline_pct:.0f}",
                    "urgency": "normal",
                    "icon": "dialog-warning",
                }
            )

    return alerts


def _can_notify() -> bool:
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    return shutil.which("notify-send") is not None


def maybe_notify(
    snapshot: Snapshot,
    *,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> list[str]:
    """Cooldown'ı geçen bildirimleri gönderir."""
    cfg = config if config is not None else cfg_mod.load_config()
    if not bool(cfg_mod.get(cfg, "notifications.enabled", True)):
        return []
    if not _can_notify():
        return []

    st = state if state is not None else cfg_mod.load_state()
    cooldown = int(cfg_mod.get(cfg, "notifications.cooldown_seconds", 300))
    app_name = str(cfg_mod.get(cfg, "notifications.app_name", "agent-notifier"))

    history = st.setdefault("notifications", {})
    if not isinstance(history, dict):
        history = {}
        st["notifications"] = history

    # Yeni bir tura (RUNNING) başlayan oturumun eski `done:` kilidini sıfırla ki
    # yeni tur bitince tekrar tek seferlik bildirim alabilsin; aynı turun 1-2 sn
    # sonraki trailing DB yazımları ise sabit `done:{harness}:{id}` ile tekilleştirilir.
    history_dirty = False
    for node in _iter_nodes(snapshot.sessions):
        if node.state == SessionState.RUNNING:
            done_key = f"done:{node.harness}:{node.id}"
            if done_key in history:
                del history[done_key]
                history_dirty = True

    now = time.time()
    # 24 saatten eski bildirim geçmişini buda (state.json şişmesin)
    cutoff = now - 86400
    for old_key in list(history):
        val = history.get(old_key)
        if isinstance(val, (int, float)) and float(val) < cutoff:
            del history[old_key]
            history_dirty = True

    alerts = collect_alerts(snapshot, cfg)
    if not alerts:
        if history_dirty:
            try:
                cfg_mod.save_state(st)
            except OSError:
                pass
        return []

    sent: list[str] = []

    for alert in alerts:
        key = alert["key"]
        last = history.get(key)
        if isinstance(last, (int, float)) and (now - float(last)) < cooldown:
            continue
        cmd = [
            "notify-send",
            "-a",
            app_name,
            "-u",
            alert.get("urgency", "normal"),
            "-i",
            alert.get("icon", "dialog-information"),
            "-t",
            "8000",
            alert["title"],
            alert["body"][:350],
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            history[key] = now
            sent.append(key)

    if sent or history_dirty:
        try:
            cfg_mod.save_state(st)
        except OSError:
            pass
    return sent
