"""Veri modelleri.

Tüm modeller yalnızca serileştirilebilir alanlar taşır ve `to_dict()` ile
JSON'a hazır sözlüklere dönüşür. Gizli veri (refreshToken, e-posta,
projectId, API anahtarı) bu modellerde yer almaz.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _d(obj: Any) -> dict[str, Any]:
    """Dataclass -> JSON uyumlu sözlük (iç içe yapılar dahil)."""
    return asdict(obj)


# ---------------------------------------------------------------------------
# Antigravity
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AntigravityGroup:
    """Tek bir Antigravity kota grubu (claude / gemini-pro / gemini-flash)."""

    key: str
    label: str
    remaining_pct: float
    remaining_fraction: float
    reset_time: str | None = None
    reset_in_seconds: int | None = None
    reset_human: str | None = None
    rate_limited: bool = False
    rate_limit_remaining_seconds: int | None = None
    rate_limit_human: str | None = None
    model_count: int = 0
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _d(self)


@dataclass(slots=True)
class AntigravityStatus:
    available: bool = False
    groups: list[AntigravityGroup] = field(default_factory=list)
    updated_at: str | None = None
    updated_ts: int | None = None
    age_seconds: int | None = None
    stale: bool = True
    refreshed: bool = False
    account_count: int = 0
    error: str | None = None

    def group(self, key: str) -> AntigravityGroup | None:
        for item in self.groups:
            if item.key == key:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        out = _d(self)
        out["groups"] = [g.to_dict() for g in self.groups]
        return out


# ---------------------------------------------------------------------------
# OpenCode oturumları
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SessionInfo:
    id: str
    directory: str = ""
    project: str = ""
    title: str = ""
    agent: str = ""
    model: str = ""
    model_id: str = ""
    provider_id: str = ""
    variant: str | None = None
    tokens_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_read: int = 0
    cost_usd: float = 0.0
    in_flight: bool = False
    recently_active: bool = False
    archived: bool = False
    updated_at: str | None = None
    updated_age_seconds: int | None = None
    is_antigravity: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _d(self)


@dataclass(slots=True)
class SessionStatus:
    available: bool = False
    sessions: list[SessionInfo] = field(default_factory=list)
    active_count: int = 0
    recent_count: int = 0
    dual_antigravity_conflict: bool = False
    conflict_sessions: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = _d(self)
        out["sessions"] = [s.to_dict() for s in self.sessions]
        return out


# ---------------------------------------------------------------------------
# OpenCode Go
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GoPoolStatus:
    key: str
    label: str
    models: list[str] = field(default_factory=list)
    limits: dict[str, float] = field(default_factory=dict)
    spend: dict[str, float] = field(default_factory=dict)
    remaining: dict[str, float] = field(default_factory=dict)
    remaining_pct: float = 100.0
    calibrated: bool = False
    calibration: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _d(self)


@dataclass(slots=True)
class GoTotalStatus:
    limits: dict[str, float] = field(default_factory=dict)
    spend: dict[str, float] = field(default_factory=dict)
    remaining: dict[str, float] = field(default_factory=dict)
    remaining_pct: float = 100.0
    calibrated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _d(self)


@dataclass(slots=True)
class GoStatus:
    available: bool = False
    pools: list[GoPoolStatus] = field(default_factory=list)
    total: GoTotalStatus = field(default_factory=GoTotalStatus)
    window_labels: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def pool(self, key: str) -> GoPoolStatus | None:
        for item in self.pools:
            if item.key == key:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        out = _d(self)
        out["pools"] = [p.to_dict() for p in self.pools]
        out["total"] = self.total.to_dict()
        return out


# ---------------------------------------------------------------------------
# GitHub Copilot
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotQuota:
    key: str
    label: str
    percent_remaining: float | None = None
    remaining: float | None = None
    entitlement: float | None = None
    unlimited: bool = False
    has_quota: bool = True
    reset_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _d(self)


@dataclass(slots=True)
class CopilotStatus:
    available: bool = False
    plan: str | None = None
    plan_label: str | None = None
    reset_date: str | None = None
    quotas: list[CopilotQuota] = field(default_factory=list)
    fetched_at: str | None = None
    age_seconds: int | None = None
    cached: bool = False
    error: str | None = None

    def quota(self, key: str) -> CopilotQuota | None:
        for item in self.quotas:
            if item.key == key:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        out = _d(self)
        out["quotas"] = [q.to_dict() for q in self.quotas]
        return out


# ---------------------------------------------------------------------------
# Öneri motoru
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Suggestion:
    rank: int
    title: str
    model_id: str
    copy_command: str
    reason: str
    remaining_pct: float | None = None
    badge: str = "Alternatif"

    def to_dict(self) -> dict[str, Any]:
        return _d(self)


@dataclass(slots=True)
class Recommendations:
    easy_medium: list[Suggestion] = field(default_factory=list)
    hard: list[Suggestion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    generated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = _d(self)
        out["easy_medium"] = [s.to_dict() for s in self.easy_medium]
        out["hard"] = [s.to_dict() for s in self.hard]
        return out


# ---------------------------------------------------------------------------
# Birleşik durum
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FullStatus:
    generated_at: str
    generated_ts: int
    sessions: SessionStatus = field(default_factory=SessionStatus)
    antigravity: AntigravityStatus = field(default_factory=AntigravityStatus)
    go: GoStatus = field(default_factory=GoStatus)
    copilot: CopilotStatus = field(default_factory=CopilotStatus)
    recommendations: Recommendations = field(default_factory=Recommendations)
    notifications: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "generated_ts": self.generated_ts,
            "sessions": self.sessions.to_dict(),
            "antigravity": self.antigravity.to_dict(),
            "go": self.go.to_dict(),
            "copilot": self.copilot.to_dict(),
            "recommendations": self.recommendations.to_dict(),
            "notifications": list(self.notifications),
        }
