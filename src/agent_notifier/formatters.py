"""Çoklu bar ve CLI formatlayıcıları (Waybar, Polybar, Plain, JSON)."""

from __future__ import annotations

import json
from typing import Any

from . import config as cfg_mod
from .models import STATE_LABELS, STATE_SHORT_LABELS, SessionNode, SessionState, Snapshot
from .utils import format_duration_tr, short_model_name

STATE_GLYPHS: dict[SessionState, str] = {
    SessionState.WAITING_INPUT: "❓",
    SessionState.ERROR: "✖",
    SessionState.RATE_LIMITED: "⛔",
    SessionState.RUNNING: "⚡",
    SessionState.DONE_UNSEEN: "✓",
    SessionState.IDLE: "●",
}

STATE_COLORS_HEX: dict[SessionState, str] = {
    SessionState.WAITING_INPUT: "#f9e2af",
    SessionState.ERROR: "#f38ba8",
    SessionState.RATE_LIMITED: "#fab387",
    SessionState.RUNNING: "#89b4fa",
    SessionState.DONE_UNSEEN: "#a6e3a1",
    SessionState.IDLE: "#9399b2",
}

PROVIDER_SHORT: dict[str, str] = {
    "opencode_go": "Go",
    "github_copilot": "GH",
}


def _escape_markup(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _quota_chunks(snapshot: Snapshot) -> list[str]:
    chunks: list[str] = []
    for prov in snapshot.providers:
        if not prov.available:
            continue
        short = PROVIDER_SHORT.get(prov.key, prov.label[:3])
        if prov.rate_limited:
            chunks.append(f"{short} KİLİT")
        elif isinstance(prov.headline_pct, (int, float)):
            chunks.append(f"{short} %{int(round(prov.headline_pct))}")
    return chunks


def _headline_session(snapshot: Snapshot) -> SessionNode | None:
    if not snapshot.sessions:
        return None
    top = snapshot.sessions[0]
    if top.effective_state != SessionState.IDLE:
        return top
    return None


def _bar_text(snapshot: Snapshot, config: dict[str, Any]) -> str:
    max_chars = int(cfg_mod.get(config, "bar.max_model_chars", 20))
    glyph = STATE_GLYPHS[snapshot.global_state]
    quotas = _quota_chunks(snapshot)
    lead = _headline_session(snapshot)

    parts: list[str] = []
    if lead is not None:
        who = lead.project or short_model_name(lead.model, max_chars)
        if len(who) > max_chars:
            who = who[: max_chars - 1] + "…"
        label = STATE_SHORT_LABELS[snapshot.global_state]
        sub_badge = f" (+{len(lead.children)})" if lead.children else ""
        parts.append(f"{glyph} {label}: {who}{sub_badge}")
    else:
        parts.append(f"{glyph} {STATE_SHORT_LABELS[SessionState.IDLE]}")

    if quotas:
        parts.extend(quotas)
    return " · ".join(parts)


def _bar_class(snapshot: Snapshot) -> str:
    mapping = {
        SessionState.WAITING_INPUT: "waiting",
        SessionState.ERROR: "critical",
        SessionState.RATE_LIMITED: "critical",
        SessionState.RUNNING: "active",
        SessionState.DONE_UNSEEN: "done",
        SessionState.IDLE: "idle",
    }
    cls = mapping[snapshot.global_state]
    if cls == "idle" and any(p.critical for p in snapshot.providers if p.available):
        return "warning"
    return cls


def _tooltip(snapshot: Snapshot) -> str:
    lines: list[str] = [f"<b>agent-notifier</b> — {_escape_markup(snapshot.global_label)}"]

    if snapshot.sessions:
        lines.append("")
        lines.append("<b>Oturumlar</b>")
        for root in snapshot.sessions[:5]:
            glyph = STATE_GLYPHS[root.effective_state]
            who = root.project or root.title or root.id[:8]
            age = format_duration_tr(root.updated_age_seconds) if root.updated_age_seconds is not None else "—"
            lines.append(
                f"  {glyph} <b>{_escape_markup(who)}</b> "
                f"<i>({_escape_markup(root.harness)} · {STATE_SHORT_LABELS[root.state]} · {age})</i>"
            )
            for child in root.children[:4]:
                c_glyph = STATE_GLYPHS[child.state]
                c_name = child.agent or child.title or child.id[:8]
                lines.append(
                    f"     └ {c_glyph} {_escape_markup(c_name)} ({STATE_SHORT_LABELS[child.state]})"
                )
    else:
        lines.append("")
        lines.append("<i>Aktif oturum yok</i>")

    if snapshot.providers:
        lines.append("")
        lines.append("<b>Seçili Kotalar</b>")
        for prov in snapshot.providers:
            if not prov.available:
                lines.append(f"  {_escape_markup(prov.label)}: veri yok")
                continue
            pct_str = (
                "KİLİTLİ"
                if prov.rate_limited
                else (f"%{prov.headline_pct:.0f}" if isinstance(prov.headline_pct, (int, float)) else "—")
            )
            reset = f" (sıfırlanma {prov.reset_human})" if prov.reset_human else ""
            lines.append(f"  <b>{_escape_markup(prov.label)}:</b> {pct_str}{_escape_markup(reset)}")

    return "\n".join(lines)


def format_waybar(snapshot: Snapshot, config: dict[str, Any]) -> str:
    pcts = [
        p.headline_pct
        for p in snapshot.providers
        if p.available and isinstance(p.headline_pct, (int, float))
    ]
    payload = {
        "text": _bar_text(snapshot, config),
        "tooltip": _tooltip(snapshot),
        "class": _bar_class(snapshot),
        "alt": snapshot.global_state.value,
        "percentage": int(round(min(pcts))) if pcts else 100,
    }
    return json.dumps(payload, ensure_ascii=False)


def format_polybar(snapshot: Snapshot, config: dict[str, Any]) -> str:
    color = STATE_COLORS_HEX[snapshot.global_state]
    text = _bar_text(snapshot, config)
    return f"%{{F{color}}}{text}%{{F-}}"


def format_plain(snapshot: Snapshot, config: dict[str, Any]) -> str:
    return _bar_text(snapshot, config)


def format_json(snapshot: Snapshot) -> str:
    return json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)


def render(snapshot: Snapshot, fmt: str, config: dict[str, Any]) -> str:
    key = (fmt or "plain").strip().lower()
    if key == "waybar":
        return format_waybar(snapshot, config)
    if key == "polybar":
        return format_polybar(snapshot, config)
    if key == "json":
        return format_json(snapshot)
    if key in ("plain", "swaybar", "tmux"):
        return format_plain(snapshot, config)
    raise ValueError(f"desteklenmeyen biçim: {fmt!r}")
