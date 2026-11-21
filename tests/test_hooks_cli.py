"""Hook yöneticisi, CLI, formatlayıcılar ve Linux SNI/ikon birim testleri."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from agent_notifier import cli, config as cfg_mod, events as events_mod, formatters, hooks as hooks_mod
from agent_notifier.linux import icons, sni
from agent_notifier.models import ProviderQuota, SessionNode, SessionState, Snapshot


class TestHooksSafety(unittest.TestCase):
    def test_antigravity_hook_preserves_existing_groups_and_backs_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "hooks.json"
            existing = {
                "beyin-v3": {
                    "Stop": [{"type": "command", "command": "echo keep-me", "timeout": 5}]
                }
            }
            target.write_text(json.dumps(existing, indent=2), encoding="utf-8")

            # Dry-run dosyaya dokunmamalı
            dry = hooks_mod.install_hook("antigravity", target=target, dry_run=True, binary="/bin/an")
            self.assertTrue(dry["dry_run"])
            self.assertNotIn("agent-notifier", json.loads(target.read_text(encoding="utf-8")))

            # Gerçek kurulum .bak almalı ve beyin-v3'ü korumalı
            res = hooks_mod.install_hook("antigravity", target=target, dry_run=False, binary="/bin/an")
            self.assertTrue(res["ok"])
            self.assertIn("beyin-v3", res["preserved_groups"])
            self.assertTrue(Path(res["backup"]).is_file())

            merged = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("beyin-v3", merged)
            self.assertIn("agent-notifier", merged)
            self.assertIn("PreToolUse", merged["agent-notifier"])
            self.assertEqual(merged["agent-notifier"]["PreToolUse"][0]["matcher"], "ask_question")

            # Uninstall yalnız agent-notifier grubunu silmeli
            un = hooks_mod.uninstall_hook("antigravity", target=target)
            self.assertTrue(un["changed"])
            after = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("beyin-v3", after)
            self.assertNotIn("agent-notifier", after)

    def test_antigravity_native_plugin_bundle_and_legacy_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugins" / "agent-notifier"
            legacy_hooks = Path(tmp) / "hooks.json"
            legacy_hooks.write_text(
                json.dumps(
                    {
                        "beyin-v3": {"Stop": []},
                        "agent-notifier": {"Stop": []},
                    }
                ),
                encoding="utf-8",
            )

            # Dry-run plugin oluşturmamalı ve legacy dosyasına dokunmamalı
            dry = hooks_mod.install_hook(
                "antigravity", target=plugin_dir, legacy_hooks_path=legacy_hooks, dry_run=True, binary="/bin/an"
            )
            self.assertTrue(dry["dry_run"])
            self.assertFalse(plugin_dir.exists())
            self.assertIn("agent-notifier", json.loads(legacy_hooks.read_text(encoding="utf-8")))

            # Gerçek kurulum plugin.json + hooks.json yazmalı ve legacy agent-notifier grubunu temizlemeli
            res = hooks_mod.install_hook(
                "antigravity", target=plugin_dir, legacy_hooks_path=legacy_hooks, dry_run=False, binary="/bin/an"
            )
            self.assertEqual(res["mode"], "plugin")
            self.assertTrue(res["cleaned_legacy_hooks"])
            self.assertTrue((plugin_dir / "plugin.json").is_file())
            self.assertTrue((plugin_dir / "hooks.json").is_file())
            manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "agent-notifier")

            legacy_after = json.loads(legacy_hooks.read_text(encoding="utf-8"))
            self.assertIn("beyin-v3", legacy_after)
            self.assertNotIn("agent-notifier", legacy_after)

            # Uninstall eklenti dizinini temizlemeli
            un = hooks_mod.uninstall_hook("antigravity", target=plugin_dir, legacy_hooks_path=legacy_hooks)
            self.assertTrue(un["changed"])
            self.assertFalse(plugin_dir.exists())

    def test_opencode_plugin_install_and_refuses_foreign_file(self) -> None:
        import shutil
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent-notifier.js"
            hooks_mod.install_hook("opencode", target=target, binary="/bin/an")
            source = target.read_text(encoding="utf-8")
            self.assertIn("Managed by agent-notifier", source)
            # OpenCode v2 default export sözleşmesi: id + setup
            self.assertIn("export default {", source)
            self.assertIn('id: "agent-notifier"', source)
            self.assertIn("async setup(ctx)", source)
            self.assertNotIn("export const AgentNotifierPlugin", source)

            # Node varsa gerçek ES module söz dizimi + varsayılan export doğrulaması
            node_bin = shutil.which("node")
            if node_bin:
                mjs = Path(tmp) / "agent-notifier.mjs"
                mjs.write_text(source, encoding="utf-8")
                proc = subprocess.run(
                    [
                        node_bin,
                        "--input-type=module",
                        "-e",
                        f'import mod from "{mjs.as_uri()}"; '
                        'if (mod.id !== "agent-notifier" || typeof mod.setup !== "function") process.exit(1);',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            un = hooks_mod.uninstall_hook("opencode", target=target)
            self.assertTrue(un["changed"])
            self.assertFalse(target.exists())

            # Kullanıcının elle yazdığı yabancı dosyayı silmemeli
            target.write_text("// custom user script\n", encoding="utf-8")
            un2 = hooks_mod.uninstall_hook("opencode", target=target)
            self.assertFalse(un2["ok"])
            self.assertTrue(target.exists())


class TestCLIAndFormatters(unittest.TestCase):
    def test_event_cli_parses_antigravity_stdin_and_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "AGENT_NOTIFIER_STATE_DIR": str(Path(tmp) / "state"),
                "AGENT_NOTIFIER_CONFIG_DIR": str(Path(tmp) / "config"),
            }
            payload = {
                "conversationId": "conv-abc-123",
                "workspacePaths": ["/home/eakgn/Projects/agent-notifier"],
                "modelName": "gemini-4-hydrogen-high",
                "lastUserInput": "Testleri kontrol et",
                "hookEventName": "PreToolUse",
            }
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "agent_notifier.cli._read_stdin_json_nonblocking", return_value=payload
            ), redirect_stdout(buf):
                rc = cli.main(
                    [
                        "event",
                        "--harness",
                        "antigravity",
                        "--state",
                        "waiting_input",
                        "--hook-event",
                        "PreToolUse",
                        "--from-stdin",
                        "--hook-output",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue().strip()), {"decision": "allow"})
            with mock.patch.dict(os.environ, env, clear=False):
                loaded = events_mod.load_events(harness="antigravity")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].session_id, "conv-abc-123")
            self.assertEqual(loaded[0].project, "agent-notifier")
            self.assertEqual(loaded[0].model, "gemini-4-hydrogen-high")
            self.assertEqual(loaded[0].title, "Testleri kontrol et")
            self.assertEqual(loaded[0].state, SessionState.WAITING_INPUT)

            # Stop + terminationReason == ERROR -> SessionState.ERROR + {"decision": "stop"}
            stop_payload = {
                "conversationId": "conv-abc-123",
                "hookEventName": "Stop",
                "terminationReason": "ERROR",
                "error": "rate limit exceeded",
            }
            buf2 = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "agent_notifier.cli._read_stdin_json_nonblocking", return_value=stop_payload
            ), redirect_stdout(buf2):
                rc2 = cli.main(
                    [
                        "event",
                        "--harness",
                        "antigravity",
                        "--state",
                        "done",
                        "--hook-event",
                        "Stop",
                        "--from-stdin",
                        "--hook-output",
                    ]
                )
            self.assertEqual(rc2, 0)
            self.assertEqual(json.loads(buf2.getvalue().strip()), {"decision": "stop"})
            with mock.patch.dict(os.environ, env, clear=False):
                loaded2 = events_mod.load_events(harness="antigravity")
            self.assertEqual(loaded2[0].state, SessionState.ERROR)
            self.assertIn("rate limit", loaded2[0].message)

    def test_formatters_waybar_polybar_plain(self) -> None:
        child = SessionNode(id="c1", harness="opencode", state=SessionState.WAITING_INPUT, agent="explore", is_subagent=True)
        root = SessionNode(
            id="r1",
            harness="opencode",
            state=SessionState.RUNNING,
            effective_state=SessionState.WAITING_INPUT,
            project="demo",
            provider="opencode-go",
            model="opencode-go/deepseek-v4.1-flash",
            children=[child],
        )
        # Aktif oturum opencode-go kullanıyor -> yalnız Go görünmeli, normal GH gizli kalmalı
        snap = Snapshot(
            generated_at="2026-11-21T00:00:00Z",
            generated_ts=1.0,
            global_state=SessionState.WAITING_INPUT,
            global_label="Soru / Onay Bekliyor",
            waiting_count=1,
            active_count=1,
            subagent_count=1,
            sessions=[root],
            providers=[
                ProviderQuota(key="opencode_go", label="OpenCode Go", available=True, headline_pct=82.0),
                ProviderQuota(key="github_copilot", label="GitHub Copilot", available=True, all_unlimited=True),
            ],
        )
        cfg = cfg_mod.DEFAULT_CONFIG
        wb = json.loads(formatters.render(snap, "waybar", cfg))
        self.assertEqual(wb["class"], "waiting")
        self.assertIn("Bekliyor: demo (+1)", wb["text"])
        self.assertIn("Go %82", wb["text"])
        self.assertNotIn("GH", wb["text"])
        self.assertIn("explore", wb["tooltip"])

        # Boşta + tüm kotalar normalken bar tertemiz yalnız "● Boşta" olmalı
        snap_idle_ok = Snapshot(
            generated_at="2026-11-21T00:00:00Z",
            generated_ts=1.0,
            global_state=SessionState.IDLE,
            global_label="Boşta",
            providers=[
                ProviderQuota(key="opencode_go", label="OpenCode Go", available=True, headline_pct=82.0),
                ProviderQuota(key="github_copilot", label="GitHub Copilot", available=True, all_unlimited=True),
            ],
        )
        wb_idle = json.loads(formatters.render(snap_idle_ok, "waybar", cfg))
        self.assertEqual(wb_idle["text"], "● Boşta")

        # Boşta ama Go kritik eşik altındaysa (%10) yalnız kritik olan görünmeli
        snap_idle_crit = Snapshot(
            generated_at="2026-11-21T00:00:00Z",
            generated_ts=1.0,
            global_state=SessionState.IDLE,
            global_label="Boşta",
            providers=[
                ProviderQuota(key="opencode_go", label="OpenCode Go", available=True, headline_pct=10.0, critical=True),
                ProviderQuota(key="github_copilot", label="GitHub Copilot", available=True, all_unlimited=True),
            ],
        )
        wb_crit = json.loads(formatters.render(snap_idle_crit, "waybar", cfg))
        self.assertEqual(wb_crit["text"], "● Boşta · Go %10")

        pb = formatters.render(snap, "polybar", cfg)
        self.assertIn("%{F#f9e2af}", pb)
        self.assertIn("demo", pb)

    def test_dashboard_and_ui_accept_toggle_and_close_flags(self) -> None:
        parser = cli.build_parser()
        for cmd in ("dashboard", "ui"):
            args_t = parser.parse_args([cmd, "--toggle"])
            self.assertTrue(args_t.toggle)
            self.assertFalse(args_t.close)

            args_c = parser.parse_args([cmd, "--close"])
            self.assertTrue(args_c.close)
            self.assertFalse(args_c.toggle)

        with mock.patch("agent_notifier.cli._launch_gui", return_value=0) as mocked:
            rc = cli.main(["ui", "--toggle"])
            self.assertEqual(rc, 0)
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args.kwargs["action"], "toggle")
            self.assertFalse(mocked.call_args.kwargs["tray_mode"])

    def test_setup_transparency_note_mentions_antigravity_tos_and_usage(self) -> None:
        from agent_notifier import setup_cmd

        self.assertIn("ToS", setup_cmd.ANTIGRAVITY_QUOTA_NOTE)
        self.assertIn("/usage", setup_cmd.ANTIGRAVITY_QUOTA_NOTE)


class TestLinuxIconsAndSNI(unittest.TestCase):
    def test_svg_and_argb32_icons_for_all_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = icons.ensure_icon_files(Path(tmp))
            for state, name in icons.STATE_ICON_NAMES.items():
                svg = out_dir / f"{name}.svg"
                self.assertTrue(svg.is_file())
                self.assertIn("<svg", svg.read_text(encoding="utf-8"))
                w, h, raw = icons.argb32_pixmap_for_state(state, 24)
                self.assertEqual((w, h), (24, 24))
                self.assertEqual(len(raw), 24 * 24 * 4)

    def test_sni_dbus_xml_is_valid(self) -> None:
        try:
            from gi.repository import Gio
        except ImportError:
            self.skipTest("PyGObject yok")
        sni_node = Gio.DBusNodeInfo.new_for_xml(sni.SNI_INTROSPECTION_XML)
        self.assertEqual(sni_node.interfaces[0].name, sni.SNI_INTERFACE)
        menu_node = Gio.DBusNodeInfo.new_for_xml(sni.DBUSMENU_INTROSPECTION_XML)
        self.assertEqual(menu_node.interfaces[0].name, sni.DBUSMENU_INTERFACE)

    def test_notify_watcher_classification_rules(self) -> None:
        from agent_notifier.linux.notify_watcher import classify_desktop_notification

        # Kendi bildirimini yok saymalı (sonsuz döngü önleme)
        self.assertIsNone(
            classify_desktop_notification("agent-notifier", "Soru / Onay Bekliyor", "Antigravity bekliyor")
        )
        # İlgisiz uygulamaları yok saymalı
        self.assertIsNone(classify_desktop_notification("Firefox", "Download complete", "file.zip"))

        # Antigravity komut izni -> WAITING_INPUT
        res = classify_desktop_notification("Antigravity", "Permission Required", "Allow run_command?")
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res[0], "antigravity")
        self.assertEqual(res[1], SessionState.WAITING_INPUT)
        self.assertIn("Allow run_command?", res[2])

        # Antigravity bitiş bildirimi -> DONE_UNSEEN
        res_done = classify_desktop_notification("Antigravity", "Task completed", "Finished edits")
        self.assertIsNotNone(res_done)
        assert res_done is not None
        self.assertEqual(res_done[1], SessionState.DONE_UNSEEN)

        # Antigravity hata bildirimi -> ERROR
        res_err = classify_desktop_notification("Antigravity", "Command failed", "Exit code 1")
        self.assertIsNotNone(res_err)
        assert res_err is not None
        self.assertEqual(res_err[1], SessionState.ERROR)

        # Red-Team R4: Belirsiz genel Antigravity bildirimi sahte WAITING_INPUT OLMAMALI (None)
        self.assertIsNone(
            classify_desktop_notification("Antigravity", "Update available", "Version 1.6.0 is ready")
        )

    def test_notify_watcher_multi_session_resolution_and_prompt_autoclear(self) -> None:
        import time

        from agent_notifier import engine
        from agent_notifier.linux.notify_watcher import resolve_target_session_id

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "AGENT_NOTIFIER_STATE_DIR": str(Path(tmp) / "state"),
                "AGENT_NOTIFIER_CONFIG_DIR": str(Path(tmp) / "cfg"),
            }
            now = time.time()
            with mock.patch.dict(os.environ, env, clear=False):
                # 2 paralel Antigravity oturumu açık
                events_mod.record_event(
                    harness="antigravity", session_id="c1", state="running", project="alpha", ts=now - 10
                )
                events_mod.record_event(
                    harness="antigravity", session_id="c2", state="running", project="beta", ts=now - 9
                )
                # Proje adı bildirimde geçiyorsa doğru oturumu bulmalı
                self.assertEqual(resolve_target_session_id("antigravity", "Allow command in beta?"), "c2")
                # Belirsizse yanlış oturuma yapıştırmamalı -> antigravity-prompt
                self.assertEqual(resolve_target_session_id("antigravity", "Permission required"), "antigravity-prompt")

                events_mod.record_event(
                    harness="antigravity",
                    session_id="antigravity-prompt",
                    state="waiting_input",
                    message="Permission required",
                    ts=now - 5,
                )
                # Gerçek oturum daha yeni bir olay gönderince geçici prompt otomatik düşmeli
                events_mod.record_event(
                    harness="antigravity", session_id="c1", state="running", project="alpha", ts=now - 1
                )
                cfg = cfg_mod.load_config()
                cfg["harnesses"] = {"opencode": False, "antigravity": True}
                cfg["providers"] = {"opencode_go": False, "github_copilot": False}
                snap = engine.collect_snapshot(config=cfg, offline=True)
                self.assertNotIn("antigravity-prompt", [s.id for s in snap.sessions])


if __name__ == "__main__":
    unittest.main()
