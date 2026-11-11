#!/usr/bin/env bash
# waybar-ai-quota kurulum betiği
#
# Kullanım:
#   ./install.sh
#
# Yaptıkları:
#   1. uv ile Python 3.12 sanal ortamı (.venv) oluşturur.
#   2. Paketi düzenlenebilir (editable) modda kurar.
#   3. ~/.local/bin/waybar-ai-quota sembolik bağını oluşturur.
#   4. Örnek yapılandırmayı (varsa) ~/.config/waybar-ai-quota/ altına kopyalar.
#
# Gizli bilgi toplamaz, ağ üzerinden hesap verisi çekmez.

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/waybar-ai-quota"
LAUNCHER="$ROOT_DIR/bin/waybar-ai-quota"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mUyarı:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mHata:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Ön koşullar
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    warn "uv bulunamadı."
    cat >&2 <<'MSG'
  Kurulum için:
    curl -LsSf https://astral.sh/uv/install.sh | sh
  veya paket yöneticinizle:
    Fedora:  sudo dnf install uv
    Arch:    sudo pacman -S uv
    Ubuntu:  curl -LsSf https://astral.sh/uv/install.sh | sh
MSG
    die "uv olmadan devam edilemez."
fi

[[ -f "$ROOT_DIR/pyproject.toml" ]] || die "pyproject.toml bulunamadı; betiği proje kökünde çalıştırın."

# ---------------------------------------------------------------------------
# 2. Python 3.12 sanal ortamı ve paket kurulumu
# ---------------------------------------------------------------------------
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    say "Mevcut .venv yeniden kullanılıyor."
else
    say "Python 3.12 sanal ortamı oluşturuluyor (.venv)..."
    uv venv --python 3.12 .venv
fi

say "Paket düzenlenebilir modda kuruluyor..."
uv pip install -e .

# ---------------------------------------------------------------------------
# 3. Yürütülebilir bağ
# ---------------------------------------------------------------------------
chmod +x "$LAUNCHER"
mkdir -p "$BIN_DIR"
ln -sfn "$LAUNCHER" "$BIN_DIR/waybar-ai-quota"
say "Sembolik bağ oluşturuldu: $BIN_DIR/waybar-ai-quota -> $LAUNCHER"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR PATH üzerinde değil. Kabuk yapılandırmanıza ekleyin:
       export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 4. Yapılandırma şablonu
# ---------------------------------------------------------------------------
if [[ ! -f "$CONFIG_DIR/config.toml" && -f "$ROOT_DIR/config.example.toml" ]]; then
    mkdir -p "$CONFIG_DIR"
    cp "$ROOT_DIR/config.example.toml" "$CONFIG_DIR/config.toml"
    chmod 600 "$CONFIG_DIR/config.toml"
    say "Örnek yapılandırma kopyalandı: $CONFIG_DIR/config.toml"
fi

# ---------------------------------------------------------------------------
# 5. GTK4 / Waybar ipuçları
# ---------------------------------------------------------------------------
if ! command -v waybar >/dev/null 2>&1; then
    warn "waybar bulunamadı. Dağıtımınızın paket yöneticisiyle kurun."
fi

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1; then
    warn "Sistem Python'unda GTK4/PyGObject yok; GTK paneli çalışmayabilir."
    cat >&2 <<'MSG'
    Fedora:  sudo dnf install python3-gobject gtk4 libadwaita gtk4-layer-shell
    Arch:    sudo pacman -S python-gobject gtk4 libadwaita gtk4-layer-shell
    Ubuntu:  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4-layer-shell-0
MSG
fi

say "Kurulum tamamlandı."
cat <<MSG

Sonraki adımlar:
  1. Waybar yapılandırmasına custom/ai-quota modülünü ekleyin (README.md).
  2. Waybar'ı yeniden yükleyin: pkill -SIGUSR2 waybar
  3. Test: waybar-ai-quota waybar
MSG
