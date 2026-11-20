# OpenCode Go & Console Kota API Notları

> Tarih: 2026-11-14  
> Amaç: `waybar-ai-quota` içindeki manuel OpenCode Go kalibrasyonunu (`collectors.py` / `collect_opencode_go`) resmî OpenCode v2 API'leri ile otomatikleştirmek ve varsa normal OpenCode (Console/Zen) bütçelerini eklemek için mimari özet.

## 1. Mevcut Durum ve Problem
- Şu an `src/waybar_ai_quota/collectors.py` (`collect_opencode_go`), `opencode.db` içindeki `session_message` / `session_v2` maliyetlerini yerel olarak toplayıp sabit `GO_POOLS` limitlerine bölüyor; farklar için `state.json` üzerinden manuel kalibrasyon (`calibrate`) gerekiyor.
- OpenCode v2 çekirdeğinde (`anomalyco/opencode` → `packages/core/src/usage-limits.ts`) Go ve Console bütçeleri artık doğrudan resmî HTTP uçlarından çekilebiliyor.

## 2. Doğrulanan Resmî Endpoint'ler

### A) OpenCode Go (Canlı Yüzde + Sıfırlanma Zamanı)
- **Endpoint:** `GET https://opencode.ai/zen/go/v1/usage`
- **Header'lar:**
  - `Authorization: Bearer <oauth_access_token veya api_key>`
  - `x-opencode-org-id: <workspaceID>`
- **Dönen JSON yapısı:**
  ```json
  {
    "usage": {
      "rolling": { "status": "ok | rate-limited", "percent": 12.5, "resetsAt": "..." },
      "weekly":  { "status": "ok | rate-limited", "percent": 8.0,  "resetsAt": "..." },
      "monthly": { "status": "ok | rate-limited", "percent": 4.2,  "resetsAt": "..." }
    }
  }
  ```
- **Önemli ayrıntılar:**
  - `percent` değeri **kullanılan yüzde**dir; kalan yüzde = `100 - percent` (ancak `status == "rate-limited"` ise `0%`).
  - Go aboneliği yoksa `403` döner.
  - Bu uç **toplam Go pencerelerini** (`rolling` 5 saat, `weekly`, `monthly`) ve `resetsAt` geri sayımını verir; model havuzu kırılımını (`flash`, `mid`, `argon`, `premium`) tek başına vermez.

### B) Normal OpenCode / Console (Zen Bütçeleri)
- **Endpoint:** `GET https://opencode.ai/api/policies/current-budgets`
- **Auth:** Console OAuth girişi (`opencode auth login opencode`). Yalnızca servis/API key ile `403` döner.
- **Dönen veri:** Çalışma alanı / üye bazlı **USD bütçeleri** (`limitDollars`, `spentDollars`, `scope`, `per`, `resetsAt`, varsa `models[].spentDollars`).
- **Kritik sınır:**
  - Bütçe/kredi kuralı tanımlı değilse `[]` döner.
  - `opencode/*-free` modelleri `$0` maliyetli olduğu için bu uçta **hiçbir zaman istek sayacı veya kalan hak görünmez** (LongCat 2.5 Free'nin 100 istek/gün limiti dahil). Free modeller yalnızca `429` / hız sınırlaması üzerinden anlaşılabilir.

### C) Yerel OpenCode Daemon Ucu (Alternatif, önerilmez)
- **Endpoint:** `GET http://127.0.0.1:<service_port>/api/experimental/usage/limits`
- **Neden ikincil:** OpenCode servisi kapalıyken erişilemez, yerel kimlik doğrulama (`401 UnauthorizedError`) ister ve `experimental` etiketlidir. Doğrudan bulut uçları + yerel `opencode.db` fallback'i Waybar için daha kararlıdır.

## 3. Kimlik Bilgisi Kaynağı (`opencode.db`)
- OpenCode v2 kimlik bilgilerini `auth.json` yerine `~/.local/share/opencode/opencode.db` içinde (`credential`, `control_account`, `workspace` tabloları) saklıyor.
- `collectors.py` zaten bu DB'yi `file:...?mode=ro` ile salt-okunur açıyor; `opencode-go` / `opencode` kimlik kaydı buradan okunup (token yenileme gerekirse OAuth refresh / `401` durumunda sessizce yerel DB hesabına düşerek) yukarıdaki uçlara `10 dk` önbellekle sorgu atılabilir.
- **Gizlilik (`SPEC.md §4`):** Token, `orgID` ve e-posta değerleri asla `state.json`, Waybar JSON veya loglara yazılmamalıdır.

## 4. Önerilen Hibrit Mimari (`waybar-ai-quota` için)
1. **Canlı toplam Go + Reset zamanı:** `zen/go/v1/usage` çağrısından `rolling` (5s), `weekly`, `monthly` kalan `%` ve `resetsAt` geri sayımlarını al (manuel kalibrasyon ihtiyacını kaldırır).
2. **Model havuzu kırılımı + Offline fallback:** Mevcut `opencode.db` SQLite maliyet toplayıcısı (`GO_POOLS`) havuz bazlı dağılımı göstermeye ve ağ/auth yoksa fallback olarak çalışmaya devam etsin.
3. **Opsiyonel Console (Zen) kartı:** `current-budgets` boş değilse ayrı "OpenCode Zen Bütçe" satırı göster; boşsa kartı gizle.
4. **Referans uygulama:** OpenCode 2 uyumlu [`@slkiser/opencode-quota`](https://github.com/slkiser/opencode-quota) aynı akışı (`/api/go/status` + `/zen/go/v1/usage`) kullanıyor; test/karşılaştırma için `npx @slkiser/opencode-quota@latest show` çalıştırılabilir.
