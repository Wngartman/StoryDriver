import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DATA = ROOT / "backend" / "data"
LOGS_DIR = BACKEND_DATA / "logs"
BACKUPS_DIR = BACKEND_DATA / "backups"
TEMP_DIR = BACKEND_DATA / "temp"
EXPORTS_DIR = BACKEND_DATA / "exports"
ARCHIVE_DIR = LOGS_DIR / "archive"
EVIDENCE_DIR = LOGS_DIR / "evidence"
REPORT = LOGS_DIR / "PROJECT_BLOAT_CLEANUP_REPORT.md"
INVENTORY_REPORT = LOGS_DIR / "PROJECT_BLOAT_INVENTORY.md"

PROTECTED_ROOT_NAMES = {
    ".codex",
    ".env",
    ".git",
    ".gitignore",
    "README.md",
    "CODEX_CONTEXT.md",
    "backend",
    "frontend",
    "scripts",
    "docs",
    "tts_engines",
}

ROOT_DOC_MOVES = {
    "CURRENT_RECOMMENDED_SETTINGS.md": ROOT / "docs" / "CURRENT_RECOMMENDED_SETTINGS.md",
    "HUMAN_TEST_PLAN.md": ROOT / "docs" / "HUMAN_TEST_PLAN.md",
}

SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEMP_SUFFIXES = {".tmp", ".temp", ".bak"}
SKIP_SIZE_DIRS = {
    ROOT / ".git",
    ROOT / "frontend" / "node_modules",
    ROOT / "backend" / ".venv",
}


@dataclass
class CleanupAction:
    category: str
    source: Path
    target: Path | None
    bytes_estimate: int
    description: str
    operation: str = "move"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{int(amount)} B" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def ensure_safe_target(path: Path) -> None:
    allowed_roots = [LOGS_DIR, BACKUPS_DIR, TEMP_DIR, EXPORTS_DIR, ROOT / "docs"]
    if not any(inside(path, allowed) or path.resolve() == allowed.resolve() for allowed in allowed_roots):
        raise ValueError(f"Refusing target outside approved cleanup roots: {path}")


def path_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            total = 0
            for child in path.rglob("*"):
                try:
                    if child.is_file() and not child.is_symlink():
                        total += child.stat().st_size
                except OSError:
                    continue
            return total
    except OSError:
        return 0
    return 0


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    count = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                count += 1
        except OSError:
            continue
    return count


def root_entries() -> list[dict]:
    rows = []
    for path in sorted(ROOT.iterdir(), key=lambda item: item.name.lower()):
        size = 0 if path.resolve() in [item.resolve() for item in SKIP_SIZE_DIRS] else path_size(path)
        rows.append(
            {
                "name": path.name,
                "kind": "dir" if path.is_dir() else "file",
                "size_bytes": size,
                "size": format_bytes(size),
                "protected": path.name in PROTECTED_ROOT_NAMES,
            }
        )
    return rows


def largest_files(limit: int = 40) -> list[dict]:
    records = []
    for path in ROOT.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if any(inside(path, skip) for skip in SKIP_SIZE_DIRS):
                continue
            records.append((path.stat().st_size, path))
        except OSError:
            continue
    records.sort(reverse=True, key=lambda item: item[0])
    return [
        {"path": str(path.relative_to(ROOT)), "size_bytes": size, "size": format_bytes(size)}
        for size, path in records[:limit]
    ]


def folder_summary() -> list[dict]:
    folders = [
        BACKEND_DATA / "generated_audio",
        BACKEND_DATA / "generated_images",
        BACKEND_DATA / "comfy_workflows",
        BACKEND_DATA / "character_refs",
        LOGS_DIR,
        BACKUPS_DIR,
        EXPORTS_DIR,
        BACKEND_DATA / "ui_presets",
        TEMP_DIR,
        ROOT / "frontend" / "dist",
        ROOT / "frontend" / "node_modules",
        ROOT / "backend" / ".venv",
    ]
    rows = []
    for folder in folders:
        rows.append(
            {
                "path": str(folder.relative_to(ROOT)),
                "exists": folder.exists(),
                "files": count_files(folder),
                "size_bytes": path_size(folder) if folder.exists() else 0,
                "size": format_bytes(path_size(folder) if folder.exists() else 0),
            }
        )
    return rows


def db_story_label_inventory(limit: int = 80) -> dict:
    db_path = BACKEND_DATA / "app.db"
    if not db_path.exists():
        return {"available": False, "reason": "app.db not found"}
    try:
        with sqlite3.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM sessions
                WHERE lower(coalesce(title, '')) LIKE '%test%'
                   OR lower(coalesce(title, '')) LIKE '%smoke%'
                   OR lower(coalesce(title, '')) LIKE '%qa%'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"available": True, "count": len(rows), "stories": [dict(row) for row in rows]}
    except sqlite3.Error as error:
        return {"available": False, "reason": str(error)}


def candidate_actions(include_logs: bool, include_backups: bool, include_temp: bool, include_all_safe: bool) -> list[CleanupAction]:
    actions: list[CleanupAction] = []
    if include_all_safe:
        for source_name, target in ROOT_DOC_MOVES.items():
            source = ROOT / source_name
            if source.exists():
                actions.append(
                    CleanupAction(
                        "root-docs",
                        source,
                        target,
                        0,
                        "Move root-level documentation into docs.",
                    )
                )
        misplaced_target = BACKUPS_DIR / "misplaced_root_artifacts_20260615"
        for source in ROOT.iterdir():
            if "%" in source.name and source.is_dir():
                actions.append(
                    CleanupAction(
                        "misplaced-root-artifacts",
                        source,
                        misplaced_target / source.name,
                        0,
                        "Archive odd root artifact folder into backend data backups without deleting contents.",
                    )
                )
    if include_logs:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for source in LOGS_DIR.iterdir() if LOGS_DIR.exists() else []:
            if source.name in {".gitkeep", "archive", "evidence"}:
                continue
            if source.is_dir() and source.name in {"browser", "image_jobs"}:
                actions.append(
                    CleanupAction("logs", source, EVIDENCE_DIR / source.name, 0, "Move evidence subfolder under logs/evidence.")
                )
                continue
            try:
                size = path_size(source)
                age_days = (datetime.now().timestamp() - source.stat().st_mtime) / 86400
            except OSError:
                continue
            if size > 5 * 1024 * 1024 or age_days > 45:
                month = datetime.fromtimestamp(source.stat().st_mtime).strftime("%Y-%m")
                actions.append(
                    CleanupAction(
                        "logs",
                        source,
                        ARCHIVE_DIR / month / source.name,
                        0,
                        "Archive older or large log/report evidence; no deletion.",
                    )
                )
    if include_backups:
        backups = []
        for source in BACKUPS_DIR.glob("*.db"):
            try:
                backups.append((source.stat().st_mtime, source))
            except OSError:
                continue
        backups.sort(reverse=True)
        for _, source in backups[20:]:
            actions.append(
                CleanupAction(
                    "backups",
                    source,
                    BACKUPS_DIR / "archive" / source.name,
                    0,
                    "Archive older DB backup beyond the newest 20 root backups; no deletion.",
                )
            )
    if include_temp:
        if TEMP_DIR.exists():
            for source in TEMP_DIR.iterdir():
                if source.name == ".gitkeep":
                    continue
                actions.append(CleanupAction("temp", source, None, path_size(source), "Delete temp folder contents.", "delete"))
        pytest_cache = ROOT / ".pytest_cache"
        if pytest_cache.exists():
            actions.append(CleanupAction("temp", pytest_cache, None, path_size(pytest_cache), "Delete pytest cache folder.", "delete"))
        for source in ROOT.glob("*"):
            if source.is_file() and source.suffix.lower() in TEMP_SUFFIXES:
                actions.append(CleanupAction("temp", source, None, path_size(source), "Delete root temp file.", "delete"))
    return actions


def unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for index in range(1, 1000):
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find unique target for {target}")


def apply_action(action: CleanupAction) -> dict:
    if not action.source.exists():
        return {"status": "skipped", "reason": "source missing", "source": str(action.source)}
    if action.operation == "delete":
        if not inside(action.source, TEMP_DIR) and action.source.name != ".pytest_cache" and action.source.parent != ROOT:
            return {"status": "skipped", "reason": "delete source not in approved temp scope", "source": str(action.source)}
        bytes_before = path_size(action.source)
        if action.source.is_dir():
            shutil.rmtree(action.source)
        else:
            action.source.unlink()
        return {"status": "deleted", "source": str(action.source), "bytes": bytes_before}
    if action.target is None:
        return {"status": "skipped", "reason": "missing target", "source": str(action.source)}
    ensure_safe_target(action.target)
    target = unique_target(action.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(action.source), str(target))
    return {"status": "moved", "source": str(action.source), "target": str(target), "bytes": path_size(target)}


def run_orphan_cleanup(apply: bool) -> dict:
    script = ROOT / "scripts" / "cleanup_orphaned_generated_files.py"
    if not script.exists():
        return {"available": False, "reason": "cleanup_orphaned_generated_files.py missing"}
    command = [sys.executable, str(script), "--json"]
    if apply:
        command.insert(2, "--apply")
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
        payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
        payload.update({"available": True, "returncode": result.returncode})
        if result.stderr.strip():
            payload["stderr"] = result.stderr.strip()[-2000:]
        return payload
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {"available": False, "reason": str(error)}


def write_inventory() -> dict:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": utc_now(),
        "root_entries": root_entries(),
        "folder_summary": folder_summary(),
        "largest_files": largest_files(),
        "test_story_candidates": db_story_label_inventory(),
    }
    lines = [
        "# Project Bloat Inventory",
        "",
        f"- generated_at: `{data['generated_at']}`",
        "- mode: inventory only; no files deleted",
        "",
        "## Folder Summary",
        "",
        "| Path | Files | Size |",
        "|---|---:|---:|",
    ]
    for row in data["folder_summary"]:
        lines.append(f"| `{row['path']}` | {row['files']} | {row['size']} |")
    lines.extend(["", "## Root Entries", "", "| Name | Kind | Size | Protected |", "|---|---|---:|---|"])
    for row in data["root_entries"]:
        lines.append(f"| `{row['name']}` | {row['kind']} | {row['size']} | {row['protected']} |")
    lines.extend(["", "## Largest Files", ""])
    for row in data["largest_files"]:
        lines.append(f"- `{row['path']}` - {row['size']}")
    lines.extend(["", "## Test/Smoke Story Name Candidates", ""])
    story_info = data["test_story_candidates"]
    if story_info.get("available"):
        lines.append(f"- candidates: {story_info['count']}")
        for story in story_info["stories"][:40]:
            lines.append(
                f"  - `{story.get('title') or '(untitled)'}` id={story.get('id')} updated={story.get('updated_at')}"
            )
    else:
        lines.append(f"- unavailable: {story_info.get('reason')}")
    lines.extend(
        [
            "",
            "## Cleanup Notes",
            "",
            "- `generated_audio` and `generated_images` should be cleaned only through orphan cleanup.",
            "- `app.db`, workflow sidecars, character references, and user-created stories are protected.",
            "- Odd root folders such as `%B%` are treated as misplaced artifacts and should be archived, not deleted.",
        ]
    )
    INVENTORY_REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return data


def write_cleanup_report(mode: str, actions: list[CleanupAction], results: list[dict], orphan_result: dict | None) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    moved = [item for item in results if item.get("status") == "moved"]
    deleted = [item for item in results if item.get("status") == "deleted"]
    skipped = [item for item in results if item.get("status") == "skipped"]
    lines = [
        f"## {utc_now()} - {mode}",
        "",
        f"- planned actions: {len(actions)}",
        f"- moved/archived: {len(moved)}",
        f"- deleted temp/cache files or folders: {len(deleted)}",
        f"- skipped: {len(skipped)}",
        f"- estimated delete savings: {format_bytes(sum(int(item.get('bytes') or 0) for item in deleted))}",
    ]
    if moved:
        lines.append("- moved:")
        for item in moved[:100]:
            lines.append(f"  - `{item['source']}` -> `{item['target']}`")
    if deleted:
        lines.append("- deleted:")
        for item in deleted[:100]:
            lines.append(f"  - `{item['source']}` ({format_bytes(int(item.get('bytes') or 0))})")
    if skipped:
        lines.append("- skipped:")
        for item in skipped[:100]:
            lines.append(f"  - `{item.get('source')}`: {item.get('reason')}")
    if orphan_result is not None:
        lines.extend(
            [
                "- orphan cleanup:",
                f"  - available: {orphan_result.get('available')}",
                f"  - mode: {orphan_result.get('mode', 'unknown')}",
                f"  - orphaned files: {orphan_result.get('orphaned_count', 'unknown')}",
                f"  - orphaned size: {orphan_result.get('orphaned_size', 'unknown')}",
                f"  - deleted files: {orphan_result.get('deleted_count', 0)}",
            ]
        )
        if orphan_result.get("reason"):
            lines.append(f"  - reason: {orphan_result['reason']}")
    lines.append("")
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else "# Project Bloat Cleanup Report\n"
    REPORT.write_text(existing.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def print_action_summary(actions: list[CleanupAction], apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN"
    print(f"StoryDriver cleanup ({mode})")
    print(f"Planned safe actions: {len(actions)}")
    by_category: dict[str, int] = {}
    for action in actions:
        by_category[action.category] = by_category.get(action.category, 0) + 1
    for category, count in sorted(by_category.items()):
        print(f"  {category}: {count}")
    move_count = sum(1 for action in actions if action.operation == "move")
    delete_bytes = sum(action.bytes_estimate for action in actions if action.operation == "delete")
    print(f"Moves/archives planned: {move_count}")
    print(f"Temp/cache delete savings if applied: {format_bytes(delete_bytes)}")
    if not apply:
        print("Dry run only. Re-run with --apply and selected flags to make changes.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run-first StoryDriver project cleanup and inventory tool.")
    parser.add_argument("--dry-run", action="store_true", help="Scan only. This is the default behavior.")
    parser.add_argument("--apply", action="store_true", help="Apply selected safe cleanup actions.")
    parser.add_argument("--logs", action="store_true", help="Archive large/old logs and evidence folders.")
    parser.add_argument("--backups", action="store_true", help="Archive older DB backups beyond the newest 20 root backups.")
    parser.add_argument("--temp", action="store_true", help="Delete approved temp/cache files.")
    parser.add_argument("--orphans", action="store_true", help="Run orphan generated-file cleanup dry-run or apply.")
    parser.add_argument("--all-safe", action="store_true", help="Include root-doc and misplaced-artifact moves.")
    parser.add_argument("--inventory", action="store_true", help="Write the project bloat inventory report.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args()

    inventory = write_inventory() if args.inventory or not any([args.logs, args.backups, args.temp, args.orphans, args.all_safe]) else None
    actions = candidate_actions(args.logs, args.backups, args.temp, args.all_safe)
    results: list[dict] = []
    if args.apply:
        for action in actions:
            try:
                results.append(apply_action(action))
            except Exception as error:
                results.append({"status": "skipped", "source": str(action.source), "reason": str(error)})
    orphan_result = run_orphan_cleanup(args.apply) if args.orphans else None
    write_cleanup_report("apply" if args.apply else "dry-run", actions, results, orphan_result)

    payload = {
        "mode": "apply" if args.apply else "dry-run",
        "inventory_report": str(INVENTORY_REPORT),
        "cleanup_report": str(REPORT),
        "planned_actions": len(actions),
        "results": results,
        "orphan_result": orphan_result,
        "folder_summary": inventory.get("folder_summary") if inventory else None,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_action_summary(actions, args.apply)
        if orphan_result:
            print(
                "Orphan cleanup: "
                f"{orphan_result.get('orphaned_count', 'unknown')} files "
                f"({orphan_result.get('orphaned_size', 'unknown')})"
            )
        print(f"Inventory: {INVENTORY_REPORT}")
        print(f"Cleanup report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
