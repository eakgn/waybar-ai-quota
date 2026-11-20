# Debug ve Kök Neden Analiz Raporu (2026-11-21)

> Kapsam: `agent-notifier` v0.2.0 (`343a282`) canlı Fedora 43 / Sway / Waybar 0.14.0 testi sonrası tüm kullanıcı bulguları, ek statik kod denetimi (mantık hataları dahil) ve süreç özeleştirisi.

---

## 1. Süreç Özeti (Neden Bu Kadar Hata Aynı Anda Çıktı?)

Sprint 0 ve Sprint 1 (24 yeni dosya, ~5.650 satır) tek bir büyük adımda ("Big-Bang") yazıldı. Scrum/XP'nin temel kuralı olan **her küçük dilimi gerçek ortam sınırında doğrulayıp sonra ilerleme** adımı atlandı:
- Harici API ve eklenti sözleşmeleri (OpenCode v2 eklenti şeması, Antigravity `plugins/` paketlemesi, `opencode.db` kimlik tablosu) canlı ortam/resmî kaynakla doğrulanmadan ezberden yazıldı.
- Mevcut Waybar yapılandırmasındaki (`~/.config/waybar/config.jsonc`) eski `on-click` argümanı (`ui --toggle`) kontrol edilmedi.
- Bundan sonra Sprint 1.1 **küçük, doğrulanmış mikro-düzeltmelerle** (her seferinde 1 sınır → canlı doğrulama → sonraki adım) ilerleyecektir.

---

## 2. Doğrulanan Hatalar ve Kök Nedenler

### Kritik / Kullanıcı Tarafından Gözlenenler

| # | Konum | Belirti | Kök Neden | Durum |
|---|---|---|---|---|
| **B1** | `src/agent_notifier/cli.py` (`cmd_dashboard`) + `~/.config/waybar/config.jsonc` | Waybar'daki `custom/ai-quota` göstergesine tıklayınca Dashboard açılmıyor (yalnız sağ üstteki `tray` ikonu açıyor). | Waybar `config.jsonc` içindeki `"on-click"` komutu `waybar-ai-quota ui --toggle` gönderiyor. Yeni `cli.py` `ui`/`dashboard` alt komutunda `--toggle` ve `--close` bayraklarını tanımlamadığı için `argparse` `exit code 2` (`unrecognized arguments: --toggle`) ile sessizce düşüyor. | **Mikro-Adım 1'de düzeltildi** |
| **B2** | `src/agent_notifier/linux/app.py` (`DashboardWindow`, `AgentNotifierApp`) | `Gtk4LayerShell` penceresi kapatılıp tekrar açıldığında Sway üzerinde bozulma/görünmeme riski; ayrıca `dashboard` çağrısı çalışan `tray` örneğini toggle etmek yerine `NON_UNIQUE` ile ikinci süreç açıyor. | Kapatmada `set_visible(False)` kullanılması GTK4 + `gtk4-layer-shell` altında Wayland `zwlr_layer_surface_v1` yüzeyini yok ediyor; aynı pencere örneğini tekrar göstermek güvenilir değil. Ayrıca `dashboard` D-Bus üzerinden çalışan `tray` örneğine bağlanmıyor. | **Mikro-Adım 1'de düzeltildi** |
| **B3** | `src/agent_notifier/hooks.py` (`opencode_plugin_source`) | OpenCode başlangıç hatası: `Server plugin error — Plugin must export a default definition with an id and an effect or setup function (err_0373493e)`. | OpenCode v1 eklenti söz dizimi (`export const AgentNotifierPlugin = async ...`) ezberden yazıldı. OpenCode v2 `export default { id: "...", setup(ctx) { ... } }` (veya `Plugin.define`) + `ctx.event.subscribe` sözleşmesini zorunlu kılıyor. | **[x] Düzeltildi (ESM + Node doğrulamalı)** |
| **B4** | `src/agent_notifier/hooks.py` (`antigravity_plugin_dir`) | Antigravity App içinde `agent-notifier` eklentisi görünmüyor. | Hook serbest `~/.gemini/config/hooks.json` dosyasına yazıldı. Antigravity App'in "Plugins" arayüzü ve `agy plugin list` yalnızca `~/.gemini/config/plugins/<ad>/plugin.json` + `hooks.json` yapısındaki eklentileri listeler. | **[x] Düzeltildi (yerel plugin paketi + eski hooks.json temizliği)** |
| **B5** | `src/agent_notifier/setup_cmd.py`, `widgets.py`, `README.md` | Kullanıcı Antigravity kotasını göstergede görmüyor; `setup` sihirbazı da Antigravity'yi kota sağlayıcısı olarak sormuyor. | **Tasarım/İletişim açığı:** Antigravity Ek Koşulları §6 (ToS — ikinci ihlal kalıcı ban) nedeniyle `cloudcode-pa` / OAuth kota sorgusu bilinçli olarak kaldırıldı; ancak bu kısıt `setup` sihirbazında ve Dashboard'da kullanıcıya açıkça anlatılmadığı için sessiz eksik/hata gibi göründü. | **[x] Düzeltildi (setup + Dashboard + README şeffaflık notu + test)** |

### Ek Kod Denetiminde Bulunan Mantık ve Güvenilirlik Hataları

| # | Konum | Hata / Mantık Kusuru | Etki | Durum |
|---|---|---|---|---|
| **B6** | `src/agent_notifier/linux/sni.py` (`start`) | `bus_own_name_on_connection` ve `bus_watch_name_on_connection` ikisi de birbirinden bağımsız `_register_with_watcher()` çağırıyor. | `watcher` geri çağrısı bus adı sahiplenilmeden önce tetiklenirse kayıt yarışı (race condition) oluşabilir. | **Mikro-Adım 1'de düzeltildi** |
| **B7** | `src/agent_notifier/linux/app.py` (`_on_tick`) | `events.json` `mtime` değerini hesaplıyor ama sonucu hiç kullanmadan her 3 saniyede bir koşulsuz `trigger_refresh` çağırıyor; `config.toml` içindeki `local_active_seconds` / `local_idle_seconds` ayarları ölü kod durumunda. | Boştayken gereksiz 3 sn'lik SQLite taraması; yapılandırma çalışmıyor. | **Mikro-Adım 1'de düzeltildi** |
| **B8** | `src/agent_notifier/linux/app.py` (`trigger_refresh`) | Arka plan taraması sürerken (`_refreshing == True`) gelen kullanıcı tıklamaları (Yenile, Ayar değiştir, Okundu işaretle) `return` ile sessizce düşürülüyor. | Ayar anahtarı veya yenileme butonu bazen anında tepki vermiyor. | **Mikro-Adım 1'de düzeltildi** |
| **B9** | `src/agent_notifier/linux/sni.py` & `app.py` | Geniş `except Exception: pass` blokları D-Bus ve arka plan thread hatalarını tamamen yutuyor. | Canlı ortamda arıza teşhisini körleştiriyor. | **Mikro-Adım 1'de düzeltildi** |
| **B10** | `src/agent_notifier/hooks.py` & `cli.py` (Antigravity hook sözleşmesi) | Resmî `hooks.md` sözleşmesine göre `PreToolUse` çıktısında `"decision"` zorunlu (biz `{}` bastık); `Stop` girdisindeki `terminationReason == "ERROR"` ve `modelName` (camelCase) alanları işlenmiyor. | Hatalı biten Antigravity turları `ERROR` yerine `DONE` görünüyor; model adı boş kalıyor; `PreToolUse` şema uyarısı verebilir. | **[x] Düzeltildi (`decision`, `terminationReason`, `modelName` + test)** |
| **B11** | `src/agent_notifier/adapters/opencode_go_provider.py` (`_read_go_credentials`) | `opencode.db` içindeki `credential` / `control_account` / `workspace` sütun adları doğrulanmadan tahmini (`data`, `value`, `secret`) yazıldı. | Sütun adı tutmazsa canlı `/zen/go/v1/usage` çağrısı hiç atılmaz, hep yerel DB tahmini kullanılır. | **[x] Düzeltildi (gerçek v2 `control_account` + `credential` + `workspace` DDL doğrulamalı)** |
| **B12** | `src/agent_notifier/adapters/opencode_harness.py` (satır 169) | Push `waiting_input` olayı, DB `time_updated` 2 sn'den yeni olursa tekrar `RUNNING` ile eziliyor; tersi yönde push olayı gelmezse eski durum kalabiliyor. | İzin penceresi açıkken DB metadata yazımı olursa `waiting_input` durumu geri düşebilir. | **[x] Düzeltildi (in-flight uzlaşması + test)** |
| **B13** | `src/agent_notifier/notifier.py` (satır 65) | `done` bildirim anahtarı saniye hassasiyetinde `int(updated_ts)` kullanıyor. | DB'ye 1 sn sonra trailing güncelleme yazılırsa aynı oturum için çift "Tamamlandı" bildirimi gidebilir. | **[x] Düzeltildi (sabit oturum anahtarı + RUNNING sıfırlama + 24s budama + test)** |

---

## 3. Sprint 1.1 — Sıralı Düzeltme Listesi (Tamamlandı)

1. `[x]` **B1, B2, B6, B7, B8, B9** (`97fc743`): Waybar `ui --toggle`/`--close`, D-Bus tek-örnek uzaktan toggle, `Gtk4LayerShell` destroy/recreate, SNI kayıt yarışı, `app.py` active/idle yenileme aralığı, `_pending_refresh` kuyruğu, hata loglama.
2. `[x]` **B3** (`c96aca5`): OpenCode v2 eklentisi (`export default { id, setup(ctx) }`) + Node ESM doğrulama testi.
3. `[x]` **B12** (`52b594d`): `opencode_harness.py` in-flight `waiting_input` ↔ DB `time_updated` uzlaşması.
4. `[x]` **B4** (`93a2c92`): Antigravity native plugin paketi (`~/.gemini/config/plugins/agent-notifier/{plugin.json,hooks.json}`) + eski `hooks.json` temizliği.
5. `[x]` **B10** (`a76a21a`): Antigravity `hooks.md` sözleşme uyumu (`PreToolUse` `decision: allow`, `Stop` `decision: stop` + `terminationReason`/`ERROR`, `modelName` camelCase).
6. `[x]` **B11** (`e5278b6`): `opencode_go_provider.py` gerçek `opencode.db` v2 `control_account` + `credential` + `workspace` DDL uyumu.
7. `[x]` **B13** (`72596e2`): `notifier.py` sabit oturum anahtarı ile çift `done` bildiriminin önlenmesi + 24 saatlik geçmiş budaması.
8. `[x]` **B5**: Antigravity kota sınırının `setup`, Dashboard ayarları ve `README.md` içinde tam şeffaflıkla belirtilmesi.

---

## 4. Antigravity `Stop` Hook + `/usage` Fikrinin Teknik ve ToS Değerlendirmesi

**Öneri:** Her Antigravity turu bittiğinde (`Stop` hook), arka planda `/usage` bilgisini çeken bir terminal komutu çalıştırmak.

**Hüküm: Uygulanmamalı (3 somut gerekçe):**
1. **Resmî non-interactive CLI ucu yok:** `agy` CLI'da `agy usage` gibi makinece okunabilir bir alt komut bulunmuyor; `/usage` (ve `/quota`) yalnızca interaktif TUI içinde açılan görsel bir paneldir. Non-interactive tek mod olan `agy -p` ise model çağrısı yapar (kotayı okumak yerine tüketir).
2. **Hız, senkron bloklama ve App/TUI çakışması:** Antigravity `Stop` hook'ları resmî `hooks.md` dokümanına göre **senkron** çalışır ve ajan döngüsünü bloklar. Her tur bitişinde sahte PTY ile ikinci bir `agy` TUI başlatıp ekran kazımak 2–5 sn gecikme, ekstra RAM ve Antigravity 2.0 App senaryosunda garip çift istemci davranışı üretir.
3. **ToS / Kalıcı Ban Riski:** Harici bir aracın `agy` istemcisini otomatik sarıp ekranını kazıması hem kırılgan hem de Antigravity Ek Koşulları §6 kapsamında gri/yasaklı otomasyon alanıdır (ikinci ihlal kalıcı ban).
4. **Geleceğe açık kapı:** Google ileride resmî bir `agy usage --json` komutu veya `Stop` hook `stdin` gövdesine resmî `quota` alanı eklerse (Claude Code `/statusline` gibi), sıfır riskle ve <10 ms maliyetle hemen eklenebilir. O güne kadar tam şeffaflıkla yalnız oturum izlenir.
