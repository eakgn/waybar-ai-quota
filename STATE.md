# waybar-ai-quota — Proje Durumu (STATE.md)

## Mimari Özeti
- **Proje Dizini:** `/home/eakgn/waybar-ai-quota`
- **Çekirdek & Veri Katmanı:** Python (`sqlite3` salt-okunur DB okuyucu, Antigravity kota okuyucu/sorgulayıcı, GitHub Copilot `gh api` sorgulayıcı, OpenCode Go 3-pencereli maliyet/kalibrasyon hesaplayıcı, Görev zorluğu bazlı model öneri motoru, `notify-send` bildirim yöneticisi).
- **Arayüz Katmanı:**
  - Waybar `custom/ai-quota` JSON çıktı üreticisi (Catppuccin Mocha uyumlu).
  - GTK4 + Libadwaita + `Gtk4LayerShell` sağ-üst açılır kontrol paneli (canlı oturumlar, ilerleme çubukları, manuel yenileme butonu, Go kalibrasyon girişi, `wl-copy` ile tek tıkla `/model` kopyalama).

## Aktif Görev
Çekirdek veri toplayıcıların, öneri motorunun, GTK4 Layer-Shell arayüzünün, Waybar entegrasyonunun alt ajanlar (subagents) ile kodlanması, test edilmesi ve GitHub üzerinde public repo olarak yayınlanması.

## Tamamlanan Adımlar
- [x] Sistem keşfi (Waybar 0.14.0, Sway, GTK 4.20.4 + Adw + Gtk4LayerShell 1.3.0, OpenCode SQLite şeması, Antigravity önbellek yapısı, GitHub Copilot API erişimi) tamamlandı.
- [x] OpenCode Go (2026) 5-saatlik (%20), haftalık (%50), aylık (%100) USD limit kuralları ve GitHub Student/Copilot API yapısı doğrulandı.
- [x] Kullanıcı ile ek özellikler, UI tercihi (`GTK4 + Layer-Shell Popover`), Go kalibrasyonu ve proje adı (`waybar-ai-quota`) onaylandı.
- [x] `SPEC.md` ve `STATE.md` oluşturuldu.
- [x] `uv` Python 3.12 proje iskeleti, `pyproject.toml`, `.gitignore` ve paket yapısı oluşturuldu.
- [x] Çekirdek modüller (`models.py`, `config.py`, `collectors.py`, `recommender.py`, `notifier.py`, `cli.py`) yazıldı.
- [x] GTK4 + Libadwaita + `Gtk4LayerShell` paneli (`src/waybar_ai_quota/ui.py`) yazıldı ve Fedora 43/Sway üzerinde doğrulandı:
  - 460x760 sağ-üst layer-shell penceresi, `KeyboardMode.ON_DEMAND`, 8/12px marjlar.
  - Catppuccin Mocha CSS, çift Antigravity uyarı bandı, canlı oturumlar, üç abonelik kartı, öneri motoru ve `/model` kopyalama.
  - Go kalibrasyonu `state.json` + Waybar `RTMIN+8` sinyaliyle anında yenileniyor; 5 saniyelik sessiz auto-refresh çalışıyor.
  - `Escape`, `✕ Kapat`, PID tabanlı tek örnek/toggle davranışı doğrulandı.
- [x] Yürütülebilir başlatıcı `bin/waybar-ai-quota` (gi bulunan Python'u seçen hibrit launcher) yazıldı; sembolik bağ üzerinden çağrıda proje kökünü çözecek ve `waybar`/`status`/`calibrate` CLI alt komutlarını Python CLI'ye devredecek şekilde güncellendi.
- [x] `README.md` (kapsamlı Türkçe + İngilizce özet), MIT `LICENSE` ve `install.sh` (uv Python 3.12 `.venv`, editable kurulum, `~/.local/bin` sembolik bağı) hazırlandı.
- [x] `tests/test_core.py` eklendi: `models`, `recommender`, `collectors` (geçici SQLite + sahte Antigravity/Copilot verisi) ve `cli` JSON çıktısı; 38 testin tamamı geçiyor.
- [x] Waybar entegrasyonu tamamlandı: `~/.config/waybar/config.jsonc` içinde `custom/ai-quota` `modules-right` başına eklendi; `style.css` ortak hap seçicisi ve `normal/active/warning/critical/conflict` Catppuccin Mocha sınıfları eklendi.
- [x] `~/.local/bin/waybar-ai-quota` -> `/home/eakgn/waybar-ai-quota/bin/waybar-ai-quota` sembolik bağı oluşturuldu; Waybar `SIGUSR2` ile yeniden yüklendi ve modül komutu JSON üretti.

## Sonraki Adım
1. GitHub üzerinde public repo oluşturup ilk commit'i yayınla (depo URL'sini README'deki `<DEPO_URL>` yerine koy).
2. Ekran görüntüsü ekle ve README'ye bağla.
3. Uçtan uca kullanıcı testi: Waybar tıklaması, GTK panel aç/kapat, canlı yenileme, kritik kota bildirimi ve çift Antigravity uyarısı.

## Bilinen Sorunlar
- Fedora 43'te `python3-gobject` (`gi`) sistem Python 3.14 altında hazır kurulu, `gtk4-devel` / `gobject-introspection-devel` ise kurulu değil. Çözüm: Çekirdek paket ve testler `uv` Python 3.12 `.venv` ile çalışırken, GTK4 UI başlatıcısı `.venv` içinde `gi` bulamazsa otomatik olarak sistem `/usr/bin/python3` GObject bağlamını kullanarak sıfır derleme sorunuyla çalışacak.
- Sistemde Nerd Font kurulu değilse başlıktaki `󰚩` (U+F06A9) glyph'i tofu olarak görünebilir; Nerd Font paketi kurulduğunda düzelir. Panelin geri kalanı Noto Emoji ile sorunsuz çalışıyor.
- `Adw.ApplicationWindow` başlatılırken GtkSettings kaynaklı `gtk-application-prefer-dark-theme` uyarısı stderr'e düşebilir; işlevsel etkisi yoktur (tema `Adw.StyleManager.FORCE_DARK` ile ayarlanıyor).
