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
| **B5** | `src/agent_notifier/setup_cmd.py` & `README.md` | Kullanıcı Antigravity kotasını göstergede görmüyor; `setup` sihirbazı da Antigravity'yi kota sağlayıcısı olarak sormuyor. | **Tasarım/İletişim açığı:** Antigravity Ek Koşulları §6 (ToS — ikinci ihlal kalıcı ban) nedeniyle `cloudcode-pa` / OAuth kota sorgusu bilinçli olarak kaldırıldı; ancak bu kısıt `setup` sihirbazında ve Dashboard'da kullanıcıya açıkça anlatılmadığı için sessiz eksik/hata gibi göründü. | **Karar / Mikro-Adım 4** |

### Ek Kod Denetiminde Bulunan Mantık ve Güvenilirlik Hataları

| # | Konum | Hata / Mantık Kusuru | Etki | Durum |
|---|---|---|---|---|
| **B6** | `src/agent_notifier/linux/sni.py` (`start`) | `bus_own_name_on_connection` ve `bus_watch_name_on_connection` ikisi de birbirinden bağımsız `_register_with_watcher()` çağırıyor. | `watcher` geri çağrısı bus adı sahiplenilmeden önce tetiklenirse kayıt yarışı (race condition) oluşabilir. | **Mikro-Adım 1'de düzeltildi** |
| **B7** | `src/agent_notifier/linux/app.py` (`_on_tick`) | `events.json` `mtime` değerini hesaplıyor ama sonucu hiç kullanmadan her 3 saniyede bir koşulsuz `trigger_refresh` çağırıyor; `config.toml` içindeki `local_active_seconds` / `local_idle_seconds` ayarları ölü kod durumunda. | Boştayken gereksiz 3 sn'lik SQLite taraması; yapılandırma çalışmıyor. | **Mikro-Adım 1'de düzeltildi** |
| **B8** | `src/agent_notifier/linux/app.py` (`trigger_refresh`) | Arka plan taraması sürerken (`_refreshing == True`) gelen kullanıcı tıklamaları (Yenile, Ayar değiştir, Okundu işaretle) `return` ile sessizce düşürülüyor. | Ayar anahtarı veya yenileme butonu bazen anında tepki vermiyor. | **Mikro-Adım 1'de düzeltildi** |
| **B9** | `src/agent_notifier/linux/sni.py` & `app.py` | Geniş `except Exception: pass` blokları D-Bus ve arka plan thread hatalarını tamamen yutuyor. | Canlı ortamda arıza teşhisini körleştiriyor. | **Mikro-Adım 1'de düzeltildi** |
| **B10** | `src/agent_notifier/hooks.py` & `cli.py` (Antigravity hook sözleşmesi) | Resmî `hooks.md` sözleşmesine göre `PreToolUse` çıktısında `"decision"` zorunlu (biz `{}` bastık); `Stop` girdisindeki `terminationReason == "ERROR"` ve `modelName` (camelCase) alanları işlenmiyor. | Hatalı biten Antigravity turları `ERROR` yerine `DONE` görünüyor; model adı boş kalıyor; `PreToolUse` şema uyarısı verebilir. | **Açık — Mikro-Adım 3** |
| **B11** | `src/agent_notifier/adapters/opencode_go_provider.py` (`_read_go_credentials`) | `opencode.db` içindeki `credential` / `control_account` / `workspace` sütun adları doğrulanmadan tahmini (`data`, `value`, `secret`) yazıldı. | Sütun adı tutmazsa canlı `/zen/go/v1/usage` çağrısı hiç atılmaz, hep yerel DB tahmini kullanılır. | **Açık — Mikro-Adım 4** |
| **B12** | `src/agent_notifier/adapters/opencode_harness.py` (satır 169) | Push `waiting_input` olayı, DB `time_updated` 2 sn'den yeni olursa tekrar `RUNNING` ile eziliyor; tersi yönde push olayı gelmezse eski durum kalabiliyor. | İzin penceresi açıkken DB metadata yazımı olursa `waiting_input` durumu geri düşebilir. | **[x] Düzeltildi (in-flight uzlaşması + test)** |
| **B13** | `src/agent_notifier/notifier.py` (satır 65) | `done` bildirim anahtarı saniye hassasiyetinde `int(updated_ts)` kullanıyor. | DB'ye 1 sn sonra trailing güncelleme yazılırsa aynı oturum için çift "Tamamlandı" bildirimi gidebilir. | **Açık — Mikro-Adım 4** |

---

## 3. Sprint 1.1 — Mikro-İyileştirme Sırası (Her Adım Tek Sınır + Doğrulama)

1. **Mikro-Adım 1 (Bu tur — uygulandı):** Waybar `custom/ai-quota` (`ui --toggle` / `--close`) + D-Bus tek-örnek Dashboard toggle + `Gtk4LayerShell` destroy/recreate + SNI kayıt yarışı + `app.py` refresh kuyruğu ve gerçek active/idle aralığı + hata loglama (**B1, B2, B6, B7, B8, B9**).
2. **Mikro-Adım 2 (Sıradaki):** OpenCode v2 eklentisini (`export default { id, setup }`) canlı OpenCode v2 ile doğrulayarak düzeltmek + Pull/Push durum çakışmasını (**B3, B12**) gidermek.
3. **Mikro-Adım 3:** Antigravity entegrasyonunu gerçek plugin paketine (`~/.gemini/config/plugins/agent-notifier/{plugin.json,hooks.json}`) taşımak + `PreToolUse`/`Stop`/`modelName` sözleşmesini tamamlamak (**B4, B10**).
4. **Mikro-Adım 4:** `opencode.db` gerçek kimlik şemasını doğrulayarak OpenCode Go canlı API'sini teyit etmek + `notifier.py` çift bildirim düzeltmesi + Antigravity kota sınırının UI/setup netleştirmesi (**B5, B11, B13**).
