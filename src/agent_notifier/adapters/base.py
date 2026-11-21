"""Adaptör kontratları (`HarnessAdapter` ve `ProviderAdapter`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..models import EventRecord, HarnessSummary, ProviderQuota, SessionNode


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    key: str
    label: str
    description: str
    supports_hooks: bool = False


class HarnessAdapter(Protocol):
    info: AdapterInfo

    def detect(self) -> bool:
        """Bu harness kullanıcı sisteminde kurulu/kullanılıyor mu?"""
        ...

    def collect(
        self,
        *,
        config: dict[str, Any],
        state: dict[str, Any],
        push_events: list[EventRecord],
        include_all: bool = False,
    ) -> tuple[HarnessSummary, list[SessionNode]]:
        """Ana oturum (ve bağlı alt ajan) düğümlerini döndürür."""
        ...


class ProviderAdapter(Protocol):
    info: AdapterInfo

    def detect(self) -> bool:
        """Bu kota sağlayıcısı kullanıcı sisteminde algılanıyor mu?"""
        ...

    def collect(
        self,
        *,
        config: dict[str, Any],
        force_refresh: bool = False,
        offline: bool = False,
        ttl_override: int | None = None,
    ) -> ProviderQuota:
        """Sağlayıcı kota/bütçe durumunu döndürür."""
        ...
