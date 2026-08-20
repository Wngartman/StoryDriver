from __future__ import annotations

import json
from typing import Any
from uuid import uuid4


def record_generation_run(
    db: Any,
    *,
    session_id: str,
    scene_id: str,
    version_id: str,
    mode: str,
    stats: dict[str, Any],
) -> str:
    run_id = str(uuid4())
    stages = stats.get("stage_timings") or stats.get("latency") or {}
    db.execute(
        """
        INSERT INTO generation_runs (
            id, session_id, scene_id, version_id, mode, task_profile, model, status,
            stage_timings_json, prompt_chars, output_chars, word_count, repair_ran, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        """,
        (
            run_id,
            session_id,
            scene_id,
            version_id,
            mode,
            str(stats.get("task_profile") or ""),
            str(stats.get("model") or ""),
            json.dumps(stages, ensure_ascii=False, separators=(",", ":")),
            int((stats.get("prompt_diagnostics") or {}).get("total_prompt_chars") or 0),
            int(stats.get("output_chars") or 0),
            int(stats.get("word_count") or 0),
            1 if bool((stats.get("deliberate_pipeline") or {}).get("repair_ran")) else 0,
        ),
    )
    return run_id


def update_generation_run(db: Any, *, version_id: str, stats: dict[str, Any]) -> None:
    stages = stats.get("stage_timings") or stats.get("latency") or {}
    db.execute(
        """
        UPDATE generation_runs
        SET stage_timings_json = ?, prompt_chars = ?, output_chars = ?, word_count = ?, repair_ran = ?
        WHERE version_id = ?
        """,
        (
            json.dumps(stages, ensure_ascii=False, separators=(",", ":")),
            int((stats.get("prompt_diagnostics") or {}).get("total_prompt_chars") or 0),
            int(stats.get("output_chars") or 0),
            int(stats.get("word_count") or 0),
            1 if bool((stats.get("deliberate_pipeline") or {}).get("repair_ran")) else 0,
            version_id,
        ),
    )
