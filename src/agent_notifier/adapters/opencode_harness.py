"""OpenCode harness adaptörü (`opencode.db` salt-okunur + push birleştirme).

`session_v2.parent_id` sütunu mevcutsa ana oturum -> alt ajan (subagent)
ağacını otomatik olarak kurar.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from .. import config as cfg_mod
from .. import events as events_mod
from ..models import EventRecord, HarnessSummary, SessionNode, SessionState
from ..utils import iso_now, opencode_db_path, ro_connect
from .base import AdapterInfo


def _parse_model(raw: Any) -> tuple[str, str]:
    if not raw:
        return "", ""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            if "/" in raw:
                prov, mod = raw.split("/", 1)
                return prov, mod
            return "", raw
    if not isinstance(raw, dict):
        return "", ""
    provider = str(raw.get("providerID") or raw.get("providerId") or raw.get("provider") or "")
    model_id = str(raw.get("id") or raw.get("modelID") or raw.get("modelId") or "")
    return provider, model_id


def _last_assistant_models(
    con: sqlite3.Connection, session_ids: list[str]
) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    if not session_ids:
        return found
    placeholders = ",".join("?" for _ in session_ids)
    query = (
        "SELECT session_id, data FROM session_message "
        f"WHERE type = 'assistant' AND session_id IN ({placeholders}) "
        "ORDER BY seq DESC"
    )
    try:
        for row in con.execute(query, session_ids):
            sid = row["session_id"]
            if sid in found:
                continue
            try:
                data = json.loads(row["data"])
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("model"):
                found[sid] = _parse_model(data["model"])
    except sqlite3.Error:
        return found
    return found


def _session_columns(con: sqlite3.Connection) -> set[str]:
    try:
        rows = con.execute("PRAGMA table_info(session_v2)").fetchall()
        return {str(row["name"]) for row in rows}
    except sqlite3.Error:
        return set()


class OpenCodeHarnessAdapter:
    info = AdapterInfo(
        key="opencode",
        label="OpenCode",
        description="OpenCode CLI yerel oturum ve alt ajan (subagent) izleyici (opencode.db)",
        supports_hooks=True,
    )

    def detect(self) -> bool:
        return opencode_db_path().is_file() or shutil.which("opencode") is not None

    def collect(
        self,
        *,
        config: dict[str, Any],
        state: dict[str, Any],
        push_events: list[EventRecord],
    ) -> tuple[HarnessSummary, list[SessionNode]]:
        summary = HarnessSummary(key=self.info.key, label=self.info.label)
        db_path = opencode_db_path()
        recent_seconds = int(cfg_mod.get(config, "thresholds.recent_activity_seconds", 300))
        now = time.time()

        push_by_id: dict[str, EventRecord] = {
            ev.session_id: ev for ev in push_events if ev.harness == self.info.key
        }

        nodes_by_id: dict[str, SessionNode] = {}

        if db_path.is_file():
            try:
                con = ro_connect(db_path)
            except sqlite3.Error as exc:
                summary.error = f"opencode.db açılamadı: {exc}"
                con = None

            if con is not None:
                try:
                    cols = _session_columns(con)
                    has_parent = "parent_id" in cols
                    parent_expr = "parent_id" if has_parent else "NULL AS parent_id"
                    rows = con.execute(
                        f"""
                        SELECT id, {parent_expr}, directory, title, agent, model, cost,
                               tokens_input, tokens_output, tokens_cache_read,
                               time_updated, time_suspended
                        FROM session_v2
                        WHERE time_archived IS NULL
                        ORDER BY time_updated DESC
                        LIMIT 60
                        """
                    ).fetchall()
                    pending = [row["id"] for row in rows if not row["model"]]
                    fallback = _last_assistant_models(con, pending)
                    summary.available = True
                except sqlite3.Error as exc:
                    rows = []
                    fallback = {}
                    summary.error = f"session_v2 okunamadı: {exc}"
                finally:
                    con.close()

                for row in rows:
                    sid = str(row["id"])
                    parent_id = str(row["parent_id"]) if row["parent_id"] else None
                    prov, mod = _parse_model(row["model"])
                    if not mod and sid in fallback:
                        prov, mod = fallback[sid]
                    model_full = f"{prov}/{mod}" if prov and mod else (mod or prov)
                    updated_ms = int(row["time_updated"] or 0)
                    updated_ts = updated_ms / 1000.0 if updated_ms else None
                    age = max(0, int(now - updated_ts)) if updated_ts else None
                    in_flight = row["time_suspended"] is not None

                    if in_flight:
                        node_state = SessionState.RUNNING
                    elif updated_ts and age is not None and age <= recent_seconds:
                        if events_mod.is_done_seen(self.info.key, sid, updated_ts, state):
                            node_state = SessionState.IDLE
                        else:
                            node_state = SessionState.DONE_UNSEEN
                    else:
                        node_state = SessionState.IDLE

                    directory = str(row["directory"] or "")
                    project = Path(directory).name if directory else ""
                    tokens_in = int(row["tokens_input"] or 0)
                    tokens_out = int(row["tokens_output"] or 0)
                    tokens_cache = int(row["tokens_cache_read"] or 0)

                    nodes_by_id[sid] = SessionNode(
                        id=sid,
                        harness=self.info.key,
                        state=node_state,
                        effective_state=node_state,
                        parent_id=parent_id,
                        is_subagent=bool(parent_id),
                        project=project,
                        title=str(row["title"] or ""),
                        agent=str(row["agent"] or ""),
                        model=model_full,
                        provider=prov,
                        tokens_total=tokens_in + tokens_out + tokens_cache,
                        tokens_input=tokens_in,
                        tokens_output=tokens_out,
                        cost_usd=round(float(row["cost"] or 0.0), 6),
                        updated_at=iso_now(updated_ts) if updated_ts else None,
                        updated_ts=updated_ts,
                        updated_age_seconds=age,
                        source="pull",
                    )
        elif not push_by_id:
            summary.error = "opencode.db bulunamadı"

        # Push olaylarını (hook) mevcut düğümlerle birleştir veya yeni ekle
        for sid, ev in push_by_id.items():
            summary.available = True
            ev_state = ev.state
            if ev_state == SessionState.DONE_UNSEEN and events_mod.is_done_seen(
                self.info.key, sid, ev.ts, state
            ):
                ev_state = SessionState.IDLE

            existing = nodes_by_id.get(sid)
            if existing is not None:
                # Push olayı DB güncellemesinden yeniyse (veya acil bir durumsa) önceliklidir
                if existing.updated_ts is None or ev.ts >= (existing.updated_ts - 2.0):
                    existing.state = ev_state
                    existing.effective_state = ev_state
                    existing.updated_ts = max(existing.updated_ts or 0.0, ev.ts)
                    existing.updated_at = iso_now(existing.updated_ts)
                    existing.updated_age_seconds = max(0, int(now - existing.updated_ts))
                    if ev.message:
                        existing.message = ev.message
                    if ev.parent_id and not existing.parent_id:
                        existing.parent_id = ev.parent_id
                        existing.is_subagent = True
                    existing.source = "merged"
            else:
                age = max(0, int(now - ev.ts))
                nodes_by_id[sid] = SessionNode(
                    id=sid,
                    harness=self.info.key,
                    state=ev_state,
                    effective_state=ev_state,
                    parent_id=ev.parent_id,
                    is_subagent=ev.is_subagent,
                    project=ev.project,
                    title=ev.title,
                    agent=ev.agent,
                    model=ev.model,
                    message=ev.message,
                    updated_at=iso_now(ev.ts),
                    updated_ts=ev.ts,
                    updated_age_seconds=age,
                    source="push",
                )

        # Ağaç kurulumu: parent_id eşleşen alt ajanları ana oturumun altına yerleştir
        roots: list[SessionNode] = []
        for node in nodes_by_id.values():
            if node.parent_id and node.parent_id in nodes_by_id and node.parent_id != node.id:
                parent = nodes_by_id[node.parent_id]
                node.is_subagent = True
                parent.children.append(node)
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
