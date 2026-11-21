"""Çekirdek toplama motoru (`engine`).

Yalnızca `config.toml` içinde etkinleştirilen (opt-in) harness ve provider
adaptörlerini çalıştırır; oturum ağaçlarını birleştirir, aktif oturumların
kullandığı sağlayıcılara hızlı önbellek ömrü (`remote_active_seconds`) uygular
ve tek bir `Snapshot` üretir.
"""

from __future__ import annotations

import time
from typing import Any

from . import config as cfg_mod
from . import events as events_mod
from . import fsm
from .adapters import HARNESS_ADAPTERS, PROVIDER_ADAPTERS
from .models import STATE_LABELS, HarnessSummary, ProviderQuota, SessionNode, SessionState, Snapshot
from .utils import iso_now

_ACTIVE_STATES = frozenset({SessionState.RUNNING, SessionState.WAITING_INPUT})


def map_session_to_provider_key(node: SessionNode) -> str | None:
    """Bir oturumun kullandığı sağlayıcı/model bilgisini `ProviderQuota.key` ile eşler."""
    combined = f"{node.provider}/{node.model}".lower()
    if "opencode-go" in combined or "opencode_go" in combined:
        return "opencode_go"
    if "github-copilot" in combined or "copilot" in combined:
        return "github_copilot"
    return None


def active_provider_keys(roots: list[SessionNode]) -> set[str]:
    """Şu an aktif (`RUNNING` / `WAITING_INPUT`) oturumların kullandığı sağlayıcı anahtarları."""
    found: set[str] = set()

    def _walk(node: SessionNode) -> None:
        if node.state in _ACTIVE_STATES:
            key = map_session_to_provider_key(node)
            if key:
                found.add(key)
        for child in node.children:
            _walk(child)

    for root in roots:
        _walk(root)
    return found


def collect_snapshot(
    *,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    force_refresh: bool = False,
    offline: bool = False,
    include_all: bool = False,
) -> Snapshot:
    """Tüm etkin kaynaklardan normalize `Snapshot` üretir."""
    cfg = config if config is not None else cfg_mod.load_config()
    st = state if state is not None else cfg_mod.load_state()
    now = time.time()

    enabled_h = [k for k in cfg_mod.enabled_harnesses(cfg) if k in HARNESS_ADAPTERS]
    enabled_p = [k for k in cfg_mod.enabled_providers(cfg) if k in PROVIDER_ADAPTERS]

    stale_seconds = int(cfg_mod.get(cfg, "thresholds.event_stale_seconds", 1800))
    push_events = events_mod.load_events(max_age_seconds=stale_seconds, now=now)

    all_roots: list[SessionNode] = []
    harness_summaries: list[HarnessSummary] = []

    for key in enabled_h:
        adapter = HARNESS_ADAPTERS[key]
        h_events = [ev for ev in push_events if ev.harness == key]
        summary, roots = adapter.collect(
            config=cfg, state=st, push_events=h_events, include_all=include_all
        )
        harness_summaries.append(summary)
        all_roots.extend(roots)

    global_state = fsm.compute_effective_states(all_roots)
    sorted_roots = fsm.sort_session_tree(all_roots)
    stats = fsm.count_tree_stats(sorted_roots)

    # Aktif oturumun kullandığı sağlayıcıya daha kısa TTL (anlık azalış takibi)
    active_provs = active_provider_keys(sorted_roots)
    active_ttl = max(10, int(cfg_mod.get(cfg, "refresh.remote_active_seconds", 30)))

    provider_quotas: list[ProviderQuota] = []
    for key in enabled_p:
        adapter = PROVIDER_ADAPTERS[key]
        ttl_override = active_ttl if key in active_provs else None
        quota = adapter.collect(
            config=cfg,
            force_refresh=force_refresh,
            offline=offline,
            ttl_override=ttl_override,
        )
        provider_quotas.append(quota)

    return Snapshot(
        generated_at=iso_now(now),
        generated_ts=now,
        global_state=global_state,
        global_label=STATE_LABELS[global_state],
        active_count=stats["active"],
        waiting_count=stats["waiting"],
        error_count=stats["error"],
        done_unseen_count=stats["done_unseen"],
        subagent_count=stats["subagents"],
        sessions=sorted_roots,
        harnesses=harness_summaries,
        providers=provider_quotas,
        enabled_harnesses=enabled_h,
        enabled_providers=enabled_p,
    )
