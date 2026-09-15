from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "native"
RELEASE = ROOT / "release"
PACKAGING_PYTHON = ROOT / "tools" / "packaging" / ".venv" / "Scripts" / "python.exe"
NSIS = ROOT / ".tools" / "nsis-3.12" / "nsis-3.12" / "makensis.exe"
LLAMA = ROOT / "runtimes" / "llama.cpp"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def checked_path(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    if resolved == parent.resolve() or parent.resolve() not in resolved.parents:
        raise RuntimeError(f"Refusing destructive build operation outside {parent}: {resolved}")
    return resolved


def fresh_directory(path: Path, parent: Path) -> None:
    checked_path(path, parent)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def build_environment() -> dict[str, str]:
    value = os.environ.copy()
    temp = ROOT / ".tools" / "temp"
    caches = {
        "TEMP": temp,
        "TMP": temp,
        "PIP_CACHE_DIR": ROOT / ".tools" / "pip-cache",
        "PYINSTALLER_CONFIG_DIR": ROOT / ".tools" / "pyinstaller",
        "npm_config_cache": ROOT / ".tools" / "npm-cache",
        "NUGET_PACKAGES": ROOT / ".tools" / "nuget",
        "DOTNET_CLI_HOME": ROOT / ".tools" / "dotnet-home",
    }
    for key, path in caches.items():
        path.mkdir(parents=True, exist_ok=True)
        value[key] = str(path)
    value["PYTHONUTF8"] = "1"
    value["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    return value


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    shutil.copytree(source, destination, dirs_exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def package_portable(app_dir: Path) -> Path:
    stage = BUILD / "portable" / "StoryDriver"
    fresh_directory(BUILD / "portable", BUILD)
    copy_tree(app_dir, stage)
    (stage / "portable.marker").write_text("StoryDriver portable data stays in .\\data\n", encoding="ascii")
    archive = RELEASE / "StoryDriver-Portable-x64.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(stage.parent))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build StoryDriver native Windows release artifacts")
    parser.add_argument("--skip-tools", action="store_true", help="Reuse existing frontend/backend/desktop build outputs")
    args = parser.parse_args()

    if not PACKAGING_PYTHON.is_file():
        raise FileNotFoundError(f"D-only packaging environment is missing: {PACKAGING_PYTHON}")
    if not NSIS.is_file():
        raise FileNotFoundError(f"D-only NSIS 3.12 tool is missing: {NSIS}")
    manifest = json.loads((ROOT / "runtimes" / "llama.cpp.manifest.json").read_text(encoding="utf-8"))
    server = LLAMA / "llama-server.exe"
    if not server.is_file() or sha256(ROOT / ".tools" / "downloads" / manifest["asset"]) != manifest["sha256"].upper():
        raise RuntimeError("The official llama.cpp Vulkan runtime or its verified source archive is missing.")

    env = build_environment()
    narration_python = ROOT / "tools" / "narration" / ".venv" / "Scripts" / "python.exe"
    if not narration_python.is_file():
        raise FileNotFoundError("Create tools/narration/.venv and install apps/narration/requirements.lock.txt first.")
    run([str(PACKAGING_PYTHON), str(ROOT / "scripts" / "prepare_narration_runtime.py")], env=env)
    RELEASE.mkdir(parents=True, exist_ok=True)
    app_dir = BUILD / "app"
    if not args.skip_tools:
        fresh_directory(BUILD, ROOT / "build")
        run(["npm.cmd", "run", "build"], cwd=ROOT / "frontend", env=env)
        run([
            str(PACKAGING_PYTHON), "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(BUILD), "--workpath", str(BUILD / "pyinstaller-work"),
            str(ROOT / "installer" / "pyinstaller" / "storydriver_backend.spec"),
        ], env=env)
        run([
            str(PACKAGING_PYTHON), "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(BUILD / "cli"), "--workpath", str(BUILD / "pyinstaller-cli-work"),
            str(ROOT / "installer" / "pyinstaller" / "storydriver_cli.spec"),
        ], env=env)
        run([
            "dotnet", "publish", str(ROOT / "apps" / "desktop" / "StoryDriver.Desktop.csproj"),
            "-c", "Release", "-r", "win-x64", "--self-contained", "true",
            "-o", str(BUILD / "desktop"), "--nologo",
        ], env=env)
        run([
            str(narration_python), "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(BUILD), "--workpath", str(BUILD / "pyinstaller-narration-work"),
            str(ROOT / "installer" / "pyinstaller" / "storydriver_narration.spec"),
        ], env=env)

        fresh_directory(app_dir, BUILD)
        copy_tree(BUILD / "desktop", app_dir)
        copy_tree(BUILD / "backend", app_dir / "backend")
        copy_tree(ROOT / "frontend" / "dist", app_dir / "backend" / "frontend_dist")
        copy_tree(LLAMA, app_dir / "runtimes" / "llama.cpp")
        copy_tree(BUILD / "narration", app_dir / "runtimes" / "kokoro")
        for asset in json.loads((ROOT / "runtimes" / "kokoro.manifest.json").read_text())["assets"]:
            shutil.copy2(ROOT / "runtimes" / "kokoro" / asset["name"], app_dir / "runtimes" / "kokoro" / asset["name"])
        copy_tree(ROOT / "third_party" / "licenses", app_dir / "LICENSES")
        for environment in (ROOT / "tools" / "packaging" / ".venv", ROOT / "tools" / "narration" / ".venv"):
            for metadata in (environment / "Lib" / "site-packages").glob("*.dist-info"):
                for path in metadata.rglob("*"):
                    if path.is_file() and any(word in path.name.lower() for word in ("license", "copying", "notice")):
                        target = app_dir / "LICENSES" / metadata.name / path.relative_to(metadata)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
        shutil.copy2(BUILD / "cli" / "StoryDriverCLI.exe", app_dir / "StoryDriverCLI.exe")
        shutil.copy2(ROOT / "apps" / "desktop" / "storydriver.config.example.json", app_dir / "storydriver.config.example.json")
        shutil.copy2(ROOT / "runtimes" / "llama.cpp.manifest.json", app_dir / "runtimes" / "llama.cpp.manifest.json")
        shutil.copy2(ROOT / "VERSION", app_dir / "VERSION")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / name, app_dir / name)

    # Keep redistribution notices for browser and native dependencies beside the binaries.
    npm_root = ROOT / "frontend" / "node_modules"
    packages = [path for path in npm_root.iterdir() if path.is_dir() and not path.name.startswith(".")]
    packages = [nested for path in packages for nested in (list(path.iterdir()) if path.name.startswith("@") else [path]) if nested.is_dir()]
    for package in packages:
        for path in package.glob("*"):
            if path.is_file() and any(word in path.name.lower() for word in ("license", "copying", "notice")):
                target = app_dir / "LICENSES" / "frontend" / package.relative_to(npm_root) / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    for path in (ROOT / ".tools" / "nuget").rglob("*"):
        if path.is_file() and any(word in path.name.lower() for word in ("license", "thirdpartynotice")):
            target = app_dir / "LICENSES" / "dotnet" / path.relative_to(ROOT / ".tools" / "nuget")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    required = [
        app_dir / "StoryDriver.exe",
        app_dir / "backend" / "StoryDriverBackend.exe",
        app_dir / "backend" / "frontend_dist" / "index.html",
        app_dir / "runtimes" / "llama.cpp" / "llama-server.exe",
        app_dir / "StoryDriverCLI.exe",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Native package is incomplete: {missing}")

    portable = package_portable(app_dir)
    shutil.copy2(ROOT / "installer" / "RELEASE_NOTES.md", RELEASE / "RELEASE_NOTES.md")
    # Uninstall only files shipped in this build. Never recursively delete a user-selected folder.
    manifest = []
    for path in sorted(app_dir.rglob("*")):
        if path.is_file():
            relative = str(path.relative_to(app_dir)).replace("$", "$$")
            manifest.append(f'Delete "$INSTDIR\\{relative}"')
    for path in sorted((p for p in app_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        relative = str(path.relative_to(app_dir)).replace("$", "$$")
        manifest.append(f'RMDir "$INSTDIR\\{relative}"')
    (BUILD / "uninstall-files.nsh").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    run([str(NSIS), "/V2", f"/DROOT_DIR={ROOT}", f"/DPRODUCT_VERSION={VERSION}", str(ROOT / "installer" / "StoryDriver.nsi")], env=env)
    installer = RELEASE / "StoryDriver-Setup-x64.exe"
    artifacts = [installer, portable, RELEASE / "StoryDriver-ThirdParty-Sources.zip", RELEASE / "RELEASE_NOTES.md"]
    checksums = RELEASE / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in artifacts), encoding="ascii")
    print(json.dumps({
        "version": VERSION,
        "app_bytes": sum(path.stat().st_size for path in app_dir.rglob("*") if path.is_file()),
        "app_files": sum(1 for path in app_dir.rglob("*") if path.is_file()),
        "installer": {"path": str(installer), "bytes": installer.stat().st_size, "sha256": sha256(installer)},
        "portable": {"path": str(portable), "bytes": portable.stat().st_size, "sha256": sha256(portable)},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
