"""Copy only indexed source into the existing sanitized release clone, never development history."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / '.tools' / 'public-release'
assert DEST.resolve() == ROOT.resolve() / '.tools' / 'public-release'
assert (DEST / '.git').is_dir(), 'Clone the sanitized public main first.'

def indexed(root):
    raw = subprocess.check_output(['git', '-c', f'safe.directory={root.as_posix()}', 'ls-files', '-z'], cwd=root)
    return {value.decode() for value in raw.split(b'\0') if value}

files = {p for p in indexed(ROOT) if not p.startswith('backend/data/')}
old = indexed(DEST)
for name in old - files:
    target = (DEST / name).resolve()
    assert DEST.resolve() in target.parents
    if target.is_file(): target.unlink()
for name in files:
    source, target = ROOT / name, DEST / name
    if not source.is_file(): continue
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
print(f'Prepared {len(files)} indexed source files; development .git and runtime data excluded.')
