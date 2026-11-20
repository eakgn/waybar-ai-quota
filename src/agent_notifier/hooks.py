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


def antigravity_plugin_dir(target: Path | None = None) -> Path:
    if target is not None:
        return target
    return Path.home() / ".gemini" / "config" / "plugins" / HOOK_GROUP_NAME


def antigravity_legacy_hooks_path() -> Path:
    return Path.home() / ".gemini" / "config" / "hooks.json"


def antigravity_plugin_manifest() -> dict[str, Any]:
    return {
        "name": HOOK_GROUP_NAME,
        "description": "Antigravity oturum durumunu (çalışıyor / soru bekliyor / bitti / hata) agent-notifier'a pasif hook ile iletir.",
        "author": "agent-notifier contributors",
        "repository": "https://github.com/eakgn/agent-notifier",
    }


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
                        "command": f"{cmd} event --harness antigravity --state waiting_input --hook-event PreToolUse --from-stdin --hook-output",
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
                        "command": f"{cmd} event --harness antigravity --state running --hook-event PostToolUse --from-stdin --hook-output",
                        "timeout": 5,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f"{cmd} event --harness antigravity --state done --hook-event Stop --from-stdin --hook-output",
                "timeout": 5,
            }
        ],
    }


def opencode_plugin_path(target: Path | None = None) -> Path:
    if target is not None:
        return target
    return Path.home() / ".config" / "opencode" / "plugins" / "agent-notifier.js"


def opencode_plugin_source(binary: str | None = None) -> str:
    """OpenCode v2 JS eklentisi (`export default { id, setup }`).

    Kabuk (shell) açmadan `execFile` ile asenkron çalışır; OpenCode döngüsünü
    bloklamaz ve enjeksiyon riski taşımaz.
    """
    cmd = (binary or _agent_notifier_cmd()).replace("\\", "\\\\").replace('"', '\\"')
    return f"""// Managed by agent-notifier (safe to remove via `agent-notifier hooks uninstall opencode`)
import {{ execFile }} from "node:child_process";

const BIN = "{cmd}";

function emit(state, sessionID, extra = []) {{
  const args = ["event", "--harness", "opencode", "--state", state, "--quiet"];
  if (sessionID) args.push("--session", String(sessionID));
  args.push(...extra);
  try {{
    execFile(BIN, args, {{ timeout: 3000, windowsHide: true }}, () => {{}});
  }} catch {{}}
}}

function resolveProject(ctx, ev) {{
  const dir =
    ev?.properties?.directory ||
    ev?.properties?.info?.directory ||
    ctx?.directory ||
    ctx?.project?.directory ||
    "";
  if (!dir || typeof dir !== "string") return "";
  return dir.split("/").filter(Boolean).pop() || "";
}}

export default {{
  id: "agent-notifier",
  async setup(ctx) {{
    const handleEvent = (envelope) => {{
      const ev = envelope?.event ?? envelope;
      if (!ev || typeof ev !== "object") return;
      const type = String(ev.type || "");
      const props = ev.properties || ev.data || {{}};
      const sid = props.sessionID || props.sessionId || props.info?.id || "";
      const project = resolveProject(ctx, ev);
      const base = project ? ["--project", project] : [];

      if (type === "permission.asked" || type === "question.asked") {{
        emit("waiting_input", sid, [...base, "--message", "OpenCode izin/cevap bekliyor"]);
      }} else if (type === "permission.replied" || type === "question.replied") {{
        emit("running", sid, base);
      }} else if (type === "session.idle") {{
        emit("done", sid, base);
      }} else if (type === "session.error") {{
        emit("error", sid, [...base, "--message", "OpenCode oturumu hata verdi"]);
      }}
    }};

    const sub = ctx?.event?.subscribe;
    if (typeof sub === "function") {{
      const unsub = await sub.call(ctx.event, handleEvent);
      if (typeof unsub === "function") {{
        return () => unsub();
      }}
    }}
  }},
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


def _cleanup_legacy_antigravity_hooks(path: Path, *, dry_run: bool = False) -> bool:
    """Eski serbest `~/.gemini/config/hooks.json` içindeki `agent-notifier` grubunu güvenle temizler."""
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    if not isinstance(raw, dict) or HOOK_GROUP_NAME not in raw:
        return False
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
    return True


def install_hook(
    harness: str,
    *,
    target: Path | None = None,
    dry_run: bool = False,
    binary: str | None = None,
    legacy_hooks_path: Path | None = None,
) -> dict[str, Any]:
    """Belirtilen harness için hook/plugin'i yedekli ve güvenli şekilde kurar."""
    key = harness.strip().lower()
    if key == "antigravity":
        # Kullanıcı açıkça .json dosya yolu verdiyse eski düz dosya merge modunu koru;
        # varsayılan olarak Antigravity App + CLI'da görünen yerel plugin paketi kur.
        if target is not None and target.suffix == ".json":
            path = target
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
                "mode": "hooks_file",
                "path": str(path),
                "backup": backup_path,
                "dry_run": dry_run,
                "preserved_groups": [k for k in existing if k != HOOK_GROUP_NAME],
                "preview": rendered,
            }

        plugin_dir = antigravity_plugin_dir(target)
        manifest_path = plugin_dir / "plugin.json"
        hooks_file = plugin_dir / "hooks.json"
        manifest_text = json.dumps(antigravity_plugin_manifest(), ensure_ascii=False, indent=2) + "\n"
        hooks_text = json.dumps({HOOK_GROUP_NAME: antigravity_hook_entry(binary)}, ensure_ascii=False, indent=2) + "\n"
        legacy_path = legacy_hooks_path if legacy_hooks_path is not None else (
            antigravity_legacy_hooks_path() if target is None else None
        )
        backup_path = None
        cleaned_legacy = False

        if not dry_run:
            cfg_mod.ensure_dir(plugin_dir, mode=0o700)
            if hooks_file.is_file():
                bak = hooks_file.with_suffix(".json.bak")
                shutil.copy2(hooks_file, bak)
                backup_path = str(bak)
            cfg_mod.atomic_write_text(manifest_path, manifest_text, mode=0o600)
            cfg_mod.atomic_write_text(hooks_file, hooks_text, mode=0o600)
            if legacy_path is not None:
                cleaned_legacy = _cleanup_legacy_antigravity_hooks(legacy_path, dry_run=False)
        elif legacy_path is not None:
            cleaned_legacy = _cleanup_legacy_antigravity_hooks(legacy_path, dry_run=True)

        preview = f"# {manifest_path}\n{manifest_text}\n# {hooks_file}\n{hooks_text}"
        return {
            "ok": True,
            "harness": key,
            "mode": "plugin",
            "path": str(plugin_dir),
            "manifest_path": str(manifest_path),
            "hooks_path": str(hooks_file),
            "backup": backup_path,
            "cleaned_legacy_hooks": cleaned_legacy,
            "dry_run": dry_run,
            "preview": preview,
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
    legacy_hooks_path: Path | None = None,
) -> dict[str, Any]:
    """Yalnızca `agent-notifier` tarafından eklenen hook/plugin kaydını kaldırır."""
    key = harness.strip().lower()
    if key == "antigravity":
        if target is not None and target.suffix == ".json":
            path = target
            if not path.is_file():
                return {"ok": True, "harness": key, "path": str(path), "changed": False, "dry_run": dry_run}
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or HOOK_GROUP_NAME not in raw:
                return {"ok": True, "harness": key, "path": str(path), "changed": False, "dry_run": dry_run}
            updated = {k: v for k, v in raw.items() if k != HOOK_GROUP_NAME}
            changed = _cleanup_legacy_antigravity_hooks(path, dry_run=dry_run)
            return {
                "ok": True,
                "harness": key,
                "path": str(path),
                "changed": changed,
                "remaining_groups": list(updated),
                "dry_run": dry_run,
            }

        plugin_dir = antigravity_plugin_dir(target)
        manifest_path = plugin_dir / "plugin.json"
        hooks_file = plugin_dir / "hooks.json"
        changed = False
        if manifest_path.is_file() or hooks_file.is_file():
            # Güvenlik: yalnız agent-notifier manifesti taşıyan klasörü temizle
            is_ours = True
            if manifest_path.is_file():
                try:
                    m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    is_ours = isinstance(m_data, dict) and m_data.get("name") == HOOK_GROUP_NAME
                except ValueError:
                    is_ours = False
            if not is_ours:
                return {
                    "ok": False,
                    "harness": key,
                    "path": str(plugin_dir),
                    "changed": False,
                    "error": "plugin.json agent-notifier'a ait değil; güvenlik için silinmedi",
                }
            changed = True
            if not dry_run:
                hooks_file.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                try:
                    if plugin_dir.is_dir() and not any(plugin_dir.iterdir()):
                        plugin_dir.rmdir()
                except OSError:
                    pass

        legacy_path = legacy_hooks_path if legacy_hooks_path is not None else (
            antigravity_legacy_hooks_path() if target is None else None
        )
        if legacy_path is not None:
            if _cleanup_legacy_antigravity_hooks(legacy_path, dry_run=dry_run):
                changed = True

        return {
            "ok": True,
            "harness": key,
            "path": str(plugin_dir),
            "changed": changed,
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
