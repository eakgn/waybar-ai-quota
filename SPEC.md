# `agent-notifier` — Mimari Tasarım, Kontratlar ve Kısıtlar (`SPEC.md`)

> Sürüm: `0.2.0` (v2 — `waybar-ai-quota` v0.1.0 devamı)  
> Yol haritası: [PLAN.md](PLAN.md) · Canlı durum: [STATE.md](STATE.md)

---

## 1. Proje Amacı ve Kapsamı

`agent-notifier`, geliştiricinin başka bir işle ilgilenirken arka planda çalışan AI ajan oturumlarının durumunu **tek bakışta** (System Tray ikonu veya WM bar göstergesi üzerinden) izlemesini ve tıkladığında açılan modern **Dashboard** penceresinde:

1. Ana ajan ↔ alt ajan (**subagent**) hiyerarşisini ağaç düzeninde görmesini,
2. Yalnızca kendi seçtiği (**opt-in**) abonelik ve API kota sağlayıcılarını takip etmesini

sağlayan Linux-öncelikli, çekirdeği OS-bağımsız bir sistem tepsisi ve masaüstü bildirim aracıdır.

**Hedef Ortamlar (Linux-Öncelikli):**
- Masaüstü Ortamları (DE): **GNOME** (AppIndicator), **KDE Plasma**, **XFCE**, **COSMIC** (`org.kde.StatusNotifierItem` / SNI).
- Pencere Yöneticileri (WM) ve Riceli Barlar: **Sway**, **Hyprland**, **Niri**, **i3** — hem SNI `tray` modülü hem de `--format waybar|polybar|plain|json` bar çıktıları.
- Taşınabilirlik: `src/agent_notifier/` çekirdeği (modeller, FSM, adaptörler, olay kutusu, yapılandırma) tamamen standart kütüphane Python ile OS-bağımsız tutulur; platforma özgü kod `src/agent_notifier/linux/` altında izole edilir.

**Kapsam Dışı (Ertelendi):**
- Model öneri motoru (`recommender`) v2.0 MVP kapsamı dışındadır.

---

## 2. Güvenlik, Gizlilik ve ToS Anayasası (Değişmez)

1. **Sıfır OAuth/Cookie Scraping ve Sıfır Tersine-Mühendislik Özel API:**
   - Hiçbir servisin OAuth token'ı, çerezi veya özel/gizli uç noktası (`cloudcode-pa`, `v1internal:*` vb.) kopyalanmaz, yenilenmez veya sahte `User-Agent` ile çağrılmaz.
   - Antigravity (`agy`) gibi istemci-kilitli servisler yalnızca **pasif yerel gözlem / resmî hook olayları** ile izlenir; ağ çağrısı yapılmaz.
2. **Sıfır Gizli Veri Sızıntısı:**
   - Token, API anahtarı, e-posta, hesap kimliği veya tam kullanıcı yolu loglara, önbelleğe, bar JSON çıktısına veya `state.json` dosyasına yazılmaz.
   - Yapılandırma ve durum dosyaları XDG standartlarında (`~/.config/agent-notifier/`, `~/.local/share/agent-notifier/`, `~/.local/state/agent-notifier/`) `0600`/`0700` izinleriyle ve atomik yazımla yönetilir.
3. **Salt-Okunur Veritabanı Erişimi:**
   - Dış araçlara ait SQLite veritabanları (`opencode.db`, ileride `~/.hermes/state.db`) daima `file:...?mode=ro` URI'si, `PRAGMA query_only = 1` ve kısa timeout (≤2 sn) ile açılır; çalışan ajanı asla kilitlemez.

---

## 3. Çekirdek Veri Modeli ve Durum Makinesi (FSM)

### 3.1. İki Ortogonal Eksen: `HarnessAdapter` ≠ `ProviderAdapter`

- **`HarnessAdapter`:** Yerel oturumları, `parent_id` tabanlı ana/subagent ağacını, anlık oturum durumunu ve token/yerel maliyet bilgilerini toplar.
- **`ProviderAdapter`:** Abonelik veya API kotasını (kalan %, pencere sıfırlanma zamanı, havuz kırılımı) toplar.
- **Opt-in İlkesi:** `config.toml` içinde etkinleştirilmeyen adaptör **ne çalıştırılır ne de UI'da gösterilir**.

### 3.2. Tek Bakışta Oturum Durum Makinesi (`SessionState`)

Birden fazla oturum veya subagent açık olduğunda global durum (Tray ikonu + bar sınıfı) sabit öncelik sırasıyla belirlenir:

| Öncelik | Durum (`SessionState`) | Anlamı | Tray / Görsel Sinyal |
|---:|---|---|---|
| 1 | `waiting_input` | Ajan soru soruyor veya izin/onay bekliyor | Amber/sarı dikkat ikonu + masaüstü bildirimi |
| 2 | `error` | Oturum hata verdi | Kırmızı hata ikonu + bildirim |
| 3 | `rate_limited` | Kota / hız sınırına takıldı | Kırmızı/turuncu kilit ikonu + bildirim |
| 4 | `running` | Ana ajan veya alt ajan aktif çalışıyor | Mavi aktif ikon |
| 5 | `done_unseen` | Oturum yeni bitti, kullanıcı henüz bakmadı | Yeşil tamamlandı rozeti (Dashboard açılınca veya `ack` ile `idle` olur) |
| 6 | `idle` | Aktif veya görülmemiş oturum yok | Nötr / sakin ikon |

Alt ağaç yayılımı (Tree Rollup): Bir ana oturumun altındaki herhangi bir subagent `waiting_input` veya `error` durumundaysa, ana kart üzerinde de dikkat işareti görünür ve global Tray önceliğine doğrudan yansır.

### 3.3. İki Kanallı Telemetri (Pull + Push)

- **Pull (Sıfır Kurulum Fallback):** Harness'ın yerel SQLite/JSONL kayıtlarının salt-okunur okunması.
- **Push (Resmî Hook Olayları):** `agent-notifier event --harness <ad> --session <id> --state <durum> [--parent <id>] [--title ...] [--project ...] [--model ...] [--message ...]` komutu (<10 ms, ağ yok) yerel olay dosyasına atomik yazar. Özellikle `waiting_input` (izin/soru bekleme) ve Antigravity (`agy`) takibi bu kanaldan sıfır gecikmeyle beslenir.

---

## 4. Sprint 1 (MVP) Adaptörleri

- **Harness'lar:**
  1. `opencode`: `~/.local/share/opencode/opencode.db` (`mode=ro`). `session_v2.parent_id` sütunu varsa ana oturum → subagent ağacı kurar; yoksa düz liste fallback'i uygular.
  2. `antigravity`: Yalnızca pasif push olay kanalı (`agy` resmî hook'ları). Ağ veya OAuth çağrısı yapmaz.
- **Provider'lar (Opt-in):**
  1. `opencode_go`: Hibrit kaynak — resmî `GET https://opencode.ai/zen/go/v1/usage` (10 dk önbellek; token yalnız bellek içinde `opencode.db` salt-okunur okunarak kullanılır, hiçbir yere yazılmaz) + yerel `opencode.db` havuz kırılımı ve çevrimdışı fallback.
  2. `github_copilot`: `gh api /copilot_internal/user` (10 dk önbellek, kişisel alanlar ayıklanır).

---

## 5. Linux Masaüstü ve Kurulum Mimarisi

- **System Tray:** GTK4/GLib döngüsü içinde saf `Gio.DBus` ile `org.kde.StatusNotifierItem` (SNI) + minimal `com.canonical.dbusmenu` (sağ tık: Dashboard Aç/Kapat, Yenile, Okundu İşaretle, Çıkış). GTK3/`AppIndicator3` bağımlılığı yoktur.
- **İkonlar:** 5 durum için dinamik SVG ikon seti (XDG ikon dizinine yazılır) + `IconThemePath` desteklemeyen tepsiler için ARGB32 `IconPixmap` yedeği.
- **Dashboard:** Modüler GTK4 + Libadwaita (`linux/app.py`, `linux/widgets.py`, `linux/styles.py`). wlroots Wayland'da `Gtk4LayerShell` (sağ üst / yapılandırılabilir kenar); GNOME/KDE/X11'de normal pencere fallback'i. İçerik:
  - Üst başlık + durum özeti + Manuel Yenile + Ayarlar geçişi.
  - Oturum Ağacı: Ana oturum kartı + dikey bağlantı çizgili (tree rail) alt ajan satırları.
  - Yalnız seçili provider kota kartları.
  - Ayarlar görünümü: Kullanılan harness ve provider aç/kapa anahtarları (anında `config.toml` kaydeder ve yeniler).
- **Bar Çıktıları:** `agent-notifier bar --format waybar|polybar|plain|json` (ve eski Waybar ayarlarıyla uyum için `waybar-ai-quota waybar` köprüsü).
- **Kurulum ve Hook Yönetimi:**
  - `./install.sh` (dağıtım algılama + `uv` Python 3.12 `.venv` + `~/.local/bin/agent-notifier` + `pipx --system-site-packages` uyumlu `pyproject.toml`).
  - `agent-notifier setup` (otomatik algılama, `config.toml`, XDG Autostart `.desktop` / `systemd --user`).
  - `agent-notifier doctor` (GTK4, Adw, LayerShell, D-Bus, `notify-send`, `gh`, kurulu harness tanılama).
  - `agent-notifier hooks print|install|uninstall <harness>` (`--dry-run` + `.bak` yedeği + mevcut hook'ları bozmayan güvenli birleştirme).
  - `agent-notifier uninstall` (autostart, servis ve eklenen hook'ları temiz kaldırma).
