param(
    [ValidateSet("start-all", "start-backend", "start-frontend", "start-kokoro", "start-lmstudio", "start-comfyui", "check", "stop", "restart", "open-browser", "run-backend", "run-frontend")]
    [string]$Action = "check"
)

$python = "D:\StoryDriver\backend\.venv\Scripts\python.exe"
$script = "D:\StoryDriver\scripts\storydriver_services.py"

if ([System.IO.File]::Exists($python)) {
    & $python $script $Action
    exit $LASTEXITCODE
}

& py -3 $script $Action
exit $LASTEXITCODE
