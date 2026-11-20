#!/usr/bin/env bash
# agent-notifier Linux kurulum betiği
#
# Kullanım:
#   ./install.sh
#
# Yaptıkları:
#   1. Dağıtımı ve GTK4/PyGObject sistem paketlerini kontrol eder.
#   2. `uv` ile Python 3.12 sanal ortamı (.venv) oluşturur ve paketi kurar.
#   3. `~/.local/bin/agent-notifier` (ve uyumluluk için `waybar-ai-quota`) bağını kurar.
#   4. İlk yapılandırma için `agent-notifier setup` ve `doctor` komutlarını gösterir.

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
LAUNCHER="$ROOT_DIR/bin/agent-notifier"
COMPAT_LAUNCHER="$ROOT_DIR/bin/waybar-ai-quota"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mUyarı:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mHata:\033[0m %s\n' "$*" >&2; exit 1; }

detect_distro() {
    if [[ -r /etc/os-release ]]; then
        local text
        text="$(tr '[:upper:]' '[:lower:]' < /etc/os-release)"
        [[ "$text" == *fedora* ]] && { echo "fedora"; return; }
        [[ "$text" == *arch* || "$text" == *cachyos* || "$text" == *endeavour* ]] && { echo "arch"; return; }
        [[ "$text" == *ubuntu* || "$text" == *debian* || "$text" == *pop* ]] && { echo "debian"; return; }
    fi
    echo "linux"
}

DISTRO="$(detect_distro)"

# ---------------------------------------------------------------------------
# 1. Python / uv ortamı
# ---------------------------------------------------------------------------
[[ -f "$ROOT_DIR/pyproject.toml" ]] || die "pyproject.toml bulunamadı; betiği proje kökünde çalıştırın."

if command -v uv >/dev/null 2>&1; then
    if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
        say "Mevcut .venv yeniden kullanılıyor."
    else
        say "uv ile Python 3.12 sanal ortamı oluşturuluyor (.venv)..."
        uv venv --python 3.12 .venv
    fi
    say "Paket düzenlenebilir (editable) modda kuruluyor..."
    uv pip install -e .
else
    warn "uv bulunamadı; sistem python3 ile doğrudan çalıştırma bağları kurulacak."
    say "Öneri: uv kurmak için https://docs.astral.sh/uv/ veya paket yöneticinizi kullanın."
fi

# ---------------------------------------------------------------------------
# 2. Yürütülebilir sembolik bağlar
# ---------------------------------------------------------------------------
chmod +x "$LAUNCHER" "$COMPAT_LAUNCHER"
mkdir -p "$BIN_DIR"
ln -sfn "$LAUNCHER" "$BIN_DIR/agent-notifier"
ln -sfn "$COMPAT_LAUNCHER" "$BIN_DIR/waybar-ai-quota"
say "Komut kuruldu: $BIN_DIR/agent-notifier"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR PATH üzerinde değil. Kabuk ayarınıza ekleyin:
       export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 3. Sistem GTK4 / Libadwaita / LayerShell denetimi
# ---------------------------------------------------------------------------
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' >/dev/null 2>&1; then
    warn "Sistem Python'unda GTK4 / Libadwaita (PyGObject) eksik; System Tray ve Dashboard için gereklidir."
    case "$DISTRO" in
        fedora) echo "  Kurulum (Fedora):  sudo dnf install python3-gobject gtk4 libadwaita gtk4-layer-shell" >&2 ;;
        arch)   echo "  Kurulum (Arch):    sudo pacman -S python-gobject gtk4 libadwaita gtk4-layer-shell" >&2 ;;
        debian) echo "  Kurulum (Debian):  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4-layer-shell-0" >&2 ;;
        *)      echo "  GTK4, Libadwaita ve PyGObject paketlerini dağıtımınızdan kurun." >&2 ;;
    esac
else
    say "Sistem GTK4 + Libadwaita doğrulandı."
fi

say "Kurulum tamamlandı!"
cat <<'MSG'

Sonraki 3 adım:
  1. Ortamı denetle:       agent-notifier doctor
  2. Harness & kota seç:   agent-notifier setup
  3. System Tray başlat:   agent-notifier tray

Not (GNOME kullanıcıları): System Tray ikonu için AppIndicator eklentisinin etkin olması gerekir.
Not (Waybar kullanıcıları): `tray` modülü ikonu doğrudan gösterir; özel metin pili için `agent-notifier bar --format waybar` kullanabilirsiniz.
MSG
