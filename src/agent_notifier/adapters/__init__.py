"""Harness ve Provider adaptör kayıt defteri."""

from __future__ import annotations

from .antigravity_harness import AntigravityHarnessAdapter
from .base import AdapterInfo, HarnessAdapter, ProviderAdapter
from .copilot_provider import GitHubCopilotProviderAdapter
from .opencode_go_provider import OpenCodeGoProviderAdapter
from .opencode_harness import OpenCodeHarnessAdapter

HARNESS_ADAPTERS: dict[str, HarnessAdapter] = {
    "opencode": OpenCodeHarnessAdapter(),
    "antigravity": AntigravityHarnessAdapter(),
}

PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {
    "opencode_go": OpenCodeGoProviderAdapter(),
    "github_copilot": GitHubCopilotProviderAdapter(),
}


def harness_infos() -> list[AdapterInfo]:
    return [adapter.info for adapter in HARNESS_ADAPTERS.values()]


def provider_infos() -> list[AdapterInfo]:
    return [adapter.info for adapter in PROVIDER_ADAPTERS.values()]


def detect_environment() -> dict[str, dict[str, bool]]:
    """Sistemde kurulu/kullanılabilir harness ve sağlayıcıları algılar."""
    return {
        "harnesses": {key: adapter.detect() for key, adapter in HARNESS_ADAPTERS.items()},
        "providers": {key: adapter.detect() for key, adapter in PROVIDER_ADAPTERS.items()},
    }
