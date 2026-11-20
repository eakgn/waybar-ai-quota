# agent-notifier

Linux System Tray (`StatusNotifierItem`) ve GTK4/Libadwaita Dashboard tabanlı **AI kodlama ajanı oturum, alt ajan (subagent) ve opt-in kota izleyicisi**.

Başka bir işle uğraşırken arka planda çalışan AI oturumlarının **soru/onay bekliyor**, **çalışıyor**, **tamamlandı** veya **hata** durumlarını sistem tepsisindeki ikondan tek bakışta görmeni sağlar; tıklayınca ana ajan ↔ alt ajan ağacını ve yalnızca kendi seçtiğin kota sağlayıcılarını açar.

> **Not:** Bu proje `waybar-ai-quota` v0.1.0'ın bar-bağımsız ve çoklu-harness v2 evrimidir (v0.1.0 geçmişi `v0.1.0` git etiketiyle korunmuştur).

---

## Temel Özellikler (v0.2.0 — MVP)

- **Tek Bakışta Oturum Durum Makinesi (FSM):**
  - `❓ Soru / Onay Bekliyor` (amber — en yüksek öncelik + bildirim)
  - `✖ Hata / ⛔ Kota Kilidi` (kırmızı/turuncu)
  - `⚡ Çalışıyor` (mavi)
  - `✓ Tamamlandı (görülmedi)` (yeşil — Dashboard açılınca otomatik okundu olur)
  - `● Boşta` (nötr)
- **Evrensel Linux Masaüstü & WM Uyumu:**
  - **System Tray:** Saf `Gio.DBus` `org.kde.StatusNotifierItem` (SNI) + sağ tık `dbusmenu` — **GNOME** (AppIndicator), **KDE Plasma**, **XFCE**, **COSMIC** ve **Waybar `tray`** modülünde GTK3 çakışması olmadan çalışır.
  - **Dashboard:** wlroots Wayland'da (Sway, Hyprland, Niri) `Gtk4LayerShell` ile köşe popover; GNOME/KDE/X11'de otomatik kayan pencere.
  - **Riceli Bar Çıktıları:** `agent-notifier bar --format waybar|polybar|swaybar|tmux|plain|json`.
- **Ana Ajan ↔ Alt Ajan (Subagent) Ağacı:**
  - Alt ajanlar ana oturumun altında dikey bağlantı çizgisi ve kendi durum rozetleriyle görünür; bir alt ajan soru/onay beklediğinde ana kart ve Tray ikonu anında uyarır.
- **Opt-in Harness ve Sağlayıcı Seçimi:**
  - Yalnızca kullandığın harness ve kota sağlayıcıları (`config.toml` veya Dashboard `⚙` ayarlarından) açılır; kapalı olanlar ne sorgulanır ne ekranda yer kaplar.
- **Sıfır OAuth/Cookie Scraping (ToS-Güvenli Mimari):**
  - Yalnız salt-okunur yerel veritabanları (`mode=ro`), resmî API uçları ve resmî harness hook'ları kullanılır.
  - **Sprint 1 Adaptörleri:** OpenCode (`opencode.db` + `parent_id` ağacı), Antigravity (`agy` pasif hook), OpenCode Go (resmî `/zen/go/v1/usage` + yerel DB fallback), GitHub Copilot (`gh api`, opt-in).

---

## Kurulum

### 1. Sistem GTK4 / Libadwaita Paketleri (Dağıtımına Göre)

- **Fedora:**
  ```bash
  sudo dnf install python3-gobject gtk4 libadwaita gtk4-layer-shell libnotify
  ```
- **Arch Linux:**
  ```bash
  sudo pacman -S python-gobject gtk4 libadwaita gtk4-layer-shell libnotify
  ```
- **Ubuntu / Debian:**
  ```bash
  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4-layer-shell-0 libnotify-bin
  ```

### 2. Uygulamayı Kur

**Seçenek A — Repo'dan (Önerilen):**
```bash
git clone https://github.com/eakgn/agent-notifier.git
cd agent-notifier
./install.sh
```

**Seçenek B — Tek Komutla (`pipx`, klonsuz):**
```bash
pipx install --system-site-packages git+https://github.com/eakgn/agent-notifier.git
```

### 3. İlk Yapılandırma ve Başlatma

```bash
agent-notifier doctor   # Bağımlılıkları ve ortamı doğrula
agent-notifier setup    # Kullanılan harness/kotaları seç, isteğe bağlı hook ve autostart kur
agent-notifier tray     # System Tray ikonunu başlat
```

---

## Hook Kurulumu (Anlık "Soru Bekliyor" ve Antigravity Takibi)

Hook kurmadan da OpenCode oturumları salt-okunur izlenir. Anlık **"soru/onay bekliyor"** ve **Antigravity (`agy`)** takibi için mevcut ayarlarına zarar vermeyen `.bak` yedekli hook kurulumunu kullanabilirsin:

```bash
# Önce ne değişeceğini gör (dosyaya yazmaz):
agent-notifier hooks install antigravity --dry-run
agent-notifier hooks install opencode --dry-run

# Güvenli kurulum (mevcut hook gruplarını korur, .bak yedeği alır):
agent-notifier hooks install antigravity
agent-notifier hooks install opencode

# Elle kopyalamak istersen:
agent-notifier hooks print antigravity

# Kaldırmak istersen:
agent-notifier hooks uninstall antigravity
agent-notifier hooks uninstall opencode
```

---

## Waybar / Polybar Entegrasyonu (İsteğe Bağlı)

System Tray ikonu Waybar'ın standart `tray` modülünde zaten görünür. Ek olarak özel metin/renk pili istersen:

```jsonc
"custom/agent-notifier": {
  "exec": "agent-notifier bar --format waybar",
  "return-type": "json",
  "interval": 5,
  "on-click": "agent-notifier dashboard",
  "on-click-right": "agent-notifier status --refresh >/dev/null"
}
```

Polybar için: `agent-notifier bar --format polybar`

---

## Kaldırma

```bash
agent-notifier uninstall
```
Bu komut eklenen XDG autostart, `systemd --user` servisi ve `agent-notifier` hook kayıtlarını temizler (`--keep-config` ile `config.toml` korunabilir).

---

## Yol Haritası

Ayrıntılı mimari ve iteratif sprint planı için [SPEC.md](SPEC.md) ve [PLAN.md](PLAN.md) dosyalarına bakın.
- **Sprint 2:** Claude Code (`.jsonl` + `/statusline` pasif kota), Codex CLI, OpenCode Zen, OpenRouter, Yerel USD Bütçe.
- **Sprint 3:** Pi, Hermes, çoklu DE/WM sertleştirme, AUR/COPR paketleme.

## Lisans

[MIT](LICENSE)
