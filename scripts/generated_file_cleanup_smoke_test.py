import json
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import DATA_DIR, DB_PATH  # noqa: E402
from app.database import init_db  # noqa: E402
import app.services.generated_file_cleanup as cleanup  # noqa: E402


BASE_URL = "http://localhost:8001"
REPORT = DATA_DIR / "logs" / "GENERATED_FILE_CLEANUP_REPORT.md"


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 20.0):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"{method} {path} failed with HTTP {error.code}: {detail}") from error


def wait_delete_job(delete_result: dict, *, timeout: float = 90.0) -> dict:
    job_id = delete_result.get("job_id")
    if not job_id:
        return delete_result
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = request_json(f"/sessions/delete-jobs/{urllib.parse.quote(job_id, safe='')}", timeout=10)
        if job.get("status") == "completed":
            return {
                **delete_result,
                "counts": job.get("counts") or {},
                "files_deleted": job.get("files_deleted") or [],
                "files_skipped": job.get("files_skipped") or [],
                "deleted_file_bytes": job.get("deleted_file_bytes", 0),
            }
        if job.get("status") == "failed":
            raise RuntimeError(f"Delete job failed: {job.get('error')}")
        time.sleep(0.5)
    raise TimeoutError(f"Delete job {job_id} did not finish within {timeout} seconds.")


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def insert_scene_version_image(session_id: str, image_path: Path) -> tuple[str, str, str]:
    scene_id = f"cleanup-scene-{uuid4()}"
    version_id = f"cleanup-version-{uuid4()}"
    image_id = f"cleanup-image-{uuid4()}"
    with db_connect() as db:
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, ?)",
            (scene_id, session_id, "Cleanup smoke note.", "Cleanup smoke generated text.", "continue"),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, scene_id, session_id, "Cleanup smoke note.", "Cleanup smoke version text.", "continue", 1),
        )
        db.execute(
            """
            INSERT INTO generated_images (id, session_id, scene_id, version_id, workflow_id, workflow_name, prompt, image_path, image_url, status, is_primary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                session_id,
                scene_id,
                version_id,
                "cleanup-smoke-workflow",
                "Cleanup Smoke Workflow",
                "cleanup prompt",
                str(image_path),
                f"/generated-images/{image_path.name}",
                "accepted",
                1,
            ),
        )
    return scene_id, version_id, image_id


def write_story_audio(session_id: str, scene_id: str, version_id: str) -> tuple[Path, Path]:
    cache_key = uuid4().hex[:32]
    audio_path = DATA_DIR / "generated_audio" / f"kokoro_{cache_key}.mp3"
    manifest_path = DATA_DIR / "generated_audio" / f"tts_manifest_{cache_key}.json"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"cleanup-smoke-audio")
    manifest_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "scene_id": scene_id,
                "version_id": version_id,
                "cache_key": cache_key,
                "audio_path": str(audio_path),
                "audio_url": f"/audio/{audio_path.name}",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return audio_path, manifest_path


def append_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def permanent_delete_file_test() -> dict:
    init_db()
    health = request_json("/health", timeout=8)
    assert_true(isinstance(health, dict) and health.get("ok"), "Backend health is not OK.")

    delete_session = request_json("/sessions", method="POST", payload={"title": "Cleanup Delete Smoke"}, timeout=10)
    keep_session = request_json("/sessions", method="POST", payload={"title": "Cleanup Keep Smoke"}, timeout=10)
    delete_id = delete_session["id"]
    keep_id = keep_session["id"]

    story_image_path = DATA_DIR / "generated_images" / f"cleanup_story_owned_{uuid4().hex}.png"
    keep_image_path = DATA_DIR / "generated_images" / f"cleanup_keep_{uuid4().hex}.png"
    story_image_path.parent.mkdir(parents=True, exist_ok=True)
    story_image_path.write_bytes(b"cleanup-smoke-image")
    keep_image_path.write_bytes(b"cleanup-keep-image")
    scene_id, version_id, _image_id = insert_scene_version_image(delete_id, story_image_path)
    keep_scene_id, keep_version_id, _keep_image_id = insert_scene_version_image(keep_id, keep_image_path)
    audio_path, manifest_path = write_story_audio(delete_id, scene_id, version_id)

    outside_path = DATA_DIR / f"cleanup_outside_{uuid4().hex}.txt"
    outside_path.write_text("must not be deleted", encoding="utf-8")
    outside_delete = cleanup.delete_generated_file_references([outside_path])
    assert_true(outside_path.exists(), "Outside generated folders path was deleted.")
    assert_true(outside_delete["files_deleted"] == [], "Outside generated folders path was reported deleted.")
    assert_true(outside_delete["files_skipped"], "Outside generated folders path was not reported skipped.")

    try:
        result = request_json(
            f"/sessions/{urllib.parse.quote(delete_id, safe='')}?permanent=true",
            method="DELETE",
            timeout=20,
        )
        result = wait_delete_job(result)
        assert_true(result["id"] == delete_id, "Permanent delete returned wrong story id.")
        assert_true(not story_image_path.exists(), "Story-owned generated image file was not deleted.")
        assert_true(not audio_path.exists(), "Story-owned generated audio file was not deleted.")
        assert_true(not manifest_path.exists(), "Story-owned TTS manifest was not deleted.")
        assert_true(keep_image_path.exists(), "Unrelated generated image file was deleted.")
        assert_true(result["counts"].get("generated_image_files_deleted", 0) >= 1, "Deleted image file count missing.")
        assert_true(result["counts"].get("generated_audio_files_deleted", 0) >= 2, "Deleted audio file count missing.")
        return {
            "deleted_story_id": delete_id,
            "kept_story_id": keep_id,
            "scene_id": scene_id,
            "version_id": version_id,
            "keep_scene_id": keep_scene_id,
            "keep_version_id": keep_version_id,
            "delete_result": result,
            "outside_skip": outside_delete["files_skipped"],
        }
    finally:
        try:
            cleanup_result = request_json(
                f"/sessions/{urllib.parse.quote(keep_id, safe='')}?permanent=true",
                method="DELETE",
                timeout=20,
            )
            wait_delete_job(cleanup_result, timeout=30)
        except Exception:
            with db_connect() as db:
                db.execute("DELETE FROM sessions WHERE id = ?", (keep_id,))
        try:
            outside_path.unlink(missing_ok=True)
        except OSError:
            pass


def isolated_orphan_cleanup_test() -> dict:
    old_image_dir = cleanup.GENERATED_IMAGE_DIR
    old_audio_dir = cleanup.GENERATED_AUDIO_DIR
    try:
        with tempfile.TemporaryDirectory(prefix="storydriver-generated-cleanup-") as temp_dir:
            temp_root = Path(temp_dir)
            cleanup.GENERATED_IMAGE_DIR = temp_root / "generated_images"
            cleanup.GENERATED_AUDIO_DIR = temp_root / "generated_audio"
            cleanup.GENERATED_IMAGE_DIR.mkdir(parents=True)
            cleanup.GENERATED_AUDIO_DIR.mkdir(parents=True)

            active_image = cleanup.GENERATED_IMAGE_DIR / "active.png"
            orphan_image = cleanup.GENERATED_IMAGE_DIR / "orphan.png"
            orphan_audio = cleanup.GENERATED_AUDIO_DIR / "kokoro_orphan.mp3"
            active_audio = cleanup.GENERATED_AUDIO_DIR / "kokoro_active.mp3"
            active_manifest = cleanup.GENERATED_AUDIO_DIR / "tts_manifest_active.json"
            for path in (active_image, orphan_image, orphan_audio, active_audio):
                path.write_bytes(b"cleanup-smoke")

            active_session_id = "active-session"
            active_scene_id = "active-scene"
            active_version_id = "active-version"
            active_manifest.write_text(
                json.dumps(
                    {
                        "session_id": active_session_id,
                        "scene_id": active_scene_id,
                        "version_id": active_version_id,
                        "cache_key": "active",
                        "audio_path": str(active_audio),
                        "audio_url": f"/audio/{active_audio.name}",
                    }
                ),
                encoding="utf-8",
            )

            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE scenes (id TEXT PRIMARY KEY, session_id TEXT)")
            db.execute("CREATE TABLE scene_versions (id TEXT PRIMARY KEY, session_id TEXT, scene_id TEXT)")
            db.execute("CREATE TABLE generated_images (image_path TEXT, image_url TEXT)")
            db.execute("INSERT INTO sessions (id) VALUES (?)", (active_session_id,))
            db.execute("INSERT INTO scenes (id, session_id) VALUES (?, ?)", (active_scene_id, active_session_id))
            db.execute(
                "INSERT INTO scene_versions (id, session_id, scene_id) VALUES (?, ?, ?)",
                (active_version_id, active_session_id, active_scene_id),
            )
            db.execute("INSERT INTO generated_images (image_path, image_url) VALUES (?, ?)", (str(active_image), ""))
            scan = cleanup.find_orphaned_generated_files(db)
            orphan_paths = {Path(record["path"]).name for record in scan["orphaned_files"]}
            assert_true("orphan.png" in orphan_paths, "Dry-run did not find orphan image.")
            assert_true("kokoro_orphan.mp3" in orphan_paths, "Dry-run did not find orphan audio.")
            assert_true("active.png" not in orphan_paths, "Dry-run marked referenced image orphan.")
            assert_true("kokoro_active.mp3" not in orphan_paths, "Dry-run marked manifest-referenced audio orphan.")

            apply_result = cleanup.delete_generated_file_references(record["path"] for record in scan["orphaned_files"])
            assert_true(not orphan_image.exists(), "Apply did not delete orphan image.")
            assert_true(not orphan_audio.exists(), "Apply did not delete orphan audio.")
            assert_true(active_image.exists(), "Apply deleted active image.")
            assert_true(active_audio.exists(), "Apply deleted active audio.")

            traversal_path, traversal_reason, _ = cleanup.safe_generated_file_path(
                cleanup.GENERATED_IMAGE_DIR / ".." / "app.db"
            )
            assert_true(traversal_path is None, "Path traversal attempt was not refused.")
            outside_path, outside_reason, _ = cleanup.safe_generated_file_path(temp_root / "outside.txt")
            assert_true(outside_path is None, "Outside path was not refused.")
            return {
                "dry_run_orphan_count": scan["orphaned_count"],
                "deleted_count": len(apply_result["files_deleted"]),
                "traversal_reason": traversal_reason,
                "outside_reason": outside_reason,
            }
    finally:
        cleanup.GENERATED_IMAGE_DIR = old_image_dir
        cleanup.GENERATED_AUDIO_DIR = old_audio_dir


def main() -> int:
    print("StoryDriver generated-file cleanup smoke test")
    permanent = permanent_delete_file_test()
    isolated = isolated_orphan_cleanup_test()
    lines = [
        "# Generated File Cleanup Report",
        "",
        "## Smoke Test",
        "- Permanent story delete removes story-owned generated image files: PASS",
        "- Permanent story delete removes story-owned Kokoro audio + manifest files: PASS",
        "- Unrelated generated file remains: PASS",
        "- Orphan cleanup dry-run finds orphan files: PASS",
        "- Orphan cleanup apply deletes only orphan files in isolated sandbox: PASS",
        "- Path traversal and outside generated-folder deletion attempts are refused: PASS",
        f"- Deleted story id: `{permanent['deleted_story_id']}`",
        f"- Kept story id: `{permanent['kept_story_id']}`",
        f"- Delete route counts: `{json.dumps(permanent['delete_result'].get('counts', {}), sort_keys=True)}`",
        f"- Outside skip: `{'; '.join(permanent['outside_skip'])}`",
        f"- Isolated orphan dry-run count: {isolated['dry_run_orphan_count']}",
        f"- Isolated orphan apply deleted count: {isolated['deleted_count']}",
        f"- Traversal refusal: `{isolated['traversal_reason']}`",
        f"- Outside refusal: `{isolated['outside_reason']}`",
    ]
    append_report(lines)
    print("[OK] generated-file cleanup smoke test passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[FAIL] {error}")
        sys.exit(1)
