"""agent-notifier çekirdek, FSM, adaptör ve opt-in filtreleme birim testleri.

Tüm testler yalıtılmıştır: gerçek kullanıcı veritabanına veya ağa dokunmaz.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_notifier import config as cfg_mod
from agent_notifier import engine, events as events_mod, fsm
from agent_notifier.adapters import antigravity_harness, copilot_provider, opencode_go_provider
from agent_notifier.models import SessionNode, SessionState

OPENCODE_SCHEMA_WITH_PARENT = """
CREATE TABLE session_v2 (
    id text PRIMARY KEY,
    parent_id text,
    project_id text NOT NULL,
    directory text NOT NULL,
    title text,
    agent text,
    model text,
    cost real DEFAULT 0 NOT NULL,
    tokens_input integer DEFAULT 0 NOT NULL,
    tokens_output integer DEFAULT 0 NOT NULL,
    tokens_cache_read integer DEFAULT 0 NOT NULL,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    time_archived integer,
    time_suspended integer
);

CREATE TABLE session_message (
    id text PRIMARY KEY,
    session_id text NOT NULL,
    type text NOT NULL,
    seq integer NOT NULL,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    data text NOT NULL
);

CREATE TABLE credential (
    id text PRIMARY KEY,
    provider text,
    data text
);

CREATE TABLE workspace (
    id text PRIMARY KEY
);
"""

OPENCODE_SCHEMA_LEGACY = """
CREATE TABLE session_v2 (
    id text PRIMARY KEY,
    project_id text NOT NULL,
    directory text NOT NULL,
    title text,
    agent text,
    model text,
    cost real DEFAULT 0 NOT NULL,
    tokens_input integer DEFAULT 0 NOT NULL,
    tokens_output integer DEFAULT 0 NOT NULL,
    tokens_cache_read integer DEFAULT 0 NOT NULL,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    time_archived integer,
    time_suspended integer
);

CREATE TABLE session_message (
    id text PRIMARY KEY,
    session_id text NOT NULL,
    type text NOT NULL,
    seq integer NOT NULL,
    time_created integer NOT NULL,
    time_updated integer NOT NULL,
    data text NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class IsolatedEnv:
    def __init__(self, tc: unittest.TestCase, *, legacy_db: bool = False) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-notifier-test-")
        tc.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "opencode.db"
        self.env = {
            "AGENT_NOTIFIER_CONFIG_DIR": str(self.root / "config"),
            "AGENT_NOTIFIER_DATA_DIR": str(self.root / "data"),
            "AGENT_NOTIFIER_STATE_DIR": str(self.root / "state"),
            "AGENT_NOTIFIER_CACHE_DIR": str(self.root / "cache"),
            "AGENT_NOTIFIER_OPENCODE_DB": str(self.db_path),
        }
        patcher = mock.patch.dict(os.environ, self.env, clear=False)
        patcher.start()
        tc.addCleanup(patcher.stop)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(OPENCODE_SCHEMA_LEGACY if legacy_db else OPENCODE_SCHEMA_WITH_PARENT)
        self.conn.commit()
        tc.addCleanup(self.conn.close)


class TestFSMAndConfig(unittest.TestCase):
    def test_fsm_priority_and_subagent_rollup(self) -> None:
        child_wait = SessionNode(id="sub-1", harness="opencode", state=SessionState.WAITING_INPUT, is_subagent=True)
        child_run = SessionNode(id="sub-2", harness="opencode", state=SessionState.RUNNING, is_subagent=True)
        root = SessionNode(
            id="root-1",
            harness="opencode",
            state=SessionState.RUNNING,
            children=[child_run, child_wait],
            updated_ts=100.0,
        )
        other = SessionNode(id="root-2", harness="antigravity", state=SessionState.DONE_UNSEEN, updated_ts=200.0)

        global_state = fsm.compute_effective_states([other, root])
        self.assertEqual(global_state, SessionState.WAITING_INPUT)
        self.assertEqual(root.effective_state, SessionState.WAITING_INPUT)

        ordered = fsm.sort_session_tree([other, root])
        self.assertEqual(ordered[0].id, "root-1")
        self.assertEqual(ordered[0].children[0].id, "sub-1")

        stats = fsm.count_tree_stats(ordered)
        self.assertEqual(stats["waiting"], 1)
        self.assertEqual(stats["active"], 2)
        self.assertEqual(stats["done_unseen"], 1)
        self.assertEqual(stats["subagents"], 2)

    def test_config_save_and_reload_roundtrip(self) -> None:
        IsolatedEnv(self)
        cfg = cfg_mod.load_config()
        cfg = cfg_mod.set_toggle(cfg, "harnesses", "antigravity", False)
        cfg = cfg_mod.set_toggle(cfg, "providers", "github_copilot", True)
        cfg_mod.save_config(cfg)

        reloaded = cfg_mod.load_config()
        self.assertEqual(cfg_mod.enabled_harnesses(reloaded), ["opencode"])
        self.assertEqual(set(cfg_mod.enabled_providers(reloaded)), {"opencode_go", "github_copilot"})


class TestAdaptersAndEngine(unittest.TestCase):
    def test_opencode_parent_subagent_tree_and_ack(self) -> None:
        env = IsolatedEnv(self)
        ts = now_ms()
        env.conn.execute(
            """
            INSERT INTO session_v2 (id, parent_id, project_id, directory, title, agent, model,
                                    cost, tokens_input, tokens_output, tokens_cache_read,
                                    time_created, time_updated, time_archived, time_suspended)
            VALUES (?, ?, 'p1', '/home/user/demo-app', 'Ana Görev', 'build',
                    '{"providerID":"opencode-go","id":"deepseek-v4.1-flash"}',
                    0.25, 1000, 500, 200, ?, ?, NULL, ?)
            """,
            ("ses_root", None, ts - 5000, ts - 1000, ts - 1000),
        )
        env.conn.execute(
            """
            INSERT INTO session_v2 (id, parent_id, project_id, directory, title, agent, model,
                                    cost, tokens_input, tokens_output, tokens_cache_read,
                                    time_created, time_updated, time_archived, time_suspended)
            VALUES (?, ?, 'p1', '/home/user/demo-app', 'Araştırma Alt Ajanı', 'explore',
                    '{"providerID":"opencode-go","id":"deepseek-v4.1-flash"}',
                    0.05, 400, 200, 0, ?, ?, NULL, NULL)
            """,
            ("ses_child", "ses_root", ts - 3000, ts - 500),
        )
        env.conn.commit()

        cfg = cfg_mod.load_config()
        cfg["providers"]["opencode_go"] = False
        cfg["harnesses"]["antigravity"] = False

        snap = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(len(snap.sessions), 1)
        root = snap.sessions[0]
        self.assertEqual(root.id, "ses_root")
        self.assertEqual(root.project, "demo-app")
        self.assertEqual(root.state, SessionState.RUNNING)
        self.assertEqual(len(root.children), 1)
        self.assertEqual(root.children[0].id, "ses_child")
        self.assertTrue(root.children[0].is_subagent)
        self.assertEqual(root.children[0].state, SessionState.DONE_UNSEEN)
        self.assertEqual(snap.global_state, SessionState.RUNNING)

        # Push olayı ile alt ajan soru bekliyor durumuna geçsin
        events_mod.record_event(
            harness="opencode",
            session_id="ses_child",
            parent_id="ses_root",
            state="waiting_input",
            message="İzin gerekiyor",
        )
        snap2 = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(snap2.global_state, SessionState.WAITING_INPUT)
        self.assertEqual(snap2.sessions[0].effective_state, SessionState.WAITING_INPUT)
        self.assertEqual(snap2.sessions[0].children[0].state, SessionState.WAITING_INPUT)

    def test_opencode_legacy_schema_without_parent_id(self) -> None:
        env = IsolatedEnv(self, legacy_db=True)
        ts = now_ms()
        env.conn.execute(
            """
            INSERT INTO session_v2 (id, project_id, directory, title, agent, model,
                                    cost, tokens_input, tokens_output, tokens_cache_read,
                                    time_created, time_updated, time_archived, time_suspended)
            VALUES ('ses_legacy', 'p1', '/tmp/proj', 'Eski Şema', 'build', 'opencode-go/deepseek-v4.1-flash',
                    0.1, 100, 50, 0, ?, ?, NULL, NULL)
            """,
            (ts - 10000, ts - 2000),
        )
        env.conn.commit()
        cfg = cfg_mod.load_config()
        cfg["providers"]["opencode_go"] = False
        cfg["harnesses"]["antigravity"] = False

        snap = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(len(snap.sessions), 1)
        self.assertEqual(snap.sessions[0].state, SessionState.DONE_UNSEEN)

        events_mod.ack_done_sessions()
        snap_after_ack = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(snap_after_ack.sessions[0].state, SessionState.IDLE)
        self.assertEqual(snap_after_ack.global_state, SessionState.IDLE)

    def test_opencode_waiting_input_reconciliation_with_db_writes(self) -> None:
        env = IsolatedEnv(self)
        now = time.time()
        # Oturum in-flight iken t-10sn'de waiting_input geldi, DB t-1sn'de metadata yazdı -> WAITING_INPUT korunmalı
        env.conn.execute(
            """
            INSERT INTO session_v2 (id, parent_id, project_id, directory, title, agent, model,
                                    cost, tokens_input, tokens_output, tokens_cache_read,
                                    time_created, time_updated, time_archived, time_suspended)
            VALUES ('ses_wait', NULL, 'p1', '/tmp/p', 'İzin Bekleyen', 'build', 'opencode-go/deepseek-v4.1-flash',
                    0.0, 0, 0, 0, ?, ?, NULL, ?)
            """,
            (int((now - 30) * 1000), int((now - 1) * 1000), int((now - 1) * 1000)),
        )
        env.conn.commit()
        events_mod.record_event(
            harness="opencode",
            session_id="ses_wait",
            state="waiting_input",
            ts=now - 10,
            message="Bash izni bekleniyor",
        )
        cfg = cfg_mod.load_config()
        cfg["providers"]["opencode_go"] = False
        cfg["harnesses"]["antigravity"] = False

        snap = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(snap.sessions[0].state, SessionState.WAITING_INPUT)

        # Oturum tamamlandığında (time_suspended = NULL, time_updated > waiting_ts) -> DONE_UNSEEN'e dönmeli
        env.conn.execute(
            "UPDATE session_v2 SET time_suspended = NULL, time_updated = ? WHERE id = 'ses_wait'",
            (int(now * 1000),),
        )
        env.conn.commit()
        snap2 = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(snap2.sessions[0].state, SessionState.DONE_UNSEEN)

    def test_antigravity_passive_events_and_zero_tos_violation(self) -> None:
        IsolatedEnv(self)
        # Kaynak kodda cloudcode veya oauth çağrısı olmadığını doğrula
        source_text = Path(antigravity_harness.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("cloudcode-pa.googleapis.com", source_text)
        self.assertNotIn("oauth2.googleapis.com", source_text)
        self.assertNotIn("refreshtoken", source_text)

        events_mod.record_event(
            harness="antigravity",
            session_id="conv-main",
            state="running",
            project="agent-notifier",
            title="Mimari Plan",
            agent="agy",
        )
        events_mod.record_event(
            harness="antigravity",
            session_id="conv-sub",
            parent_id="conv-main",
            state="waiting_input",
            project="agent-notifier",
            agent="research",
            message="Onay bekliyor",
        )

        cfg = cfg_mod.load_config()
        cfg["harnesses"] = {"opencode": False, "antigravity": True}
        cfg["providers"] = {"opencode_go": False, "github_copilot": False}

        snap = engine.collect_snapshot(config=cfg, offline=True)
        self.assertEqual(snap.global_state, SessionState.WAITING_INPUT)
        self.assertEqual(len(snap.sessions), 1)
        self.assertEqual(snap.sessions[0].id, "conv-main")
        self.assertEqual(len(snap.sessions[0].children), 1)
        self.assertEqual(snap.sessions[0].children[0].state, SessionState.WAITING_INPUT)
        self.assertEqual(snap.providers, [])

    def test_opt_in_providers_not_queried_when_disabled(self) -> None:
        IsolatedEnv(self)
        cfg = cfg_mod.load_config()
        cfg["harnesses"] = {"opencode": False, "antigravity": False}
        cfg["providers"] = {"opencode_go": False, "github_copilot": False}

        with mock.patch.object(
            copilot_provider.GitHubCopilotProviderAdapter, "collect", side_effect=AssertionError("çağrılmamalı")
        ), mock.patch.object(
            opencode_go_provider.OpenCodeGoProviderAdapter, "collect", side_effect=AssertionError("çağrılmamalı")
        ):
            snap = engine.collect_snapshot(config=cfg, offline=True)
            self.assertEqual(snap.providers, [])
            self.assertEqual(snap.harnesses, [])

    def test_opencode_go_hybrid_live_api_and_sanitized_cache(self) -> None:
        env = IsolatedEnv(self)
        ts = now_ms()
        env.conn.execute(
            "INSERT INTO credential (id, provider, data) VALUES ('1', 'opencode-go', ?)",
            (json.dumps({"access_token": "SECRET_TOKEN_DO_NOT_LEAK"}),),
        )
        env.conn.execute("INSERT INTO workspace (id) VALUES ('ws_secret_org_123')")
        env.conn.execute(
            """
            INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)
            VALUES ('m1', 's1', 'assistant', 1, ?, ?, ?)
            """,
            (
                ts - 60_000,
                ts - 60_000,
                json.dumps({"model": {"providerID": "opencode-go", "id": "deepseek-v4.1-flash"}, "cost": 4.0}),
            ),
        )
        env.conn.commit()

        api_payload = {
            "usage": {
                "rolling": {"status": "ok", "percent": 25.0, "resetsAt": "2099-01-01T00:00:00Z"},
                "weekly": {"status": "ok", "percent": 10.0, "resetsAt": "2099-01-07T00:00:00Z"},
                "monthly": {"status": "ok", "percent": 5.0, "resetsAt": "2099-02-01T00:00:00Z"},
            }
        }

        class FakeResp:
            def read(self) -> bytes:
                return json.dumps(api_payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        cfg = cfg_mod.load_config()
        adapter = opencode_go_provider.OpenCodeGoProviderAdapter()
        with mock.patch.object(opencode_go_provider, "urlopen", return_value=FakeResp()):
            quota = adapter.collect(config=cfg, force_refresh=True, offline=False)

        self.assertTrue(quota.available)
        self.assertEqual(quota.source, "hybrid")
        self.assertEqual(quota.headline_pct, 75.0)
        self.assertEqual(len(quota.windows), 3)
        self.assertEqual(len(quota.pools), 4)

        # Önbellekte gizli token veya orgID sızıntısı olmamalı
        cache_text = cfg_mod.provider_cache_path("opencode_go").read_text(encoding="utf-8")
        self.assertNotIn("SECRET_TOKEN_DO_NOT_LEAK", cache_text)
        self.assertNotIn("ws_secret_org_123", cache_text)

    def test_opencode_go_reads_real_v2_control_account_and_credential_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode_v2.db"
            con = sqlite3.connect(db)
            con.executescript(
                """
                CREATE TABLE control_account (
                    email text NOT NULL,
                    url text NOT NULL,
                    access_token text NOT NULL,
                    refresh_token text NOT NULL,
                    token_expiry integer,
                    active integer NOT NULL,
                    time_created integer NOT NULL,
                    time_updated integer NOT NULL
                );
                CREATE TABLE credential (
                    id text PRIMARY KEY,
                    integration_id text,
                    label text NOT NULL,
                    value text NOT NULL,
                    connector_id text,
                    method_id text,
                    active integer,
                    time_created integer NOT NULL,
                    time_updated integer NOT NULL
                );
                CREATE TABLE workspace (
                    id text PRIMARY KEY,
                    provider text NOT NULL,
                    binding text,
                    created_at integer NOT NULL,
                    last_used_at integer NOT NULL
                );
                INSERT INTO control_account VALUES
                    ('a@example.com', 'https://opencode.ai', 'TOK_CTRL_ACTIVE', 'REF', NULL, 1, 100, 200);
                INSERT INTO workspace VALUES
                    ('ws_old', 'other', NULL, 100, 150),
                    ('ws_go_latest', 'opencode', NULL, 100, 300);
                """
            )
            con.commit()

            # credential boşken control_account'tan active access_token ve opencode workspace okunmalı
            tok, org = opencode_go_provider._read_go_credentials(db)
            self.assertEqual(tok, "TOK_CTRL_ACTIVE")
            self.assertEqual(org, "ws_go_latest")

            # credential tablosunda opencode-go ham anahtar eklendiğinde öncelik almalı
            con.execute(
                "INSERT INTO credential VALUES ('c1', 'opencode-go', 'Go Key', 'RAW_GO_KEY', 'opencode-go', 'api', 1, 100, 400)"
            )
            con.commit()
            con.close()

            tok2, org2 = opencode_go_provider._read_go_credentials(db)
            self.assertEqual(tok2, "RAW_GO_KEY")
            self.assertEqual(org2, "ws_go_latest")


if __name__ == "__main__":
    unittest.main()
