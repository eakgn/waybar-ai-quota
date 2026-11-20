"""GTK4 + Libadwaita + Gio.DBus SNI uygulaması (Tray + Toggleable Dashboard)."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import threading
from datetime import datetime
from typing import Any

# Gtk4LayerShell, GTK4'ten ÖNCE RTLD_GLOBAL ile yüklenmelidir (Fedora/wlroots)
for _lib in ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so"):
    try:
        ctypes.CDLL(_lib, mode=ctypes.RTLD_GLOBAL)
        break
    except OSError:
        continue

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

LAYER_SHELL: Any = None
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LAYER_SHELL  # noqa: E402
except (ImportError, ValueError):
    LAYER_SHELL = None

from .. import config as cfg_mod  # noqa: E402
from .. import engine, events as events_mod, notifier  # noqa: E402
from ..models import STATE_LABELS, SessionState, Snapshot  # noqa: E402
from .sni import StatusNotifierTray  # noqa: E402
from .styles import DASHBOARD_CSS  # noqa: E402
from .widgets import build_quota_card, build_session_tree_card, build_settings_panel  # noqa: E402

APP_ID = "org.eakgn.AgentNotifier"


def _clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


class DashboardWindow(Adw.ApplicationWindow):
    """Tıklanınca açılan, wlroots'ta Layer-Shell, diğer DE'lerde kayan Dashboard."""

    def __init__(self, app: AgentNotifierApp, *, anchor: str = "top-right") -> None:
        super().__init__(application=app, title="Agent Notifier")
        self._app = app
        self._settings_open = False
        self.add_css_class("an-window")
        self.set_default_size(430, 620)
        self.set_resizable(True)

        self._configure_layer_shell(anchor)
        self._build_layout()

        # Escape ile kapat / gizle
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Tray modunda kapatma tuşu pencereyi gizler; tek seferlik dashboard modunda çıkar
        self.connect("close-request", self._on_close_request)

    def _configure_layer_shell(self, anchor: str) -> None:
        if LAYER_SHELL is None or not os.environ.get("WAYLAND_DISPLAY"):
            return
        try:
            if not LAYER_SHELL.is_supported():
                return
            LAYER_SHELL.init_for_window(self)
            LAYER_SHELL.set_namespace(self, "agent-notifier")
            LAYER_SHELL.set_layer(self, LAYER_SHELL.Layer.OVERLAY)
            LAYER_SHELL.set_keyboard_mode(self, LAYER_SHELL.KeyboardMode.ON_DEMAND)

            parts = (anchor or "top-right").lower()
            top = "bottom" not in parts
            right = "left" not in parts
            LAYER_SHELL.set_anchor(self, LAYER_SHELL.Edge.TOP, top)
            LAYER_SHELL.set_anchor(self, LAYER_SHELL.Edge.BOTTOM, not top)
            LAYER_SHELL.set_anchor(self, LAYER_SHELL.Edge.RIGHT, right)
            LAYER_SHELL.set_anchor(self, LAYER_SHELL.Edge.LEFT, not right)
            LAYER_SHELL.set_margin(self, LAYER_SHELL.Edge.TOP, 10)
            LAYER_SHELL.set_margin(self, LAYER_SHELL.Edge.BOTTOM, 10)
            LAYER_SHELL.set_margin(self, LAYER_SHELL.Edge.RIGHT, 12)
            LAYER_SHELL.set_margin(self, LAYER_SHELL.Edge.LEFT, 12)
        except Exception:
            pass

    def _build_layout(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("an-header")

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_col.set_hexpand(True)
        self._title_lbl = Gtk.Label(label="Agent Notifier", xalign=0.0)
        self._title_lbl.add_css_class("an-title")
        self._subtitle_lbl = Gtk.Label(label="Yükleniyor…", xalign=0.0)
        self._subtitle_lbl.add_css_class("an-subtitle")
        title_col.append(self._title_lbl)
        title_col.append(self._subtitle_lbl)
        header.append(title_col)

        self._refresh_btn = Gtk.Button(label="↻")
        self._refresh_btn.set_tooltip_text("Şimdi Yenile (Canlı API)")
        self._refresh_btn.add_css_class("an-icon-btn")
        self._refresh_btn.connect("clicked", lambda _b: self._app.trigger_refresh(force_remote=True))
        header.append(self._refresh_btn)

        self._settings_btn = Gtk.Button(label="⚙")
        self._settings_btn.set_tooltip_text("Harness ve Kota Ayarları")
        self._settings_btn.add_css_class("an-icon-btn")
        self._settings_btn.connect("clicked", self._on_toggle_settings)
        header.append(self._settings_btn)

        close_btn = Gtk.Button(label="✕")
        close_btn.set_tooltip_text("Kapat (Esc)")
        close_btn.add_css_class("an-icon-btn")
        close_btn.connect("clicked", lambda _b: self._dismiss())
        header.append(close_btn)
        outer.append(header)

        # Kaydırılabilir gövde
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._content.set_margin_top(12)
        self._content.set_margin_bottom(14)
        self._content.set_margin_start(14)
        self._content.set_margin_end(14)
        scroll.set_child(self._content)
        outer.append(scroll)

        self.set_content(outer)

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._dismiss()
            return True
        return False

    def _on_close_request(self, _win) -> bool:
        if self._app.tray_mode:
            self.set_visible(False)
            return True
        return False

    def _dismiss(self) -> None:
        if self._app.tray_mode:
            self.set_visible(False)
        else:
            self.close()

    def _on_toggle_settings(self, _btn) -> None:
        self._settings_open = not self._settings_open
        self._settings_btn.set_label("←" if self._settings_open else "⚙")
        self.render(self._app.latest_snapshot, self._app.config)

    def render(self, snapshot: Snapshot | None, config: dict[str, Any]) -> None:
        _clear_box(self._content)
        clock = datetime.now().strftime("%H:%M:%S")

        if self._settings_open:
            self._subtitle_lbl.set_label("Ayarlar · Opt-in kaynak seçimi")
            self._content.append(build_settings_panel(config, self._app.on_setting_toggled))
            return

        if snapshot is None:
            self._subtitle_lbl.set_label(f"Bekleniyor · {clock}")
            empty = Gtk.Label(label="Durum toplanıyor…")
            empty.add_css_class("an-empty")
            self._content.append(empty)
            return

        self._subtitle_lbl.set_label(f"{snapshot.global_label} · {clock}")

        # 1. Oturumlar (Ana Ajan + Subagent Ağacı)
        sess_title = Gtk.Label(label="OTURUMLAR VE ALT AJANLAR", xalign=0.0)
        sess_title.add_css_class("an-section-title")
        self._content.append(sess_title)

        visible_sessions = [
            s for s in snapshot.sessions if s.effective_state != SessionState.IDLE
        ]
        if not visible_sessions and snapshot.sessions:
            visible_sessions = snapshot.sessions[:3]

        if visible_sessions:
            for root in visible_sessions[:8]:
                self._content.append(build_session_tree_card(root))
        else:
            empty = Gtk.Label(
                label="Aktif veya yakın zamanda çalışan oturum yok.\nAyarlar (⚙) menüsünden izlenen harness'ları yönetebilirsin.",
                xalign=0.0,
                wrap=True,
            )
            empty.add_css_class("an-empty")
            self._content.append(empty)

        # 2. Yalnız Seçili (Opt-in) Kota Sağlayıcıları
        if snapshot.providers:
            q_title = Gtk.Label(label="SEÇİLİ KOTA SAĞLAYICILARI", xalign=0.0)
            q_title.add_css_class("an-section-title")
            self._content.append(q_title)
            for prov in snapshot.providers:
                self._content.append(build_quota_card(prov))


class AgentNotifierApp(Adw.Application):
    """Ana GTK4/Adw uygulaması: Tray (sürekli) veya tek seferlik Dashboard."""

    def __init__(self, *, tray_mode: bool = True) -> None:
        flags = Gio.ApplicationFlags.NON_UNIQUE if not tray_mode else Gio.ApplicationFlags.FLAGS_NONE
        super().__init__(application_id=APP_ID, flags=flags)
        self.tray_mode = tray_mode
        self.config: dict[str, Any] = cfg_mod.load_config()
        self.latest_snapshot: Snapshot | None = None
        self._window: DashboardWindow | None = None
        self._tray: StatusNotifierTray | None = None
        self._poll_source_id = 0
        self._last_events_mtime = 0.0
        self._refreshing = False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        provider = Gtk.CssProvider()
        provider.load_from_data(DASHBOARD_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        if self.tray_mode:
            self.hold()
            self._tray = StatusNotifierTray(
                on_activate=self.toggle_dashboard,
                on_refresh=lambda: self.trigger_refresh(force_remote=True),
                on_ack=self.ack_done,
                on_quit=self.quit_app,
            )
            self._tray.start()

        # İlk veri çekimi ve düzenli kontrol
        self.trigger_refresh(force_remote=False)
        self._poll_source_id = GLib.timeout_add_seconds(3, self._on_tick)

    def do_activate(self) -> None:
        if not self.tray_mode:
            self.show_dashboard()

    def _ensure_window(self) -> DashboardWindow:
        if self._window is None:
            anchor = str(cfg_mod.get(self.config, "ui.anchor", "top-right"))
            self._window = DashboardWindow(self, anchor=anchor)
        return self._window

    def show_dashboard(self) -> None:
        win = self._ensure_window()
        if bool(cfg_mod.get(self.config, "ui.ack_done_on_open", True)):
            if self.latest_snapshot and self.latest_snapshot.done_unseen_count > 0:
                self.ack_done()
        win.render(self.latest_snapshot, self.config)
        win.set_visible(True)
        win.present()

    def toggle_dashboard(self) -> None:
        win = self._ensure_window()
        if win.get_visible():
            win.set_visible(False)
        else:
            self.show_dashboard()

    def ack_done(self) -> None:
        events_mod.ack_done_sessions()
        self.trigger_refresh(force_remote=False)

    def on_setting_toggled(self, section: str, key: str, enabled: bool) -> None:
        self.config = cfg_mod.set_toggle(self.config, section, key, enabled)
        cfg_mod.save_config(self.config)
        self.trigger_refresh(force_remote=False)

    def _on_tick(self) -> bool:
        ev_file = cfg_mod.events_path()
        try:
            mtime = ev_file.stat().st_mtime if ev_file.is_file() else 0.0
        except OSError:
            mtime = 0.0
        if mtime != self._last_events_mtime:
            self._last_events_mtime = mtime
        self.trigger_refresh(force_remote=False)
        return True

    def trigger_refresh(self, *, force_remote: bool = False) -> None:
        if self._refreshing:
            return
        self._refreshing = True

        def _worker() -> None:
            try:
                cfg = cfg_mod.load_config()
                state = cfg_mod.load_state()
                snap = engine.collect_snapshot(
                    config=cfg,
                    state=state,
                    force_refresh=force_remote,
                    offline=False,
                )
                snap.notifications_sent = notifier.maybe_notify(snap, config=cfg, state=state)
                GLib.idle_add(self._apply_snapshot, snap, cfg)
            finally:
                self._refreshing = False

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_snapshot(self, snapshot: Snapshot, config: dict[str, Any]) -> bool:
        self.config = config
        self.latest_snapshot = snapshot
        if self._tray is not None:
            summary_bits = [STATE_LABELS[snapshot.global_state]]
            if snapshot.waiting_count:
                summary_bits.append(f"{snapshot.waiting_count} bekliyor")
            if snapshot.active_count:
                summary_bits.append(f"{snapshot.active_count} aktif")
            if snapshot.done_unseen_count:
                summary_bits.append(f"{snapshot.done_unseen_count} tamamlandı")
            self._tray.update(
                snapshot.global_state,
                "Agent Notifier",
                " · ".join(summary_bits),
            )
        if self._window is not None and self._window.get_visible():
            self._window.render(snapshot, config)
        return False

    def quit_app(self) -> None:
        if self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = 0
        if self._tray is not None:
            self._tray.stop()
        if self.tray_mode:
            self.release()
        self.quit()


def run_gui(*, tray_mode: bool = True) -> int:
    app = AgentNotifierApp(tray_mode=tray_mode)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (app.quit_app(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit_app(), False)[1])
    return int(app.run([]))


if __name__ == "__main__":  # pragma: no cover
    mode = "dashboard" if "--dashboard" in sys.argv else "tray"
    raise SystemExit(run_gui(tray_mode=(mode == "tray")))
