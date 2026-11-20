# `agent-notifier` — Proje Durumu (`STATE.md`)

## Mimari Özeti
- **Proje Dizini:** `/home/eakgn/Projects/waybar-ai-quota` (paket: `agent-notifier` `0.2.0`; v0.1.0 `v0.1.0` git etiketiyle korunuyor)
- **Çekirdek (`src/agent_notifier/`):** OS-bağımsız saf Python — `models.py`, `config.py`, `events.py`, `fsm.py`, `engine.py`, `notifier.py`, `formatters.py`, `hooks.py`, `setup_cmd.py`, `cli.py`, `adapters/` (`opencode`, `antigravity`, `opencode_go`, `github_copilot`).
- **Linux Katmanı (`src/agent_notifier/linux/`):** `icons.py` (6 durumlu SVG + ARGB32 `IconPixmap`), `sni.py` (saf `Gio.DBus` `StatusNotifierItem` + `dbusmenu`), `styles.py`, `widgets.py`, `app.py` (GTK4/Adw + `Gtk4LayerShell` destroy/recreate + D-Bus tek-örnek uzaktan toggle).

## Aktif Görev
**Sprint 1.1 (MVP Hata Düzeltme) TAMAMLANDI** — 13/13 hata sırayla düzeltildi, atomik commit'lendi ve 19/19 birim testi geçti. Canlı ortamda yeni OpenCode v2 ve Antigravity native plugin'lerinin yeniden kurulup doğrulanması bekleniyor.

## Tamamlanan Adımlar
- [x] `v0.1.0` git etiketi oluşturuldu; Sprint 0 ve Sprint 1 temel kod tabanı kuruldu (`343a282`).
- [x] Canlı test sonrası 13 maddelik kök neden raporu yazıldı ([docs/DEBUG_REPORT_2026-11-21.md](docs/DEBUG_REPORT_2026-11-21.md)).
- [x] **B1, B2, B6, B7, B8, B9** (`97fc743`): Waybar `ui --toggle`, D-Bus uzaktan toggle, `Gtk4LayerShell` destroy/recreate, SNI yarış düzeltmesi, adaptive refresh & kuyruk, hata loglama.
- [x] **B3** (`c96aca5`): OpenCode v2 plugin (`export default { id, setup }`) + Node ESM doğrulaması.
- [x] **B12** (`52b594d`): `opencode_harness.py` in-flight `waiting_input` ↔ DB uzlaşması.
- [x] **B4** (`93a2c92`): Antigravity native plugin (`~/.gemini/config/plugins/agent-notifier/{plugin.json,hooks.json}`) + eski `hooks.json` temizliği.
- [x] **B10** (`a76a21a`): Antigravity `PreToolUse`/`Stop` JSON sözleşmesi ve `modelName` camelCase.
- [x] **B11** (`e5278b6`): `opencode_go_provider.py` gerçek `opencode.db` v2 DDL (`control_account` + `credential` + `workspace`) uyumu.
- [x] **B13** (`72596e2`): `notifier.py` sabit oturum anahtarı ile çift `done` bildiriminin önlenmesi + 24s budama.
- [x] **B5**: Antigravity kota ToS şeffaflık notu (`setup`, Dashboard, `README.md`) ve `Stop` hook + `/usage` fikrinin teknik/ToS değerlendirmesi.

## Sonraki Adım
1. Canlı masaüstünde yeni eklentileri yeniden kurup doğrulamak:
   - `agent-notifier hooks install opencode` (OpenCode'u açıp `err_0373493e` olmadığını doğrula)
   - `agent-notifier hooks install antigravity` (Antigravity App Plugins listesinde veya `agy plugin list`'te `agent-notifier`'ı doğrula)
   - `agent-notifier status --refresh` ile OpenCode Go canlı API (`source: "hybrid"`) yanıtını doğrula.
2. MVP canlıda pürüzsüz doğrulandıktan sonra **Sprint 2** (Claude Code + Codex CLI + OpenRouter).

## Bilinen Sorunlar
- Açık kritik hata yok (B1–B13 tamamı kapatıldı).
- Antigravity canlı kota yüzdesi Google harici bir API veya hook payload alanı sunmadığı sürece ToS gereği dışarıdan çekilmez (`agy /usage` içinde izlenir).
