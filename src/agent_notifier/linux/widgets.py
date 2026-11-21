"""Modüler GTK4 Dashboard bileşenleri (oturum ağacı, kota kartı, ayarlar)."""

from __future__ import annotations

from typing import Any, Callable

from ..adapters import harness_infos, provider_infos
from ..models import (
    STATE_SHORT_LABELS,
    ProviderQuota,
    SessionNode,
    SessionState,
)
from ..utils import format_duration_tr, short_model_name


def _state_css_suffix(state: SessionState) -> str:
    if state == SessionState.WAITING_INPUT:
        return "waiting"
    if state in (SessionState.ERROR, SessionState.RATE_LIMITED):
        return "error"
    if state == SessionState.RUNNING:
        return "running"
    if state == SessionState.DONE_UNSEEN:
        return "done"
    return "idle"


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _bar_color_class(pct: float | None, rate_limited: bool = False) -> str:
    if rate_limited:
        return "an-bar-red"
    if pct is None:
        return "an-bar-blue"
    if pct < 15:
        return "an-bar-red"
    if pct < 40:
        return "an-bar-yellow"
    return "an-bar-green"


def build_session_tree_card(node: SessionNode):
    """Ana oturum kartı + bağlı alt ajan (subagent) ağacı."""
    from gi.repository import Gtk, Pango

    suffix = _state_css_suffix(node.effective_state)
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.add_css_class("an-card")
    if suffix != "idle":
        card.add_css_class(f"an-card-{suffix}")

    # Üst satır: Proje/başlık + Harness rozeti + Durum rozeti
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    title_text = node.project or node.title or node.id[:10]
    title = Gtk.Label(label=title_text, xalign=0.0)
    title.set_hexpand(True)
    title.set_ellipsize(Pango.EllipsizeMode.END)
    title.add_css_class("an-session-title")
    top.append(title)

    h_pill = Gtk.Label(label=node.harness)
    h_pill.add_css_class("an-harness-pill")
    top.append(h_pill)

    badge = Gtk.Label(label=STATE_SHORT_LABELS[node.effective_state])
    badge.add_css_class("an-badge")
    badge.add_css_class(f"an-badge-{suffix}")
    top.append(badge)
    card.append(top)

    # İkinci satır: Ana ajan başlığı + model + yaş + token
    meta_parts: list[str] = []
    if node.title and node.title != title_text:
        meta_parts.append(node.title[:42] + ("…" if len(node.title) > 42 else ""))
    if node.agent:
        meta_parts.append(f"ana: {node.agent}")
    if node.model:
        meta_parts.append(short_model_name(node.model, 24))
    if node.tokens_total > 0:
        meta_parts.append(f"{_fmt_tokens(node.tokens_total)} tok")
    if node.updated_age_seconds is not None:
        meta_parts.append(format_duration_tr(node.updated_age_seconds) or "şimdi")

    if meta_parts:
        meta = Gtk.Label(label=" · ".join(meta_parts), xalign=0.0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.add_css_class("an-meta")
        card.append(meta)

    if node.message:
        msg = Gtk.Label(label=node.message, xalign=0.0, wrap=True)
        msg.add_css_class("an-message")
        card.append(msg)

    # Alt ajan (subagent) dalı — dikey çizgiyle girintili hiyerarşi
    if node.children:
        tree_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        tree_box.add_css_class("an-subagent-tree")

        sub_header = Gtk.Label(
            label=f"Alt Ajanlar ({len(node.children)})", xalign=0.0
        )
        sub_header.add_css_class("an-meta")
        tree_box.append(sub_header)

        for child in node.children:
            tree_box.append(_build_subagent_row(child))
        card.append(tree_box)

    return card


def _build_subagent_row(child: SessionNode):
    from gi.repository import Gtk, Pango

    c_suffix = _state_css_suffix(child.effective_state)
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    row.add_css_class("an-subagent-row")

    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    name = child.agent or child.title or child.id[:8]
    label = Gtk.Label(label=f"↳ {name}", xalign=0.0)
    label.set_hexpand(True)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.add_css_class("an-subagent-label")
    line.append(label)

    if child.model:
        mod = Gtk.Label(label=short_model_name(child.model, 16))
        mod.add_css_class("an-meta")
        line.append(mod)

    badge = Gtk.Label(label=STATE_SHORT_LABELS[child.state])
    badge.add_css_class("an-badge")
    badge.add_css_class(f"an-badge-{c_suffix}")
    line.append(badge)
    row.append(line)

    if child.message:
        msg = Gtk.Label(label=child.message, xalign=0.0, wrap=True)
        msg.add_css_class("an-message")
        row.append(msg)

    return row


def build_quota_card(provider: ProviderQuota):
    """Tek bir seçili (opt-in) kota sağlayıcısı kartı."""
    from gi.repository import Gtk, Pango

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.add_css_class("an-card")
    if provider.rate_limited or provider.critical:
        card.add_css_class("an-card-error")

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    title = Gtk.Label(label=provider.label, xalign=0.0)
    title.set_hexpand(True)
    title.add_css_class("an-session-title")
    top.append(title)

    if provider.plan_label:
        plan = Gtk.Label(label=provider.plan_label)
        plan.add_css_class("an-harness-pill")
        top.append(plan)

    if provider.available:
        if provider.rate_limited:
            pct_text = "KİLİTLİ"
        elif provider.all_unlimited:
            pct_text = "∞"
        elif isinstance(provider.headline_pct, (int, float)):
            pct_text = f"%{provider.headline_pct:.0f}"
        else:
            pct_text = "—"
        pct_lbl = Gtk.Label(label=pct_text)
        pct_lbl.add_css_class("an-session-title")
        top.append(pct_lbl)
    card.append(top)

    if not provider.available:
        err = Gtk.Label(label=provider.error or "Canlı API hatası", xalign=0.0, wrap=True)
        err.add_css_class("an-message")
        card.append(err)
        if isinstance(provider.local_spend_5h_usd, (int, float)):
            spend_lbl = Gtk.Label(
                label=f"Son 5s yerel harcama (bilgi): ${provider.local_spend_5h_usd:.2f}",
                xalign=0.0,
            )
            spend_lbl.add_css_class("an-meta")
            card.append(spend_lbl)
        return card

    if isinstance(provider.headline_pct, (int, float)):
        bar = Gtk.ProgressBar()
        bar.set_fraction(max(0.0, min(1.0, provider.headline_pct / 100.0)))
        bar.add_css_class("an-progress")
        bar.add_css_class(_bar_color_class(provider.headline_pct, provider.rate_limited))
        card.append(bar)

    if provider.error:
        warn = Gtk.Label(label=provider.error, xalign=0.0, wrap=True)
        warn.add_css_class("an-meta")
        card.append(warn)

    # Pencere özeti (ör. 5 Saatlik / Haftalık / Aylık)
    for win in provider.windows:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        w_lbl = Gtk.Label(label=win.label, xalign=0.0)
        w_lbl.set_hexpand(True)
        w_lbl.add_css_class("an-meta")
        row.append(w_lbl)

        val_parts: list[str] = []
        if win.rate_limited:
            val_parts.append("KİLİTLİ")
        elif isinstance(win.remaining_pct, (int, float)):
            val_parts.append(f"%{win.remaining_pct:.0f}")
        if win.reset_human:
            val_parts.append(f"↻ {win.reset_human}")
        val = Gtk.Label(label=" · ".join(val_parts))
        val.add_css_class("an-meta")
        row.append(val)
        card.append(row)

    if isinstance(provider.local_spend_5h_usd, (int, float)):
        spend_lbl = Gtk.Label(
            label=f"Son 5s yerel harcama: ${provider.local_spend_5h_usd:.2f}",
            xalign=0.0,
        )
        spend_lbl.add_css_class("an-meta")
        card.append(spend_lbl)

    # Havuz kırılımı (ör. Flash / Pro veya Chat / Premium)
    for pool in provider.pools:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        p_lbl = Gtk.Label(label=pool.label, xalign=0.0)
        p_lbl.set_hexpand(True)
        p_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        p_lbl.add_css_class("an-meta")
        row.append(p_lbl)

        right = "∞" if pool.unlimited else (
            f"%{pool.remaining_pct:.0f}" if isinstance(pool.remaining_pct, (int, float)) else "—"
        )
        if pool.detail and not pool.unlimited:
            right = f"{right} ({pool.detail})"
        p_val = Gtk.Label(label=right)
        p_val.add_css_class("an-meta")
        row.append(p_val)
        card.append(row)

    return card


def build_settings_panel(
    config: dict[str, Any],
    on_toggle: Callable[[str, str, bool], None],
):
    """Dashboard içi opt-in Harness ve Provider aç/kapa ayar görünümü."""
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

    def _add_group(heading: str, section: str, infos: list[Any]) -> None:
        lbl = Gtk.Label(label=heading, xalign=0.0)
        lbl.add_css_class("an-section-title")
        box.append(lbl)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("an-card")
        section_map = config.get(section) or {}

        for info in infos:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text_col.set_hexpand(True)
            name_lbl = Gtk.Label(label=info.label, xalign=0.0)
            name_lbl.add_css_class("an-session-title")
            desc_lbl = Gtk.Label(label=info.description, xalign=0.0, wrap=True)
            desc_lbl.add_css_class("an-meta")
            text_col.append(name_lbl)
            text_col.append(desc_lbl)
            row.append(text_col)

            switch = Gtk.Switch()
            switch.set_active(bool(section_map.get(info.key, False)))
            switch.set_valign(Gtk.Align.CENTER)
            switch.connect(
                "state-set",
                lambda _sw, active, s=section, k=info.key: (on_toggle(s, k, bool(active)), False)[1],
            )
            row.append(switch)
            card.append(row)
        box.append(card)

    _add_group("İZLENEN HARNESS'LAR", "harnesses", harness_infos())
    _add_group("GÖSTERİLEN KOTA SAĞLAYICILARI (OPT-IN)", "providers", provider_infos())

    note = Gtk.Label(
        label=(
            "Kapalı kaynaklar sorgulanmaz ve dashboard'da yer kaplamaz. Değişiklikler anında kaydedilir.\n\n"
            "Antigravity şeffaflık notu: Canlı kota yüzdesi Google ToS gereği dışarıdan çekilmez "
            "(OAuth / ekran kazıma yok); Antigravity yalnızca pasif oturum eklentisiyle izlenir. "
            "Canlı kota için `agy /usage` veya Antigravity App kullanın."
        ),
        xalign=0.0,
        wrap=True,
    )
    note.add_css_class("an-meta")
    box.append(note)
    return box
