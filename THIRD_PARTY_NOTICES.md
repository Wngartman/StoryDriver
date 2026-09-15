# Third-Party Components

StoryDriver's core code is MIT licensed. Components retain their original terms.
No personal reference recordings or text-model weights are included.

| Component | License | Source |
| --- | --- | --- |
| React, Vite, Zustand, Framer Motion, Tailwind CSS | MIT | Respective package metadata in frontend/package-lock.json |
| Lucide | ISC | https://github.com/lucide-icons/lucide |
| Python | PSF | https://www.python.org/psf/license/ |
| FastAPI, Uvicorn, Pydantic | MIT / BSD-3-Clause | Installed distribution license files |
| .NET | MIT | https://github.com/dotnet/runtime |
| Microsoft WebView2 SDK and Runtime | Microsoft redistribution terms | https://developer.microsoft.com/microsoft-edge/webview2/ |
| llama.cpp b10507 | MIT | https://github.com/ggml-org/llama.cpp/tree/b10507 |
| Kokoro-82M v1.0 model and stock voices | Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M |
| kokoro-onnx 0.6.1 | MIT | https://github.com/thewh1teagle/kokoro-onnx |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| NumPy | BSD-3-Clause and bundled-library licenses | https://github.com/numpy/numpy |
| phonemizer 3.4.0 | GPL-3.0-or-later | https://github.com/bootphon/phonemizer |
| eSpeak NG / espeakng-loader 0.2.4 | GPL-3.0-or-later / loader license | https://github.com/thewh1teagle/espeakng-loader |
| PyInstaller bootloader | GPL with distribution exception | https://pyinstaller.org/en/stable/license.html |
| NSIS installer runtime | zlib/libpng | https://nsis.sourceforge.io/License |

The isolated StoryDriverNarration executable and its worker source are distributed
under GPL-3.0-or-later because they link with GPL speech components. This does not
change the license of the separate desktop/backend applications communicating
over the local HTTP API. The worker source is in apps/narration; build instructions
are in docs/BUILDING.md. Corresponding third-party sources and build scripts are
included in StoryDriver-ThirdParty-Sources.zip alongside each binary release.
Exact dependency versions are recorded in apps/narration/requirements.lock.txt.
License texts from installed packages are included under LICENSES in the app.

The WebView2 runtime remains Microsoft's separately serviced prerequisite.
Text models chosen by the user have their own model-specific terms.
