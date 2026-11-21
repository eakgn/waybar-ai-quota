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
- [x] **D-Bus Bildirim Dinleyicisi (`linux/notify_watcher.py`):** Antigravity komut izni istediğinde gönderdiği `org.freedesktop.Notifications.Notify` çağrısını ayrı bir `BecomeMonitor` bağlantısıyla pasif dinleyip Tray ikonunu anında `WAITING_INPUT` durumuna geçiren modül eklendi (`20/20` test + ardışık 2x D-Bus E2E testi geçti).
- [x] **Red-Team Sertleştirme & Dinamik Bar / 90dk-24s Filtre (R1–R5):**
  - **R1:** `copilot_provider.py` sınırsız havuzları `%100` diye uydurmayı bıraktı (`all_unlimited=True`, `headline_pct=None`).
  - **R2:** `opencode_go_provider.py` sabit `GO_POOLS` yerel yüzde tahmini kaldırıldı; yalnız resmî `/zen/go/v1/usage` (veya <=30 dk gerçek bayat önbellek) kullanılır, yoksa açıkça `"Canlı API hatası"` yazılır.
  - **R3:** `opencode_harness.py` gerçek `time_busy` sütununu da sorguluyor ve varsayılan SQL sorgusu son 24 saatle sınırlandı (`status --summary` / `--all`).
  - **R4:** `notify_watcher.py` kör `else -> WAITING_INPUT` varsayımı kaldırıldı (belirsiz bildirimler `None`), çoklu oturumda proje eşleştirme + geçici `antigravity-prompt` otomatik temizleme eklendi.
  - **R5 & Dinamik Bar:** Bar boştayken yalnız kritik eşik altındaki kotaları gösterir (`● Boşta`); oturum aktifken yalnız o oturumun kullandığı sağlayıcıyı 30 sn canlı TTL ile günceller. Dashboard son 90 dk'da oturum yoksa `"SON OTURUMLAR (24 SAAT)"` altında en fazla 3 oturum gösterir (`25/25` birim testi geçti).

## Sonraki Adım
1. Canlı masaüstünde yeni eklentileri yeniden kurup doğrulamak:
   - `agent-notifier hooks install opencode` (OpenCode'u açıp `err_0373493e` olmadığını doğrula)
   - `agent-notifier hooks install antigravity` (Antigravity App Plugins listesinde veya `agy plugin list`'te `agent-notifier`'ı doğrula)
   - `agent-notifier status --refresh` ile OpenCode Go canlı API (`source: "hybrid"`) yanıtını doğrula.
2. MVP canlıda pürüzsüz doğrulandıktan sonra **Sprint 2** (Claude Code + Codex CLI + OpenRouter).

## Bilinen Sorunlar
- Açık kritik hata yok (B1–B13 tamamı kapatıldı).
- Antigravity canlı kota yüzdesi Google harici bir API veya hook payload alanı sunmadığı sürece ToS gereği dışarıdan çekilmez (`agy /usage` içinde izlenir).
