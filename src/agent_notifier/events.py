"""Push olay kutusu (hook'lardan gelen anlık oturum durum olayları).

`agent-notifier event ...` çağrısı hiçbir ağ isteği yapmadan bu modül
üzerinden olay dosyasına atomik kayıt yazar (<10 ms) ve hemen çıkar.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import config as cfg_mod
from .models import EventRecord, SessionState


def _normalize_state(raw: str) -> SessionState:
    cleaned = (raw or "").strip().lower().replace("-", "_")
    aliases = {
        "working": SessionState.RUNNING,
        "active": SessionState.RUNNING,
        "start": SessionState.RUNNING,
        "started": SessionState.RUNNING,
        "running": SessionState.RUNNING,
        "waiting": SessionState.WAITING_INPUT,
        "waiting_input": SessionState.WAITING_INPUT,
        "permission": SessionState.WAITING_INPUT,
        "question": SessionState.WAITING_INPUT,
        "input": SessionState.WAITING_INPUT,
        "done": SessionState.DONE_UNSEEN,
        "done_unseen": SessionState.DONE_UNSEEN,
        "completed": SessionState.DONE_UNSEEN,
        "finished": SessionState.DONE_UNSEEN,
        "stop": SessionState.DONE_UNSEEN,
        "stopped": SessionState.DONE_UNSEEN,
        "error": SessionState.ERROR,
        "failed": SessionState.ERROR,
        "rate_limited": SessionState.RATE_LIMITED,
        "ratelimit": SessionState.RATE_LIMITED,
        "idle": SessionState.IDLE,
        "clear": SessionState.IDLE,
    }
    if cleaned in aliases:
        return aliases[cleaned]
    raise ValueError(f"bilinmeyen oturum durumu: {raw!r}")


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    target = path or cfg_mod.events_path()
    if not target.is_file():
        return {"version": 1, "events": {}}
    try:
        raw = cfg_mod.read_json(target)
    except (OSError, ValueError):
        return {"version": 1, "events": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("events"), dict):
        return {"version": 1, "events": {}}
    return raw


def _event_key(harness: str, session_id: str) -> str:
    return f"{harness}:{session_id}"


def record_event(
    *,
    harness: str,
    session_id: str,
    state: str | SessionState,
    parent_id: str | None = None,
    is_subagent: bool = False,
    project: str = "",
    title: str = "",
    agent: str = "",
    model: str = "",
    message: str = "",
    ts: float | None = None,
    path: Path | None = None,
) -> EventRecord:
    """Bir push olayını olay kutusuna atomik olarak kaydeder."""
    parsed_state = state if isinstance(state, SessionState) else _normalize_state(state)
    now = float(ts if ts is not None else time.time())
    harness_clean = harness.strip().lower()
    sid_clean = session_id.strip() or f"{harness_clean}-default"
    parent_clean = (parent_id or "").strip() or None
    subagent_flag = bool(is_subagent or parent_clean)

    store = _load_raw(path)
    events = store.setdefault("events", {})
    key = _event_key(harness_clean, sid_clean)
    previous = events.get(key) if isinstance(events.get(key), dict) else {}

    record = EventRecord(
        harness=harness_clean,
        session_id=sid_clean,
        state=parsed_state,
        ts=now,
        parent_id=parent_clean or previous.get("parent_id"),
        is_subagent=subagent_flag or bool(previous.get("is_subagent")),
        project=project.strip() or str(previous.get("project") or ""),
        title=title.strip() or str(previous.get("title") or ""),
        agent=agent.strip() or str(previous.get("agent") or ""),
        model=model.strip() or str(previous.get("model") or ""),
        message=message.strip(),
    )

    events[key] = {
        "harness": record.harness,
        "session_id": record.session_id,
        "state": record.state.value,
        "ts": record.ts,
        "parent_id": record.parent_id,
        "is_subagent": record.is_subagent,
        "project": record.project,
        "title": record.title,
        "agent": record.agent,
        "model": record.model,
        "message": record.message,
    }
    cfg_mod.atomic_write_json(path or cfg_mod.events_path(), store, mode=0o600)
    return record


def load_events(
    *,
    harness: str | None = None,
    max_age_seconds: int = 1800,
    now: float | None = None,
    path: Path | None = None,
) -> list[EventRecord]:
    """Geçerli (bayatlamamış) push olaylarını döndürür."""
    current = float(now if now is not None else time.time())
    store = _load_raw(path)
    result: list[EventRecord] = []
    for entry in (store.get("events") or {}).values():
        if not isinstance(entry, dict):
            continue
        entry_harness = str(entry.get("harness") or "").strip().lower()
        if harness and entry_harness != harness.strip().lower():
            continue
        ts = entry.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        age = current - float(ts)
        if max_age_seconds > 0 and age > max_age_seconds:
            continue
        try:
            state = _normalize_state(str(entry.get("state") or "idle"))
        except ValueError:
            continue
        result.append(
            EventRecord(
                harness=entry_harness,
                session_id=str(entry.get("session_id") or ""),
                state=state,
                ts=float(ts),
                parent_id=entry.get("parent_id") or None,
                is_subagent=bool(entry.get("is_subagent") or entry.get("parent_id")),
                project=str(entry.get("project") or ""),
                title=str(entry.get("title") or ""),
                agent=str(entry.get("agent") or ""),
                model=str(entry.get("model") or ""),
                message=str(entry.get("message") or ""),
            )
        )
    result.sort(key=lambda item: item.ts, reverse=True)
    return result


def ack_done_sessions(
    session_keys: list[str] | None = None,
    *,
    now: float | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """'Tamamlandı (görülmedi)' durumundaki oturumları okundu olarak işaretler."""
    current = float(now if now is not None else time.time())
    target_state = state if state is not None else cfg_mod.load_state()
    seen = target_state.setdefault("seen_done", {})
    if not isinstance(seen, dict):
        seen = {}
        target_state["seen_done"] = seen

    if session_keys is None:
        seen["__all__"] = current
    else:
        for key in session_keys:
            seen[key] = current

    # Çok eski kayıtları (7 gün+) buda
    cutoff = current - 7 * 86400
    for key in list(seen):
        if key != "__all__" and isinstance(seen[key], (int, float)) and seen[key] < cutoff:
            del seen[key]

    cfg_mod.save_state(target_state)
    return target_state


def is_done_seen(
    harness: str,
    session_id: str,
    updated_ts: float | None,
    state: dict[str, Any] | None,
) -> bool:
    """Oturumun bitiş zamanı kullanıcının son kontrolünden önce mi?"""
    if not state or not updated_ts:
        return False
    seen = state.get("seen_done") or {}
    if not isinstance(seen, dict):
        return False
    global_ack = seen.get("__all__")
    if isinstance(global_ack, (int, float)) and float(global_ack) >= updated_ts:
        return True
    specific = seen.get(_event_key(harness, session_id))
    if isinstance(specific, (int, float)) and float(specific) >= updated_ts:
        return True
    return False
