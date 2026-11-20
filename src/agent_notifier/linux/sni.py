"""Saf `Gio.DBus` tabanlı `org.kde.StatusNotifierItem` (SNI) + `dbusmenu`.

GTK3 / `AppIndicator3` bağımlılığı olmadan doğrudan GTK4 / GLib ana döngüsünde
çalışır. GNOME (AppIndicator), KDE Plasma, XFCE, COSMIC ve Waybar `tray`
modülünde desteklenir.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..models import STATE_LABELS, SessionState
from .icons import STATE_ICON_NAMES, argb32_pixmap_for_state, ensure_icon_files

SNI_INTERFACE = "org.kde.StatusNotifierItem"
SNI_OBJECT_PATH = "/StatusNotifierItem"
DBUSMENU_INTERFACE = "com.canonical.dbusmenu"
DBUSMENU_OBJECT_PATH = "/MenuBar"
WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

SNI_INTROSPECTION_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
  </interface>
</node>
"""

DBUSMENU_INTROSPECTION_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
  </interface>
</node>
"""


class StatusNotifierTray:
    """D-Bus oturum veriyoluna kayıt olan SNI sistem tepsisi ikonu."""

    def __init__(
        self,
        *,
        on_activate: Callable[[], None],
        on_refresh: Callable[[], None],
        on_ack: Callable[[], None],
        on_quit: Callable[[], None],
        icon_dir: Path | None = None,
    ) -> None:
        from gi.repository import Gio, GLib

        self._Gio = Gio
        self._GLib = GLib
        self._on_activate = on_activate
        self._on_refresh = on_refresh
        self._on_ack = on_ack
        self._on_quit = on_quit

        self._icon_dir = ensure_icon_files(icon_dir)
        self._state = SessionState.IDLE
        self._tooltip_title = "Agent Notifier"
        self._tooltip_body = STATE_LABELS[SessionState.IDLE]
        self._menu_revision = 1

        self._bus: object | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._sni_reg_id = 0
        self._menu_reg_id = 0
        self._name_owner_id = 0
        self._watcher_watch_id = 0

    def start(self) -> bool:
        """Oturum D-Bus bağlantısını kurar ve tepsiye kaydolur."""
        Gio = self._Gio
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return False

        sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_INTROSPECTION_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_INTROSPECTION_XML).interfaces[0]

        self._sni_reg_id = self._bus.register_object(
            SNI_OBJECT_PATH,
            sni_info,
            self._handle_sni_call,
            self._handle_sni_get_prop,
            None,
        )
        self._menu_reg_id = self._bus.register_object(
            DBUSMENU_OBJECT_PATH,
            menu_info,
            self._handle_menu_call,
            self._handle_menu_get_prop,
            None,
        )
        self._name_owner_id = Gio.bus_own_name_on_connection(
            self._bus,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            lambda _conn, _name: self._register_with_watcher(),
            None,
        )
        # Panel / Waybar yeniden başlatılırsa otomatik yeniden kayıt ol
        self._watcher_watch_id = Gio.bus_watch_name_on_connection(
            self._bus,
            WATCHER_BUS,
            Gio.BusNameWatcherFlags.NONE,
            lambda _conn, _name, _owner: self._register_with_watcher(),
            None,
        )
        return True

    def stop(self) -> None:
        Gio = self._Gio
        if self._watcher_watch_id:
            Gio.bus_unwatch_name(self._watcher_watch_id)
            self._watcher_watch_id = 0
        if self._name_owner_id:
            Gio.bus_unown_name(self._name_owner_id)
            self._name_owner_id = 0
        if self._bus is not None:
            if self._sni_reg_id:
                self._bus.unregister_object(self._sni_reg_id)
                self._sni_reg_id = 0
            if self._menu_reg_id:
                self._bus.unregister_object(self._menu_reg_id)
                self._menu_reg_id = 0

    def update(self, state: SessionState, title: str, body: str) -> None:
        """Tray ikonunu, durumunu ve ipucu metnini canlı günceller."""
        state_changed = state != self._state
        tip_changed = (title != self._tooltip_title) or (body != self._tooltip_body)
        self._state = state
        self._tooltip_title = title
        self._tooltip_body = body

        if self._bus is None:
            return
        GLib = self._GLib
        try:
            if state_changed:
                self._bus.emit_signal(None, SNI_OBJECT_PATH, SNI_INTERFACE, "NewIcon", None)
                sni_status = (
                    "NeedsAttention"
                    if state in (SessionState.WAITING_INPUT, SessionState.ERROR, SessionState.RATE_LIMITED)
                    else "Active"
                )
                self._bus.emit_signal(
                    None,
                    SNI_OBJECT_PATH,
                    SNI_INTERFACE,
                    "NewStatus",
                    GLib.Variant("(s)", (sni_status,)),
                )
            if tip_changed:
                self._bus.emit_signal(None, SNI_OBJECT_PATH, SNI_INTERFACE, "NewToolTip", None)
        except Exception:
            pass

    def _register_with_watcher(self) -> None:
        if self._bus is None:
            return
        Gio, GLib = self._Gio, self._GLib
        try:
            self._bus.call(
                WATCHER_BUS,
                WATCHER_PATH,
                WATCHER_BUS,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._bus_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
                None,
                None,
            )
        except Exception:
            pass

    def _handle_sni_call(
        self, _conn, _sender, _path, _iface, method_name: str, _params, invocation
    ) -> None:
        GLib = self._GLib
        if method_name == "Activate":
            GLib.idle_add(self._call_once, self._on_activate)
            invocation.return_value(None)
        elif method_name == "SecondaryActivate":
            GLib.idle_add(self._call_once, self._on_refresh)
            invocation.return_value(None)
        elif method_name in ("ContextMenu", "Scroll"):
            invocation.return_value(None)
        else:
            invocation.return_value(None)

    def _handle_sni_get_prop(self, _conn, _sender, _path, _iface, prop: str):
        GLib = self._GLib
        icon_name = STATE_ICON_NAMES[self._state]
        w, h, raw = argb32_pixmap_for_state(self._state, 24)
        pixmap_list = [(w, h, raw)]
        sni_status = (
            "NeedsAttention"
            if self._state in (SessionState.WAITING_INPUT, SessionState.ERROR, SessionState.RATE_LIMITED)
            else "Active"
        )
        props = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "agent-notifier"),
            "Title": GLib.Variant("s", "Agent Notifier"),
            "Status": GLib.Variant("s", sni_status),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", str(self._icon_dir)),
            "IconName": GLib.Variant("s", icon_name),
            "IconPixmap": GLib.Variant("a(iiay)", pixmap_list),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", icon_name),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", pixmap_list),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)", (icon_name, pixmap_list, self._tooltip_title, self._tooltip_body)
            ),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", DBUSMENU_OBJECT_PATH),
        }
        return props.get(prop)

    def _menu_items(self) -> list[tuple[int, dict[str, object]]]:
        GLib = self._GLib
        return [
            (1, {"label": GLib.Variant("s", "Dashboard Aç / Kapat"), "enabled": GLib.Variant("b", True)}),
            (2, {"label": GLib.Variant("s", "Şimdi Yenile"), "enabled": GLib.Variant("b", True)}),
            (3, {"label": GLib.Variant("s", "Tamamlananları Okundu İşaretle"), "enabled": GLib.Variant("b", True)}),
            (4, {"type": GLib.Variant("s", "separator")}),
            (5, {"label": GLib.Variant("s", "Çıkış"), "enabled": GLib.Variant("b", True)}),
        ]

    def _handle_menu_call(
        self, _conn, _sender, _path, _iface, method_name: str, params, invocation
    ) -> None:
        GLib = self._GLib
        if method_name == "GetLayout":
            children = [
                GLib.Variant("(ia{sv}av)", (item_id, props, []))
                for item_id, props in self._menu_items()
            ]
            root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._menu_revision, root)))
        elif method_name == "GetGroupProperties":
            ids = set(params.unpack()[0])
            out = [(item_id, props) for item_id, props in self._menu_items() if item_id in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method_name == "GetProperty":
            item_id, prop_name = params.unpack()
            found = GLib.Variant("s", "")
            for mid, props in self._menu_items():
                if mid == item_id and prop_name in props:
                    found = props[prop_name]
                    break
            invocation.return_value(GLib.Variant("(v)", (found,)))
        elif method_name == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method_name == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._dispatch_menu_click(int(item_id))
            invocation.return_value(None)
        elif method_name == "EventGroup":
            for item_id, event_id, _data, _ts in params.unpack()[0]:
                if event_id == "clicked":
                    self._dispatch_menu_click(int(item_id))
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        else:
            invocation.return_value(None)

    def _handle_menu_get_prop(self, _conn, _sender, _path, _iface, prop: str):
        GLib = self._GLib
        props = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", [str(self._icon_dir)]),
        }
        return props.get(prop)

    def _dispatch_menu_click(self, item_id: int) -> None:
        GLib = self._GLib
        if item_id == 1:
            GLib.idle_add(self._call_once, self._on_activate)
        elif item_id == 2:
            GLib.idle_add(self._call_once, self._on_refresh)
        elif item_id == 3:
            GLib.idle_add(self._call_once, self._on_ack)
        elif item_id == 5:
            GLib.idle_add(self._call_once, self._on_quit)

    @staticmethod
    def _call_once(fn: Callable[[], None]) -> bool:
        fn()
        return False
