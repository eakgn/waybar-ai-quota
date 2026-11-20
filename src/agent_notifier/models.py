"""Normalize çekirdek veri modelleri.

Tüm modeller serileştirilebilir ve platformdan bağımsızdır. Gizli bilgiler
(API anahtarı, OAuth token, e-posta, tam kişisel dosya yolları) bu modellerde
yer almaz.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    """Tek bakışta okunabilir oturum durum makinesi (FSM)."""

    WAITING_INPUT = "waiting_input"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    RUNNING = "running"
    DONE_UNSEEN = "done_unseen"
    IDLE = "idle"


# Küçük sayı = daha yüksek görsel/bildirim önceliği
STATE_PRIORITY: dict[SessionState, int] = {
    SessionState.WAITING_INPUT: 0,
    SessionState.ERROR: 1,
    SessionState.RATE_LIMITED: 2,
    SessionState.RUNNING: 3,
    SessionState.DONE_UNSEEN: 4,
    SessionState.IDLE: 5,
}

STATE_LABELS: dict[SessionState, str] = {
    SessionState.WAITING_INPUT: "Soru / Onay Bekliyor",
    SessionState.ERROR: "Hata",
    SessionState.RATE_LIMITED: "Kota Kilidi",
    SessionState.RUNNING: "Çalışıyor",
    SessionState.DONE_UNSEEN: "Tamamlandı",
    SessionState.IDLE: "Boşta",
}

STATE_SHORT_LABELS: dict[SessionState, str] = {
    SessionState.WAITING_INPUT: "Bekliyor",
    SessionState.ERROR: "Hata",
    SessionState.RATE_LIMITED: "Kilitli",
    SessionState.RUNNING: "Çalışıyor",
    SessionState.DONE_UNSEEN: "Bitti",
    SessionState.IDLE: "Boşta",
}


def _serialize(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


@dataclass(slots=True)
class SessionNode:
    """Ana oturum veya alt ajan (subagent) düğümü."""

    id: str
    harness: str
    state: SessionState = SessionState.IDLE
    effective_state: SessionState = SessionState.IDLE
    parent_id: str | None = None
    is_subagent: bool = False
    project: str = ""
    title: str = ""
    agent: str = ""
    model: str = ""
    provider: str = ""
    message: str = ""
    tokens_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    updated_at: str | None = None
    updated_ts: float | None = None
    updated_age_seconds: int | None = None
    source: str = "pull"  # "pull" | "push" | "merged"
    children: list[SessionNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class HarnessSummary:
    """Tek bir etkin harness adaptörünün toplama sonucu."""

    key: str
    label: str
    available: bool = False
    active_count: int = 0
    waiting_count: int = 0
    done_unseen_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class QuotaWindow:
    """Tek bir zaman penceresi (ör. 5s, haftalık, aylık, chat)."""

    key: str
    label: str
    remaining_pct: float | None = None
    used_pct: float | None = None
    spent_usd: float | None = None
    limit_usd: float | None = None
    remaining: float | None = None
    entitlement: float | None = None
    unlimited: bool = False
    rate_limited: bool = False
    reset_at: str | None = None
    reset_in_seconds: int | None = None
    reset_human: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class QuotaPool:
    """Bir sağlayıcı içindeki alt havuz (ör. Go Flash / Pro veya Copilot Chat)."""

    key: str
    label: str
    remaining_pct: float | None = None
    unlimited: bool = False
    rate_limited: bool = False
    critical: bool = False
    reset_human: str | None = None
    detail: str = ""
    windows: list[QuotaWindow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class ProviderQuota:
    """Tek bir opt-in kota sağlayıcısının normalize durumu."""

    key: str
    label: str
    available: bool = False
    plan_label: str | None = None
    headline_pct: float | None = None
    critical: bool = False
    rate_limited: bool = False
    source: str = "local"  # "api" | "hybrid" | "local" | "cache"
    reset_human: str | None = None
    fetched_at: str | None = None
    age_seconds: int | None = None
    cached: bool = False
    pools: list[QuotaPool] = field(default_factory=list)
    windows: list[QuotaWindow] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class EventRecord:
    """Resmî hook / CLI üzerinden gelen anlık push olayı."""

    harness: str
    session_id: str
    state: SessionState
    ts: float
    parent_id: str | None = None
    is_subagent: bool = False
    project: str = ""
    title: str = ""
    agent: str = ""
    model: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class Snapshot:
    """Tüm yüzeylerin (Tray, Dashboard, Waybar/Polybar, CLI) tükettiği tek şema."""

    generated_at: str
    generated_ts: float
    global_state: SessionState = SessionState.IDLE
    global_label: str = STATE_LABELS[SessionState.IDLE]
    active_count: int = 0
    waiting_count: int = 0
    error_count: int = 0
    done_unseen_count: int = 0
    subagent_count: int = 0
    sessions: list[SessionNode] = field(default_factory=list)
    harnesses: list[HarnessSummary] = field(default_factory=list)
    providers: list[ProviderQuota] = field(default_factory=list)
    enabled_harnesses: list[str] = field(default_factory=list)
    enabled_providers: list[str] = field(default_factory=list)
    notifications_sent: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))
