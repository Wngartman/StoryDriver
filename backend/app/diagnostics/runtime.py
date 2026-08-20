from __future__ import annotations

import os


def diagnostic_logging_enabled() -> bool:
    return os.environ.get("STORYDRIVER_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}
