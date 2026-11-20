# `agent-notifier` — Proje Durumu (`STATE.md`)

## Mimari Özeti
- **Proje Dizini:** `/home/eakgn/Projects/waybar-ai-quota` (paket: `agent-notifier` `0.2.0`; v0.1.0 `v0.1.0` git etiketiyle korunuyor)
- **Çekirdek (`src/agent_notifier/`):** OS-bağımsız saf Python — `models.py`, `config.py`, `events.py`, `fsm.py`, `engine.py`, `notifier.py`, `formatters.py`, `hooks.py`, `setup_cmd.py`, `cli.py`, `adapters/` (`opencode`, `antigravity`, `opencode_go`, `github_copilot`).
- **Linux Katmanı (`src/agent_notifier/linux/`):** `icons.py` (6 durumlu SVG + ARGB32 `IconPixmap`), `sni.py` (saf `Gio.DBus` `StatusNotifierItem` + `dbusmenu`), `styles.py`, `widgets.py` (ana oturum → subagent ağacı, opt-in kota kartı, ayarlar anahtarı), `app.py` (GTK4/Adw + `Gtk4LayerShell` + kayan pencere fallback).

## Aktif Görev
Sprint 0 ve Sprint 1 (MVP) tamamlandı ve doğrulandı — canlı masaüstü kurulumu (`./install.sh`, `agent-notifier setup`, `agent-notifier tray`) ve ardından Sprint 2'ye hazır.

## Tamamlanan Adımlar
- [x] v0.1.0 git etiketi (`v0.1.0` -> `748461f`) oluşturuldu.
- [x] **Sprint 0:** `cloudcode-pa` / Google OAuth scraping kodu tamamen kaldırıldı; `SPEC.md`, `PLAN.md`, `pyproject.toml`, `config.example.toml` v2 (`agent-notifier 0.2.0`) olarak güncellendi; normalize FSM + `SessionNode` ağaç + `events.py` + `HarnessAdapter`/`ProviderAdapter` kontratları yazıldı.
- [x] **Sprint 1 (MVP):**
  - OpenCode harness adaptörü (`opencode.db` `mode=ro`, `parent_id` ana/subagent ağacı + eski şema fallback + push birleştirme).
  - Antigravity (`agy`) pasif hook adaptörü (sıfır OAuth, sıfır ağ çağrısı).
  - OpenCode Go hibrit sağlayıcısı (resmî `/zen/go/v1/usage` + gizlilik-temiz önbellek + yerel DB fallback) ve opt-in GitHub Copilot sağlayıcısı.
  - Saf `Gio.DBus` SNI Tray + `dbusmenu` + 6 durumlu dinamik SVG/ARGB32 ikon seti + modüler GTK4/Adw Dashboard (ağaç görünümü, opt-in kota kartları, `⚙` ayarlar çekmecesi).
  - Waybar / Polybar / Swaybar / Tmux / Plain / JSON formatlayıcıları.
  - `./install.sh`, `bin/agent-notifier` (+ `waybar-ai-quota` uyumluluk sarmalayıcısı), `setup`, `doctor`, `.bak` yedekli `hooks print|install|uninstall`, `uninstall`, güncel `README.md`.
  - 13 yalıtılmış birim testi (`tests/test_core.py`, `tests/test_hooks_cli.py`) ve CLI/GTK4 import duman testleri geçti.

## Sonraki Adım
1. Canlı Sway masaüstünde `./install.sh && agent-notifier doctor && agent-notifier setup && agent-notifier tray` ile canlı doğrulama yapmak (ve istenirse GitHub'da repo adını `agent-notifier` yapıp push etmek).
2. Canlı geri bildirim sonrası **Sprint 2**: Claude Code (`.jsonl` + `/statusline` pasif kota) + Codex CLI + OpenRouter + Yerel USD bütçe sağlayıcısı.

## Bilinen Sorunlar
- Mevcut `.venv` eski proje yolunu işaret ettiği için canlı ortamda `./install.sh` (veya `rm -rf .venv && uv venv --python 3.12 .venv && uv pip install -e .`) ile yenilenmelidir.
- GNOME üzerinde System Tray ikonu için AppIndicator eklentisinin etkin olması gerekir (`agent-notifier doctor` bunu kullanıcıya bildirir).
