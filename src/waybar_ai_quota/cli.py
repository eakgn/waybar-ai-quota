"""Komut satırı arayüzü.

Alt komutlar:
  waybar     Waybar `custom/ai-quota` için tek satır JSON
  status     Tam sistem durumu (JSON), `--refresh` ile canlı API
  calibrate  OpenCode Go kalibrasyonu (kalan % / offset / ek harcama)
  ui         GTK4 Layer-Shell panelini açar (gi yoksa sistem Python'una düşer)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, config as cfg_mod, notifier, recommender
from . import models as m
from .collectors import (
    collect_all,
    format_duration_tr,
    short_model_name,
)

BAR_ICON_ACTIVE = "⚡"
BAR_ICON_IDLE = "🤖"


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _effective_ag_pct(status: m.AntigravityStatus) -> float | None:
    if not status.groups:
        return None
    return min(0.0 if group.rate_limited else group.remaining_pct for group in status.groups)


def _effective_go_pct(status: m.GoStatus) -> float | None:
    return status.total.remaining_pct if status.available else None


def _effective_copilot_pct(status: m.CopilotStatus) -> float | None:
    values: list[float] = []
    for quota in status.quotas:
        if quota.unlimited:
            continue
        if quota.has_quota and isinstance(quota.percent_remaining, (int, float)):
            values.append(float(quota.percent_remaining))
    return min(values) if values else None


def _pct(value: float | None) -> str:
    return f"{int(round(value))}" if isinstance(value, (int, float)) else "—"


def _build_status(
    force_refresh: bool = False,
    offline: bool = False,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> m.FullStatus:
    config = config or cfg_mod.load_config()
    state = state if state is not None else cfg_mod.load_state()
    data = collect_all(
        force_refresh=force_refresh,
        config=config,
        offline=offline,
        state=state,
    )
    recommendations = recommender.build_recommendations(
        data["sessions"], data["antigravity"], data["go"], data["copilot"], config
    )
    return m.FullStatus(recommendations=recommendations, **data)


def _active_session(status: m.SessionStatus) -> m.SessionInfo | None:
    for session in status.sessions:
        if session.in_flight or session.recently_active:
            return session
    return status.sessions[0] if status.sessions else None


def _escape_markup(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bar_text(status: m.FullStatus, max_model_chars: int) -> str:
    ag = _effective_ag_pct(status.antigravity)
    go = _effective_go_pct(status.go)
    gh = _effective_copilot_pct(status.copilot)
    session = _active_session(status.sessions)
    if session and (session.in_flight or session.recently_active):
        model = short_model_name(session.model, max_model_chars)
        return f"{BAR_ICON_ACTIVE} {model} · AG %{_pct(ag)} · Go %{_pct(go)}"
    return f"{BAR_ICON_IDLE} AG %{_pct(ag)} · Go %{_pct(go)} · GH %{_pct(gh)}"


def _bar_class(status: m.FullStatus, critical_pct: float) -> str:
    values = [
        _effective_ag_pct(status.antigravity),
        _effective_go_pct(status.go),
        _effective_copilot_pct(status.copilot),
    ]
    known = [value for value in values if isinstance(value, (int, float))]
    lowest = min(known) if known else 100.0
    if status.sessions.dual_antigravity_conflict:
        return "conflict"
    if lowest < critical_pct:
        return "critical"
    if lowest < critical_pct * 2:
        return "warning"
    return "normal"


def _tooltip(status: m.FullStatus, critical_pct: float) -> str:
    lines: list[str] = []
    session = _active_session(status.sessions)
    if session:
        state = "çalışıyor" if session.in_flight else (
            f"son {format_duration_tr(session.updated_age_seconds) or '—'} önce aktif"
        )
        lines.append(f"<b>Oturum:</b> {_escape_markup(session.title or session.id[:8])} ({state})")
        lines.append(
            f"<b>Model:</b> {_escape_markup(session.model or '—')} · "
            f"<b>Ajan:</b> {_escape_markup(session.agent or '—')} · "
            f"<b>Token:</b> {session.tokens_total:,} · <b>${session.cost_usd:.4f}</b>"
        )
    else:
        lines.append("<b>Oturum:</b> boşta")

    lines.append("")
    lines.append("<b>Antigravity</b>")
    if status.antigravity.groups:
        if status.antigravity.age_seconds is not None:
            age_text = format_duration_tr(status.antigravity.age_seconds) or "—"
            stale_text = " · bayat" if status.antigravity.stale else ""
            lines.append(f"  <i>önbellek: {age_text} önce{stale_text}</i>")
        for group in status.antigravity.groups:
            extra = []
            if group.reset_human:
                extra.append(f"sıfırlanma {group.reset_human}")
            if group.rate_limited:
                extra.append(f"KİLİTLİ {group.rate_limit_human or ''}".strip())
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"  {_escape_markup(group.label)}: %{group.remaining_pct:.0f}{suffix}")
    else:
        lines.append(f"  veri yok ({_escape_markup(status.antigravity.error or '—')})")

    lines.append("")
    lines.append("<b>OpenCode Go (3 pencere)</b>")
    if status.go.available:
        for pool in status.go.pools:
            window = pool.remaining.get("5h", 0.0)
            lines.append(
                f"  {_escape_markup(pool.label)}: %{pool.remaining_pct:.0f} "
                f"(5s ${pool.spend.get('5h', 0):.2f}/${pool.limits.get('5h', 0):.2f}, "
                f"5s %{window:.0f})"
            )
        if status.go.total.calibrated:
            lines.append("  kalibrasyon uygulandı")
    else:
        lines.append(f"  veri yok ({_escape_markup(status.go.error or '—')})")

    lines.append("")
    lines.append("<b>GitHub Copilot</b>")
    if status.copilot.available:
        lines.append(f"  {_escape_markup(status.copilot.plan_label or 'Copilot')}")
        for quota in status.copilot.quotas:
            pct = "∞" if quota.unlimited else (
                f"%{quota.percent_remaining:.0f}" if isinstance(quota.percent_remaining, (int, float)) else "—"
            )
            lines.append(f"  {_escape_markup(quota.label)}: {pct}")
    else:
        lines.append(f"  veri yok ({_escape_markup(status.copilot.error or '—')})")

    lines.append("")
    lines.append("<b>Kolay / Orta</b>")
    for suggestion in status.recommendations.easy_medium[:2]:
        lines.append(f"  {suggestion.rank}. {_escape_markup(suggestion.model_id)} — {_escape_markup(suggestion.reason)}")
    lines.append("<b>Zor / Mimari</b>")
    for suggestion in status.recommendations.hard[:2]:
        lines.append(f"  {suggestion.rank}. {_escape_markup(suggestion.model_id)} — {_escape_markup(suggestion.reason)}")

    if status.sessions.dual_antigravity_conflict:
        lines.append("")
        lines.append(f"<b>UYARI:</b> çift Antigravity çakışması ({len(status.sessions.conflict_sessions)} oturum)")
    return "\n".join(lines)


def _waybar_payload(status: m.FullStatus, config: dict[str, Any]) -> dict[str, Any]:
    critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
    max_model_chars = int(cfg_mod.get(config, "waybar.max_model_chars", 22))
    values = [
        _effective_ag_pct(status.antigravity),
        _effective_go_pct(status.go),
        _effective_copilot_pct(status.copilot),
    ]
    known = [value for value in values if isinstance(value, (int, float))]
    percentage = int(round(min(known))) if known else 0
    return {
        "text": _bar_text(status, max_model_chars),
        "tooltip": _tooltip(status, critical_pct),
        "class": _bar_class(status, critical_pct),
        "percentage": percentage,
    }


# ---------------------------------------------------------------------------
# Alt komutlar
# ---------------------------------------------------------------------------


def cmd_waybar(args: argparse.Namespace) -> int:
    config = cfg_mod.load_config()
    status = _build_status(force_refresh=False, offline=True, config=config)
    payload = _waybar_payload(status, config)
    try:
        print(json.dumps(payload, ensure_ascii=False))
    except BrokenPipeError:
        return 0
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = cfg_mod.load_config()
    state = cfg_mod.load_state()
    status = _build_status(
        force_refresh=bool(args.refresh),
        offline=False,
        config=config,
        state=state,
    )
    if not args.no_notify:
        status.notifications = notifier.maybe_notify(status, config=config, state=state)
    print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    config = cfg_mod.load_config()
    state = cfg_mod.load_state()

    if args.reset:
        target = args.pool or "all"
        cfg_mod.reset_calibration(state, target)
        cfg_mod.save_state(state)
        print(
            json.dumps(
                {"ok": True, "reset": target, "go_calibration": state["go_calibration"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    has_value = any(
        value is not None
        for value in (args.remaining_pct, args.offset_pct, args.spent_usd)
    )
    if has_value:
        if not args.pool:
            print("Hata: kalibrasyon yazmak için --pool belirtin (flash/mid/argon/premium/total).", file=sys.stderr)
            return 2
        if args.remaining_pct is not None and not (0 <= args.remaining_pct <= 100):
            print("Hata: --remaining-pct 0 ile 100 arasında olmalı.", file=sys.stderr)
            return 2
        entry = cfg_mod.set_calibration(
            state,
            args.pool,
            remaining_pct=args.remaining_pct,
            offset_pct=args.offset_pct,
            spent_usd=args.spent_usd,
        )
        cfg_mod.save_state(state)
        print(
            json.dumps(
                {"ok": True, "pool": args.pool, "calibration": entry},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    go = _build_status(offline=True, config=config, state=state).go
    print(
        json.dumps(
            {
                "go_calibration": state.get("go_calibration", {}),
                "pools": [
                    {
                        "key": pool.key,
                        "remaining_pct": pool.remaining_pct,
                        "calibrated": pool.calibrated,
                    }
                    for pool in go.pools
                ],
                "total_remaining_pct": go.total.remaining_pct,
                "usage": (
                    "waybar-ai-quota calibrate --pool flash --remaining-pct 85\n"
                    "waybar-ai-quota calibrate --pool total --offset-pct 5\n"
                    "waybar-ai-quota calibrate --reset"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _gi_available() -> bool:
    """Mevcut yorumlayıcıda PyGObject (`gi`) var mı?"""
    try:
        import gi  # noqa: F401

        return True
    except Exception:
        return False


def _run_ui(extra: list[str], config: dict[str, Any]) -> int:
    """Önce mevcut yorumlayıcıda `ui` modülünü dener, `gi` yoksa sistem Python'una geçer."""
    if _gi_available():
        try:
            import importlib

            ui_module = importlib.import_module("waybar_ai_quota.ui")
            entry = getattr(ui_module, "main", None)
            if callable(entry):
                return int(entry(extra) or 0)
        except SystemExit:
            pass
        except ImportError:
            pass
        except Exception:
            pass

    ui_path = Path(__file__).with_name("ui.py")
    if not ui_path.is_file():
        print(
            "Hata: ui.py bulunamadı. GTK4 paneli bu kurulumda mevcut değil.",
            file=sys.stderr,
        )
        return 1
    gtk_python = str(cfg_mod.get(config, "ui.gtk_python", "/usr/bin/python3") or "/usr/bin/python3")
    python = gtk_python if Path(gtk_python).exists() else (shutil.which("python3") or sys.executable)
    env = dict(os.environ)
    src_root = str(Path(__file__).resolve().parent.parent)
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_root + (os.pathsep + previous if previous else "")
    try:
        return subprocess.call([python, str(ui_path), *extra], env=env)
    except OSError as exc:
        print(f"Hata: GTK4 paneli başlatılamadı: {exc}", file=sys.stderr)
        return 1


def cmd_ui(args: argparse.Namespace) -> int:
    config = cfg_mod.load_config()
    return _run_ui(list(args.ui_args or []), config)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waybar-ai-quota",
        description="Waybar + GTK4 panel: Antigravity, OpenCode Go ve GitHub Copilot kota takibi.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    bar = subparsers.add_parser("waybar", help="Waybar custom/ai-quota için tek satır JSON üret")
    bar.set_defaults(func=cmd_waybar)

    status = subparsers.add_parser("status", help="Tam sistem durumunu JSON olarak yazdır")
    status.add_argument(
        "--refresh",
        "-r",
        action="store_true",
        help="Uzak API'leri (Antigravity + Copilot) önbelleği beklemeden yenile",
    )
    status.add_argument(
        "--no-notify",
        action="store_true",
        help="Kritik kota / çakışma bildirimlerini gönderme",
    )
    status.set_defaults(func=cmd_status)

    calibrate = subparsers.add_parser(
        "calibrate", help="OpenCode Go kalan yüzde / offset kalibrasyonunu ayarla veya sıfırla"
    )
    calibrate.add_argument(
        "--pool",
        choices=list(cfg_mod.CALIBRATION_TARGETS),
        help="Kalibrasyon hedefi (flash/mid/argon/premium) veya genel toplam (total)",
    )
    calibrate.add_argument("--remaining-pct", type=float, help="Konsolda görünen kalan yüzde (0-100)")
    calibrate.add_argument("--offset-pct", type=float, help="Yerel yüzdeden düşülecek sapma")
    calibrate.add_argument("--spent-usd", type=float, help="Yerel harcamaya eklenecek ek USD")
    calibrate.add_argument("--reset", action="store_true", help="Kalibrasyonu sıfırla (--pool yoksa tümü)")
    calibrate.set_defaults(func=cmd_calibrate)

    ui = subparsers.add_parser("ui", help="GTK4 Layer-Shell panelini aç/kapat")
    ui.add_argument("ui_args", nargs=argparse.REMAINDER, help="ui.py'ye iletilecek ek argümanlar")
    ui.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # `ui` alt komutu kendi argümanlarını (ör. --toggle) olduğu gibi ui.py'ye geçirir.
    ui_extra: list[str] = []
    if "ui" in raw:
        index = raw.index("ui")
        ui_extra = raw[index + 1 :]
        raw = raw[: index + 1]
    parser = build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    if args.command == "ui":
        args.ui_args = ui_extra
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
