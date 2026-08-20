import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import DATA_DIR  # noqa: E402
from app.database import db_session, init_db  # noqa: E402
from app.services.generated_file_cleanup import (  # noqa: E402
    delete_generated_file_references,
    find_orphaned_generated_files,
)


REPORT = DATA_DIR / "logs" / "ORPHANED_GENERATED_FILES_CLEANUP_REPORT.md"
PRE_APPLY_REPORT = DATA_DIR / "logs" / "ORPHANED_GENERATED_FILES_PRE_APPLY.md"
APPLY_REPORT = DATA_DIR / "logs" / "ORPHANED_GENERATED_FILES_APPLY_REPORT.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{int(value)} B"


def write_report(*, apply: bool, scan: dict, delete_result: dict | None = None) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"## {utc_now()} - {'Apply' if apply else 'Dry run'}",
        "",
        f"- mode: `{'apply' if apply else 'dry-run'}`",
        f"- scanned files: {scan['scanned_count']}",
        f"- referenced files: {scan['referenced_count']}",
        f"- orphaned files: {scan['orphaned_count']}",
        f"- orphaned size: {format_bytes(scan['orphaned_bytes'])}",
    ]
    if apply and delete_result is not None:
        lines.extend(
            [
                f"- deleted files: {len(delete_result['files_deleted'])}",
                f"- deleted size: {format_bytes(delete_result['deleted_bytes'])}",
                f"- deleted image files: {delete_result['deleted_counts'].get('image', 0)}",
                f"- deleted audio files: {delete_result['deleted_counts'].get('audio', 0)}",
            ]
        )
    if scan["orphaned_files"]:
        lines.append("- orphaned_files:")
        for record in scan["orphaned_files"][:250]:
            lines.append(
                f"  - `{record['path']}` ({record['media_type']}, {format_bytes(int(record.get('size_bytes') or 0))})"
            )
        if len(scan["orphaned_files"]) > 250:
            lines.append(f"  - ... {len(scan['orphaned_files']) - 250} more")
    if apply and delete_result and delete_result["files_deleted"]:
        lines.append("- deleted_files:")
        lines.extend(f"  - `{path}`" for path in delete_result["files_deleted"][:250])
        if len(delete_result["files_deleted"]) > 250:
            lines.append(f"  - ... {len(delete_result['files_deleted']) - 250} more")
    skipped = list(scan.get("skipped") or [])
    if delete_result:
        skipped.extend(delete_result.get("files_skipped") or [])
    if skipped:
        lines.append("- skipped:")
        lines.extend(f"  - {item}" for item in skipped[:250])
        if len(skipped) > 250:
            lines.append(f"  - ... {len(skipped) - 250} more")
    lines.append("")
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines)
    REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")
    phase_report = APPLY_REPORT if apply else PRE_APPLY_REPORT
    if apply or scan["orphaned_files"] or not phase_report.exists():
        phase_report.write_text(section, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run-first cleanup for StoryDriver orphaned generated image/audio files."
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan only. This is the default behavior.")
    parser.add_argument("--apply", action="store_true", help="Permanently delete orphaned generated files.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    init_db()
    with db_session() as db:
        scan = find_orphaned_generated_files(db)

    delete_result = None
    if args.apply and scan["orphaned_files"]:
        delete_result = delete_generated_file_references(record["path"] for record in scan["orphaned_files"])

    write_report(apply=args.apply, scan=scan, delete_result=delete_result)

    payload = {
        "mode": "apply" if args.apply else "dry-run",
        "scanned_count": scan["scanned_count"],
        "referenced_count": scan["referenced_count"],
        "orphaned_count": scan["orphaned_count"],
        "orphaned_bytes": scan["orphaned_bytes"],
        "orphaned_size": format_bytes(scan["orphaned_bytes"]),
        "deleted_count": len(delete_result["files_deleted"]) if delete_result else 0,
        "deleted_bytes": delete_result["deleted_bytes"] if delete_result else 0,
        "report": str(REPORT),
        "phase_report": str(APPLY_REPORT if args.apply else PRE_APPLY_REPORT),
        "skipped_count": len(scan.get("skipped") or []) + (len(delete_result.get("files_skipped") or []) if delete_result else 0),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("StoryDriver orphaned generated-file cleanup")
        print(f"Mode: {payload['mode']}")
        print(f"Scanned files: {payload['scanned_count']}")
        print(f"Referenced files: {payload['referenced_count']}")
        print(f"Orphaned files: {payload['orphaned_count']} ({payload['orphaned_size']})")
        if args.apply:
            print(f"Deleted files: {payload['deleted_count']} ({format_bytes(payload['deleted_bytes'])})")
        else:
            print("Dry run only. Re-run with --apply to permanently delete the orphaned files.")
        if payload["skipped_count"]:
            print(f"Skipped entries: {payload['skipped_count']}")
        print(f"Report: {REPORT}")
        print(f"Phase report: {payload['phase_report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
