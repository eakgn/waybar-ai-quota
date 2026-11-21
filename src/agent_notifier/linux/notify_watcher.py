"""Pasif D-Bus masaüstü bildirim dinleyicisi (`org.freedesktop.Notifications`).

Antigravity (`agy` / Antigravity App) gibi izin isteği için ayrı hook olayı
sunmayan istemciler, kullanıcıdan komut/izin onayı isterken Linux masaüstüne
`org.freedesktop.Notifications.Notify` çağrısı gönderir.

Bu modül, ana SNI bağlantısından tamamen ayrı bir salt-izleme (`BecomeMonitor`)
D-Bus bağlantısı açar; yalnızca `Notify` çağrılarını dinler (%0 CPU, sıfır
gecikme, ToS-güvenli). Kendi bildirimlerini (`agent-notifier`) yok sayar.
"""

from __future__ import annotations

import logging
from typing import Callable

from .. import events as events_mod
from ..models import SessionState

logger = logging.getLogger(__name__)

_HARNESS_APP_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("antigravity", ("antigravity", "agy")),
    ("opencode", ("opencode",)),
)

_DONE_WORDS = ("finished", "completed", "done", "tamamland", "bitti")
_ERROR_WORDS = ("error", "failed", "failure", "hata", "başarısız")
_WAITING_WORDS = (
    "permission",
    "approval",
    "approve",
    "allow",
    "confirm",
    "confirmation",
    "question",
    "waiting",
    "input",
    "review",
    "action required",
    "needs your",
    "izin",
    "onay",
    "soru",
    "bekliyor",
)


def classify_desktop_notification(
    app_name: str,
    summary: str,
    body: str,
    *,
    own_app_name: str = "agent-notifier",
) -> tuple[str, SessionState, str] | None:
    """Bir masaüstü bildirimini (harness, durum, mesaj) üçlüsüne eşler; ilgisizse None."""
    app_low = (app_name or "").strip().lower()
    if not app_low and not summary:
        return None
    if app_low == own_app_name.lower() or "agent-notifier" in app_low:
        return None

    combined_id = f"{app_low} {(summary or '').lower()}"
    matched_harness: str | None = None
    for harness_key, needles in _HARNESS_APP_HINTS:
        if any(n in combined_id for n in needles):
            matched_harness = harness_key
            break
    if not matched_harness:
        return None

    text_low = f"{summary or ''} {body or ''}".lower()
    if any(w in text_low for w in _WAITING_WORDS):
        state = SessionState.WAITING_INPUT
    elif any(w in text_low for w in _ERROR_WORDS):
        state = SessionState.ERROR
    elif any(w in text_low for w in _DONE_WORDS):
        state = SessionState.DONE_UNSEEN
    else:
        # Antigravity varsayılan olarak dikkat/izin bildirimleri gönderdiği için
        # açıkça done/error olmayan eşleşen bildirimleri waiting_input say.
        state = SessionState.WAITING_INPUT

    clean_summary = (summary or "").strip()
    clean_body = (body or "").strip()
    msg = f"{clean_summary}: {clean_body}" if (clean_summary and clean_body) else (clean_summary or clean_body)
    return matched_harness, state, msg[:160]


def _resolve_latest_session_id(harness: str) -> str:
    """O harness için en güncel aktif oturum kimliğini bulur (yoksa varsayılan)."""
    existing = events_mod.load_events(harness=harness, max_age_seconds=3600)
    if existing:
        return existing[0].session_id
    return f"{harness}-default"


class DesktopNotificationWatcher:
    """Ayrı bir `BecomeMonitor` bağlantısıyla `org.freedesktop.Notifications.Notify` izler."""

    def __init__(self, *, on_event: Callable[[], None] | None = None) -> None:
        from gi.repository import Gio, GLib

        self._Gio = Gio
        self._GLib = GLib
        self._on_event = on_event
        self._conn: object | None = None
        self._filter_id = 0

    def start(self) -> bool:
        Gio, GLib = self._Gio, self._GLib
        try:
            addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            conn = Gio.DBusConnection.new_for_address_sync(
                addr,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
            match = "type='method_call',interface='org.freedesktop.Notifications',member='Notify'"
            conn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus.Monitoring",
                "BecomeMonitor",
                GLib.Variant("(asu)", ([match], 0)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            # Filtreyi BecomeMonitor yanıtı alındıktan SONRA ekle; gelen tüm
            # izleme mesajları None döndürülerek tüketilir (yanıt gönderilmez).
            self._filter_id = conn.add_filter(self._on_dbus_message)
            self._conn = conn
            return True
        except Exception as exc:
            logger.debug("D-Bus bildirim dinleyici başlatılamadı: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        if self._conn is not None:
            try:
                if self._filter_id:
                    self._conn.remove_filter(self._filter_id)
                self._conn.close_sync(None)
            except Exception:
                pass
            self._conn = None
            self._filter_id = 0

    def _on_dbus_message(self, _conn, message, incoming: bool):
        if not incoming:
            return message
        try:
            if (
                message.get_member() == "Notify"
                and message.get_interface() == "org.freedesktop.Notifications"
            ):
                body_var = message.get_body()
                if body_var is not None:
                    unpacked = body_var.unpack()
                    if len(unpacked) >= 5:
                        app_name = str(unpacked[0] or "")
                        summary = str(unpacked[3] or "")
                        body = str(unpacked[4] or "")
                        classified = classify_desktop_notification(app_name, summary, body)
                        if classified is not None:
                            harness, state, msg = classified
                            sid = _resolve_latest_session_id(harness)
                            events_mod.record_event(
                                harness=harness,
                                session_id=sid,
                                state=state,
                                message=msg,
                            )
                            if self._on_event is not None:
                                self._GLib.idle_add(self._fire_callback)
        except Exception as exc:
            logger.debug("Bildirim mesajı işlenemedi: %s", exc)
        # BecomeMonitor bağlantısı yanıt/hata gönderemez; gelen mesajı Gio'ya
        # dispatch ettirmeden tüketmek için None döndür.
        return None

    def _fire_callback(self) -> bool:
        if self._on_event is not None:
            self._on_event()
        return False
