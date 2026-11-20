"""6 FSM durumu için dinamik SVG ikon seti ve ARGB32 `IconPixmap` üreteci.

- SVG dosyaları XDG veri dizinindeki `icons/` klasörüne yazılır (`IconThemePath`).
- `IconThemePath` yok sayan tepsiler için aynı durum renginde saf-Python 24x24
  ARGB32 piksel haritası (`IconPixmap`) üretilir; harici görüntü kütüphanesi gerekmez.
"""

from __future__ import annotations

import math
from pathlib import Path

from .. import config as cfg_mod
from ..models import SessionState

STATE_ICON_NAMES: dict[SessionState, str] = {
    SessionState.WAITING_INPUT: "agent-notifier-waiting",
    SessionState.ERROR: "agent-notifier-error",
    SessionState.RATE_LIMITED: "agent-notifier-ratelimited",
    SessionState.RUNNING: "agent-notifier-running",
    SessionState.DONE_UNSEEN: "agent-notifier-done",
    SessionState.IDLE: "agent-notifier-idle",
}

STATE_PALETTE: dict[SessionState, tuple[str, tuple[int, int, int], str]] = {
    # state: (hex_fill, rgb_tuple, svg_inner_symbol)
    SessionState.WAITING_INPUT: (
        "#f9e2af",
        (249, 226, 175),
        '<text x="12" y="16.5" text-anchor="middle" font-family="sans-serif" '
        'font-size="14" font-weight="800" fill="#1e1e2e">?</text>',
    ),
    SessionState.ERROR: (
        "#f38ba8",
        (243, 139, 168),
        '<path d="M8.5 8.5 L15.5 15.5 M15.5 8.5 L8.5 15.5" stroke="#1e1e2e" '
        'stroke-width="2.4" stroke-linecap="round"/>',
    ),
    SessionState.RATE_LIMITED: (
        "#fab387",
        (250, 179, 135),
        '<rect x="7" y="10.5" width="10" height="3" rx="1.2" fill="#1e1e2e"/>',
    ),
    SessionState.RUNNING: (
        "#89b4fa",
        (137, 180, 250),
        '<path d="M13 5.8 L8.5 12.6 H12 L11 18.2 L15.5 11.4 H12 Z" fill="#1e1e2e"/>',
    ),
    SessionState.DONE_UNSEEN: (
        "#a6e3a1",
        (166, 227, 161),
        '<path d="M7.5 12.4 L10.6 15.5 L16.8 8.8" fill="none" stroke="#1e1e2e" '
        'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    ),
    SessionState.IDLE: (
        "#6c7086",
        (108, 112, 134),
        '<circle cx="12" cy="12" r="3.4" fill="#1e1e2e"/>',
    ),
}

_PIXMAP_CACHE: dict[SessionState, bytes] = {}


def svg_for_state(state: SessionState) -> str:
    fill, _rgb, inner = STATE_PALETTE[state]
    ring = "#1e1e2e"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">\n'
        f'  <circle cx="12" cy="12" r="10.5" fill="{fill}" stroke="{ring}" stroke-width="1.4"/>\n'
        f"  {inner}\n"
        "</svg>\n"
    )


def ensure_icon_files(root: Path | None = None) -> Path:
    """SVG ikonlarını XDG ikon teması dizinine yazar ve kök dizini döndürür."""
    base = root or cfg_mod.icon_theme_dir()
    cfg_mod.ensure_dir(base, mode=0o755)
    for state, name in STATE_ICON_NAMES.items():
        target = base / f"{name}.svg"
        content = svg_for_state(state)
        if not target.is_file() or target.read_text(encoding="utf-8", errors="ignore") != content:
            cfg_mod.atomic_write_text(target, content, mode=0o644)
    return base


def argb32_pixmap_for_state(state: SessionState, size: int = 24) -> tuple[int, int, bytes]:
    """SNI `IconPixmap` özelliği için 24x24 big-endian ARGB32 byte tamponu üretir."""
    if size == 24 and state in _PIXMAP_CACHE:
        return size, size, _PIXMAP_CACHE[state]

    _hex, (r, g, b), _inner = STATE_PALETTE[state]
    dark = (30, 30, 46)
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    outer_r = size * 0.44
    inner_r = size * 0.16
    buf = bytearray(size * size * 4)

    for y in range(size):
        for x in range(size):
            dx = x - cx
            dy = y - cy
            dist = math.hypot(dx, dy)
            idx = (y * size + x) * 4
            if dist <= outer_r + 0.6:
                edge_alpha = max(0.0, min(1.0, outer_r + 0.6 - dist))
                a = int(round(edge_alpha * 255))
                # Merkeze küçük koyu nokta / işaret koyarak okunurluk sağla
                use_dark = False
                if state == SessionState.IDLE and dist <= inner_r:
                    use_dark = True
                elif state == SessionState.RUNNING and abs(dx) <= 1.2 and abs(dy) <= outer_r * 0.45:
                    use_dark = True
                elif state == SessionState.WAITING_INPUT and (
                    (abs(dx) <= 1.3 and -outer_r * 0.42 <= dy <= 0.5)
                    or (abs(dx) <= 1.4 and outer_r * 0.28 <= dy <= outer_r * 0.48)
                ):
                    use_dark = True
                elif state == SessionState.DONE_UNSEEN and (
                    (abs((dx + 1.5) - (dy - 1.5)) <= 1.1 and -4.0 <= dx <= 0.0)
                    or (abs((dx + 0.5) + (dy - 2.0)) <= 1.1 and -1.0 <= dx <= 5.0)
                ):
                    use_dark = True
                elif state == SessionState.ERROR and (
                    (abs(dx - dy) <= 1.2 or abs(dx + dy) <= 1.2) and abs(dx) <= outer_r * 0.42
                ):
                    use_dark = True
                elif state == SessionState.RATE_LIMITED and (
                    abs(dy) <= 1.4 and abs(dx) <= outer_r * 0.48
                ):
                    use_dark = True

                pr, pg, pb = dark if use_dark else (r, g, b)
                buf[idx] = a
                buf[idx + 1] = pr
                buf[idx + 2] = pg
                buf[idx + 3] = pb

    data = bytes(buf)
    if size == 24:
        _PIXMAP_CACHE[state] = data
    return size, size, data
