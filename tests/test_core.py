"""waybar-ai-quota çekirdek birim testleri.

Testler gerçek hesap verisine veya ağa dokunmaz:
- Geçici bir SQLite `opencode.db` şeması oluşturulur.
- Antigravity ve Copilot verileri sahte JSON dosyalarından okunur.
- Tüm XDG yolları geçici dizine yönlendirilir.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from waybar_ai_quota import cli, collectors, config as cfg_mod, models as m, recommender

# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

DB_SCHEMA = """
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

CONFIG_TOML = """
[thresholds]
critical_pct = 15
antigravity_primary_pct = 20
recent_activity_seconds = 300
conflict_window_seconds = 90

[refresh]
antigravity_cache_seconds = 600
copilot_cache_seconds = 600

[cloud]
antigravity_live_refresh = false

[copilot]
gh_binary = "/nonexistent/gh-for-tests"

[notifications]
enabled = false
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_in(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TempProject:
    """Geçici XDG dizinleri + test veritabanı oluşturur."""

    def __init__(self, testcase: unittest.TestCase) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="waybar-ai-quota-test-")
        testcase.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "opencode.db"
        self.accounts_path = self.root / "antigravity-accounts.json"
        self.config_path = self.root / "config.toml"
        self.state_path = self.root / "state.json"
        self.copilot_path = self.root / "copilot_cache.json"
        self.config_path.write_text(CONFIG_TOML, encoding="utf-8")
        self.env = {
            "WAYBAR_AI_QUOTA_OPENCODE_DB": str(self.db_path),
            "WAYBAR_AI_QUOTA_ACCOUNTS": str(self.accounts_path),
            "WAYBAR_AI_QUOTA_CONFIG": str(self.config_path),
            "WAYBAR_AI_QUOTA_STATE": str(self.state_path),
            "WAYBAR_AI_QUOTA_COPILOT_CACHE": str(self.copilot_path),
            "WAYBAR_AI_QUOTA_DATA_DIR": str(self.root / "data"),
            "WAYBAR_AI_QUOTA_CONFIG_DIR": str(self.root / "config"),
            "WAYBAR_AI_QUOTA_CACHE_DIR": str(self.root / "cache"),
            "WAYBAR_AI_QUOTA_ANTIGRAVITY_CLIENT_ID": "",
            "WAYBAR_AI_QUOTA_ANTIGRAVITY_CLIENT_SECRET": "",
        }
        patcher = mock.patch.dict(os.environ, self.env, clear=False)
        patcher.start()
        testcase.addCleanup(patcher.stop)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(DB_SCHEMA)
        self.conn.commit()
        testcase.addCleanup(self.conn.close)

    def add_session(
        self,
        session_id: str,
        *,
        model: dict | str | None = None,
        title: str = "Test oturumu",
        agent: str = "build",
        directory: str = "/home/test/proje-a",
        cost: float = 0.0,
        tokens: int = 1200,
        updated_ago_seconds: int = 0,
        in_flight: bool = False,
        archived: bool = False,
    ) -> None:
        updated = now_ms() - updated_ago_seconds * 1000
        model_text = json.dumps(model) if isinstance(model, dict) else model
        self.conn.execute(
            """
            INSERT INTO session_v2
                (id, project_id, directory, title, agent, model, cost,
                 tokens_input, tokens_output, tokens_cache_read,
                 time_created, time_updated, time_archived, time_suspended)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                "proj_test",
                directory,
                title,
                agent,
                model_text,
                cost,
                tokens,
                tokens // 2,
                0,
                updated,
                updated,
                updated if archived else None,
                updated if in_flight else None,
            ),
        )
        self.conn.commit()

    def add_message(
        self,
        message_id: str,
        session_id: str,
        *,
        model_provider: str = "opencode-go",
        model_id: str = "deepseek-v4.1-flash",
        cost: float = 0.0,
        ago_seconds: int = 0,
        message_type: str = "assistant",
        seq: int = 1,
    ) -> None:
        created = now_ms() - ago_seconds * 1000
        data = {"model": {"providerID": model_provider, "id": model_id}, "cost": cost}
        self.conn.execute(
            """
            INSERT INTO session_message
                (id, session_id, type, seq, time_created, time_updated, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, message_type, seq, created, created, json.dumps(data)),
        )
        self.conn.commit()

    def write_accounts(self, accounts: list[dict]) -> None:
        self.accounts_path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")

    def write_copilot_cache(self, data: dict, fetched_at: float | None = None) -> None:
        payload = {"fetched_at": fetched_at if fetched_at is not None else time.time(), "data": data}
        self.copilot_path.write_text(json.dumps(payload), encoding="utf-8")


def make_antigravity(
    *,
    claude: float = 70.0,
    gemini_pro: float = 80.0,
    gemini_flash: float = 90.0,
    claude_locked: bool = False,
) -> m.AntigravityStatus:
    def group(key: str, label: str, pct: float, locked: bool = False) -> m.AntigravityGroup:
        return m.AntigravityGroup(
            key=key,
            label=label,
            remaining_pct=pct,
            remaining_fraction=pct / 100.0,
            rate_limited=locked,
            critical=pct < 15,
        )

    return m.AntigravityStatus(
        available=True,
        groups=[
            group("claude", "Claude (Opus/Sonnet)", claude, claude_locked),
            group("gemini-pro", "Gemini Pro (Argon)", gemini_pro),
            group("gemini-flash", "Gemini Flash", gemini_flash),
        ],
    )


def make_go(
    *,
    flash: float | None = 80.0,
    mid: float | None = 60.0,
    argon: float | None = 40.0,
    premium: float | None = 25.0,
) -> m.GoStatus:
    specs = [
        ("flash", "DeepSeek V4.1 Flash", flash),
        ("mid", "GLM 5.3 Flash / Kimi K2.7 Code", mid),
        ("argon", "Gemini 4 Argon", argon),
        ("premium", "Premium havuz", premium),
    ]
    pools = [
        m.GoPoolStatus(key=key, label=label, remaining_pct=pct)
        for key, label, pct in specs
        if pct is not None
    ]
    known = [pool.remaining_pct for pool in pools]
    return m.GoStatus(
        available=bool(pools),
        pools=pools,
        total=m.GoTotalStatus(remaining_pct=min(known) if known else 100.0),
    )


def make_copilot(*, chat: float = 100.0, completions: float = 100.0, premium: float = 50.0) -> m.CopilotStatus:
    return m.CopilotStatus(
        available=True,
        plan_label="Copilot Student",
        quotas=[
            m.CopilotQuota(key="chat", label="Chat", percent_remaining=chat),
            m.CopilotQuota(key="completions", label="Completions", percent_remaining=completions),
            m.CopilotQuota(
                key="premium_interactions",
                label="Premium Interactions / AI Credits",
                percent_remaining=premium,
            ),
        ],
    )


def no_sessions() -> m.SessionStatus:
    return m.SessionStatus(available=True, sessions=[])


CONFIG = {"thresholds": {"critical_pct": 15, "antigravity_primary_pct": 20}}


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class ModelsTest(unittest.TestCase):
    def test_antigravity_group_lookup_and_serialization(self) -> None:
        status = make_antigravity(claude=42.5)
        self.assertIsNotNone(status.group("claude"))
        self.assertIsNone(status.group("yok"))
        payload = status.to_dict()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["groups"][0]["key"], "claude")
        self.assertEqual(payload["groups"][0]["remaining_pct"], 42.5)
        self.assertNotIn("refreshToken", json.dumps(payload))

    def test_go_and_copilot_lookup(self) -> None:
        go = make_go()
        self.assertEqual(go.pool("flash").remaining_pct, 80.0)
        self.assertIsNone(go.pool("yok"))
        self.assertEqual(go.to_dict()["pools"][0]["key"], "flash")
        self.assertIn("total", go.to_dict())

        copilot = make_copilot(chat=33.0)
        self.assertEqual(copilot.quota("chat").percent_remaining, 33.0)
        self.assertIsNone(copilot.quota("yok"))
        self.assertEqual(copilot.to_dict()["quotas"][0]["label"], "Chat")

    def test_session_and_suggestion_fields(self) -> None:
        session = m.SessionInfo(id="ses_1", title="Demo", model="opencode-go/deepseek-v4.1-flash")
        self.assertEqual(session.to_dict()["id"], "ses_1")
        suggestion = m.Suggestion(
            rank=1,
            title="Flash",
            model_id="opencode-go/deepseek-v4.1-flash",
            copy_command="/model opencode-go/deepseek-v4.1-flash",
            reason="test",
        )
        self.assertTrue(suggestion.copy_command.startswith("/model "))

    def test_full_status_contains_all_sections(self) -> None:
        status = m.FullStatus(generated_at="2026-01-01T00:00:00Z", generated_ts=0)
        payload = status.to_dict()
        for key in ("sessions", "antigravity", "go", "copilot", "recommendations", "notifications"):
            self.assertIn(key, payload)


# ---------------------------------------------------------------------------
# recommender
# ---------------------------------------------------------------------------


class RecommenderTest(unittest.TestCase):
    def test_easy_prefers_go_flash_when_healthy(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(),
            make_go(),
            make_copilot(),
            CONFIG,
        )
        self.assertEqual(result.easy_medium[0].model_id, recommender.MODEL_FLASH)
        self.assertEqual(result.easy_medium[0].badge, recommender.BADGE_PRIMARY)
        self.assertTrue(result.easy_medium[0].reason)

    def test_easy_falls_back_to_mid_pool(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(),
            make_go(flash=5.0, mid=70.0),
            make_copilot(),
            CONFIG,
        )
        self.assertEqual(result.easy_medium[0].model_id, recommender.MODEL_GLM)
        self.assertIn("%5", result.easy_medium[0].reason)

    def test_easy_all_go_empty_uses_copilot(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(),
            make_go(flash=0.0, mid=0.0),
            make_copilot(chat=88.0, completions=88.0, premium=0.0),
            CONFIG,
        )
        self.assertEqual(result.easy_medium[0].model_id, recommender.MODEL_COPILOT_GPT)

    def test_hard_prefers_antigravity_claude_when_healthy(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(claude=75.0),
            make_go(),
            make_copilot(),
            CONFIG,
        )
        self.assertEqual(result.hard[0].model_id, recommender.MODEL_AG_CLAUDE)

    def test_hard_avoid_claude_on_dual_antigravity_conflict(self) -> None:
        sessions = m.SessionStatus(
            available=True,
            dual_antigravity_conflict=True,
            conflict_sessions=["proje-a · antigravity/x", "proje-b · antigravity/y"],
        )
        result = recommender.build_recommendations(
            sessions,
            make_antigravity(claude=95.0),
            make_go(premium=60.0),
            make_copilot(),
            CONFIG,
        )
        self.assertEqual(result.hard[0].model_id, recommender.MODEL_GO_PRO)
        self.assertNotEqual(result.hard[0].model_id, recommender.MODEL_AG_CLAUDE)
        self.assertTrue(any("Çift Antigravity" in note for note in result.notes))
        self.assertIn("çakış", result.hard[0].reason.lower())

    def test_hard_falls_back_when_claude_locked(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(claude=90.0, claude_locked=True),
            make_go(premium=55.0),
            make_copilot(),
            CONFIG,
        )
        self.assertEqual(result.hard[0].model_id, recommender.MODEL_GO_PRO)

    def test_everything_exhausted_returns_free_model_last_resort(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(claude=0.0, gemini_pro=0.0, gemini_flash=0.0),
            make_go(flash=0.0, mid=0.0, argon=0.0, premium=0.0),
            m.CopilotStatus(available=False),
            CONFIG,
        )
        self.assertEqual(result.easy_medium[0].model_id, recommender.MODEL_FREE)
        self.assertEqual(result.hard[0].model_id, recommender.MODEL_FREE)
        self.assertEqual(result.hard[-1].badge, recommender.BADGE_LAST)

    def test_ranks_are_sequential_and_models_unique(self) -> None:
        result = recommender.build_recommendations(
            no_sessions(),
            make_antigravity(),
            make_go(),
            make_copilot(),
            CONFIG,
        )
        for items in (result.easy_medium, result.hard):
            ids = [item.model_id for item in items]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual([item.rank for item in items], list(range(1, len(items) + 1)))
            for item in items:
                self.assertEqual(item.copy_command, f"/model {item.model_id}")
        self.assertIsNotNone(result.generated_at)


# ---------------------------------------------------------------------------
# collectors
# ---------------------------------------------------------------------------


class SessionCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TempProject(self)

    def test_missing_db_returns_graceful_error(self) -> None:
        self.project.db_path.unlink()
        status = collectors.collect_sessions()
        self.assertFalse(status.available)
        self.assertIn("bulunamadı", status.error or "")

    def test_sessions_are_read_and_conflict_is_detected(self) -> None:
        ag = {"providerID": "google", "id": "antigravity-claude-opus-5-5-high"}
        self.project.add_session("ses_a", model=ag, directory="/home/test/proje-a", in_flight=True)
        self.project.add_session("ses_b", model=ag, directory="/home/test/proje-b", in_flight=True)
        self.project.add_session(
            "ses_c",
            model={"providerID": "opencode-go", "id": "deepseek-v4.1-flash"},
            directory="/home/test/proje-c",
        )

        status = collectors.collect_sessions()
        self.assertTrue(status.available)
        self.assertEqual(len(status.sessions), 3)
        self.assertEqual(status.active_count, 2)
        self.assertTrue(status.dual_antigravity_conflict)
        self.assertEqual(len(status.conflict_sessions), 2)
        self.assertTrue(any(item.is_antigravity for item in status.sessions))
        self.assertEqual(
            {item.project for item in status.sessions},
            {"proje-a", "proje-b", "proje-c"},
        )

    def test_model_falls_back_to_last_assistant_message(self) -> None:
        self.project.add_session("ses_fallback", model=None)
        self.project.add_message(
            "msg_1",
            "ses_fallback",
            model_id="glm-5.3-flash",
            cost=0.1,
        )
        status = collectors.collect_sessions()
        session = next(item for item in status.sessions if item.id == "ses_fallback")
        self.assertEqual(session.provider_id, "opencode-go")
        self.assertEqual(session.model_id, "glm-5.3-flash")
        self.assertEqual(session.model, "opencode-go/glm-5.3-flash")

    def test_archived_sessions_are_excluded(self) -> None:
        self.project.add_session("ses_old", archived=True)
        status = collectors.collect_sessions()
        self.assertEqual(status.sessions, [])

    def test_short_model_name_and_duration_formatting(self) -> None:
        self.assertEqual(
            collectors.short_model_name("google/antigravity-gemini-3.8-flash"),
            "gemini-3.8-flash",
        )
        self.assertEqual(collectors.short_model_name(""), "boşta")
        self.assertEqual(collectors.format_duration_tr(45), "45sn")
        self.assertEqual(collectors.format_duration_tr(3600 + 120), "1s 2dk")
        self.assertEqual(collectors.format_duration_tr(0), "0sn")


class GoCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TempProject(self)

    def test_three_window_spend_per_pool(self) -> None:
        self.project.add_message("m1", "ses_1", cost=2.0, ago_seconds=6 * 3600)
        self.project.add_message("m2", "ses_1", cost=1.0, ago_seconds=60)
        self.project.add_message(
            "m3", "ses_2", model_id="glm-5.3-flash", cost=3.0, ago_seconds=600
        )
        self.project.add_message(
            "m4",
            "ses_2",
            model_provider="github-copilot",
            model_id="gpt-4.1",
            cost=9.0,
            ago_seconds=60,
        )

        status = collectors.collect_opencode_go()
        self.assertTrue(status.available)
        flash = status.pool("flash")
        mid = status.pool("mid")
        self.assertAlmostEqual(flash.spend["5h"], 1.0, places=4)
        self.assertAlmostEqual(flash.spend["weekly"], 3.0, places=4)
        self.assertAlmostEqual(flash.spend["monthly"], 3.0, places=4)
        self.assertAlmostEqual(flash.remaining["5h"], 95.0, places=1)
        self.assertAlmostEqual(mid.spend["5h"], 3.0, places=4)
        self.assertAlmostEqual(mid.remaining["5h"], 75.0, places=1)
        self.assertAlmostEqual(status.total.spend["5h"], 4.0, places=4)
        self.assertFalse(flash.calibrated)

    def test_session_residual_cost_is_counted(self) -> None:
        self.project.add_session(
            "ses_res",
            model={"providerID": "opencode-go", "id": "deepseek-v4.1-flash"},
            cost=2.5,
        )
        status = collectors.collect_opencode_go()
        flash = status.pool("flash")
        self.assertAlmostEqual(flash.spend["5h"], 2.5, places=4)
        self.assertAlmostEqual(flash.remaining["5h"], 87.5, places=1)

    def test_calibration_overrides_pool_headline(self) -> None:
        self.project.add_message("m1", "ses_1", cost=4.0, ago_seconds=60)
        state = cfg_mod.load_state()
        cfg_mod.set_calibration(state, "flash", remaining_pct=50.0)
        cfg_mod.save_state(state)

        status = collectors.collect_opencode_go(state=cfg_mod.load_state())
        flash = status.pool("flash")
        self.assertTrue(flash.calibrated)
        self.assertEqual(flash.remaining_pct, 50.0)
        self.assertEqual(flash.calibration["remaining_pct"], 50.0)

    def test_missing_db_returns_error(self) -> None:
        self.project.db_path.unlink()
        status = collectors.collect_opencode_go()
        self.assertFalse(status.available)
        self.assertIn("bulunamadı", status.error or "")


class AntigravityCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TempProject(self)

    def test_cached_quota_groups_critical_and_rate_limit(self) -> None:
        self.project.write_accounts(
            [
                {
                    "enabled": True,
                    "cachedQuotaUpdatedAt": now_ms(),
                    "cachedQuota": {
                        "claude": {
                            "remainingFraction": 0.5,
                            "remainingFractionResetTime": iso_in(7200),
                            "modelCount": 3,
                        },
                        "gemini-pro": {
                            "remainingFraction": 0.1,
                            "remainingFractionResetTime": iso_in(86400),
                            "modelCount": 1,
                        },
                        "gemini-flash": {
                            "remainingFraction": 0.9,
                            "remainingFractionResetTime": iso_in(3600),
                            "modelCount": 2,
                        },
                    },
                    "rateLimitResetTimes": {"claude-opus": iso_in(1800)},
                }
            ]
        )

        status = collectors.collect_antigravity(allow_live=False)
        self.assertTrue(status.available)
        self.assertEqual(status.account_count, 1)
        self.assertFalse(status.stale)
        claude = status.group("claude")
        self.assertAlmostEqual(claude.remaining_pct, 50.0, places=1)
        self.assertTrue(claude.rate_limited)
        self.assertIsNotNone(claude.rate_limit_human)
        self.assertIsNotNone(claude.reset_human)
        self.assertEqual(claude.model_count, 3)
        pro = status.group("gemini-pro")
        self.assertTrue(pro.critical)
        self.assertAlmostEqual(pro.remaining_pct, 10.0, places=1)
        self.assertFalse(status.group("gemini-flash").critical)

    def test_disabled_account_is_ignored(self) -> None:
        self.project.write_accounts(
            [
                {
                    "enabled": False,
                    "cachedQuota": {"claude": {"remainingFraction": 0.4}},
                }
            ]
        )
        status = collectors.collect_antigravity(allow_live=False)
        self.assertFalse(status.available)
        self.assertEqual(status.account_count, 0)

    def test_missing_accounts_file_is_graceful(self) -> None:
        status = collectors.collect_antigravity(allow_live=False)
        self.assertFalse(status.available)
        self.assertIn("bulunamadı", status.error or "")

    def test_lowest_fraction_wins_across_accounts(self) -> None:
        self.project.write_accounts(
            [
                {
                    "cachedQuotaUpdatedAt": now_ms(),
                    "cachedQuota": {"claude": {"remainingFraction": 0.8}},
                },
                {
                    "cachedQuotaUpdatedAt": now_ms(),
                    "cachedQuota": {"claude": {"remainingFraction": 0.3}},
                },
            ]
        )
        status = collectors.collect_antigravity(allow_live=False)
        self.assertAlmostEqual(status.group("claude").remaining_pct, 30.0, places=1)


class CopilotCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TempProject(self)

    def test_cached_data_is_used_without_network(self) -> None:
        self.project.write_copilot_cache(
            {
                "plan": "individual",
                "plan_label": "Copilot Pro",
                "reset_date": "2026-12-01",
                "quotas": [
                    {
                        "key": "chat",
                        "label": "Chat",
                        "percent_remaining": 72.0,
                        "unlimited": False,
                        "has_quota": True,
                    },
                    {
                        "key": "premium_interactions",
                        "label": "Premium Interactions / AI Credits",
                        "percent_remaining": 12.0,
                        "unlimited": False,
                        "has_quota": True,
                    },
                ],
            }
        )
        status = collectors.collect_github_copilot(offline=True)
        self.assertTrue(status.available)
        self.assertTrue(status.cached)
        self.assertEqual(status.plan_label, "Copilot Pro")
        self.assertEqual(status.quota("chat").percent_remaining, 72.0)
        self.assertEqual(status.quota("premium_interactions").percent_remaining, 12.0)

    def test_stale_cache_offline_returns_cached_with_warning(self) -> None:
        self.project.write_copilot_cache(
            {"plan": "free", "plan_label": "Copilot Free", "quotas": []},
            fetched_at=time.time() - 7200,
        )
        status = collectors.collect_github_copilot(offline=True)
        self.assertTrue(status.cached)
        self.assertIn("bayat", status.error or "")

    def test_missing_cache_and_offline(self) -> None:
        status = collectors.collect_github_copilot(offline=True)
        self.assertFalse(status.available)
        self.assertIn("önbelle", (status.error or "").lower())

    def test_sanitize_strips_personal_fields(self) -> None:
        payload = {
            "access_type_sku": "copilot_student",
            "copilot_plan": "individual",
            "quota_reset_date": "2026-12-01",
            "email": "kisi@example.invalid",
            "login": "kullanici",
            "quota_snapshots": {
                "chat": {"percent_remaining": 80.0, "unlimited": False, "has_quota": True},
                "completions": {
                    "percent_remaining": 100.0,
                    "unlimited": True,
                    "has_quota": True,
                },
            },
        }
        data = collectors._sanitize_copilot(payload, CONFIG)
        serialized = json.dumps(data)
        self.assertNotIn("example.com", serialized)
        self.assertNotIn("kullanici", serialized)
        self.assertNotIn("email", data)
        self.assertEqual(data["plan_label"], "Copilot Student")
        self.assertEqual(data["quotas"][0]["key"], "chat")
        self.assertTrue(data["quotas"][1]["unlimited"])


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TempProject(self)
        self.project.add_session(
            "ses_cli",
            model={"providerID": "opencode-go", "id": "deepseek-v4.1-flash"},
            in_flight=True,
        )
        self.project.add_message("msg_cli", "ses_cli", cost=1.0, ago_seconds=60)
        self.project.write_accounts(
            [
                {
                    "cachedQuotaUpdatedAt": now_ms(),
                    "cachedQuota": {
                        "claude": {"remainingFraction": 0.8, "remainingFractionResetTime": iso_in(3600)},
                        "gemini-pro": {"remainingFraction": 0.7, "remainingFractionResetTime": iso_in(86400)},
                        "gemini-flash": {"remainingFraction": 0.9, "remainingFractionResetTime": iso_in(3600)},
                    },
                }
            ]
        )
        self.project.write_copilot_cache(
            {
                "plan": "student",
                "plan_label": "Copilot Student",
                "quotas": [
                    {"key": "chat", "label": "Chat", "percent_remaining": 75.0, "has_quota": True},
                    {"key": "completions", "label": "Completions", "percent_remaining": 90.0, "has_quota": True},
                ],
            }
        )

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buffer = StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return code, buffer.getvalue()

    def test_waybar_outputs_valid_single_line_json(self) -> None:
        code, output = self._run(["waybar"])
        self.assertEqual(code, 0)
        lines = [line for line in output.strip().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertIn("text", payload)
        self.assertIn("tooltip", payload)
        self.assertIn("class", payload)
        self.assertIsInstance(payload["percentage"], int)
        self.assertIn(payload["class"], {"normal", "active", "warning", "critical", "conflict"})
        self.assertIn("AG %", payload["text"])
        self.assertIn("Go %", payload["text"])
        self.assertNotIn("refreshToken", output)
        self.assertNotIn("email", output)

    def test_status_outputs_full_json(self) -> None:
        code, output = self._run(["status", "--no-notify"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        for key in (
            "generated_at",
            "sessions",
            "antigravity",
            "go",
            "copilot",
            "recommendations",
            "notifications",
        ):
            self.assertIn(key, payload)
        self.assertTrue(payload["antigravity"]["available"])
        self.assertTrue(payload["go"]["available"])
        self.assertTrue(payload["copilot"]["available"])
        self.assertGreaterEqual(len(payload["recommendations"]["easy_medium"]), 1)
        self.assertGreaterEqual(len(payload["recommendations"]["hard"]), 1)

    def test_calibrate_writes_and_reports_json(self) -> None:
        code, output = self._run(["calibrate", "--pool", "flash", "--remaining-pct", "85"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pool"], "flash")
        self.assertEqual(payload["calibration"]["remaining_pct"], 85.0)

        code, output = self._run(["calibrate"])
        self.assertEqual(code, 0)
        summary = json.loads(output)
        self.assertEqual(summary["go_calibration"]["pools"]["flash"]["remaining_pct"], 85.0)
        flash = next(item for item in summary["pools"] if item["key"] == "flash")
        self.assertTrue(flash["calibrated"])
        self.assertEqual(flash["remaining_pct"], 85.0)

    def test_calibrate_rejects_out_of_range_percentage(self) -> None:
        stderr = StringIO()
        with redirect_stdout(StringIO()), mock.patch("sys.stderr", stderr):
            code = cli.main(["calibrate", "--pool", "flash", "--remaining-pct", "150"])
        self.assertEqual(code, 2)
        self.assertIn("0 ile 100", stderr.getvalue())

    def test_calibrate_reset(self) -> None:
        self._run(["calibrate", "--pool", "flash", "--remaining-pct", "40"])
        code, output = self._run(["calibrate", "--reset"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reset"], "all")
        self.assertEqual(payload["go_calibration"]["pools"], {})

    def test_version_flag(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(StringIO()):
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)


# ---------------------------------------------------------------------------
# config / kalibrasyon
# ---------------------------------------------------------------------------


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TempProject(self)

    def test_load_config_merges_defaults(self) -> None:
        config = cfg_mod.load_config()
        self.assertEqual(config["thresholds"]["critical_pct"], 15)
        self.assertEqual(config["refresh"]["local_active_seconds"], 5)
        self.assertEqual(config["copilot"]["gh_binary"], "/nonexistent/gh-for-tests")

    def test_state_roundtrip_and_calibration_math(self) -> None:
        state = cfg_mod.load_state()
        entry = cfg_mod.set_calibration(state, "total", offset_pct=10.0)
        self.assertEqual(entry["offset_pct"], 10.0)
        cfg_mod.save_state(state)
        reloaded = cfg_mod.load_state()
        self.assertEqual(cfg_mod.get_calibration(reloaded, "total")["offset_pct"], 10.0)

        pct, spend, applied = cfg_mod.apply_calibration(80.0, 1.0, 10.0, {"offset_pct": 12.5})
        self.assertTrue(applied)
        self.assertEqual(pct, 67.5)
        self.assertEqual(spend, 1.0)

        pct, spend, applied = cfg_mod.apply_calibration(
            80.0, 1.0, 10.0, {"spent_usd": 2.0, "offset_pct": 0.0}
        )
        self.assertTrue(applied)
        self.assertEqual(pct, 70.0)
        self.assertEqual(spend, 3.0)

    def test_reset_calibration_single_pool(self) -> None:
        state = cfg_mod.load_state()
        cfg_mod.set_calibration(state, "flash", remaining_pct=50.0)
        cfg_mod.set_calibration(state, "mid", remaining_pct=40.0)
        cfg_mod.reset_calibration(state, "flash")
        self.assertIsNone(cfg_mod.get_calibration(state, "flash"))
        self.assertIsNotNone(cfg_mod.get_calibration(state, "mid"))


if __name__ == "__main__":
    unittest.main()
