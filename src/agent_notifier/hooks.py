"""Güvenli harness hook yöneticisi (`print`, `install`, `uninstall`).

Temel ilkeler:
- Mevcut kullanıcı hook'larına asla zarar vermez; yalnız kendi ad alanını
  (`agent-notifier`) ekler/günceller/kaldırır.
- Yazmadan önce `.bak` yedeği alır ve atomik yazar.
- `--dry-run` ile değişiklikleri yazmadan önizler.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import config as cfg_mod

HOOK_GROUP_NAME = "agent-notifier"


def _agent_notifier_cmd() -> str:
    return shutil.which("agent-notifier") or "agent-notifier"


def antigravity_hooks_path(target: Path | None = None) -> Path:
    if target is not None:
        return target
    return Path.home() / ".gemini" / "config" / "hooks.json"


def antigravity_hook_entry(binary: str | None = None) -> dict[str, Any]:
    """Resmî Antigravity `hooks.json` şemasına uygun hook grubu."""
    cmd = binary or _agent_notifier_cmd()
    return {
        "SessionStart": [
            {
                "type": "command",
                "command": f"{cmd} event --harness antigravity --state running --from-stdin --hook-output",
                "timeout": 5,
            }
        ],
        "PreInvocation": [
            {
                "type": "command",
                "command": f"{cmd} event --harness antigravity --state running --from-stdin --hook-output",
                "timeout": 5,
            }
        ],
        "PreToolUse": [
            {
                "matcher": "ask_question",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{cmd} event --harness antigravity --state waiting_input --from-stdin --hook-output",
                        "timeout": 5,
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "ask_question",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{cmd} event --harness antigravity --state running --from-stdin --hook-output",
                        "timeout": 5,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f"{cmd} event --harness antigravity --state done --from-stdin --hook-output",
                "timeout": 5,
            }
        ],
    }


def opencode_plugin_path(target: Path | None = None) -> Path:
    if target is not None:
        return target
    return Path.home() / ".config" / "opencode" / "plugins" / "agent-notifier.js"


def opencode_plugin_source(binary: str | None = None) -> str:
    """OpenCode JS eklentisi: izin/soru bekleme ve bitiş olaylarını anlık bildirir."""
    cmd = (binary or _agent_notifier_cmd()).replace("\\", "\\\\").replace('"', '\\"')
    return f"""// Managed by agent-notifier (safe to remove via `agent-notifier hooks uninstall opencode`)
import {{ execFile }} from "node:child_process";

const BIN = "{cmd}";

function emit(state, sessionID, extra = []) {{
  const args = ["event", "--harness", "opencode", "--state", state];
  if (sessionID) args.push("--session", String(sessionID));
  args.push(...extra);
  try {{
    execFile(BIN, args, {{ timeout: 4000 }}, () => {{}});
  }} catch {{}}
}}

export const AgentNotifierPlugin = async ({{ directory }}) => {{
  const project = directory ? directory.split("/").filter(Boolean).pop() || "" : "";
  const base = project ? ["--project", project] : [];
  return {{
    event: async ({{ event }}) => {{
      const sid = event?.properties?.sessionID || event?.properties?.info?.id || "";
      if (event?.type === "permission.asked" || event?.type === "question.asked") {{
        emit("waiting_input", sid, [...base, "--message", "OpenCode izin/cevap bekliyor"]);
      }} else if (event?.type === "session.idle") {{
        emit("done", sid, base);
      }} else if (event?.type === "session.error") {{
        emit("error", sid, [...base, "--message", "OpenCode oturumu hata verdi"]);
      }}
    }},
  }};
}};
"""


SUPPORTED_HOOK_HARNESSES: tuple[str, ...] = ("antigravity", "opencode")


def render_snippet(harness: str, binary: str | None = None) -> str:
    key = harness.strip().lower()
    if key == "antigravity":
        return json.dumps({HOOK_GROUP_NAME: antigravity_hook_entry(binary)}, ensure_ascii=False, indent=2)
    if key == "opencode":
        return opencode_plugin_source(binary)
    raise ValueError(f"hook desteklenmeyen harness: {harness!r}")


def install_hook(
    harness: str,
    *,
    target: Path | None = None,
    dry_run: bool = False,
    binary: str | None = None,
) -> dict[str, Any]:
    """Belirtilen harness için hook'u yedekli ve güvenli şekilde kurar."""
    key = harness.strip().lower()
    if key == "antigravity":
        path = antigravity_hooks_path(target)
        existing: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("mevcut hooks.json bir JSON nesnesi değil")
                existing = raw
            except ValueError as exc:
                raise ValueError(f"{path} okunamadı: {exc}") from exc

        merged = dict(existing)
        merged[HOOK_GROUP_NAME] = antigravity_hook_entry(binary)
        rendered = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
        backup_path: str | None = None

        if not dry_run:
            if path.is_file():
                bak = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, bak)
                backup_path = str(bak)
            cfg_mod.atomic_write_text(path, rendered, mode=0o600)

        return {
            "ok": True,
            "harness": key,
            "path": str(path),
            "backup": backup_path,
            "dry_run": dry_run,
            "preserved_groups": [k for k in existing if k != HOOK_GROUP_NAME],
            "preview": rendered,
        }

    if key == "opencode":
        path = opencode_plugin_path(target)
        content = opencode_plugin_source(binary)
        backup_path = None
        if not dry_run:
            if path.is_file():
                bak = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, bak)
                backup_path = str(bak)
            cfg_mod.atomic_write_text(path, content, mode=0o600)
        return {
            "ok": True,
            "harness": key,
            "path": str(path),
            "backup": backup_path,
            "dry_run": dry_run,
            "preview": content,
        }

    raise ValueError(f"hook desteklenmeyen harness: {harness!r}")


def uninstall_hook(
    harness: str,
    *,
    target: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Yalnızca `agent-notifier` tarafından eklenen hook kaydını kaldırır."""
    key = harness.strip().lower()
    if key == "antigravity":
        path = antigravity_hooks_path(target)
        if not path.is_file():
            return {"ok": True, "harness": key, "path": str(path), "changed": False, "dry_run": dry_run}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or HOOK_GROUP_NAME not in raw:
            return {"ok": True, "harness": key, "path": str(path), "changed": False, "dry_run": dry_run}
        updated = {k: v for k, v in raw.items() if k != HOOK_GROUP_NAME}
        if not dry_run:
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            if updated:
                cfg_mod.atomic_write_text(
                    path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n", mode=0o600
                )
            else:
                path.unlink()
        return {
            "ok": True,
            "harness": key,
            "path": str(path),
            "changed": True,
            "remaining_groups": list(updated),
            "dry_run": dry_run,
        }

    if key == "opencode":
        path = opencode_plugin_path(target)
        if not path.is_file():
            return {"ok": True, "harness": key, "path": str(path), "changed": False, "dry_run": dry_run}
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "Managed by agent-notifier" not in text:
            return {
                "ok": False,
                "harness": key,
                "path": str(path),
                "changed": False,
                "error": "dosya agent-notifier tarafından oluşturulmamış; güvenlik için silinmedi",
            }
        if not dry_run:
            path.unlink()
        return {"ok": True, "harness": key, "path": str(path), "changed": True, "dry_run": dry_run}

    raise ValueError(f"hook desteklenmeyen harness: {harness!r}")
