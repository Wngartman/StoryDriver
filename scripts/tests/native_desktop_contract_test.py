from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
project = (ROOT / "apps" / "desktop" / "StoryDriver.Desktop.csproj").read_text(encoding="utf-8")
window = (ROOT / "apps" / "desktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
configuration = (ROOT / "apps" / "desktop" / "DesktopConfiguration.cs").read_text(encoding="utf-8")
supervisor = (ROOT / "backend" / "app" / "services" / "service_supervisor.py").read_text(encoding="utf-8")
backend_main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
frontend_api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")

checks = {
    "windows_gui_subsystem": "<OutputType>WinExe</OutputType>" in project,
    "webview2": "Microsoft.Web.WebView2" in project,
    "single_instance": "StoryDriver.Desktop.Instance" in (ROOT / "apps" / "desktop" / "App.xaml.cs").read_text(encoding="utf-8"),
    "hidden_backend": "CreateNoWindow = true" in window.replace("\r", "") or "CreateNoWindow = true" in (ROOT / "apps" / "desktop" / "BackendProcessHost.cs").read_text(encoding="utf-8"),
    "process_tree_cleanup": "JobObjectLimitKillOnJobClose" in (ROOT / "apps" / "desktop" / "BackendProcessHost.cs").read_text(encoding="utf-8"),
    "portable_and_installed_data_roots": "LocalApplicationData" in configuration and "portable.marker" in configuration,
    "loading_artwork": "storydriver-loading.png" in project and "LoadingArtwork" in window,
    "kokoro_background_start": "STORYDRIVER_AUTO_START_KOKORO" in configuration,
    "hidden_kokoro": "CREATE_NO_WINDOW" in supervisor and "shell=True" not in supervisor,
    "one_port_frontend": "resolve_frontend_dist" in backend_main and 'return ""' in frontend_api,
    "external_requests_blocked": "Blocked by StoryDriver local-only mode" in window,
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise AssertionError(f"Native desktop contract failed: {', '.join(failed)}")

print(f"native desktop contract: PASS ({len(checks)} checks)")
