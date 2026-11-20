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
            payload = json.dumps(
                {
                    "conversationId": "conv-abc-123",
                    "workspacePaths": ["/home/eakgn/Projects/agent-notifier"],
                }
            )
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "agent_notifier.cli._read_stdin_json_nonblocking", return_value=json.loads(payload)
            ), redirect_stdout(buf):
                rc = cli.main(
                    [
                        "event",
                        "--harness",
                        "antigravity",
                        "--state",
                        "waiting_input",
                        "--from-stdin",
                        "--hook-output",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().strip(), "{}")
            with mock.patch.dict(os.environ, env, clear=False):
                loaded = events_mod.load_events(harness="antigravity")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].session_id, "conv-abc-123")
            self.assertEqual(loaded[0].project, "agent-notifier")
            self.assertEqual(loaded[0].state, SessionState.WAITING_INPUT)

    def test_formatters_waybar_polybar_plain(self) -> None:
        child = SessionNode(id="c1", harness="opencode", state=SessionState.WAITING_INPUT, agent="explore", is_subagent=True)
        root = SessionNode(
            id="r1",
            harness="opencode",
            state=SessionState.RUNNING,
            effective_state=SessionState.WAITING_INPUT,
            project="demo",
            children=[child],
        )
        snap = Snapshot(
            generated_at="2026-11-21T00:00:00Z",
            generated_ts=1.0,
            global_state=SessionState.WAITING_INPUT,
            global_label="Soru / Onay Bekliyor",
            waiting_count=1,
            active_count=1,
            subagent_count=1,
            sessions=[root],
            providers=[ProviderQuota(key="opencode_go", label="OpenCode Go", available=True, headline_pct=82.0)],
        )
        cfg = cfg_mod.DEFAULT_CONFIG
        wb = json.loads(formatters.render(snap, "waybar", cfg))
        self.assertEqual(wb["class"], "waiting")
        self.assertIn("Bekliyor: demo (+1)", wb["text"])
        self.assertIn("Go %82", wb["text"])
        self.assertIn("explore", wb["tooltip"])

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


if __name__ == "__main__":
    unittest.main()
