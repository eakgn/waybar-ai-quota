"""agent-notifier komut satırı arayüzü (CLI).

Alt komutlar:
  tray       System Tray (SNI) + tıklanınca açılan Dashboard'u başlatır
  dashboard  Dashboard penceresini tek seferlik açar (`ui` takma adı desteklenir)
  status     Tam normalize Snapshot'ı JSON olarak yazdırır
  bar        Waybar / Polybar / Swaybar / Tmux / Plain / JSON bar çıktısı üretir
  waybar     `bar --format waybar` kısa yolu (v0.1 uyumluluğu)
  event      Hook'lardan gelen anlık oturum olayını (<10 ms) kaydeder
  ack        Tamamlanmış (görülmemiş) oturumları okundu olarak işaretler
  hooks      Harness hook'larını yazdırır / yedekli kurar / kaldırır
  setup      İlk kurulum sihirbazı (otomatik algılama, config, autostart)
  doctor     Sistem bağımlılıklarını ve harness algılamasını denetler
  uninstall  Autostart, systemd servisi ve eklenen hook'ları temizler
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from . import config as cfg_mod
from . import engine, events as events_mod, formatters, hooks as hooks_mod, notifier, setup_cmd


def _read_stdin_json_nonblocking() -> dict[str, Any]:
    """Hook çağrılarında stdin'den gelen JSON gövdesini güvenli okur."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            return {}
        raw = sys.stdin.read().strip()
        if not raw:
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_project_from_payload(payload: dict[str, Any]) -> str:
    ws = payload.get("workspacePaths") or payload.get("workspace_paths")
    if isinstance(ws, list) and ws:
        first = str(ws[0] or "").strip()
        if first:
            return Path(first).name
    cwd = str(payload.get("cwd") or payload.get("directory") or "").strip()
    if cwd:
        return Path(cwd).name
    return str(payload.get("project") or "").strip()


def _gi_available() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        return True
    except Exception:
        return False


def _launch_gui(*, tray_mode: bool, config: dict[str, Any]) -> int:
    if _gi_available():
        from .linux.app import run_gui

        return run_gui(tray_mode=tray_mode)

    gtk_py = str(cfg_mod.get(config, "ui.gtk_python", "/usr/bin/python3") or "/usr/bin/python3")
    python = gtk_py if Path(gtk_py).exists() else (shutil.which("python3") or sys.executable)
    env = dict(os.environ)
    src_root = str(Path(__file__).resolve().parent.parent)
    prev = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_root + (os.pathsep + prev if prev else "")
    args = [python, "-m", "agent_notifier.linux.app"]
    if not tray_mode:
        args.append("--dashboard")
    try:
        return subprocess.call(args, env=env)
    except OSError as exc:
        print(f"Hata: GTK4 arayüzü başlatılamadı: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Alt komut işleyicileri
# ---------------------------------------------------------------------------


def cmd_tray(_args: argparse.Namespace) -> int:
    return _launch_gui(tray_mode=True, config=cfg_mod.load_config())


def cmd_dashboard(_args: argparse.Namespace) -> int:
    return _launch_gui(tray_mode=False, config=cfg_mod.load_config())


def cmd_status(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load_config()
    state = cfg_mod.load_state()
    snap = engine.collect_snapshot(
        config=cfg,
        state=state,
        force_refresh=bool(args.refresh),
        offline=bool(args.offline),
    )
    if not args.no_notify:
        snap.notifications_sent = notifier.maybe_notify(snap, config=cfg, state=state)
    print(formatters.format_json(snap))
    return 0


def cmd_bar(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load_config()
    snap = engine.collect_snapshot(
        config=cfg,
        force_refresh=bool(args.refresh),
        offline=not bool(args.refresh),
    )
    try:
        print(formatters.render(snap, args.format, cfg))
    except BrokenPipeError:
        return 0
    return 0


def cmd_waybar(args: argparse.Namespace) -> int:
    args.format = "waybar"
    args.refresh = getattr(args, "refresh", False)
    return cmd_bar(args)


def cmd_event(args: argparse.Namespace) -> int:
    payload = _read_stdin_json_nonblocking() if args.from_stdin else {}
    session_id = (
        args.session
        or str(payload.get("conversationId") or payload.get("session_id") or payload.get("sessionId") or "").strip()
        or f"{args.harness}-default"
    )
    parent_id = (
        args.parent
        or str(payload.get("parentConversationId") or payload.get("parent_id") or payload.get("parentId") or "").strip()
        or None
    )
    project = args.project or _extract_project_from_payload(payload)
    title = args.title or str(payload.get("title") or "").strip()
    agent = args.agent or str(payload.get("agent") or payload.get("role") or "").strip()
    model = args.model or str(payload.get("model") or "").strip()
    message = args.message or str(payload.get("message") or "").strip()

    try:
        rec = events_mod.record_event(
            harness=args.harness,
            session_id=session_id,
            state=args.state,
            parent_id=parent_id,
            is_subagent=bool(args.subagent or parent_id),
            project=project,
            title=title,
            agent=agent,
            model=model,
            message=message,
        )
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2

    if args.hook_output:
        # Antigravity / Claude hook sözleşmesi: stdout geçerli JSON nesnesi olmalı
        print("{}")
    elif not args.quiet:
        print(json.dumps({"ok": True, "event": rec.to_dict()}, ensure_ascii=False))
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    keys = args.sessions if args.sessions else None
    events_mod.ack_done_sessions(keys)
    print(json.dumps({"ok": True, "acked": keys or "all"}, ensure_ascii=False))
    return 0


def cmd_hooks(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser() if getattr(args, "target", None) else None
    action = args.hooks_action
    if action == "print":
        print(hooks_mod.render_snippet(args.harness))
        return 0
    if action == "install":
        try:
            res = hooks_mod.install_hook(args.harness, target=target, dry_run=bool(args.dry_run))
        except ValueError as exc:
            print(f"Hata: {exc}", file=sys.stderr)
            return 2
        if args.dry_run:
            print(f"[DRY-RUN] Yazılacak dosya: {res['path']}\n")
            print(res["preview"])
        else:
            print(json.dumps({k: v for k, v in res.items() if k != "preview"}, ensure_ascii=False, indent=2))
        return 0
    if action == "uninstall":
        res = hooks_mod.uninstall_hook(args.harness, target=target, dry_run=bool(args.dry_run))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1
    return 2


def cmd_setup(args: argparse.Namespace) -> int:
    if args.autostart:
        p = setup_cmd.install_autostart()
        print(f"XDG autostart oluşturuldu: {p}")
        return 0
    if args.systemd:
        p = setup_cmd.install_systemd_user()
        print(f"systemd --user birimi oluşturuldu: {p}")
        print("Etkinleştirmek için: systemctl --user daemon-reload && systemctl --user enable --now agent-notifier")
        return 0
    res = setup_cmd.run_setup(non_interactive=bool(args.non_interactive))
    if args.non_interactive:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = setup_cmd.run_doctor()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    def _mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    print(f"agent-notifier v{__version__} · Sistem Tanılama ({report['distro']} / {report['desktop']} / {report['session_type']})")
    print(f"  [{_mark(report['gtk4_available'])}] GTK4 + Libadwaita ({report['gtk_python'] or 'yok'})")
    print(f"  [{_mark(report['layer_shell_available'])}] Gtk4LayerShell (wlroots köşe popover; yoksa kayan pencere)")
    print(f"  [{_mark(report['dbus_session'])}] D-Bus Oturum Veriyolu (System Tray / SNI)")
    print(f"  [{_mark(report['notify_send'])}] notify-send (masaüstü bildirimleri)")
    print(f"  [{_mark(report['config_exists'])}] Yapılandırma: {report['config_path']}")
    print(f"  [{_mark(report['autostart_enabled'])}] XDG Autostart")
    print("\nAlgılanan Harness'lar:", ", ".join(k for k, v in report["detected"]["harnesses"].items() if v) or "yok")
    print("Etkin Harness'lar:    ", ", ".join(report["enabled_harnesses"]) or "yok")
    print("Etkin Sağlayıcılar:   ", ", ".join(report["enabled_providers"]) or "yok")
    if report["hints"]:
        print("\nÖneriler:")
        for hint in report["hints"]:
            print(f"  - {hint}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    res = setup_cmd.run_uninstall(keep_config=bool(args.keep_config))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-notifier",
        description="Linux System Tray & Dashboard: AI ajan oturumları, alt ajanlar ve opt-in kotalar.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_tray = sub.add_parser("tray", help="System Tray (SNI) ikonu ve tıklanınca açılan Dashboard'u başlat")
    p_tray.set_defaults(func=cmd_tray)

    for alias in ("dashboard", "ui"):
        p_dash = sub.add_parser(alias, help="Dashboard penceresini doğrudan aç")
        p_dash.set_defaults(func=cmd_dashboard)

    p_status = sub.add_parser("status", help="Tam normalize durumu JSON olarak yazdır")
    p_status.add_argument("-r", "--refresh", action="store_true", help="Uzak kota API önbelleğini yenile")
    p_status.add_argument("--offline", action="store_true", help="Hiçbir ağ çağrısı yapma")
    p_status.add_argument("--no-notify", action="store_true", help="Masaüstü bildirimi gönderme")
    p_status.set_defaults(func=cmd_status)

    p_bar = sub.add_parser("bar", help="WM barları için tek satır çıktı üret")
    p_bar.add_argument(
        "--format",
        "-f",
        default="waybar",
        choices=("waybar", "polybar", "swaybar", "tmux", "plain", "json"),
        help="Çıktı biçimi (varsayılan: waybar)",
    )
    p_bar.add_argument("-r", "--refresh", action="store_true", help="Uzak API'yi yenile")
    p_bar.set_defaults(func=cmd_bar)

    p_waybar = sub.add_parser("waybar", help="Waybar custom modülü için tek satır JSON (bar --format waybar)")
    p_waybar.add_argument("-r", "--refresh", action="store_true")
    p_waybar.set_defaults(func=cmd_waybar)

    p_ev = sub.add_parser("event", help="Hook veya betikten anlık oturum durum olayı kaydet")
    p_ev.add_argument("--harness", required=True, help="Harness adı (ör. antigravity, opencode)")
    p_ev.add_argument("--state", required=True, help="Durum (running, waiting_input, done, error, rate_limited, idle)")
    p_ev.add_argument("--session", default="", help="Oturum kimliği")
    p_ev.add_argument("--parent", default="", help="Üst oturum kimliği (subagent ise)")
    p_ev.add_argument("--subagent", action="store_true", help="Alt ajan olarak işaretle")
    p_ev.add_argument("--project", default="", help="Proje adı")
    p_ev.add_argument("--title", default="", help="Oturum başlığı")
    p_ev.add_argument("--agent", default="", help="Ajan / rol adı")
    p_ev.add_argument("--model", default="", help="Model adı")
    p_ev.add_argument("--message", default="", help="Kullanıcıya gösterilecek kısa mesaj")
    p_ev.add_argument("--from-stdin", action="store_true", help="Hook JSON gövdesini stdin'den oku")
    p_ev.add_argument("--hook-output", action="store_true", help="Hook sözleşmesi için stdout'a {} yaz")
    p_ev.add_argument("--quiet", "-q", action="store_true", help="Çıktı yazdırma")
    p_ev.set_defaults(func=cmd_event)

    p_ack = sub.add_parser("ack", help="Tamamlanan (görülmemiş) oturumları okundu işaretle")
    p_ack.add_argument("sessions", nargs="*", help="Belirli harness:session anahtarları (boşsa tümü)")
    p_ack.set_defaults(func=cmd_ack)

    p_hooks = sub.add_parser("hooks", help="Harness hook'larını yazdır / güvenli kur / kaldır")
    h_sub = p_hooks.add_subparsers(dest="hooks_action", required=True)
    for act in ("print", "install", "uninstall"):
        hp = h_sub.add_parser(act)
        hp.add_argument("harness", choices=hooks_mod.SUPPORTED_HOOK_HARNESSES)
        if act in ("install", "uninstall"):
            hp.add_argument("--dry-run", action="store_true", help="Dosyaya yazmadan önizle")
            hp.add_argument("--target", default="", help="Özel hedef dosya yolu")
        hp.set_defaults(func=cmd_hooks)

    p_setup = sub.add_parser("setup", help="İlk kurulum sihirbazı (harness/kota seçimi, hook, autostart)")
    p_setup.add_argument("--non-interactive", action="store_true", help="Algılananları sormadan config.toml'a yaz")
    p_setup.add_argument("--autostart", action="store_true", help="Yalnızca XDG autostart .desktop dosyasını kur")
    p_setup.add_argument("--systemd", action="store_true", help="Yalnızca systemd --user servisini oluştur")
    p_setup.set_defaults(func=cmd_setup)

    p_doc = sub.add_parser("doctor", help="Bağımlılık ve ortam tanılaması")
    p_doc.add_argument("--json", action="store_true", help="JSON çıktısı ver")
    p_doc.set_defaults(func=cmd_doctor)

    p_un = sub.add_parser("uninstall", help="Autostart, systemd servisi ve eklenen hook'ları kaldır")
    p_un.add_argument("--keep-config", action="store_true", help="config.toml dosyasını koru")
    p_un.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Argümansız çağrıda varsayılan: System Tray başlat
    if not raw:
        raw = ["tray"]
    parser = build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
