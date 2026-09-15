"""Download public build-time assets; never called by the running application."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download(url: str, destination: Path, digest: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        print(f"Downloading {destination.name}", flush=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        urllib.request.urlretrieve(url, temporary)
        temporary.replace(destination)
    with destination.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest and actual != digest:
        raise RuntimeError(f"Checksum mismatch for {destination}")


def main():
    manifest = json.loads((ROOT / "runtimes" / "kokoro.manifest.json").read_text())
    for asset in manifest["assets"]:
        download(asset["url"], ROOT / "runtimes" / "kokoro" / asset["name"], asset["sha256"])
    sources = ROOT / "third_party" / "sources"
    download("https://github.com/thewh1teagle/espeakng-loader/archive/146599e29be31bf17d99f0bcb7dbb2f92aef3d95.tar.gz", sources / "espeakng-loader-0.2.4.tar.gz")
    download("https://github.com/espeak-ng/espeak-ng/archive/4870adfa25b1a32b4361592f1be8a40337c58d6c.tar.gz", sources / "espeak-ng-4870adf.tar.gz")
    download("https://files.pythonhosted.org/packages/bc/7d/5a96ddb130552f6365a090b5fd12ace803a95a858e3f67258f2ad13dc51f/phonemizer-3.4.0.tar.gz", sources / "phonemizer-3.4.0.tar.gz", "e13231980c50bc671ec0466379ba027260ad9d61929952d8ae9665b3d0f251eb")
    licenses = ROOT / "third_party" / "licenses"
    download("https://raw.githubusercontent.com/espeak-ng/espeak-ng/4870adfa25b1a32b4361592f1be8a40337c58d6c/COPYING", licenses / "GPL-3.0.txt")
    download("https://www.apache.org/licenses/LICENSE-2.0.txt", licenses / "Apache-2.0.txt")
    download("https://raw.githubusercontent.com/ggml-org/llama.cpp/b10507/LICENSE", licenses / "llama.cpp-MIT.txt")
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    with zipfile.ZipFile(release / "StoryDriver-ThirdParty-Sources.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sources.iterdir()):
            if path.is_file() and not path.name.endswith(".partial"):
                archive.write(path, path.name)
        for path in (ROOT / "apps" / "narration").glob("*.py"):
            archive.write(path, f"StoryDriverNarration/{path.name}")
        archive.write(ROOT / "apps" / "narration" / "requirements.lock.txt", "StoryDriverNarration/requirements.lock.txt")
        archive.write(ROOT / "installer" / "pyinstaller" / "storydriver_narration.spec", "StoryDriverNarration/storydriver_narration.spec")
        archive.writestr("BUILD.txt", "StoryDriverNarration: Python 3.12 x64; create an isolated venv and install requirements.lock.txt. Run the repository's scripts/build_native_release.py. The loader 0.2.4 espeak-ng submodule uses the enclosed 4870adf source; extract it into espeak-ng and follow build.sh. Original upstream build scripts and license texts are included. Complete StoryDriver sources accompany the same GitHub release.\n")
    print("Narration assets and corresponding-source archive ready.")


if __name__ == "__main__":
    main()
