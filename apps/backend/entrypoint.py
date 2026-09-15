from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import traceback

import uvicorn


def enabled(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    multiprocessing.freeze_support()
    try:
        from app.main import app

        host = "0.0.0.0" if enabled(os.getenv("STORYDRIVER_LAN_ENABLED"), default=False) else "127.0.0.1"
        port = int(os.getenv("STORYDRIVER_BACKEND_PORT", "8001"))
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_config=None,
            access_log=False,
            workers=1,
        )
    except BaseException:
        data_root = Path(os.getenv("STORYDRIVER_DATA_DIR", Path.cwd() / "data"))
        log_root = data_root / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "backend-fatal.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
