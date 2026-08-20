import sqlite3
from pathlib import Path


root = Path(__file__).resolve().parents[1]
live_db = root / "backend/data/app.db"
backup_db = root / "backend/data/backups/product_polish_20260715/entity_extraction/app.db"
cleanup_script = (root / "scripts/review_invalid_auto_characters.py").read_text(encoding="utf-8")

if not backup_db.exists() or backup_db.stat().st_size < 1_000_000:
    raise AssertionError("entity cleanup backup is missing")
with sqlite3.connect(backup_db) as backup:
    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise AssertionError("entity cleanup backup failed integrity check")
    before = backup.execute("SELECT auto_created FROM characters WHERE name = 'Wait'").fetchone()
    if before != (1,):
        raise AssertionError("backup does not preserve the reviewed automatic Wait record")

with sqlite3.connect(live_db) as live:
    live.execute("PRAGMA foreign_keys = ON")
    if live.execute("SELECT COUNT(*) FROM characters WHERE name = 'Wait'").fetchone()[0] != 0:
        raise AssertionError("invalid Wait character still exists")
    if live.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise AssertionError("live database integrity check failed")
    if live.execute("PRAGMA foreign_key_check").fetchall():
        raise AssertionError("live database has foreign-key violations")

for marker in ("BEGIN IMMEDIATE", "expected name does not match", "record changed after the safety review", "PRAGMA foreign_key_check"):
    if marker not in cleanup_script:
        raise AssertionError(f"cleanup utility is missing guard: {marker}")

print("Wait character cleanup contract: PASS")
