"""Oturum durum makinesi (FSM) önceliklendirme ve ağaç yayılımı."""

from __future__ import annotations

from typing import Iterable

from .models import STATE_PRIORITY, SessionNode, SessionState


def pick_highest_priority(states: Iterable[SessionState]) -> SessionState:
    """Verilen durumlar içinden en yüksek öncelikli (en acil) durumu döndürür."""
    best = SessionState.IDLE
    best_rank = STATE_PRIORITY[best]
    for state in states:
        rank = STATE_PRIORITY.get(state, 99)
        if rank < best_rank:
            best = state
            best_rank = rank
    return best


def compute_effective_states(roots: list[SessionNode]) -> SessionState:
    """Her düğümün alt ağaç dahil `effective_state` değerini hesaplar ve global durumu döndürür.

    Bir ana oturum boşta veya çalışıyorken alt ajanlarından biri `waiting_input`
    durumuna geçtiyse, hem ana oturumun `effective_state`'i hem de global durum
    `waiting_input` olur.
    """

    def _visit(node: SessionNode) -> SessionState:
        child_states = [_visit(child) for child in node.children]
        node.effective_state = pick_highest_priority([node.state, *child_states])
        return node.effective_state

    root_states = [_visit(root) for root in roots]
    return pick_highest_priority(root_states)


def sort_session_tree(roots: list[SessionNode]) -> list[SessionNode]:
    """Ağacı en acil durum → en güncel zaman sırasına göre sıralar."""

    def _sort_key(node: SessionNode) -> tuple[int, float]:
        return (STATE_PRIORITY.get(node.effective_state, 99), -(node.updated_ts or 0.0))

    for root in roots:
        if root.children:
            root.children = sort_session_tree(root.children)
    return sorted(roots, key=_sort_key)


def count_tree_stats(roots: list[SessionNode]) -> dict[str, int]:
    """Tüm düğümler (ana + alt ajanlar) üzerinden özet sayaçları hesaplar."""
    stats = {
        "active": 0,
        "waiting": 0,
        "error": 0,
        "done_unseen": 0,
        "subagents": 0,
    }

    def _walk(node: SessionNode, depth: int) -> None:
        if depth > 0 or node.is_subagent:
            stats["subagents"] += 1
        if node.state == SessionState.RUNNING:
            stats["active"] += 1
        elif node.state == SessionState.WAITING_INPUT:
            stats["waiting"] += 1
        elif node.state in (SessionState.ERROR, SessionState.RATE_LIMITED):
            stats["error"] += 1
        elif node.state == SessionState.DONE_UNSEEN:
            stats["done_unseen"] += 1
        for child in node.children:
            _walk(child, depth + 1)

    for root in roots:
        _walk(root, 0)
    return stats
