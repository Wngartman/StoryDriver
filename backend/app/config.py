from pathlib import Path
import os


def load_env_files() -> None:
    root = Path(__file__).resolve().parents[2]
    shell_keys = set(os.environ)
    for path in (root / ".env", root / "backend" / ".env", root / "scripts" / "storydriver.local.env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in shell_keys:
                os.environ[key] = value.strip().strip('"')


load_env_files()

APP_NAME = "StoryDriver"
BASE_DIR = Path(os.getenv("STORYDRIVER_BASE_DIR", Path(__file__).resolve().parents[2])).resolve()
DATA_DIR = Path(os.getenv("STORYDRIVER_DATA_DIR", BASE_DIR / "backend" / "data")).resolve()
DB_PATH = Path(os.getenv("STORYDRIVER_DB_PATH", DATA_DIR / "app.db")).resolve()

OPEN_BROWSER_AFTER_START = os.getenv("OPEN_BROWSER_AFTER_START", "true")
OPEN_BROWSER_ALWAYS = os.getenv("OPEN_BROWSER_ALWAYS", "false")
STORYDRIVER_FRONTEND_URL = os.getenv("STORYDRIVER_FRONTEND_URL", "http://localhost:5173")
STORYDRIVER_BACKEND_URL = os.getenv("STORYDRIVER_BACKEND_URL", "http://localhost:8001")

LM_STUDIO_BASE_URL = os.getenv(
    "LM_STUDIO_OPENAI_BASE_URL",
    os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
)
LM_STUDIO_REST_BASE_URL = os.getenv("LM_STUDIO_REST_BASE_URL", "http://localhost:1234/api/v1")
LM_STUDIO_API_TOKEN = os.getenv("LM_STUDIO_API_TOKEN", "")
LM_STUDIO_WORKING_DIR = os.getenv("LM_STUDIO_WORKING_DIR", "")
LM_STUDIO_START_COMMAND = os.getenv("LM_STUDIO_START_COMMAND", "")
LM_STUDIO_WAIT_SECONDS = os.getenv("LM_STUDIO_WAIT_SECONDS", "45")
KOKORO_BASE_URL = os.getenv("KOKORO_BASE_URL", "http://localhost:8880")
KOKORO_SPEECH_ENDPOINT = os.getenv("KOKORO_SPEECH_ENDPOINT", "/v1/audio/speech")
KOKORO_START_COMMAND = os.getenv("KOKORO_START_COMMAND", "")
KOKORO_WORKING_DIR = os.getenv("KOKORO_WORKING_DIR", "")
KOKORO_PYTHON_EXE = os.getenv("KOKORO_PYTHON_EXE", "")
KOKORO_WAIT_SECONDS = os.getenv("KOKORO_WAIT_SECONDS", "45")
QWEN_TTS_BASE_URL = os.getenv("QWEN_TTS_BASE_URL", "http://127.0.0.1:8891")
COMFYUI_BASE_URL = os.getenv("COMFYUI_BASE_URL", "http://localhost:8188")
COMFYUI_WORKING_DIR = os.getenv("COMFYUI_WORKING_DIR", "")
COMFYUI_START_COMMAND = os.getenv("COMFYUI_START_COMMAND", "")
COMFYUI_WAIT_SECONDS = os.getenv("COMFYUI_WAIT_SECONDS", "60")
COMFYUI_LAUNCH_ARGS = os.getenv("COMFYUI_LAUNCH_ARGS", "")
COMFYUI_EXPECTED_BACKEND = os.getenv("COMFYUI_EXPECTED_BACKEND", "unknown")
COMFYUI_PERF_NOTES = os.getenv("COMFYUI_PERF_NOTES", "")
RESTART_EXISTING_SERVICES = os.getenv("RESTART_EXISTING_SERVICES", "false")
STORYDRIVER_DESKTOP_MODE = os.getenv("STORYDRIVER_DESKTOP_MODE", "false")
STORYDRIVER_AUTO_START_KOKORO = os.getenv("STORYDRIVER_AUTO_START_KOKORO", "false")
STORYDRIVER_FRONTEND_DIST = os.getenv("STORYDRIVER_FRONTEND_DIST", "")


class Settings:
    app_name: str = APP_NAME
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR
    db_path: Path = DB_PATH
    open_browser_after_start: str = OPEN_BROWSER_AFTER_START
    open_browser_always: str = OPEN_BROWSER_ALWAYS
    storydriver_frontend_url: str = STORYDRIVER_FRONTEND_URL
    storydriver_backend_url: str = STORYDRIVER_BACKEND_URL
    lm_studio_base_url: str = LM_STUDIO_BASE_URL
    lm_studio_rest_base_url: str = LM_STUDIO_REST_BASE_URL
    lm_studio_api_token: str = LM_STUDIO_API_TOKEN
    lm_studio_working_dir: str = LM_STUDIO_WORKING_DIR
    lm_studio_start_command: str = LM_STUDIO_START_COMMAND
    lm_studio_wait_seconds: str = LM_STUDIO_WAIT_SECONDS
    kokoro_base_url: str = KOKORO_BASE_URL
    kokoro_speech_endpoint: str = KOKORO_SPEECH_ENDPOINT
    kokoro_start_command: str = KOKORO_START_COMMAND
    kokoro_working_dir: str = KOKORO_WORKING_DIR
    kokoro_python_exe: str = KOKORO_PYTHON_EXE
    kokoro_wait_seconds: str = KOKORO_WAIT_SECONDS
    qwen_tts_base_url: str = QWEN_TTS_BASE_URL
    comfyui_base_url: str = COMFYUI_BASE_URL
    comfyui_working_dir: str = COMFYUI_WORKING_DIR
    comfyui_start_command: str = COMFYUI_START_COMMAND
    comfyui_wait_seconds: str = COMFYUI_WAIT_SECONDS
    comfyui_launch_args: str = COMFYUI_LAUNCH_ARGS
    comfyui_expected_backend: str = COMFYUI_EXPECTED_BACKEND
    comfyui_perf_notes: str = COMFYUI_PERF_NOTES
    restart_existing_services: str = RESTART_EXISTING_SERVICES
    desktop_mode: str = STORYDRIVER_DESKTOP_MODE
    auto_start_kokoro: str = STORYDRIVER_AUTO_START_KOKORO
    frontend_dist: str = STORYDRIVER_FRONTEND_DIST


settings = Settings()
