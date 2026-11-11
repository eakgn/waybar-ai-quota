"""Görev zorluğuna göre model öneri motoru.

İki kategori üretir: `easy_medium` (kolay/orta işler) ve `hard`
(mimari planlama / derin hata ayıklama). Her öneri canlı kota yüzdelerine
ve aktif oturum durumuna göre sıralanır; gerekçe Türkçe ve somuttur.
"""

from __future__ import annotations

from typing import Any

from . import config as cfg_mod
from . import models as m

BADGE_PRIMARY = "Önerilen"
BADGE_ALT = "Alternatif"
BADGE_LAST = "Son Çare / Ücretsiz"

MODEL_FLASH = "opencode-go/deepseek-v4.1-flash"
MODEL_GLM = "opencode-go/glm-5.3-flash"
MODEL_KIMI = "opencode-go/kimi-k2.7-code"
MODEL_GO_PRO = "opencode-go/deepseek-v4-pro#max"
MODEL_GO_ARGON = "opencode-go/gemini-4-argon"
MODEL_AG_CLAUDE = "google/antigravity-claude-opus-5-5-high"
MODEL_AG_GEMINI_PRO = "google/antigravity-gemini-pro"
MODEL_AG_GEMINI_FLASH = "google/antigravity-gemini-3.8-flash"
MODEL_COPILOT_GPT = "github-copilot/gpt-4.1"
MODEL_COPILOT_SONNET = "github-copilot/claude-sonnet-4"
MODEL_FREE = "opencode/deepseek-v4.1-flash-free"


def _pct_text(value: float | None) -> str:
    return f"%{value:.0f}" if isinstance(value, (int, float)) else "bilinmiyor"


def _ag_group(status: m.AntigravityStatus, key: str) -> m.AntigravityGroup | None:
    return status.group(key)


def _ag_effective(group: m.AntigravityGroup | None) -> float | None:
    if group is None:
        return None
    if group.rate_limited:
        return 0.0
    return group.remaining_pct


def _go_pool(status: m.GoStatus, key: str) -> m.GoPoolStatus | None:
    return status.pool(key)


def _pool_pct(pool: m.GoPoolStatus | None) -> float | None:
    return pool.remaining_pct if pool else None


def _copilot_quota(status: m.CopilotStatus, key: str) -> m.CopilotQuota | None:
    return status.quota(key)


def _copilot_pct(status: m.CopilotStatus, key: str) -> float | None:
    quota = _copilot_quota(status, key)
    if quota is None:
        return None
    if quota.unlimited:
        return 100.0
    if not quota.has_quota:
        return None
    return quota.percent_remaining


def _best_copilot_base(status: m.CopilotStatus) -> tuple[float | None, str]:
    candidates = [
        ("completions", _copilot_pct(status, "completions")),
        ("chat", _copilot_pct(status, "chat")),
    ]
    available = [(key, pct) for key, pct in candidates if isinstance(pct, (int, float))]
    if not available:
        return None, "chat"
    key, pct = max(available, key=lambda item: item[1])
    return pct, key


def _copilot_premium(status: m.CopilotStatus) -> float | None:
    return _copilot_pct(status, "premium_interactions")


def _suggest(
    *,
    title: str,
    model_id: str,
    reason: str,
    remaining_pct: float | None,
    badge: str,
) -> m.Suggestion:
    return m.Suggestion(
        rank=0,
        title=title,
        model_id=model_id,
        copy_command=f"/model {model_id}",
        reason=reason,
        remaining_pct=round(remaining_pct, 1) if isinstance(remaining_pct, (int, float)) else None,
        badge=badge,
    )


def _finalize(items: list[m.Suggestion]) -> list[m.Suggestion]:
    """Tekrarlanan modelleri temizler, sıra numarası ve son madde rozetini atar."""
    seen: set[str] = set()
    unique: list[m.Suggestion] = []
    for item in items:
        if item.model_id in seen:
            continue
        seen.add(item.model_id)
        unique.append(item)
    for index, item in enumerate(unique, start=1):
        item.rank = index
    if unique:
        unique[-1].badge = BADGE_LAST
        if len(unique) == 1:
            unique[0].badge = BADGE_PRIMARY
    return unique


# ---------------------------------------------------------------------------
# Kolay / Orta işler
# ---------------------------------------------------------------------------


def _easy_medium(
    sessions: m.SessionStatus,
    antigravity: m.AntigravityStatus,
    go: m.GoStatus,
    copilot: m.CopilotStatus,
    min_go_pct: float,
) -> list[m.Suggestion]:
    flash = _go_pool(go, "flash")
    mid = _go_pool(go, "mid")
    flash_pct = _pool_pct(flash)
    mid_pct = _pool_pct(mid)
    ag_flash_pct = _ag_effective(_ag_group(antigravity, "gemini-flash"))
    copilot_pct, copilot_key = _best_copilot_base(copilot)
    any_antigravity_active = sessions.dual_antigravity_conflict

    items: list[m.Suggestion] = []
    flash_ok = isinstance(flash_pct, (int, float)) and flash_pct >= min_go_pct
    mid_ok = isinstance(mid_pct, (int, float)) and mid_pct >= min_go_pct

    if flash_ok:
        items.append(
            _suggest(
                title="DeepSeek V4.1 Flash",
                model_id=MODEL_FLASH,
                reason=(
                    f"Go Flash havuzunda {_pct_text(flash_pct)} kota kaldı; kolay/orta işler için "
                    "en ucuz ve en hızlı birincil seçenek."
                ),
                remaining_pct=flash_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif mid_ok:
        items.append(
            _suggest(
                title="GLM 5.3 Flash",
                model_id=MODEL_GLM,
                reason=(
                    f"Go Flash {_pct_text(flash_pct)} seviyesinde; kolay/orta işleri "
                    f"Go Mid havuzuna ({_pct_text(mid_pct)}) kaydır. Kimi K2.7 Code aynı havuzu paylaşır."
                ),
                remaining_pct=mid_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif isinstance(copilot_pct, (int, float)):
        items.append(
            _suggest(
                title="GitHub Copilot GPT-4.1",
                model_id=MODEL_COPILOT_GPT,
                reason=(
                    f"Go Flash {_pct_text(flash_pct)}, Go Mid {_pct_text(mid_pct)}; kolay işler için "
                    f"Copilot havuzuna geç ({_pct_text(copilot_pct)})."
                ),
                remaining_pct=copilot_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif isinstance(ag_flash_pct, (int, float)) and ag_flash_pct >= min_go_pct and not any_antigravity_active:
        items.append(
            _suggest(
                title="Antigravity Gemini 3.8 Flash",
                model_id=MODEL_AG_GEMINI_FLASH,
                reason=(
                    f"Go havuzları daraldı (Flash {_pct_text(flash_pct)}, Mid {_pct_text(mid_pct)}); "
                    f"Antigravity Flash {_pct_text(ag_flash_pct)} ile devam et."
                ),
                remaining_pct=ag_flash_pct,
                badge=BADGE_PRIMARY,
            )
        )
    else:
        known_go = [pct for pct in (flash_pct, mid_pct) if isinstance(pct, (int, float))]
        if known_go:
            reason = (
                f"Go Flash {_pct_text(flash_pct)}, Go Mid {_pct_text(mid_pct)}; tüm ücretli havuzlar "
                "daraldı, ücretsiz modelle ilerle."
            )
        else:
            reason = "Go kota verisi okunamadı; ücretsiz modelle ilerle ve durumu yenile."
        items.append(
            _suggest(
                title="DeepSeek V4.1 Flash Free",
                model_id=MODEL_FREE,
                reason=reason,
                remaining_pct=None,
                badge=BADGE_PRIMARY,
            )
        )

    if mid_ok:
        items.append(
            _suggest(
                title="GLM 5.3 Flash",
                model_id=MODEL_GLM,
                reason=(
                    f"İkincil Go havuzunda {_pct_text(mid_pct)} kota mevcut; Flash kotası tükendiğinde "
                    "refactor ve keşif işlerini burada sürdür. Kimi K2.7 Code aynı havuzu paylaşır."
                ),
                remaining_pct=mid_pct,
                badge=BADGE_ALT,
            )
        )
    if isinstance(copilot_pct, (int, float)):
        items.append(
            _suggest(
                title="GitHub Copilot GPT-4.1",
                model_id=MODEL_COPILOT_GPT,
                reason=(
                    f"Copilot {copilot_key} kotası {_pct_text(copilot_pct)}; Go havuzları daraldığında "
                    "günlük kodlama için sınırsız/ucuz yedek."
                ),
                remaining_pct=copilot_pct,
                badge=BADGE_ALT,
            )
        )
    if isinstance(ag_flash_pct, (int, float)):
        conflict_note = (
            " DİKKAT: çift Antigravity çakışması var, geçmeden önce diğer sekmeyi kapat."
            if any_antigravity_active
            else ""
        )
        items.append(
            _suggest(
                title="Antigravity Gemini 3.8 Flash",
                model_id=MODEL_AG_GEMINI_FLASH,
                reason=(
                    f"Antigravity Flash kotası {_pct_text(ag_flash_pct)}; hızlı keşif ve küçük "
                    f"düzenlemeler için uygun{conflict_note}."
                ),
                remaining_pct=ag_flash_pct,
                badge=BADGE_ALT,
            )
        )
    items.append(
        _suggest(
            title="DeepSeek V4.1 Flash Free",
            model_id=MODEL_FREE,
            reason="Tüm ücretli Go/Copilot havuzları tükendiğinde sıfır maliyetli son çare.",
            remaining_pct=None,
            badge=BADGE_LAST,
        )
    )
    return _finalize(items)


# ---------------------------------------------------------------------------
# Zor işler
# ---------------------------------------------------------------------------


def _hard(
    sessions: m.SessionStatus,
    antigravity: m.AntigravityStatus,
    go: m.GoStatus,
    copilot: m.CopilotStatus,
    min_go_pct: float,
    ag_primary_pct: float,
) -> list[m.Suggestion]:
    claude = _ag_group(antigravity, "claude")
    claude_pct = claude.remaining_pct if claude else None
    gemini_pro_pct = _ag_effective(_ag_group(antigravity, "gemini-pro"))
    go_pro = _go_pool(go, "premium")
    go_pro_pct = _pool_pct(go_pro)
    argon_pct = _pool_pct(_go_pool(go, "argon"))
    premium_copilot = _copilot_premium(copilot)
    conflict = sessions.dual_antigravity_conflict
    conflict_count = len(sessions.conflict_sessions)

    claude_locked = bool(claude and claude.rate_limited)
    claude_ok = (
        isinstance(claude_pct, (int, float))
        and claude_pct >= ag_primary_pct
        and not claude_locked
        and not conflict
    )
    go_pro_ok = isinstance(go_pro_pct, (int, float)) and go_pro_pct >= min_go_pct

    items: list[m.Suggestion] = []

    if claude_ok:
        items.append(
            _suggest(
                title="Antigravity Claude Opus 5.5",
                model_id=MODEL_AG_CLAUDE,
                reason=(
                    f"Antigravity Claude kotası {_pct_text(claude_pct)} ve başka pencerede Antigravity "
                    "çalışmıyor; mimari planlama ve derin hata ayıklama için en güçlü seçenek."
                ),
                remaining_pct=claude_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif go_pro_ok:
        reason = (
            f"Go Pro havuzunda {_pct_text(go_pro_pct)} kaldı; Claude tarafı "
            f"{_pct_text(claude_pct)}"
        )
        if conflict:
            reason += f" ve {conflict_count} Antigravity oturumu çakışıyor"
        reason += ". Zor iş için Go Pro en güvenli birincil seçenek."
        items.append(
            _suggest(
                title="DeepSeek V4 Pro #max",
                model_id=MODEL_GO_PRO,
                reason=reason,
                remaining_pct=go_pro_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif (
        isinstance(gemini_pro_pct, (int, float))
        and gemini_pro_pct >= min_go_pct
        and not conflict
    ):
        items.append(
            _suggest(
                title="Antigravity Gemini Pro",
                model_id=MODEL_AG_GEMINI_PRO,
                reason=(
                    f"Claude {_pct_text(claude_pct)}, Go Pro {_pct_text(go_pro_pct)}; haftalık rezerv "
                    f"niteliğindeki Gemini Pro ({_pct_text(gemini_pro_pct)}) zor işler için en iyi kalan seçenek."
                ),
                remaining_pct=gemini_pro_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif isinstance(argon_pct, (int, float)) and argon_pct >= min_go_pct:
        items.append(
            _suggest(
                title="Go Gemini 4 Argon",
                model_id=MODEL_GO_ARGON,
                reason=(
                    f"Claude ve Go Pro tükendi; Argon havuzunda {_pct_text(argon_pct)} var. "
                    "Haftalık rezervi zor işler için kullan."
                ),
                remaining_pct=argon_pct,
                badge=BADGE_PRIMARY,
            )
        )
    elif isinstance(premium_copilot, (int, float)) and premium_copilot >= min_go_pct:
        items.append(
            _suggest(
                title="GitHub Copilot Claude Sonnet 4",
                model_id=MODEL_COPILOT_SONNET,
                reason=(
                    f"Go/Antigravity zor iş havuzları daraldı; Copilot premium kotası "
                    f"{_pct_text(premium_copilot)} ile Claude Sonnet'e geç."
                ),
                remaining_pct=premium_copilot,
                badge=BADGE_PRIMARY,
            )
        )
    else:
        reason = (
            "Tüm zor iş havuzları tükendi; ücretsiz modelle ilerle ve kalibrasyonu kontrol et."
            if any(
                isinstance(pct, (int, float))
                for pct in (claude_pct, go_pro_pct, gemini_pro_pct, argon_pct)
            )
            else "Kota verisi alınamadı; ücretsiz modelle ilerle ve durumu yenile."
        )
        items.append(
            _suggest(
                title="DeepSeek V4.1 Flash Free",
                model_id=MODEL_FREE,
                reason=reason,
                remaining_pct=None,
                badge=BADGE_PRIMARY,
            )
        )

    if isinstance(claude_pct, (int, float)) and claude_pct > 0 and not claude_locked:
        warning = ""
        if conflict:
            warning = (
                f" Çift Antigravity çakışması ({conflict_count} oturum) var; geçmeden önce "
                "diğer sekmeyi kapat."
            )
        elif claude_pct < 5:
            warning = " Kota pratikte tükendi; yalnızca zorunlu durumda kullan."
        elif claude_pct < ag_primary_pct:
            warning = " Kota azaldı; yalnızca kritik adımlar için kullan."
        items.append(
            _suggest(
                title="Antigravity Claude Opus 5.5",
                model_id=MODEL_AG_CLAUDE,
                reason=f"Kalan Claude kotası {_pct_text(claude_pct)}.{warning}",
                remaining_pct=claude_pct,
                badge=BADGE_ALT,
            )
        )
    if go_pro_ok:
        items.append(
            _suggest(
                title="DeepSeek V4 Pro #max",
                model_id=MODEL_GO_PRO,
                reason=(
                    f"Go Pro {_pct_text(go_pro_pct)}; Claude tarafı dolduğunda $3'lük 5 saatlik "
                    "havuzu zor görevlerde değerlendir."
                ),
                remaining_pct=go_pro_pct,
                badge=BADGE_ALT,
            )
        )
    if isinstance(gemini_pro_pct, (int, float)):
        items.append(
            _suggest(
                title="Antigravity Gemini Pro",
                model_id=MODEL_AG_GEMINI_PRO,
                reason=(
                    f"Haftalık rezerv Gemini Pro {_pct_text(gemini_pro_pct)}; Claude/Go Pro "
                    "tükendiğinde uzun bağlamlı mimari işler için alternatif."
                    + (
                        " Çift Antigravity çakışması var; geçmeden önce diğer sekmeyi kapat."
                        if conflict
                        else ""
                    )
                ),
                remaining_pct=gemini_pro_pct,
                badge=BADGE_ALT,
            )
        )
    if isinstance(argon_pct, (int, float)):
        items.append(
            _suggest(
                title="Go Gemini 4 Argon",
                model_id=MODEL_GO_ARGON,
                reason=(
                    f"Go Argon havuzu {_pct_text(argon_pct)}; haftalık rezervi acil zor işler "
                    "dışında harcama."
                ),
                remaining_pct=argon_pct,
                badge=BADGE_ALT,
            )
        )
    if isinstance(premium_copilot, (int, float)) and premium_copilot >= min_go_pct:
        items.append(
            _suggest(
                title="GitHub Copilot Claude Sonnet 4",
                model_id=MODEL_COPILOT_SONNET,
                reason=(
                    f"Copilot premium/AI Credits kotası {_pct_text(premium_copilot)}; "
                    "en son abonelik yedeği."
                ),
                remaining_pct=premium_copilot,
                badge=BADGE_ALT,
            )
        )
    items.append(
        _suggest(
            title="DeepSeek V4.1 Flash Free",
            model_id=MODEL_FREE,
            reason="Ücretsiz son çare; kota pencereleri sıfırlanana kadar küçük adımlarla ilerle.",
            remaining_pct=None,
            badge=BADGE_LAST,
        )
    )
    return _finalize(items)


# ---------------------------------------------------------------------------
# Genel giriş noktası
# ---------------------------------------------------------------------------


def build_recommendations(
    sessions: m.SessionStatus,
    antigravity: m.AntigravityStatus,
    go: m.GoStatus,
    copilot: m.CopilotStatus,
    config: dict[str, Any] | None = None,
) -> m.Recommendations:
    config = config or cfg_mod.load_config()
    critical_pct = float(cfg_mod.get(config, "thresholds.critical_pct", 15))
    ag_primary_pct = float(cfg_mod.get(config, "thresholds.antigravity_primary_pct", 20))
    min_go_pct = max(critical_pct, 15.0)

    result = m.Recommendations(
        easy_medium=_easy_medium(sessions, antigravity, go, copilot, min_go_pct),
        hard=_hard(
            sessions,
            antigravity,
            go,
            copilot,
            min_go_pct,
            ag_primary_pct,
        ),
        generated_at=datetime_now_iso(),
    )

    if sessions.dual_antigravity_conflict:
        result.notes.append(
            f"Çift Antigravity çakışması: {len(sessions.conflict_sessions)} oturum aynı anda Antigravity kullanıyor."
        )
    if go.total.calibrated:
        result.notes.append("Go harcamasına kullanıcı kalibrasyonu uygulandı; konsol yüzdesiyle doğrula.")
    if not go.available:
        result.notes.append(f"Go kotası okunamadı: {go.error or 'bilinmeyen hata'}")
    if not antigravity.available:
        result.notes.append(f"Antigravity kotası okunamadı: {antigravity.error or 'bilinmeyen hata'}")
    if not copilot.available:
        result.notes.append("GitHub Copilot verisi yok; önerilerde dikkate alınmadı.")
    return result


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
