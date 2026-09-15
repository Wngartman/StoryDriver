from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata, collect_submodules

root = Path(SPECPATH).parents[1]
datas, binaries, hidden = [], [], collect_submodules("uvicorn")
for name in ("kokoro_onnx", "espeakng_loader", "phonemizer", "onnxruntime"):
    data, binary, imports = collect_all(name)
    datas += data
    binaries += binary
    hidden += imports
datas += copy_metadata("kokoro-onnx")
a = Analysis([str(root / "apps" / "narration" / "worker.py")], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "pytest", "torch", "onnxruntime_gpu"], optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="StoryDriverNarration", console=False,
          debug=False, strip=False, upx=False, disable_windowed_traceback=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="narration")
