"""Run deterministic release checks against an isolated, initialized database."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / '.tools' / 'static-test-data'
DATA.mkdir(parents=True, exist_ok=True)
os.environ.update(STORYDRIVER_DATA_DIR=str(DATA), STORYDRIVER_DB_PATH=str(DATA / 'app.db'))
sys.path.insert(0, str(ROOT / 'backend'))
from app.database import init_db
init_db()

tests = [
    'scripts/prose_v3_static_test.py', 'scripts/scene_contract_blocking_static_test.py',
    'scripts/story_foundation_static_test.py', 'scripts/deliberate_pipeline_static_test.py',
    'scripts/tests/relationship_location_accuracy_test.py', 'scripts/tests/native_desktop_contract_test.py',
    'scripts/tests/release_contract_test.py',
    'scripts/tests/release_writing_regression_test.py',
    'scripts/tests/local_generation_transport_test.py',
]
failed = []
for test in tests:
    if subprocess.run([sys.executable, test], cwd=ROOT).returncode:
        failed.append(test)
for test in ['scripts/tests/qwen_premium_plan_test.mjs', 'scripts/tests/tts_buffered_seek_test.mjs', 'scripts/tests/tts_local_voice_test.mjs', 'scripts/tts_follow_sync_static_test.mjs']:
    if subprocess.run(['node', test], cwd=ROOT).returncode:
        failed.append(test)
if failed:
    raise SystemExit('Failed: ' + ', '.join(failed))
print('PASS: all release checks')
