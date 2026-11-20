# `agent-notifier` — v2 Mimari Plan ve İteratif Yol Haritası (`PLAN.md`)

> Durum: Planlama (Çekirdek kararlar onaylandı — 2026-11-21)  
> Proje / Paket Adı: **`agent-notifier`** (onaylandı)  
> Metodoloji: MVP-öncelikli, iteratif (Spiral / XP — her sprint sonunda çalışan, test edilmiş yazılım)  
> Not: v0.1.0 kapsamı [SPEC.md](SPEC.md) içinde korunmaktadır; repo stratejisi ve kurulum akışı onaylanınca `SPEC.md` v2'ye güncellenecektir.

---

## 1. Onaylanan Ürün Kararları

1. **Çekirdek Ürün Amacı (Glanceable Session Monitor):**
   - Kullanıcı başka bir işle uğraşırken arka planda çalışan AI ajan oturumlarının durumunu **tek bakışta** (System Tray ikonu veya WM bar göstergesi üzerinden) görebilmesi: *çalışıyor*, *soru/onay bekliyor*, *bitti (görülmedi)*, *hata/kilit*, *boşta*.
   - İkona tıklandığında açılan modern **Dashboard** üzerinden hem oturum detaylarının (ana ajan + alt ajanlar) hem de kota durumunun izlenmesi.
2. **Hedef Platform (Linux-Öncelikli, Taşınabilir Çekirdek):**
   - Birincil hedef **Linux**: hem popüler masaüstü ortamları (**GNOME, KDE Plasma, XFCE, COSMIC**) hem de özelleştirilmiş (riced) WM barları (**Waybar, Polybar, Swaybar, Hyprpanel/Eww/AGS**) ile tam uyum.
   - Çekirdek motor (`core`) OS-bağımsız tasarlanır; ileride macOS (menu bar) ve Windows (system tray) arayüz kabukları çekirdeği bozmadan eklenebilir.
3. **Ana Ajan ↔ Subagent Hiyerarşisi:**
   - Dashboard'da oturumlar düz liste yerine **ağaç (parent → child)** düzeninde sunulur; ana oturum kartının altında bağlı alt ajanlar (subagent) görsel bağlantı çizgisi ve kendi durum göstergeleriyle (çalışıyor / bekliyor / bitti) ayrıştırılır.
4. **Opt-in Harness ve Provider Seçimi:**
   - Kullanıcı yalnızca kullandığı harness ve kota sağlayıcılarını aktif eder (hem `config.toml` hem Dashboard ayar çekmecesi üzerinden).
   - Seçilmeyen kaynaklar **ne sorgulanır ne de arayüzde yer kaplar** (sıfır gereksiz CPU/ağ ve temiz ekran).
5. **Ertelenen Kapsam:**
   - Model öneri motoru (`recommender`) şimdilik **rafa kaldırıldı**; MVP kapsamına alınmayacak, ileri sürümlerde değerlendirilecek.
6. **Güvenlik ve ToS Anayasası (Değişmez Kural):**
   - **Sıfır OAuth/cookie scraping, sıfır tersine-mühendislik özel API.**
   - Yalnız resmî API uçları, salt-okunur (`mode=ro`) yerel oturum dosyaları ve resmî harness hook'ları kullanılır.
   - v0.1.0 içindeki `cloudcode-pa` / Google OAuth yenileme kodu ([collectors.py](src/waybar_ai_quota/collectors.py)) ilk adımda tamamen kaldırılır; Antigravity (`agy`) yalnız pasif yerel gözlem/hook ile izlenir.

---

## 2. Mimari Tasarım Özeti

### 2.1. İki Ortogonal Eksen: `Harness ≠ Provider`

| Eksen | Sorumluluk | Örnekler |
|---|---|---|
| **`HarnessAdapter`** | Yerel oturumlar, ana/subagent ağacı, oturum durumu (FSM), token/yerel maliyet | OpenCode, Antigravity (`agy`), Claude Code, Codex CLI, Pi, Hermes |
| **`ProviderAdapter`** | Abonelik kotası, pencere sıfırlanma süresi, kredi/bütçe takibi | OpenCode Go (resmî `/zen/go/v1/usage`), OpenCode Zen, GitHub Copilot, Claude Pro/Max (pasif `/statusline`), Codex abonelik (pasif JSONL), OpenRouter (`/api/v1/key`), Yerel USD Bütçe |

### 2.2. Tek Bakışta Durum Makinesi (Session FSM & Tray Önceliği)

Birden fazla oturum aynı anda açık olduğunda Tray ikonu en yüksek öncelikli durumu gösterir:

| Öncelik | Durum | Anlamı | Görsel Sinyal (Tray / Bar) |
|---|---|---|---|
| 1 (En Acil) | `WAITING_INPUT` | Ajan soru soruyor veya izin/onay bekliyor | Dikkat rengi (amber/sarı) + belirgin rozet + masaüstü bildirimi |
| 2 | `ERROR` / `RATE_LIMITED` | Oturum hata aldı veya kota kilidine takıldı | Kırmızı uyarı rozeti |
| 3 | `RUNNING` | Ana ajan veya alt ajan aktif çalışıyor | Aktif renk (mavi/accent) + çalışma ikonu |
| 4 | `DONE_UNSEEN` | Oturum yeni tamamlandı, kullanıcı henüz bakmadı | Yeşil onay rozeti (Dashboard açılınca / zaman aşımıyla `IDLE` olur) |
| 5 | `IDLE` | Aktif veya okunmamış oturum yok | Sakin / nötr ikon |

### 2.3. İki Kanallı Telemetri (Pull + Push)

- **Pull Kanalı (Sıfır Kurulum Fallback):** Salt-okunur SQLite (`mode=ro`) veya JSONL okuma. `RUNNING` ve `DONE` durumlarını ek ayar gerektirmeden yakalar.
- **Push Kanalı (Resmî Hook Köprüsü — Kesin `WAITING_INPUT`):** `agy`, Claude Code, OpenCode gibi hook destekleyen harness'lar tek satırlık CLI çağrısıyla (`<app> event ...`) anlık `running` / `waiting_input` / `done` / `error` olaylarını yerel event kutusuna yazar. Böylece "soru bekliyor" durumu sıfır gecikmeyle ve tahmin yürütmeden yakalanır.

### 2.4. Linux Gösterge Katmanı (DE + Riceli WM Uyumu)

- **System Tray (Birincil):** GTK3/`AppIndicator3` çakışmasını önlemek için GTK4/GLib süreci içinde **saf `Gio.DBus` `org.kde.StatusNotifierItem` (SNI)** + minimal `com.canonical.dbusmenu` (sağ tık menüsü: Aç, Yenile, Çıkış). KDE, GNOME (AppIndicator), XFCE, COSMIC ve Waybar `tray` modülünde doğrudan çalışır.
- **Dashboard Penceresi:** Modüler GTK4 + Libadwaita arayüzü (`ui.py` monolitinden ayrılmış bileşenler). wlroots Wayland'da (Sway, Hyprland, Niri) `Gtk4LayerShell` ile köşe popover; GNOME/KDE/X11'de normal çerçevesiz/kayan pencere fallback'i.
- **WM Bar Formatter'ları (İkincil/Bonus):** Riceli barlar için özel metin/renk isteyen kullanıcılara `--format waybar|polybar|swaybar|json` CLI çıktıları.

### 2.5. Kurulum, Dağıtım ve Hook Deneyimi (Taslak — Onay Bekliyor)

Python + GTK4 uygulamalarında `PyGObject` (`gi`) C kütüphanelerine bağlı olduğu için saf `pip` kurulumları derleme hatası çıkarır; Flatpak ise host'taki `~/.local/share/opencode`, `~/.claude`, `~/.hermes` ve `gh` erişiminde sandbox sürtüşmesi yaratır. Bu nedenle Linux-native 3 adımlı kurulum hedeflenir:

1. **Kurulum (2 yol):**
   - **Repo'dan:** `git clone ... && cd agent-notifier && ./install.sh` (dağıtımı algılar, eksik GTK4/PyGObject sistem paketini söyler, `uv` Python 3.12 çekirdeği + `~/.local/bin/agent-notifier` başlatıcısını kurar).
   - **Tek komut (klonsuz):** `pipx install --system-site-packages git+https://github.com/eakgn/agent-notifier` (ileride PyPI + AUR/COPR paketleri).
2. **İlk Yapılandırma Sihirbazı (`agent-notifier setup`):**
   - Sistemde kurulu harness'ları (OpenCode, `agy`, Claude Code vb.) otomatik algılar, kullanıcının seçtiklerini `~/.config/agent-notifier/config.toml` içine yazar.
   - İsteğe bağlı olarak XDG Autostart (`~/.config/autostart/agent-notifier.desktop`) veya `systemd --user` servisi kurar.
   - `agent-notifier doctor` eksik sistem paketlerini/izinleri tanılar.
3. **Güvenli Hook Yönetimi (`agent-notifier hooks`):**
   - Hook'suz varsayılan mod her zaman çalışır (Pull fallback).
   - `agent-notifier hooks install <harness>` mevcut harness ayar dosyasının `.bak` yedeğini alır, mevcut kullanıcı hook'larını bozmadan `agent-notifier event ...` çağrısını ekler (`--dry-run` önizleme destekli).
   - `agent-notifier hooks print <harness>` elle kurulum için kopyalanabilir parça üretir; `agent-notifier hooks uninstall` / `agent-notifier uninstall` eklenen her şeyi temizce geri alır.

---

## 3. İteratif Geliştirme Planı (Spiral / XP)

Her döngü **çalışan, doğrulanabilir ve testli bir çıktı** üretir; riskli mimari kararlar ilk döngülerde çözülür.

### Sprint 0 — Temizlik, Güvenlik ve Çekirdek Kontratlar [TAMAMLANDI]
- **Risk odağı:** Antigravity ToS temizliği, yeni proje kimliği, kırık `.venv` ve katı v0.1.0 veri modelinin kırılması.
- **Çıktılar:**
  - [x] `v0.1.0` git etiketi oluşturuldu; `cloudcode-pa` / Google OAuth kodu tamamen kaldırıldı.
  - [x] `agent-notifier` (`0.2.0`) paket ve dizin düzeni (`src/agent_notifier/`) kuruldu.
  - [x] Normalize veri modeli (`SessionNode` + `parent_id`, `SessionState` FSM, `QuotaPool`, `Snapshot`), `HarnessAdapter` / `ProviderAdapter` arayüzleri, `enabled` filtreleme ve yerel event inbox (`agent-notifier event`).
  - [x] Çekirdek birim testleri (yalıtılmış, ağsız).

### Sprint 1 — MVP (Çalışan Tray + Glanceable FSM + İlk Adaptörler + Yeni Dashboard) [TAMAMLANDI]
- **Hedef:** Günlük gerçek kullanıma alınabilir ilk v2 sürümü.
- **Harness Adaptörleri:**
  - [x] **OpenCode:** `opencode.db` (`mode=ro`) + `session_v2.parent_id` ile ana oturum ↔ subagent ağacı (+ eski şema fallback).
  - [x] **Antigravity (`agy`):** Resmî hook olaylarıyla pasif `RUNNING` / `WAITING_INPUT` / `DONE` takibi (sıfır OAuth/API).
- **Provider Adaptörleri (Opt-in):**
  - [x] **OpenCode Go:** Resmî `GET https://opencode.ai/zen/go/v1/usage` + `opencode.db` havuz kırılımı & offline fallback ([OPENCODE_QUOTA_API_NOTES.md](OPENCODE_QUOTA_API_NOTES.md)).
  - [x] **GitHub Copilot:** `gh api` (yalnız kullanıcı işaretlerse aktif).
- **Arayüzler & Kurulum:**
  - [x] Native `Gio.DBus` SNI Tray ikonu (6 FSM durumuna göre dinamik SVG + ARGB32 `IconPixmap` + `dbusmenu` + `notify-send`).
  - [x] Modüler GTK4/Adw Dashboard (`linux/app.py`, `widgets.py`, `styles.py`): ana oturum → girintili subagent ağacı, yalnız seçili kotalar, ayarlar çekmecesi (`⚙`), `Gtk4LayerShell` + kayan pencere fallback.
  - [x] Bar çıktıları (`--format waybar|polybar|swaybar|tmux|plain|json`).
  - [x] `./install.sh`, `agent-notifier setup`, `doctor`, `.bak` yedekli `hooks print|install|uninstall`, `uninstall`.

### Sprint 1.1 — MVP Hata Düzeltme & Gerçek Doğrulama (Küçük Mikro-Adımlar)
> Ayrıntılı kök neden raporu: [docs/DEBUG_REPORT_2026-11-21.md](docs/DEBUG_REPORT_2026-11-21.md)

- [x] **Mikro-Adım 1 (Tray & Bar Tıklama / Yaşam Döngüsü):** `dashboard`/`ui` `--toggle`/`--close`, D-Bus tek-örnek uzaktan toggle, `Gtk4LayerShell` destroy/recreate, SNI kayıt yarışı, `app.py` active/idle yenileme aralığı, `_pending_refresh` kuyruğu ve hata loglama (**B1, B2, B6, B7, B8, B9**).
- [ ] **Mikro-Adım 2 (OpenCode v2 Eklenti + Pull/Push Uzlaşması):** `opencode_plugin_source` OpenCode v2 `export default { id, setup(ctx) }` sözleşmesine güncelleme + canlı doğrulama + `opencode_harness.py` zaman yarışı düzeltmesi (**B3, B12**).
- [ ] **Mikro-Adım 3 (Antigravity Native Plugin + Hook Sözleşmesi):** `~/.gemini/config/plugins/agent-notifier/{plugin.json,hooks.json}` paket yapısı + `PreToolUse` `decision` + `Stop` `terminationReason`/`modelName` desteği (**B4, B10**).
- [ ] **Mikro-Adım 4 (Go Canlı API Şema Doğrulama + Bildirim + Antigravity Kota UX):** `opencode.db` gerçek kimlik tablosu doğrulaması, `notifier.py` çift bildirim düzeltmesi ve Antigravity kota ToS açıklamasının `setup`/Dashboard'a işlenmesi (**B5, B11, B13**).

### Sprint 2 — Çoklu Harness & API Genişlemesi
- **Claude Code:** `~/.claude/projects/**/*.jsonl` + `/statusline` pasif `rate_limits` köprüsü + hook desteği.
- **Codex CLI:** `~/.codex/sessions/**/*.jsonl` (`token_count` + `rate_limits` pasif okuma).
- **OpenCode Console / Zen** (`/api/policies/current-budgets`) ve **OpenRouter** (`/api/v1/key`) + **Yerel USD Bütçe** sağlayıcısı.
- Tek komutluk opsiyonel hook kurulum yardımcısı (`<app> hooks install`).

### Sprint 3 — BYOK Harness'lar, Çoklu DE/WM Sertleştirme ve Dağıtım
- **Pi:** `~/.pi/agent/sessions/` dallanan JSONL okuyucu (`usage.cost.total` + subagent toplamı).
- **Hermes:** `~/.hermes/state.db` (`session_model_usage`, `mode=ro`).
- Polybar / Swaybar / tmux formatlayıcıları, `systemd --user` servisi + XDG autostart (`.desktop`), GNOME/KDE/XFCE/Hyprland/Sway uyumluluk matrisi ve güncel `README.md`.

---

## 4. Karar Takibi

### Onaylananlar
- [x] **Proje / Paket Adı:** `agent-notifier`
- [x] **Teknoloji Yığını:** OS-bağımsız saf Python `core/` + Linux `GTK4/Adw + Gio.DBus SNI` kabuğu.
- [x] **Sprint 1 (MVP) Adaptör Kesiti:** OpenCode + Antigravity (`agy` pasif hook) + OpenCode Go (resmî API) + GitHub Copilot (opt-in).
- [x] **Öneri Motoru:** Rafa kaldırıldı (v2.x+ sonrası).
- [x] **Repo Stratejisi:** Mevcut repo `v0.1.0` tag'i ile korunarak yerinde `agent-notifier` v2 (`0.2.0`)'ye güncellendi.
- [x] **Kurulum ve Hook Akışı (§2.5):** `./install.sh` + `agent-notifier setup` / `doctor` + `.bak` yedekli `hooks install|print|uninstall` + `uninstall`.
