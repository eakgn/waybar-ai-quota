# waybar-ai-quota — Mimari Tasarım, Kararlar ve Kısıtlar (SPEC.md)

## 1. Proje Amacı ve Kapsamı
`waybar-ai-quota`, Fedora 43 (Sway / Wayland) ortamında çalışan ve **OpenCode CLI** üzerinden kullanılan çoklu yapay zeka aboneliklerini (**Antigravity / Google AI Pro**, **OpenCode Go**, **GitHub Copilot Student / Free / Pro**) gerçek zamanlı takip eden, aktif çalışan oturumları (session) gösteren ve kota durumuna göre görev zorluğu bazlı (**Kolay/Orta** vs **Zor/Mimari**) akıllı model geçiş önerileri sunan bir **Waybar eklentisi ve GTK4 Layer-Shell açılır kontrol panelidir (popover dashboard)**.

---

## 2. Alınan Mimari Kararlar (Onaylanan Özellikler)

### 2.1. Arayüz (UI) ve Deneyim
- **Waybar Modülü (`custom/ai-quota`):**
  - Mevcut Catppuccin Mocha (`#1e1e2e`, `#cdd6f4`, `#89b4fa`, `#a6e3a1`, `#f9e2af`, `#f38ba8`) hap (pill) tasarımına birebir uyumlu çalışır.
  - Bar üzerinde anlık durumu özetler:
    - Aktif oturum varsa: `⚡ [Model Kısa Adı] · AG %XX · Go %YY` (veya en kritik kota yüzdesi).
    - Boştaysa: `󰚩 AG %XX · Go %YY · GH %ZZ`.
    - Çift Antigravity çakışması veya kritik kota (<%15) durumunda CSS sınıfı (`warning`, `critical`, `conflict`) ile görsel uyarı verir.
- **Tıklanınca Açılan Panel (GTK4 + Libadwaita + Gtk4LayerShell Popover):**
  - Waybar modülüne sol tıklandığında ekranın sağ üst köşesinde (Waybar'ın hemen altında) Wayland `gtk4-layer-shell` protokolüyle pencere yöneticisiyle (Sway) çakışmayan, şık bir Catppuccin Mocha kontrol paneli açılır.
  - Panel bileşenleri:
    1. **Üst Bar (Header):** Uygulama başlığı, son güncellenme zamanı (`Az önce` / `HH:MM:SS`), **🔄 Manuel Yenile** butonu (canlı API + DB taramasını anında tetikler) ve **⚖️ Go Kalibrasyon** açılır alanı.
    2. **Aktif Oturumlar (Live Sessions) Kartı:**
       - Şu an çalışan (`in-flight`) veya en son aktif olan OpenCode oturumu: Proje dizini, oturum başlığı, aktif ajan (`build`, `plan`, `explore`, `rescue` vb.), kullanılan model, tur durumu ve oturumun toplam token / tahmini maliyet bilgisi.
       - **Çift Antigravity Çakışma Uyarısı:** Aynı anda birden fazla terminalde Antigravity modeli (`antigravity-*`) aktifse kırmızı/turuncu uyarı bandı gösterir.
    3. **Abonelik ve Kota Durumu Kartları (İlerleme Çubuklu):**
       - **Antigravity (Google AI Pro):**
         - `Claude (Opus/Sonnet)` (5 saatlik pencere) — Kalan % ve sıfırlanma geri sayımı (`resetTime`).
         - `Gemini Pro (Argon)` (Haftalık/Rezerv pencere) — Kalan % ve sıfırlanma süresi.
         - `Gemini Flash` — Kalan % ve sıfırlanma süresi.
         - Aktif rate-limit kilidi (`rateLimitResetTimes`) varsa geri sayım uyarısı.
       - **OpenCode Go ($10/ay Planı - 3 Pencere Takibi + Kalibrasyon):**
         - Resmi Go kurallarına göre: **5 Saatlik Pencere (Aylık limitin %20'si)**, **Haftalık Pencere (%50'si)**, **Aylık Pencere (%100'ü)**.
         - Model bazlı havuzlar: `DeepSeek V4.1 Flash` ($100/ay → 5s $20), `GLM-5.3-Flash` / `Kimi K2.7 Code` ($60/ay → 5s $12), `Gemini 4 Argon` ($30/ay → 5s $6), `DeepSeek V4 Pro` / `Claude Haiku 5.5` / `GPT 6 Luna` / `Grok 4.7` / `Kimi K3` ($15/ay → 5s $3).
         - `opencode.db` üzerinden pencere bazlı yerel harcama otomatik hesaplanır; kullanıcı dilerse `opencode.ai/console` ekranındaki gerçek yüzdeyi/tutarı girerek **Kalibrasyon (Offset)** uygulayabilir.
       - **GitHub Copilot (Student Dev Pack / Free / Pro):**
         - `gh api /copilot_internal/user` üzerinden canlı çekilen plan tipi (`Student`, `Pro`, `Free`), kalan `Chat`, `Completions` ve `Premium Interactions / AI Credits` yüzdeleri ve sıfırlanma tarihi.
    4. **Akıllı Model Öneri Motoru (Görev Zorluğuna Göre Fallback):**
       - **🟢 Kolay / Orta İşler (Günlük Kodlama, Refactor, Keşif):**
         1. *Birincil:* `opencode-go/deepseek-v4.1-flash` (Go Flash 5s/haftalık kotası müsaitse).
         2. *Go Flash Azaldığında / Dolduğunda:* `opencode-go/glm-5.3-flash` veya `opencode-go/kimi-k2.7-code` ($60'lık ikincil Go havuzları).
         3. *Go Tükendiğinde:* `github-copilot/gpt-4.1` (veya Copilot Student Auto/Base sınırsız havuzu) → `google/antigravity-gemini-3.8-flash` → `opencode/*-free`.
       - **🔥 Zor İşler (Mimari Planlama, Derin Hata Ayıklama / Rescue):**
         1. *Birincil:* `google/antigravity-claude-opus-5-5-high` veya `claude-sonnet` (Antigravity Claude kotası >%20 ise ve başka sekmede Antigravity çalışmıyorsa).
         2. *Antigravity Claude Dolu / Kilitli İse:* `opencode-go/deepseek-v4-pro#max` (Go Pro $15/ay, 5s $3 havuzu müsaitse).
         3. *Go Pro da Dolduysa:* `google/antigravity-gemini-pro` / `opencode-go/gemini-4-argon` (Haftalık rezerv / $30 Go havuzu) → `github-copilot/claude-sonnet-4` (Copilot Student AI Credits / Premium havuzu).
       - **📋 Tek Tıkla Model Kopyalama:** Her önerinin yanındaki kopyala butonuna tıklandığında `/model <sağlayıcı/model>` komutu `wl-copy` ile Wayland panosuna kopyalanır ve buton üzerinde `"✓ Kopyalandı"` geri bildirimi verilir.
    5. **Kritik Durum Masaüstü Bildirimleri (`notify-send`):**
       - Herhangi bir aktif sağlayıcının kotası **%15'in altına** düştüğünde veya **Çift Antigravity çakışması** tespit edildiğinde spam yapmadan (durum başına cooldown uygulanarak) `notify-send` ile masaüstü bildirimi gönderilir.

---

## 3. Yenileme (Refresh) ve Performans Optimizasyonu
- **Kademeli (Tiered) Yenileme Mimarisi:**
  1. **Yerel Veri Katmanı (SQLite `opencode.db` + `antigravity-accounts.json`):**
     - Ağ isteği yapmaz, salt-okunur (`mode=ro`) SQLite bağlantısı kullanır (`opencode` çalışırken DB kilitlemesi yaşatmaz).
     - Aktif oturum (`time_suspended IS NOT NULL`) varken **5 saniyede bir**, boştayken **15 saniyede bir** yenilenir.
  2. **Uzak API Katmanı (`gh api /copilot_internal/user` & Antigravity Canlı Kota):**
     - Rate-limit ve gereksiz ağ trafiğini önlemek için arka planda **10 dakikada bir** (600 sn) önbelleği günceller.
     - Açılır paneldeki **"🔄 Şimdi Yenile"** butonuna basıldığında veya sağ tıklandığında önbellek süresi beklenmeden **anında** tüm uzak ve yerel kaynaklar taze veriyi çeker.

---

## 4. Sistem Uyumluluğu, Güvenlik ve Gizlilik Kısıtları
1. **Fedora 43 & Python Ortam Uyumluluğu:**
   - Proje bağımlılıkları ve CLI/çekirdek motoru `uv` ile Python 3.12 (`.venv`) altında yönetilir.
   - GTK4 + Libadwaita + Gtk4LayerShell arayüz süreci, sistemde hazır bulunan `PyGObject` (`gi`) bağlamını C derleme hatası veya `sudo dnf install *-devel` zorunluluğu olmadan sorunsuz çalıştıracak hibrit başlatıcıya sahiptir (`.venv` içinde `gi` varsa onu, yoksa Fedora'nın yerel `/usr/bin/python3` GObject bağlamını kullanır).
2. **Sıfır Gizli Veri Sızıntısı (Public GitHub Repo Güvenliği):**
   - `antigravity-accounts.json`, `~/.local/share/opencode/opencode.db`, GitHub tokenları, e-posta adresleri, `refreshToken`, `projectId` veya kişisel dosya yolları **asla** kaynak koda, loglara, commit geçmişine veya dokümantasyona yazılmaz.
   - Tüm yollar XDG standartları (`~/.config/opencode`, `~/.local/share/opencode`, `~/.config/waybar-ai-quota/config.toml`) üzerinden dinamik çözülür.
   - Kalibrasyon ve bildirim geçmişi `~/.local/share/waybar-ai-quota/state.json` altında yerel tutulur ve `.gitignore` ile korunur.
3. **Veritabanı Güvenliği:**
   - `opencode.db` her zaman `file:...?mode=ro` (salt okunur URI) ve kısa timeout ile açılır; OpenCode CLI'ın yazma işlemlerini (`WAL` modu) asla bloklamaz.
