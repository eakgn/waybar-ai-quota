"""Antigravity (`agy`) pasif harness adaptörü.

ToS Anayasası (SPEC.md §2):
- Google OAuth token'ı okumaz, yenilemez veya taşımaz.
- `cloudcode-pa` veya başka hiçbir ağ uç noktasına istek atmaz.
- Yalnızca `agy` resmî hook'larından (`SessionStart`, `Stop`, `Notification` vb.)
  gelen yerel push olaylarını okur.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from .. import events as events_mod
from ..models import EventRecord, HarnessSummary, SessionNode, SessionState
from ..utils import iso_now
from .base import AdapterInfo


class AntigravityHarnessAdapter:
    info = AdapterInfo(
        key="antigravity",
        label="Antigravity (agy)",
        description="Google Antigravity CLI pasif hook izleyicisi (sıfır ağ / sıfır OAuth)",
        supports_hooks=True,
    )

    def detect(self) -> bool:
        if shutil.which("agy"):
            return True
        return (Path.home() / ".gemini" / "antigravity-cli").is_dir()

    def collect(
        self,
        *,
        config: dict[str, Any],
        state: dict[str, Any],
        push_events: list[EventRecord],
        include_all: bool = False,
    ) -> tuple[HarnessSummary, list[SessionNode]]:
        summary = HarnessSummary(key=self.info.key, label=self.info.label)
        now = time.time()
        ag_events = [ev for ev in push_events if ev.harness == self.info.key]

        summary.available = bool(ag_events) or self.detect()
        if not ag_events:
            return summary, []

        # Çoklu oturumda belirsiz bildirim için açılmış genel `antigravity-prompt` kaydı,
        # herhangi bir gerçek oturumun daha yeni bir hook olayı geldiğinde düşürülür.
        latest_real_ts = max(
            (ev.ts for ev in ag_events if ev.session_id != "antigravity-prompt"),
            default=0.0,
        )

        nodes_by_id: dict[str, SessionNode] = {}
        for ev in ag_events:
            if ev.session_id == "antigravity-prompt" and latest_real_ts > ev.ts:
                continue
            ev_state = ev.state
            if ev_state == SessionState.DONE_UNSEEN and events_mod.is_done_seen(
                self.info.key, ev.session_id, ev.ts, state
            ):
                ev_state = SessionState.IDLE
            age = max(0, int(now - ev.ts))
            nodes_by_id[ev.session_id] = SessionNode(
                id=ev.session_id,
                harness=self.info.key,
                state=ev_state,
                effective_state=ev_state,
                parent_id=ev.parent_id,
                is_subagent=ev.is_subagent,
                project=ev.project,
                title=ev.title or "Antigravity oturumu",
                agent=ev.agent or ("subagent" if ev.is_subagent else "agy"),
                model=ev.model,
                message=ev.message,
                updated_at=iso_now(ev.ts),
                updated_ts=ev.ts,
                updated_age_seconds=age,
                source="push",
            )

        roots: list[SessionNode] = []
        for node in nodes_by_id.values():
            if node.parent_id and node.parent_id in nodes_by_id and node.parent_id != node.id:
                node.is_subagent = True
                nodes_by_id[node.parent_id].children.append(node)
            else:
                roots.append(node)

        for node in nodes_by_id.values():
            if node.state == SessionState.RUNNING:
                summary.active_count += 1
            elif node.state == SessionState.WAITING_INPUT:
                summary.waiting_count += 1
            elif node.state == SessionState.DONE_UNSEEN:
                summary.done_unseen_count += 1

        return summary, roots
