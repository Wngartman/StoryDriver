from __future__ import annotations

from fastapi import APIRouter

from app.config import DATA_DIR
from app.database import db_session
from app.services.generated_file_cleanup import find_orphaned_generated_files
from app.services.story_delete_jobs import list_delete_jobs


router = APIRouter(prefix="/storage", tags=["storage"])


def _format_bytes(value: int) -> str:
    amount = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{int(value or 0)} B"


@router.get("/maintenance")
def get_storage_maintenance_status() -> dict:
    with db_session() as db:
        scan = find_orphaned_generated_files(db)
    jobs = list_delete_jobs(limit=10, include_completed=False)
    active_jobs = [job for job in jobs if job.get("status") in {"queued", "running"}]
    failed_jobs = [job for job in jobs if job.get("status") == "failed"]
    return {
        "data_dir": str(DATA_DIR),
        "generated_media": {
            "scanned_count": scan["scanned_count"],
            "referenced_count": scan["referenced_count"],
            "orphaned_count": scan["orphaned_count"],
            "orphaned_bytes": scan["orphaned_bytes"],
            "orphaned_size": _format_bytes(int(scan["orphaned_bytes"])),
            "skipped_count": len(scan.get("skipped") or []),
        },
        "delete_jobs": {
            "active_count": len(active_jobs),
            "failed_count": len(failed_jobs),
            "recent": jobs,
        },
        "cleanup_script": str(DATA_DIR.parent.parent / "scripts" / "cleanup_orphaned_generated_files.bat"),
        "safe_roots": [
            str(DATA_DIR / "generated_audio"),
            str(DATA_DIR / "generated_images"),
        ],
    }
