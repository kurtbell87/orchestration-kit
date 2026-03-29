from __future__ import annotations

import os
import sys

DEPRECATION_EXIT_CODE = 86
OVERRIDE_ENV = "ORCHESTRATION_KIT_ALLOW_LEGACY"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def legacy_override_enabled() -> bool:
    raw = os.getenv(OVERRIDE_ENV, "")
    return raw.strip().lower() in _TRUE_VALUES


def deprecation_message(tool_name: str) -> str:
    return "\n".join(
        [
            "[deprecated] orchestration-kit is retired as a live runtime.",
            f"[deprecated] {tool_name} is blocked by default.",
            "[deprecated] Use the active stack instead:",
            "[deprecated]   - /Users/brandonbell/LOCAL_DEV/kenoma-kbus",
            "[deprecated]   - /Users/brandonbell/LOCAL_DEV/kenoma-oracle-pod",
            "[deprecated]   - /Users/brandonbell/LOCAL_DEV/tdd-kit",
            "[deprecated]   - /Users/brandonbell/LOCAL_DEV/research-kit",
            "[deprecated]   - /Users/brandonbell/LOCAL_DEV/mathematics-kit",
            f"[deprecated] For emergency legacy use only, set {OVERRIDE_ENV}=1 and re-run.",
        ]
    )


def require_legacy_override(tool_name: str) -> None:
    if legacy_override_enabled():
        print(
            f"[deprecated] {tool_name}: continuing because {OVERRIDE_ENV}=1 is set.",
            file=sys.stderr,
        )
        return
    print(deprecation_message(tool_name), file=sys.stderr)
    raise SystemExit(DEPRECATION_EXIT_CODE)
