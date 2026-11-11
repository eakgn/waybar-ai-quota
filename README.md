# waybar-ai-quota

**Waybar + GTK4 panel for tracking AI subscription quotas on Linux (Sway / Wayland).**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Waybar](https://img.shields.io/badge/waybar-0.14%2B-89b4fa.svg)](https://github.com/Alexays/Waybar)
[![Catppuccin Mocha](https://img.shields.io/badge/theme-Catppuccin%20Mocha-cba6f7.svg)](https://catppuccin.com/)

`waybar-ai-quota`, OpenCode CLI üzerinden kullanılan **Antigravity (Google AI Pro)**, **OpenCode Go** ve **GitHub Copilot** kotalarını gerçek zamanlı izleyen; aktif OpenCode oturumlarını gösteren ve görev zorluğuna göre akıllı model önerileri sunan bir Waybar modülü + GTK4 Layer-Shell panelidir.

Tüm veriler yerel olarak, salt-okunur biçimde okunur. Token, e-posta, hesap kimliği veya proje kimliği hiçbir zaman kaynak koda, loglara ya da dışarıya yazılmaz.

---

## İçindekiler

- [Özellikler](#özellikler)
- [Mimari ve Veri Akışı](#mimari-ve-veri-akışı)
- [Gereksinimler](#gereksinimler)
- [Kurulum](#kurulum)
- [Waybar Entegrasyonu](#waybar-entegrasyonu)
- [CLI Komutları](#cli-komutları)
- [Yapılandırma](#yapılandırma)
- [Go Kalibrasyonu](#go-kalibrasyonu)
- [Gizlilik ve Güvenlik](#gizlilik-ve-güvenlik)
- [Testler](#testler)
- [Proje Yapısı](#proje-yapısı)
- [English Summary](#english-summary)
- [Lisans](#lisans)

---

## Özellikler

### Canlı OpenCode oturum takibi
- `opencode.db` (SQLite) üzerinden arşivlenmemiş oturumlar **salt-okunur** (`mode=ro`) okunur; OpenCode'un WAL yazmalarını asla kilitlemez.
- Çalışan (`in-flight`) oturumun projesi, başlığı, aktif ajanı (`build`, `plan`, `explore`, `rescue`…), modeli, token sayısı ve tahmini maliyeti gösterilir.
- Model alanı boş oturumlarda son assistant mesajındaki model otomatik çözümlenir.

### Kota takibi
- **Antigravity (Google AI Pro):** Claude (Opus/Sonnet, 5 saatlik pencere), Gemini Pro (Argon, haftalık rezerv) ve Gemini Flash grupları; kalan yüzde, sıfırlanma geri sayımı ve aktif rate-limit kilidi.
- **OpenCode Go (3 pencere):** 5 saatlik (%20), haftalık (%50) ve aylık (%100) USD limitleri; Flash / Mid / Argon / Premium model havuzları ayrı ayrı hesaplanır.
- **GitHub Copilot (Student / Free / Pro):** `gh api /copilot_internal/user` üzerinden plan tipi, Chat, Completions ve Premium Interactions / AI Credits yüzdeleri.

### Akıllı model öneri motoru
Görev zorluğuna göre iki ayrı sıralı öneri listesi üretir:

| Görev | Birincil | Yedekler |
|---|---|---|
| **Kolay / Orta** | OpenCode Go Flash | Go Mid (GLM / Kimi) → Copilot GPT-4.1 → Antigravity Flash → ücretsiz model |
| **Zor / Mimari** | Antigravity Claude (çakışma yoksa) | Go Pro `#max` → Antigravity Gemini Pro / Go Argon → Copilot Claude Sonnet → ücretsiz model |

Her öneri somut bir gerekçe (kalan yüzde, pencere durumu, çakışma uyarısı) ile birlikte gelir.

### Tek tıkla `/model` kopyalama
Paneldeki kopyala düğmesi `/model <sağlayıcı/model>` komutunu `wl-copy` ile Wayland panosuna yazar ve `✓ Kopyalandı` geri bildirimi gösterir.

### Çift Antigravity çakışma uyarısı
Aynı anda birden fazla terminalde `antigravity-*` modeli çalışıyorsa bar `conflict` sınıfına geçer, panelde turuncu uyarı bandı çıkar ve `notify-send` bildirimi gönderilir (cooldown'lı).

### Go kalibrasyonu
Yerel `opencode.db` harcaması ile `opencode.ai/console` ekranı arasında fark varsa üç yöntemle düzeltilir:

- `--remaining-pct`: Konsolda görünen kalan yüzdeyi mutlak olarak yaz.
- `--offset-pct`: Yerel yüzdeden sabit sapma düş.
- `--spent-usd`: Yerel harcamaya ek USD ekle.

### Catppuccin Mocha arayüzü
Waybar hap (pill) tasarımıyla uyumlu `normal` / `active` / `warning` / `critical` / `conflict` durum sınıfları ve GTK4 + Libadwaita + `gtk4-layer-shell` ile açılan sağ üst panel.

### Kademeli yenileme
- Yerel katman: aktif oturumda **5 sn**, boşta **15 sn**.
- Uzak API katmanı: **10 dakika** önbellek; paneldeki **🔄 Şimdi Yenile** veya sağ tık ile anında tazelenir.

---

## Mimari ve Veri Akışı

```
opencode.db (ro) ──┐
antigravity-*.json ┼──► collectors ──► models ──► recommender ──► CLI ──► Waybar JSON
gh api (cache)  ───┘                                      │
                                                          └──► GTK4 Layer-Shell panel
state.json (0600) ── kalibrasyon + bildirim cooldown
```

- Çekirdek paket **yalnızca Python standart kütüphanesini** kullanır.
- GTK4 paneli ayrı bir süreçte çalışır; `.venv` içinde `gi` yoksa sistem Python'una otomatik düşer (derleme gerektirmez).
- Tüm yollar XDG standartlarından çözülür ve ortam değişkenleriyle geçersiz kılınabilir.

---

## Gereksinimler

### Ortak
- Linux + Wayland (Sway önerilir; diğer wlroots tabanlı compositor'lar da çalışır)
- Waybar **0.14+**
- `uv` (Python 3.12 sanal ortamı için)
- `gh` CLI (GitHub Copilot kotası için, opsiyonel)
- `wl-clipboard` (`wl-copy` için, opsiyonel)
- `libnotify` (`notify-send` için, opsiyonel)
- Bir Nerd Font (bar ikonlarının düzgün görünmesi için önerilir)

### Fedora
```bash
sudo dnf install waybar python3-gobject gtk4 libadwaita gtk4-layer-shell \
                 wl-clipboard libnotify gh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Arch Linux
```bash
sudo pacman -S waybar python-gobject gtk4 libadwaita gtk4-layer-shell \
               wl-clipboard libnotify github-cli uv
```

### Ubuntu / Debian
```bash
sudo apt install waybar python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
                 gir1.2-gtk4-layer-shell-0 wl-clipboard libnotify-bin gh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> **Not:** GTK4 paneli sistem Python'unda `gi` arar. `install.sh` bu bağımlılığı kurmaz; yukarıdaki paketlerden birini kurmanız yeterlidir.

---

## Kurulum

```bash
git clone https://github.com/eakgn/waybar-ai-quota.git waybar-ai-quota
cd waybar-ai-quota
./install.sh
```

`install.sh` şunları yapar:

1. `uv venv --python 3.12 .venv`
2. `uv pip install -e .`
3. `~/.local/bin/waybar-ai-quota` sembolik bağını `bin/waybar-ai-quota` dosyasına yönlendirir.
4. Örnek yapılandırmayı `~/.config/waybar-ai-quota/config.toml` altına kopyalar (dosya yoksa).

`~/.local/bin` PATH üzerinde değilse:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Doğrulama:

```bash
waybar-ai-quota --version
waybar-ai-quota waybar
```

İkinci komut tek satır JSON üretmelidir.

---

## Waybar Entegrasyonu

### `~/.config/waybar/config.jsonc`

`modules-right` listesinin **en başına** `custom/ai-quota` ekleyin ve modül tanımını yapın:

```jsonc
{
    "modules-right": [
        "custom/ai-quota",
        "cpu",
        "memory",
        "network",
        "pulseaudio",
        "battery",
        "tray"
    ],

    "custom/ai-quota": {
        "exec": "/home/<kullanici>/.local/bin/waybar-ai-quota waybar",
        "return-type": "json",
        "interval": 5,
        "signal": 8,
        "on-click": "/home/<kullanici>/.local/bin/waybar-ai-quota ui --toggle",
        "on-click-right": "/home/<kullanici>/.local/bin/waybar-ai-quota status --refresh >/dev/null 2>&1 && pkill -RTMIN+8 waybar",
        "tooltip": true
    }
}
```

`~` yerine tam yol kullanmak Waybar'ın `exec` ortamında daha güvenilirdir.

### `~/.config/waybar/style.css`

Ortak hap seçicisine `#custom-ai-quota` ekleyin:

```css
#workspaces, #window, #clock, #pulseaudio, #network, #cpu, #memory,
#battery, #tray, #custom-power, #custom-ai-quota {
    background-color: #1e1e2e;
    padding: 0 12px;
    margin: 3px 2px;
    border-radius: 15px;
}

#custom-ai-quota {
    color: #cdd6f4;
}

#custom-ai-quota.normal {
    color: #a6e3a1;
}

#custom-ai-quota.active {
    color: #89b4fa;
    background-color: #313244;
}

#custom-ai-quota.warning {
    color: #f9e2af;
}

#custom-ai-quota.critical {
    color: #11111b;
    background-color: #f38ba8;
    animation: blink 0.5s alternate infinite;
}

#custom-ai-quota.conflict {
    color: #11111b;
    background-color: #fab387;
    animation: blink 0.8s alternate infinite;
}

@keyframes blink {
    to {
        background-color: #cdd6f4;
        color: #11111b;
    }
}
```

Yeniden yükleme:

```bash
pkill -SIGUSR2 waybar
```

### Davranış

| Durum | Sınıf | Anlam |
|---|---|---|
| Kota sağlıklı | `normal` | En düşük kota ≥ kritik eşiğin 2 katı |
| Aktif oturum | `active` | Bar metni model adını gösterir |
| Kota azalıyor | `warning` | En düşük kota kritik eşiğin altında ama > kritik |
| Kota kritik | `critical` | En düşük kota `< critical_pct` (varsayılan %15) |
| Çift Antigravity | `conflict` | Aynı anda ≥2 Antigravity oturumu |

---

## CLI Komutları

| Komut | Açıklama |
|---|---|
| `waybar-ai-quota waybar` | Waybar için tek satır JSON (`text`, `tooltip`, `class`, `percentage`). Ağ isteği yapmaz. |
| `waybar-ai-quota status` | Tam durum JSON'u (oturumlar, kotalar, öneriler, bildirimler). |
| `waybar-ai-quota status --refresh` | Uzak API'leri önbelleği beklemeden tazeler. |
| `waybar-ai-quota status --no-notify` | Bildirim göndermeden durum yazdırır. |
| `waybar-ai-quota ui` | GTK4 Layer-Shell panelini açar. |
| `waybar-ai-quota ui --toggle` | Panel açıksa kapatır, kapalıysa açar. |
| `waybar-ai-quota calibrate` | Mevcut kalibrasyonu ve havuz yüzdelerini gösterir. |
| `waybar-ai-quota calibrate --pool flash --remaining-pct 85` | Flash havuzunu konsoldaki değere sabitler. |
| `waybar-ai-quota calibrate --pool total --offset-pct 5` | Genel toplamdan %5 sapma düşer. |
| `waybar-ai-quota calibrate --pool mid --spent-usd 1.25` | Mid havuzuna 1.25 USD ek harcama yazar. |
| `waybar-ai-quota calibrate --reset` | Tüm kalibrasyonu sıfırlar. |

`--pool` değerleri: `flash`, `mid`, `argon`, `premium`, `total`.

Çıktı örneği:

```json
{
  "text": "🤖 AG %82 · Go %94 · GH %71",
  "tooltip": "<b>Oturum:</b> boşta\n...",
  "class": "normal",
  "percentage": 71
}
```

---

## Yapılandırma

Şablon: [`config.example.toml`](config.example.toml)

```bash
mkdir -p ~/.config/waybar-ai-quota
cp config.example.toml ~/.config/waybar-ai-quota/config.toml
chmod 600 ~/.config/waybar-ai-quota/config.toml
```

Öne çıkan ayarlar:

| Bölüm | Anahtar | Varsayılan | Açıklama |
|---|---|---|---|
| `refresh` | `local_active_seconds` | `5` | Aktif oturumda yerel yenileme |
| `refresh` | `local_idle_seconds` | `15` | Boşta yerel yenileme |
| `refresh` | `remote_seconds` | `600` | Uzak API önbelleği |
| `thresholds` | `critical_pct` | `15` | Kritik kota eşiği |
| `thresholds` | `antigravity_primary_pct` | `20` | Zor işlerde Claude'un birincil olma eşiği |
| `notifications` | `cooldown_seconds` | `1800` | Aynı uyarı için bekleme süresi |
| `waybar` | `max_model_chars` | `22` | Bar metnindeki model adı sınırı |
| `ui` | `gtk_python` | `/usr/bin/python3` | GTK4 panelini çalıştıran yorumlayıcı |

### Ortam değişkenleri (test / özel kurulum)

| Değişken | Etki |
|---|---|
| `WAYBAR_AI_QUOTA_CONFIG` | `config.toml` yolu |
| `WAYBAR_AI_QUOTA_STATE` | `state.json` yolu |
| `WAYBAR_AI_QUOTA_DATA_DIR` | Veri dizini |
| `WAYBAR_AI_QUOTA_CACHE_DIR` | Önbellek dizini |
| `WAYBAR_AI_QUOTA_OPENCODE_DB` | `opencode.db` yolu |
| `WAYBAR_AI_QUOTA_ACCOUNTS` | `antigravity-accounts.json` yolu |
| `WAYBAR_AI_QUOTA_COPILOT_CACHE` | Copilot önbellek dosyası |
| `WAYBAR_AI_QUOTA_PYTHON` | Panel için Python yorumlayıcısı |

### Antigravity OAuth istemcisi

Depoda **hiçbir istemci anahtarı tutulmaz**. `client_id` / `client_secret` şu sırayla çözülür:

1. `WAYBAR_AI_QUOTA_ANTIGRAVITY_CLIENT_ID` / `..._CLIENT_SECRET`
2. `config.toml` → `[antigravity]`
3. Kurulu `opencode-antigravity-auth` eklentisinin `constants.js` dosyası (yalnızca okunur)

Hiçbiri yoksa yalnızca yerel `cachedQuota` verisi kullanılır; canlı yenileme sessizce atlanır.

---

## Go Kalibrasyonu

Yerel harcama `opencode.db` içindeki assistant mesajlarının `cost` alanından hesaplanır. Konsol ile fark varsa:

```bash
# Konsolda "kalan %85" görüyorsanız:
waybar-ai-quota calibrate --pool flash --remaining-pct 85

# Yerel hesap %5 fazla gösteriyorsa:
waybar-ai-quota calibrate --pool total --offset-pct 5

# Panelden yapılan ama DB'ye yansımayan harcama:
waybar-ai-quota calibrate --pool premium --spent-usd 2.40

# Sıfırla:
waybar-ai-quota calibrate --reset
```

Kalibrasyon `~/.local/share/waybar-ai-quota/state.json` dosyasında `0600` izinleriyle atomik yazılır. Waybar'ı anında yenilemek için:

```bash
pkill -RTMIN+8 waybar
```

---

## Gizlilik ve Güvenlik

- `opencode.db` her zaman `file:...?mode=ro` URI'si ve kısa timeout ile açılır.
- `antigravity-accounts.json` yalnızca okunur; token yenileme gerektiğinde atomik güncellenir ve dosya izinleri korunur.
- `refreshToken`, e-posta, `projectId`, hesap kimliği, API anahtarı **hiçbir zaman** loglanmaz veya JSON çıktısına eklenmez.
- `state.json` ve Copilot önbelleği `0600` izinleriyle yazılır; `.gitignore` ile korunur.
- Depoda hiçbir gerçek token, anahtar veya kişisel veri bulunmaz.
- Uzak istekler yalnızca sabit HTTPS adreslerine yapılır; kullanıcı verisi gönderilmez.

---

## Testler

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Testler geçici bir SQLite veritabanı, sahte Antigravity kota dosyası ve sahte Copilot önbelleği kullanır; gerçek hesap verisine veya ağa dokunmaz.

---

## Proje Yapısı

```
waybar-ai-quota/
├── bin/waybar-ai-quota          # Hibrit başlatıcı (CLI + GTK toggle)
├── src/waybar_ai_quota/
│   ├── cli.py                   # Alt komutlar, Waybar JSON üretimi
│   ├── collectors.py            # Salt-okunur veri toplayıcılar
│   ├── config.py                # XDG yolları, TOML, state.json, kalibrasyon
│   ├── models.py                # Dataclass veri modelleri
│   ├── notifier.py              # notify-send + cooldown
│   ├── recommender.py           # Görev zorluğu bazlı öneri motoru
│   └── ui.py                    # GTK4 + Libadwaita + Layer-Shell paneli
├── tests/test_core.py           # Birim testleri
├── config.example.toml
├── install.sh
├── LICENSE
└── README.md
```

---

## English Summary

**waybar-ai-quota** is a Waybar module and GTK4 Layer-Shell popover for Linux (Sway/Wayland) that tracks:

- **Live OpenCode sessions** from `opencode.db` (read-only, WAL-safe): project, title, agent, model, tokens, cost, in-flight state.
- **Antigravity (Google AI Pro)** quotas: Claude, Gemini Pro (Argon), Gemini Flash, reset countdowns and rate-limit locks.
- **OpenCode Go** three-window budgets (5-hour / weekly / monthly) across Flash, Mid, Argon and Premium model pools, with optional console calibration.
- **GitHub Copilot** (Student / Free / Pro) Chat, Completions and Premium Interactions quotas via `gh api`.

It also provides a **task-difficulty-aware model recommendation engine**, one-click `/model` copy, dual-Antigravity conflict warnings, `notify-send` alerts with cooldown, and a Catppuccin Mocha UI that matches a standard Waybar pill layout.

### Quick start

```bash
git clone <REPO_URL> waybar-ai-quota
cd waybar-ai-quota
./install.sh
```

Add the `custom/ai-quota` module to `~/.config/waybar/config.jsonc`, add the styles from this README to `~/.config/waybar/style.css`, then reload Waybar with `pkill -SIGUSR2 waybar`.

### CLI

```bash
waybar-ai-quota waybar              # one-line JSON for Waybar
waybar-ai-quota status --refresh    # full status, force remote refresh
waybar-ai-quota ui --toggle         # open/close the GTK4 panel
waybar-ai-quota calibrate --pool flash --remaining-pct 85
```

### Privacy

No tokens, emails, account IDs or project IDs are ever stored in the repository, logs or JSON output. All state lives under XDG directories with `0600` permissions. The package uses only the Python standard library; the GTK4 UI runs in a separate process and falls back to the system Python automatically.

### Requirements

Waybar 0.14+, Python 3.12 with `uv`, GTK4 + Libadwaita + `gtk4-layer-shell`, and optionally `gh`, `wl-clipboard` and `libnotify`. See the per-distribution commands above.

---

## Lisans / License

[MIT](LICENSE) © waybar-ai-quota contributors
