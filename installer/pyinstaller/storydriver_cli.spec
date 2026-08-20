from pathlib import Path


repo_root = Path(SPECPATH).parents[1]
entrypoint = repo_root / "apps" / "backend" / "cli.py"
icon = repo_root / "assets" / "desktop" / "storydriver.ico"

a = Analysis(
    [str(entrypoint)],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StoryDriverCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(icon),
    disable_windowed_traceback=False,
)
