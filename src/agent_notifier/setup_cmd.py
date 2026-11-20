"""Kurulum sihirbazı (`setup`), tanılama (`doctor`) ve kaldırma (`uninstall`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config as cfg_mod
from . import hooks as hooks_mod
from .adapters import detect_environment, harness_infos, provider_infos

ANTIGRAVITY_QUOTA_NOTE = (
    "Not: Antigravity canlı kota yüzdesi Google ToS (Ek Koşullar §6 — üçüncü taraf erişim / "
    "sarma yasağı) gereği dışarıdan çekilmez; yalnız oturum durumu pasif plugin ile izlenir "
    "(canlı kota için `agy /usage` veya Antigravity App)."
)


def autostart_desktop_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "autostart" / "agent-notifier.desktop"


def systemd_service_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "systemd" / "user" / "agent-notifier.service"


def detect_distro() -> str:
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return "linux"
    text = os_release.read_text(encoding="utf-8", errors="ignore").lower()
    if "fedora" in text:
        return "fedora"
    if "arch" in text or "cachyos" in text or "endeavour" in text:
        return "arch"
    if "ubuntu" in text or "debian" in text or "pop" in text:
        return "debian"
    return "linux"


def distro_gtk_hint(distro: str) -> str:
    hints = {
        "fedora": "sudo dnf install python3-gobject gtk4 libadwaita gtk4-layer-shell",
        "arch": "sudo pacman -S python-gobject gtk4 libadwaita gtk4-layer-shell",
        "debian": "sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4-layer-shell-0",
    }
    return hints.get(distro, hints["fedora"])


def _probe_python_gtk(python_bin: str) -> dict[str, bool]:
    code = (
        "import ctypes\n"
        "for lib in ('libgtk4-layer-shell.so.0', 'libgtk4-layer-shell.so'):\n"
        "  try: ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL); break\n"
        "  except OSError: pass\n"
        "import gi\n"
        "gi.require_version('Gtk', '4.0')\n"
        "gi.require_version('Adw', '1')\n"
        "from gi.repository import Gtk, Adw, Gio\n"
        "ok_ls = False\n"
        "try:\n"
        "  gi.require_version('Gtk4LayerShell', '1.0')\n"
        "  from gi.repository import Gtk4LayerShell\n"
        "  ok_ls = True\n"
        "except Exception: pass\n"
        "print('LS=' + ('1' if ok_ls else '0'))\n"
    )
    try:
        proc = subprocess.run(
            [python_bin, "-c", code],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"gtk4": False, "layer_shell": False}
    if proc.returncode != 0:
        return {"gtk4": False, "layer_shell": False}
    return {"gtk4": True, "layer_shell": "LS=1" in proc.stdout}


def run_doctor(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sistem bağımlılıklarını, harness/provider algılamasını ve yapılandırmayı denetler."""
    cfg = config if config is not None else cfg_mod.load_config()
    distro = detect_distro()
    gtk_py = str(cfg_mod.get(cfg, "ui.gtk_python", "/usr/bin/python3") or "/usr/bin/python3")
    candidates = [p for p in (gtk_py, sys.executable, "/usr/bin/python3") if p and Path(p).exists()]
    gtk_probe = {"gtk4": False, "layer_shell": False}
    gtk_python_found: str | None = None
    for cand in dict.fromkeys(candidates):
        res = _probe_python_gtk(cand)
        if res["gtk4"]:
            gtk_probe = res
            gtk_python_found = cand
            break

    detected = detect_environment()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")
    session_type = os.environ.get("XDG_SESSION_TYPE", "")

    checks = {
        "distro": distro,
        "desktop": desktop or "bilinmiyor",
        "session_type": session_type or ("wayland" if os.environ.get("WAYLAND_DISPLAY") else "headless"),
        "gtk4_available": gtk_probe["gtk4"],
        "gtk_python": gtk_python_found,
        "layer_shell_available": gtk_probe["layer_shell"],
        "notify_send": shutil.which("notify-send") is not None,
        "dbus_session": bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
        "config_exists": cfg_mod.config_path().is_file(),
        "config_path": str(cfg_mod.config_path()),
        "autostart_enabled": autostart_desktop_path().is_file(),
        "detected": detected,
        "enabled_harnesses": cfg_mod.enabled_harnesses(cfg),
        "enabled_providers": cfg_mod.enabled_providers(cfg),
        "hints": [],
    }
    if not gtk_probe["gtk4"]:
        checks["hints"].append(f"GTK4/PyGObject eksik. Kurulum: {distro_gtk_hint(distro)}")
    if not checks["notify_send"]:
        checks["hints"].append("Masaüstü bildirimleri için `libnotify` (`notify-send`) kurun.")
    if "gnome" in desktop.lower():
        checks["hints"].append(
            "GNOME üzerinde System Tray ikonu için AppIndicator (appindicatorsupport@rgcjonas.gmail.com) eklentisinin açık olduğundan emin olun."
        )
    return checks


def install_autostart(binary: str | None = None) -> Path:
    cmd = binary or shutil.which("agent-notifier") or "agent-notifier"
    desktop = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Agent Notifier\n"
        "Comment=AI coding agent session & quota tray monitor\n"
        f"Exec={cmd} tray\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Categories=Development;Utility;\n"
    )
    path = autostart_desktop_path()
    cfg_mod.atomic_write_text(path, desktop, mode=0o644)
    return path


def install_systemd_user(binary: str | None = None) -> Path:
    cmd = binary or shutil.which("agent-notifier") or "agent-notifier"
    unit = (
        "[Unit]\n"
        "Description=Agent Notifier (AI coding agent tray & session monitor)\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n\n"
        "[Service]\n"
        f"ExecStart={cmd} tray\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    )
    path = systemd_service_path()
    cfg_mod.atomic_write_text(path, unit, mode=0o644)
    return path


def _ask_yes_no(prompt: str, default: bool) -> bool:
    hint = "E/h" if default else "e/H"
    try:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("e", "evet", "y", "yes", "1")


def run_setup(*, non_interactive: bool = False) -> dict[str, Any]:
    """İlk kurulum sihirbazı: algıla, config yaz, isteğe bağlı hook ve autostart kur."""
    cfg = cfg_mod.load_config()
    detected = detect_environment()

    if non_interactive:
        for h_key, found in detected["harnesses"].items():
            cfg = cfg_mod.set_toggle(cfg, "harnesses", h_key, found)
        for p_key, found in detected["providers"].items():
            cfg = cfg_mod.set_toggle(cfg, "providers", p_key, found)
        saved = cfg_mod.save_config(cfg)
        return {
            "ok": True,
            "config_path": str(saved),
            "detected": detected,
            "enabled_harnesses": cfg_mod.enabled_harnesses(cfg),
            "enabled_providers": cfg_mod.enabled_providers(cfg),
        }

    print("agent-notifier kurulum sihirbazı\n")
    print("1) İzlenecek AI Harness'ları (sadece seçtiklerin izlenir):")
    for info in harness_infos():
        found = detected["harnesses"].get(info.key, False)
        tag = "sistemde bulundu" if found else "bulunamadı"
        enabled = _ask_yes_no(f"  - {info.label} ({tag})", default=found)
        cfg = cfg_mod.set_toggle(cfg, "harnesses", info.key, enabled)

    print("\n2) Gösterilecek Kota Sağlayıcıları (opt-in):")
    for info in provider_infos():
        found = detected["providers"].get(info.key, False)
        tag = "algılandı" if found else "algılanmadı"
        enabled = _ask_yes_no(f"  - {info.label} ({tag})", default=found)
        cfg = cfg_mod.set_toggle(cfg, "providers", info.key, enabled)
    print(f"  ℹ {ANTIGRAVITY_QUOTA_NOTE}")

    saved = cfg_mod.save_config(cfg)
    print(f"\nYapılandırma kaydedildi: {saved}")

    installed_hooks: list[str] = []
    for h_key in cfg_mod.enabled_harnesses(cfg):
        if h_key in hooks_mod.SUPPORTED_HOOK_HARNESSES:
            extra = " (~/.gemini/config/plugins/agent-notifier/)" if h_key == "antigravity" else " (.bak yedekli)"
            if _ask_yes_no(
                f"\n3) {h_key} için anlık 'soru bekliyor / bitti' eklentisi kurulsun mu?{extra}",
                default=True,
            ):
                res = hooks_mod.install_hook(h_key)
                installed_hooks.append(h_key)
                print(f"   Kuruldu: {res['path']}")

    autostart_path: str | None = None
    if _ask_yes_no("\n4) Oturum açıldığında System Tray'de otomatik başlasın mı? (XDG autostart)", default=True):
        autostart_path = str(install_autostart())
        print(f"   Oluşturuldu: {autostart_path}")

    print("\nKurulum tamamlandı. Başlatmak için: agent-notifier tray")
    return {
        "ok": True,
        "config_path": str(saved),
        "installed_hooks": installed_hooks,
        "autostart": autostart_path,
    }


def run_uninstall(*, keep_config: bool = False) -> dict[str, Any]:
    """Eklenen autostart, systemd servisi ve hook kayıtlarını temizler."""
    removed: list[str] = []
    for path in (autostart_desktop_path(), systemd_service_path()):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    for h_key in hooks_mod.SUPPORTED_HOOK_HARNESSES:
        try:
            res = hooks_mod.uninstall_hook(h_key)
            if res.get("changed"):
                removed.append(f"hook:{h_key}")
        except Exception:
            pass
    if not keep_config and cfg_mod.config_path().is_file():
        cfg_mod.config_path().unlink()
        removed.append(str(cfg_mod.config_path()))
    return {"ok": True, "removed": removed}
