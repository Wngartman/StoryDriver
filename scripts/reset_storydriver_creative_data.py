from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "backend" / "data"
DB_PATH = DATA_DIR / "app.db"
LOG_DIR = DATA_DIR / "logs"
REPORT_PATH = LOG_DIR / "CREATIVE_DATA_RESET_REPORT.md"

PRESERVED_LOG_NAMES = {
    "COMPLETE_REVAMP_AUDIT.md",
    "COMPLETE_REVAMP_ROLLBACK.md",
    "COMPLETE_STORYDRIVER_REVAMP_REPORT.md",
    REPORT_PATH.name,
}

CREATIVE_TABLES = (
    # Narration and generated-media metadata.
    "narration_chunks",
    "narration_jobs",
    "pronunciation_aliases",
    "generated_images",
    "scene_image_prompts",
    "scene_image_settings",
    "session_image_settings",
    # Quality, caches, and extraction history.
    "scene_quality_checklists",
    "session_quality_notes",
    "next_prompt_memory_cache_v3",
    "state_snapshots",
    "story_state_runs",
    "generation_runs",
    # Canonical and compatibility memory/history tables.
    "appearance_changes",
    "object_transfers",
    "relationship_events",
    "emotional_memories",
    "story_events",
    "character_state_events",
    "story_memories",
    "summaries",
    "session_summaries",
    # Live state.
    "character_inventory",
    "character_clothing",
    "scene_presence",
    "scene_blocking",
    "scene_live_state",
    "world_live_state",
    "object_state",
    "relationship_state",
    "character_live_state",
    "plot_threads",
    # Foundation and identity.
    "character_reference_images",
    "character_visual_profiles",
    "character_base_profiles",
    "session_characters",
    "story_foundations",
    "locations",
    "world_rules",
    "world_notes",
    # Core story records. Child rows are listed before parents for clarity;
    # foreign keys are disabled only inside the reset transaction.
    "scene_versions",
    "scenes",
    "story_delete_jobs",
    "sessions",
    "stories",
    "characters",
)

MEDIA_DIR_NAMES = (
    "generated_audio",
    "generated_images",
    "character_refs",
    "exports",
)


@dataclass(frozen=True)
class FileStats:
    files: int
    bytes: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_approved_path(path: Path, *, allow_root: Path = DATA_DIR) -> Path:
    resolved = path.resolve()
    approved = allow_root.resolve()
    if resolved != approved and approved not in resolved.parents:
        raise RuntimeError(f"Refusing path outside {approved}: {resolved}")
    return resolved


def iter_files(path: Path) -> Iterable[Path]:
    if not path.exists():
        return ()
    return (item for item in path.rglob("*") if item.is_file())


def file_stats(path: Path) -> FileStats:
    files = list(iter_files(path))
    return FileStats(files=len(files), bytes=sum(item.stat().st_size for item in files))


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def table_counts(connection: sqlite3.Connection, names: Iterable[str]) -> dict[str, int]:
    available = table_names(connection)
    counts: dict[str, int] = {}
    for name in names:
        if name in available:
            counts[name] = int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    return counts


def clear_directory(path: Path, *, keep_names: set[str] | None = None) -> int:
    resolved = require_approved_path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    removed = 0
    for item in list(resolved.iterdir()):
        if keep_names and item.name in keep_names:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed += 1
    return removed


def clear_runtime_files(recovery_snapshot: Path | None) -> dict[str, int]:
    removed: dict[str, int] = {}
    for name in MEDIA_DIR_NAMES:
        removed[name] = clear_directory(DATA_DIR / name, keep_names={".gitkeep"})

    removed["backups"] = clear_directory(DATA_DIR / "backups", keep_names={".gitkeep"})
    removed["logs"] = clear_directory(LOG_DIR, keep_names=PRESERVED_LOG_NAMES | {".gitkeep"})

    keep_temp = {".gitkeep"}
    if recovery_snapshot is not None:
        checked = require_approved_path(recovery_snapshot)
        if checked.parent != (DATA_DIR / "temp").resolve() or not checked.name.startswith("pre_revamp_recovery_"):
            raise RuntimeError(f"Invalid recovery snapshot path: {checked}")
        keep_temp.add(checked.name)
    removed["temp"] = clear_directory(DATA_DIR / "temp", keep_names=keep_temp)
    return removed


def reset_database() -> tuple[dict[str, int], dict[str, int], str, list[tuple]]:
    require_approved_path(DB_PATH)
    with sqlite3.connect(DB_PATH, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout = 30000")
        before = table_counts(connection, CREATIVE_TABLES)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for name in CREATIVE_TABLES:
                if name in table_names(connection):
                    connection.execute(f'DELETE FROM "{name}"')
            if "sqlite_sequence" in table_names(connection):
                placeholders = ",".join("?" for _ in CREATIVE_TABLES)
                connection.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    CREATIVE_TABLES,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

        connection.execute("VACUUM")
        after = table_counts(connection, CREATIVE_TABLES)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    return before, after, integrity, foreign_keys


def write_report(
    *,
    before: dict[str, int],
    after: dict[str, int],
    before_files: dict[str, FileStats],
    after_files: dict[str, FileStats],
    removed_entries: dict[str, int],
    integrity: str,
    foreign_keys: list[tuple],
    recovery_snapshot: Path | None,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    nonzero_after = {name: count for name, count in after.items() if count}
    lines = [
        "# Creative Data Reset Report",
        "",
        f"Generated: {utc_now()}",
        "",
        "This report contains counts and paths only. It contains no story prose, character names, director notes, or summaries.",
        "",
        "## Result",
        "",
        f"- Creative rows before: `{sum(before.values())}`",
        f"- Creative rows after: `{sum(after.values())}`",
        f"- Nonzero creative tables after: `{json.dumps(nonzero_after, sort_keys=True)}`",
        f"- SQLite integrity: `{integrity}`",
        f"- Foreign-key violations: `{len(foreign_keys)}`",
        f"- Temporary recovery snapshot preserved: `{recovery_snapshot or 'none'}`",
        "",
        "## Table Counts",
        "",
        "| Table | Before | After |",
        "|---|---:|---:|",
    ]
    for name in sorted(before | after):
        lines.append(f"| `{name}` | {before.get(name, 0)} | {after.get(name, 0)} |")

    lines.extend(
        [
            "",
            "## File Counts",
            "",
            "| Area | Files before | Files after | Bytes before | Bytes after | Removed entries |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name in sorted(before_files | after_files):
        prior = before_files.get(name, FileStats(0, 0))
        final = after_files.get(name, FileStats(0, 0))
        lines.append(
            f"| `{name}` | {prior.files} | {final.files} | {prior.bytes} | {final.bytes} | {removed_entries.get(name, 0)} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Permanently remove StoryDriver creative content.")
    parser.add_argument(
        "--confirm-permanent-reset",
        action="store_true",
        help="Required acknowledgement that all creative content will be permanently deleted.",
    )
    parser.add_argument(
        "--preserve-recovery-snapshot",
        type=Path,
        default=None,
        help="Temporary pre_revamp_recovery_* directory to preserve until acceptance passes.",
    )
    args = parser.parse_args()
    if not args.confirm_permanent_reset:
        parser.error("--confirm-permanent-reset is required")

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    tracked_paths = {name: DATA_DIR / name for name in (*MEDIA_DIR_NAMES, "backups", "logs", "temp")}
    before_files = {name: file_stats(path) for name, path in tracked_paths.items()}
    before, after, integrity, foreign_keys = reset_database()
    removed_entries = clear_runtime_files(args.preserve_recovery_snapshot)
    after_files = {name: file_stats(path) for name, path in tracked_paths.items()}
    write_report(
        before=before,
        after=after,
        before_files=before_files,
        after_files=after_files,
        removed_entries=removed_entries,
        integrity=integrity,
        foreign_keys=foreign_keys,
        recovery_snapshot=args.preserve_recovery_snapshot,
    )

    if any(after.values()) or integrity.lower() != "ok" or foreign_keys:
        raise RuntimeError("Reset verification failed; inspect the content-free reset report.")

    print(f"Permanent creative reset complete. Report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
