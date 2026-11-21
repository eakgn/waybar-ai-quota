"""GTK4 + Libadwaita + Gio.DBus SNI uygulaması (Tray + Toggleable Dashboard)."""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
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
from .notify_watcher import DesktopNotificationWatcher  # noqa: E402
from .sni import StatusNotifierTray  # noqa: E402
from .styles import DASHBOARD_CSS  # noqa: E402
from .widgets import build_quota_card, build_session_tree_card, build_settings_panel  # noqa: E402

APP_ID = "org.eakgn.AgentNotifier"
APP_OBJECT_PATH = "/org/eakgn/AgentNotifier"

logger = logging.getLogger("agent_notifier.linux.app")


def _clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def _pid_file() -> Path:
    return cfg_mod.state_dir() / "dashboard.pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _close_standalone_pid() -> bool:
    """Tray çalışmıyorken tekil Dashboard penceresini PID dosyası üzerinden kapatır."""
    path = _pid_file()
    if not path.is_file():
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return False
    if pid == os.getpid() or not _pid_alive(pid):
        path.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        path.unlink(missing_ok=True)
        return False
    for _ in range(30):
        if not _pid_alive(pid):
            break
        time.sleep(0.03)
    path.unlink(missing_ok=True)
    return True


def try_remote_dashboard_action(action: str) -> bool:
    """Çalışan `agent-notifier tray` örneği varsa D-Bus üzerinden eylemi tetikler."""
    action_map = {
        "toggle": "toggle-dashboard",
        "open": "open-dashboard",
        "close": "close-dashboard",
    }
    dbus_action = action_map.get(action, "toggle-dashboard")
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owned = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (APP_ID,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        if not owned.unpack()[0]:
            return False
        bus.call_sync(
            APP_ID,
            APP_OBJECT_PATH,
            "org.freedesktop.Application",
            "ActivateAction",
            GLib.Variant("(sava{sv})", (dbus_action, [], {})),
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        return True
    except Exception as exc:
        logger.debug("Uzak D-Bus dashboard çağrısı yapılamadı: %s", exc)
        return False


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

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

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
        except Exception as exc:
            logger.warning("Gtk4LayerShell başlatılamadı (kayan pencereye düşülüyor): %s", exc)

    def _build_layout(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

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
        # Gtk4LayerShell altında pencereyi gizlemek yerine yok edip yeniden kurmak güvenlidir
        self._app.on_window_closed(self)
        return False

    def _dismiss(self) -> None:
        if self._app.tray_mode:
            self._app.hide_dashboard()
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

        sess_title = Gtk.Label(label="OTURUMLAR VE ALT AJANLAR", xalign=0.0)
        sess_title.add_css_class("an-section-title")
        self._content.append(sess_title)

        active_window_sec = max(60, int(cfg_mod.get(config, "thresholds.active_window_minutes", 90)) * 60)
        history_window_sec = max(3600, int(cfg_mod.get(config, "thresholds.recent_history_hours", 24)) * 3600)

        recent_active = [
            s
            for s in snapshot.sessions
            if s.effective_state != SessionState.IDLE
            or (s.updated_age_seconds is not None and s.updated_age_seconds <= active_window_sec)
        ]

        if recent_active:
            for root in recent_active[:8]:
                self._content.append(build_session_tree_card(root))
        else:
            empty = Gtk.Label(
                label="Son 90 dakikada çalışan oturum yok.",
                xalign=0.0,
                wrap=True,
            )
            empty.add_css_class("an-empty")
            self._content.append(empty)

            last_24h = [
                s
                for s in snapshot.sessions
                if s.updated_age_seconds is not None and s.updated_age_seconds <= history_window_sec
            ][:3]
            if last_24h:
                hist_title = Gtk.Label(label="SON OTURUMLAR (24 SAAT)", xalign=0.0)
                hist_title.add_css_class("an-section-title")
                self._content.append(hist_title)
                for root in last_24h:
                    self._content.append(build_session_tree_card(root))

        if snapshot.providers:
            q_title = Gtk.Label(label="SEÇİLİ KOTA SAĞLAYICILARI", xalign=0.0)
            q_title.add_css_class("an-section-title")
            self._content.append(q_title)
            for prov in snapshot.providers:
                self._content.append(build_quota_card(prov))


class AgentNotifierApp(Adw.Application):
    """Ana GTK4/Adw uygulaması: Tray (sürekli + D-Bus kontrol) veya tekil Dashboard."""

    def __init__(self, *, tray_mode: bool = True) -> None:
        flags = Gio.ApplicationFlags.NON_UNIQUE if not tray_mode else Gio.ApplicationFlags.FLAGS_NONE
        super().__init__(application_id=APP_ID, flags=flags)
        self.tray_mode = tray_mode
        self.config: dict[str, Any] = cfg_mod.load_config()
        self.latest_snapshot: Snapshot | None = None
        self._window: DashboardWindow | None = None
        self._tray: StatusNotifierTray | None = None
        self._notify_watcher: DesktopNotificationWatcher | None = None
        self._poll_source_id = 0
        self._last_events_mtime = 0.0
        self._last_refresh_ts = 0.0
        self._refreshing = False
        self._pending_refresh = False
        self._pending_force_remote = False

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

        # Uzaktan D-Bus toggle / open / close eylemleri (Waybar custom pill vb.)
        for name, cb in (
            ("toggle-dashboard", self.toggle_dashboard),
            ("open-dashboard", self.show_dashboard),
            ("close-dashboard", self.hide_dashboard),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, fn=cb: fn())
            self.add_action(act)

        if self.tray_mode:
            self.hold()
            self._tray = StatusNotifierTray(
                on_activate=self.toggle_dashboard,
                on_refresh=lambda: self.trigger_refresh(force_remote=True),
                on_ack=self.ack_done,
                on_quit=self.quit_app,
            )
            self._tray.start()
            self._notify_watcher = DesktopNotificationWatcher(
                on_event=lambda: self.trigger_refresh(force_remote=False)
            )
            self._notify_watcher.start()

        self.trigger_refresh(force_remote=False)
        # 1 sn ucuz stat kontrolü; gerçek tarama active/idle aralığına göre yapılır
        self._poll_source_id = GLib.timeout_add_seconds(1, self._on_tick)

    def do_activate(self) -> None:
        if not self.tray_mode:
            self.show_dashboard()

    def on_window_closed(self, win: DashboardWindow) -> None:
        if self._window is win:
            self._window = None

    def show_dashboard(self) -> None:
        if self._window is None:
            anchor = str(cfg_mod.get(self.config, "ui.anchor", "top-right"))
            self._window = DashboardWindow(self, anchor=anchor)
        if bool(cfg_mod.get(self.config, "ui.ack_done_on_open", True)):
            if self.latest_snapshot and self.latest_snapshot.done_unseen_count > 0:
                self.ack_done()
        self._window.render(self.latest_snapshot, self.config)
        self._window.present()

    def hide_dashboard(self) -> None:
        if self._window is not None:
            win = self._window
            self._window = None
            win.destroy()

    def toggle_dashboard(self) -> None:
        if self._window is not None and self._window.get_visible():
            self.hide_dashboard()
        else:
            self.show_dashboard()

    def ack_done(self) -> None:
        events_mod.ack_done_sessions()
        self.trigger_refresh(force_remote=False)

    def on_setting_toggled(self, section: str, key: str, enabled: bool) -> None:
        self.config = cfg_mod.set_toggle(self.config, section, key, enabled)
        cfg_mod.save_config(self.config)
        self.trigger_refresh(force_remote=False)

    def _refresh_interval_seconds(self) -> float:
        snap = self.latest_snapshot
        is_busy = bool(
            snap
            and (
                snap.active_count > 0
                or snap.waiting_count > 0
                or snap.global_state in (SessionState.RUNNING, SessionState.WAITING_INPUT)
            )
        )
        if is_busy:
            return max(1.0, float(cfg_mod.get(self.config, "refresh.local_active_seconds", 3)))
        return max(2.0, float(cfg_mod.get(self.config, "refresh.local_idle_seconds", 10)))

    def _on_tick(self) -> bool:
        ev_file = cfg_mod.events_path()
        try:
            mtime = ev_file.stat().st_mtime if ev_file.is_file() else 0.0
        except OSError:
            mtime = 0.0
        events_changed = mtime != self._last_events_mtime
        if events_changed:
            self._last_events_mtime = mtime

        elapsed = time.monotonic() - self._last_refresh_ts
        if events_changed or elapsed >= self._refresh_interval_seconds():
            self.trigger_refresh(force_remote=False)
        return True

    def trigger_refresh(self, *, force_remote: bool = False) -> None:
        if self._refreshing:
            self._pending_refresh = True
            self._pending_force_remote = self._pending_force_remote or force_remote
            return
        self._refreshing = True
        self._last_refresh_ts = time.monotonic()

        def _worker() -> None:
            snap: Snapshot | None = None
            cfg = self.config
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
            except Exception as exc:
                logger.warning("Arka plan durum yenileme hatası: %s", exc)
            finally:
                GLib.idle_add(self._finish_refresh, snap, cfg)

        threading.Thread(target=_worker, daemon=True).start()

    def _finish_refresh(self, snapshot: Snapshot | None, config: dict[str, Any]) -> bool:
        self._refreshing = False
        if snapshot is not None:
            self._apply_snapshot(snapshot, config)
        if self._pending_refresh:
            force = self._pending_force_remote
            self._pending_refresh = False
            self._pending_force_remote = False
            self.trigger_refresh(force_remote=force)
        return False

    def _apply_snapshot(self, snapshot: Snapshot, config: dict[str, Any]) -> None:
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

    def quit_app(self) -> None:
        if self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = 0
        self.hide_dashboard()
        if self._notify_watcher is not None:
            self._notify_watcher.stop()
        if self._tray is not None:
            self._tray.stop()
        if self.tray_mode:
            self.release()
        self.quit()


def run_gui(*, tray_mode: bool = True, action: str = "open") -> int:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING, format="agent-notifier: %(levelname)s: %(message)s")

    if not tray_mode:
        # Çalışan `agent-notifier tray` örneği varsa D-Bus üzerinden doğrudan onu tetikle
        if try_remote_dashboard_action(action):
            return 0
        # Tray çalışmıyorsa tekil pencere PID dosyası üzerinden toggle/close yap
        if action == "close":
            _close_standalone_pid()
            return 0
        if action == "toggle" and _close_standalone_pid():
            return 0
        pid_path = _pid_file()
        try:
            cfg_mod.atomic_write_text(pid_path, f"{os.getpid()}\n", mode=0o600)
        except OSError:
            pass
        try:
            app = AgentNotifierApp(tray_mode=False)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (app.quit_app(), False)[1])
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit_app(), False)[1])
            return int(app.run([]))
        finally:
            try:
                if pid_path.is_file() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_path.unlink(missing_ok=True)
            except OSError:
                pass

    app = AgentNotifierApp(tray_mode=True)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (app.quit_app(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit_app(), False)[1])
    return int(app.run([]))


if __name__ == "__main__":  # pragma: no cover
    is_dashboard = "--dashboard" in sys.argv
    act = "open"
    for arg in sys.argv[1:]:
        if arg.startswith("--action="):
            act = arg.split("=", 1)[1]
    raise SystemExit(run_gui(tray_mode=not is_dashboard, action=act))
