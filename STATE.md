# `agent-notifier` — Proje Durumu (`STATE.md`)

## Mimari Özeti
- **Proje Dizini:** `/home/eakgn/Projects/waybar-ai-quota` (paket: `agent-notifier` `0.2.0`; v0.1.0 `v0.1.0` git etiketiyle korunuyor)
- **Çekirdek (`src/agent_notifier/`):** OS-bağımsız saf Python — `models.py`, `config.py`, `events.py`, `fsm.py`, `engine.py`, `notifier.py`, `formatters.py`, `hooks.py`, `setup_cmd.py`, `cli.py`, `adapters/` (`opencode`, `antigravity`, `opencode_go`, `github_copilot`).
- **Linux Katmanı (`src/agent_notifier/linux/`):** `icons.py` (6 durumlu SVG + ARGB32 `IconPixmap`), `sni.py` (saf `Gio.DBus` `StatusNotifierItem` + `dbusmenu`), `styles.py`, `widgets.py`, `app.py` (GTK4/Adw + `Gtk4LayerShell` destroy/recreate + D-Bus tek-örnek uzaktan toggle).

## Aktif Görev
**Sprint 1.1 (MVP Hata Düzeltme — Mikro-Adımlar):**
- Canlı test sonrası tam denetim raporu [docs/DEBUG_REPORT_2026-11-21.md](docs/DEBUG_REPORT_2026-11-21.md) içine işlendi.
- **Mikro-Adım 1 tamamlandı** (Waybar `ui --toggle` / `--close`, D-Bus uzaktan Dashboard toggle, `Gtk4LayerShell` destroy/recreate, SNI kayıt yarışı, `app.py` adaptive interval + `_pending_refresh` kuyruğu).
- Sıradaki tek adım: Mikro-Adım 1 canlı doğrulaması sonrası **Mikro-Adım 2** (OpenCode v2 eklenti şeması).

## Tamamlanan Adımlar
- [x] `v0.1.0` git etiketi oluşturuldu; Sprint 0 ve Sprint 1 temel kod tabanı kuruldu (`343a282`).
- [x] Canlı Fedora 43 / Sway / Waybar 0.14.0 testi sonrası 13 maddelik kök neden ve mantık hatası denetim raporu yazıldı ([docs/DEBUG_REPORT_2026-11-21.md](docs/DEBUG_REPORT_2026-11-21.md)).
- [x] **Sprint 1.1 / Mikro-Adım 1:** B1 (`ui --toggle`/`--close`), B2 (`Gtk4LayerShell` destroy/recreate + D-Bus single-instance toggle), B6 (SNI kayıt yarışı), B7 (gerçek active/idle yenileme aralığı), B8 (`_pending_refresh` kuyruğu), B9 (hata loglama) düzeltildi ve test edildi.

## Sonraki Adım
1. Canlı masaüstünde `agent-notifier tray` yeniden başlatılarak hem Waybar `custom/ai-quota` hem sağ üst `tray` tıklamasını doğrulamak.
2. **Mikro-Adım 2:** OpenCode v2 eklentisini (`export default { id, setup(ctx) }`) canlı OpenCode ile doğrulayıp düzeltmek (**B3, B12**).
3. **Mikro-Adım 3:** Antigravity'yi gerçek plugin paketine (`~/.gemini/config/plugins/agent-notifier/`) taşıyıp `hooks.md` sözleşmesini tamamlamak (**B4, B10**).
4. **Mikro-Adım 4:** `opencode.db` kimlik şeması doğrulaması, `notifier.py` çift bildirim düzeltmesi ve Antigravity kota ToS UX netleştirmesi (**B5, B11, B13**).

## Bilinen Sorunlar (Açık Kayıt — Ayrıntı: `docs/DEBUG_REPORT_2026-11-21.md`)
- **B3 (Açık — Kritik):** `~/.config/opencode/plugins/agent-notifier.js` v1 formatında üretildiği için OpenCode v2'de `err_0373493e` hatası veriyor. Geçici çözüm: `agent-notifier hooks uninstall opencode`; kalıcı düzeltme Mikro-Adım 2'de.
- **B4 & B10 (Açık):** Antigravity hook'u `~/.gemini/config/hooks.json` yerine `~/.gemini/config/plugins/agent-notifier/{plugin.json,hooks.json}` olarak paketlenmeli; `PreToolUse` `decision` ve `Stop` `terminationReason`/`modelName` işlenmeli (Mikro-Adım 3).
- **B5 (Açık — UX/ToS):** Antigravity canlı kotası ToS (Ek Koşullar §6 — ikinci ihlal kalıcı ban) gereği dışarıdan çekilmiyor; ancak bu kısıt `setup` ve Dashboard'da kullanıcıya açıkça belirtilmeli (Mikro-Adım 4).
- **B11 (Açık):** `opencode_go_provider.py` içindeki `credential`/`control_account` sütun adları canlı `opencode.db` şemasına göre doğrulanmalı (Mikro-Adım 4).
- **B12 (Açık):** `opencode_harness.py` Push `waiting_input` ↔ Pull `time_updated` 2 sn zaman yarışı (Mikro-Adım 2).
- **B13 (Açık):** `notifier.py` `done` bildirim anahtarında saniye hassasiyeti nedeniyle çift bildirim riski (Mikro-Adım 4).
