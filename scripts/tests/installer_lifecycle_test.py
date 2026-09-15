"""Exercise the actual installer, upgrade and uninstaller with disposable D-only fixtures."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
TEST = (ROOT / "build" / "tests" / "installer-lifecycle").resolve()
assert ROOT.resolve() in TEST.parents
APP, DATA = TEST / "app", TEST / "data"
assert not APP.exists(), "Use a fresh, dedicated installer fixture directory."
DATA.mkdir(parents=True, exist_ok=True)
database = DATA / "preservation.db"
with sqlite3.connect(database) as db:
    db.execute("CREATE TABLE fixture (value TEXT)")
    db.execute("INSERT INTO fixture VALUES ('Synthetic preserved story')")
before = hashlib.sha256(database.read_bytes()).hexdigest()
env = os.environ.copy()
env["TEMP"] = env["TMP"] = str(ROOT / ".tools" / "temp")

def run(args):
    result = subprocess.run([str(value) for value in args], env=env, timeout=150,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, f"Installer returned {result.returncode}"

installer = ROOT / "release" / "StoryDriver-Setup-x64.exe"
run([installer, "/S", "/NOSHORTCUTS", f"/DATAROOT={DATA}", "/LAN=0", f"/D={APP}"])
assert (APP / "StoryDriver.exe").is_file()
config = APP / "storydriver.config.json"
settings = json.loads(config.read_text())
assert Path(settings["dataRoot"]).resolve() == DATA
settings["backendPort"] = 8123
config.write_text(json.dumps(settings), encoding="utf-8")
preserved_config = config.read_bytes()
sentinel = APP / "unrelated-user-file.txt"
sentinel.write_text("This file is not shipped by StoryDriver.", encoding="ascii")
run([installer, "/S", "/NOSHORTCUTS", f"/DATAROOT={TEST / 'unused-data'}", "/LAN=1", f"/D={APP}"])
assert config.read_bytes() == preserved_config, "Upgrade changed existing configuration"
assert hashlib.sha256(database.read_bytes()).hexdigest() == before
run([APP / "Uninstall.exe", "/S"])
deadline = time.monotonic() + 30
while (APP / "StoryDriver.exe").exists() and time.monotonic() < deadline:
    time.sleep(0.25)
assert not (APP / "StoryDriver.exe").exists(), "Uninstaller did not remove the shipped executable"
assert sentinel.read_text() == "This file is not shipped by StoryDriver."
assert hashlib.sha256(database.read_bytes()).hexdigest() == before
with sqlite3.connect(database) as db:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT value FROM fixture").fetchone()[0] == "Synthetic preserved story"
result = {"fresh_install": True, "upgrade_preserved_config": True, "uninstall_preserved_data": True,
          "uninstall_preserved_unrelated_files": True, "no_shortcuts": True, "test_root": str(TEST)}
output = ROOT / ".tools" / "release-test-data" / "installer-evidence.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
