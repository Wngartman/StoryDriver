param(
    [ValidateSet("diagnose", "stop", "start", "restart", "free")]
    [string]$Action = "diagnose"
)

$ErrorActionPreference = "Continue"
$Root = "D:\StoryDriver"
$BackendDir = Join-Path $Root "backend"
$DataDir = Join-Path $BackendDir "data"
$LogDir = Join-Path $DataDir "logs"
$WorkflowDir = Join-Path $DataDir "comfy_workflows"
$ReportPath = Join-Path $LogDir "comfyui_diagnostics.txt"

$Config = [ordered]@{
    COMFYUI_BASE_URL = "http://localhost:8188"
    COMFYUI_WORKING_DIR = ""
    COMFYUI_START_COMMAND = ""
    COMFYUI_LAUNCH_ARGS = ""
    COMFYUI_EXPECTED_BACKEND = "unknown"
    COMFYUI_PERF_NOTES = ""
    STORYDRIVER_BASE_DIR = $Root
    STORYDRIVER_DATA_DIR = $DataDir
}

function Load-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $parts = $line -split '=', 2
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($Config.Contains($key)) {
            $Config[$key] = $value
        }
    }
}

Load-EnvFile (Join-Path $Root ".env")
Load-EnvFile (Join-Path $BackendDir ".env")
Load-EnvFile (Join-Path $Root "scripts\storydriver.local.env")
foreach ($key in @($Config.Keys)) {
    $envValue = [Environment]::GetEnvironmentVariable($key)
    if ($envValue) {
        $Config[$key] = $envValue
    }
}

function Port-FromUrl {
    param([string]$Url, [int]$Fallback)
    try {
        $uri = [uri]$Url
        if ($uri.Port -gt 0) { return $uri.Port }
    } catch {}
    return $Fallback
}

function Normalize-Url {
    param([string]$Url)
    if (-not $Url) { return "" }
    return ([string]$Url).Trim().TrimEnd("/")
}

$ComfyBaseUrl = Normalize-Url $Config.COMFYUI_BASE_URL
if (-not $ComfyBaseUrl) { $ComfyBaseUrl = "http://localhost:8188" }
$ConfiguredComfyPort = Port-FromUrl $ComfyBaseUrl 8188

$script:ReportLines = New-Object System.Collections.Generic.List[string]
$script:CaptureReport = $false

function Write-ReportLine {
    param([string]$Text = "")
    Write-Host $Text
    if ($script:CaptureReport) {
        $script:ReportLines.Add($Text) | Out-Null
    }
}

function Get-ProcessCommandLine {
    param([int]$PidValue)
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $PidValue) -ErrorAction SilentlyContinue
    if ($proc) { return $proc.CommandLine }
    return ""
}

function Get-Listeners {
    param([int[]]$Ports)
    $items = @()
    foreach ($port in ($Ports | Where-Object { $_ } | Sort-Object -Unique)) {
        $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $connections) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            $items += [pscustomobject]@{
                Port = $port
                Address = $conn.LocalAddress
                Pid = $conn.OwningProcess
                ProcessName = if ($proc) { $proc.ProcessName } else { "unknown" }
                CommandLine = Get-ProcessCommandLine $conn.OwningProcess
            }
        }
    }
    return $items
}

function Safe-ComfyReason {
    param([object]$Process)
    $cmd = [string]$Process.CommandLine
    $exe = [string]$Process.ExecutablePath
    $workingDir = [string]$Config.COMFYUI_WORKING_DIR
    if ($workingDir -and ($cmd -like "*$workingDir*" -or $exe -like "$workingDir*")) {
        return "configured COMFYUI_WORKING_DIR"
    }
    if ($cmd -match '(?i)(\\|/)ComfyUI(\\|/)main\.py') {
        return "ComfyUI main.py command line"
    }
    if ($Process.Name -ieq "ComfyUI.exe" -and $exe -match '(?i)(\\|/)ComfyUI(\\|/)ComfyUI\.exe$') {
        return "ComfyUI.exe executable path"
    }
    return ""
}

function Get-ComfyProcesses {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $items = @()
    foreach ($proc in $all) {
        $name = [string]$proc.Name
        $cmd = [string]$proc.CommandLine
        $exe = [string]$proc.ExecutablePath
        $looksLikeComfy =
            ($name -ieq "ComfyUI.exe") -or
            (($name -match '^(python|pythonw)\.exe$') -and ($cmd -match '(?i)ComfyUI|comfyui|\\ComfyUI\\main\.py'))
        if (-not $looksLikeComfy) { continue }
        $reason = Safe-ComfyReason $proc
        $port = $null
        if ($cmd -match '--port\s+([0-9]+)') {
            $port = [int]$matches[1]
        }
        $items += [pscustomobject]@{
            Pid = $proc.ProcessId
            ParentPid = $proc.ParentProcessId
            Name = $name
            ExecutablePath = $exe
            CommandLine = $cmd
            SafeToStop = [bool]$reason
            SafeReason = $reason
            ServerPort = $port
            IsPythonServer = ($cmd -match '(?i)(\\|/)ComfyUI(\\|/)main\.py')
            IsDesktop = ($name -ieq "ComfyUI.exe" -or $cmd -match "desktop_app")
        }
    }
    return $items | Sort-Object Pid
}

function Invoke-Http {
    param(
        [string]$Method,
        [string]$Url,
        [string]$Body = ""
    )
    try {
        if ($Body) {
            $response = Invoke-WebRequest -UseBasicParsing -Method $Method -Uri $Url -ContentType "application/json" -Body $Body -TimeoutSec 6
        } else {
            $response = Invoke-WebRequest -UseBasicParsing -Method $Method -Uri $Url -TimeoutSec 6
        }
        return @{
            Ok = $true
            StatusCode = $response.StatusCode
            Content = [string]$response.Content
            Error = ""
        }
    } catch {
        return @{
            Ok = $false
            StatusCode = $null
            Content = ""
            Error = $_.Exception.Message
        }
    }
}

function Test-ComfyUrl {
    param([string]$BaseUrl)
    $base = Normalize-Url $BaseUrl
    if (-not $base) { return $false }
    $result = Invoke-Http "GET" "$base/system_stats"
    if (-not $result.Ok) { return $false }
    return $result.Content -match "comfyui_version|devices|pytorch_version"
}

function Get-DetectedComfyUrls {
    $urls = @($ComfyBaseUrl)
    foreach ($proc in Get-ComfyProcesses) {
        if ($proc.ServerPort) {
            $urls += "http://127.0.0.1:$($proc.ServerPort)"
            $urls += "http://localhost:$($proc.ServerPort)"
        }
    }
    return $urls | Where-Object { $_ } | Sort-Object -Unique
}

function Get-ActiveComfyBaseUrl {
    if (Test-ComfyUrl $ComfyBaseUrl) { return $ComfyBaseUrl }
    $reachable = @()
    foreach ($url in Get-DetectedComfyUrls) {
        if ($url -eq $ComfyBaseUrl) { continue }
        if (Test-ComfyUrl $url) { $reachable += $url }
    }
    $reachable = $reachable | Sort-Object -Unique
    $localReachable = @($reachable | Where-Object { $_ -match "127\.0\.0\.1" })
    if ($localReachable.Count -gt 0) { return $localReachable[0] }
    if ($reachable.Count -gt 0) { return $reachable[0] }
    return ""
}

function Get-JsonValue {
    param([string]$Json)
    try { return $Json | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}

function Write-EndpointSummary {
    param([string]$BaseUrl, [string]$Path)
    $url = "$(Normalize-Url $BaseUrl)$Path"
    Write-ReportLine "Endpoint: $url"
    $result = Invoke-Http "GET" $url
    if (-not $result.Ok) {
        Write-ReportLine "  ERROR: $($result.Error)"
        return
    }
    Write-ReportLine "  HTTP $($result.StatusCode)"
    $content = $result.Content
    if ($content.Length -gt 5000) {
        $content = $content.Substring(0, 5000) + "`n  ... truncated ..."
    }
    $content.Split("`n") | ForEach-Object { Write-ReportLine "  $_" }
}

function Write-WorkflowSummary {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Write-ReportLine ""
    Write-ReportLine "Workflow: $Path"
    try {
        $workflow = Get-Content -Raw -Path $Path | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-ReportLine "  Could not parse JSON: $($_.Exception.Message)"
        return
    }

    $nodes = @()
    if ($workflow.nodes) { $nodes += $workflow.nodes }
    if ($workflow.definitions -and $workflow.definitions.subgraphs) {
        foreach ($subgraph in $workflow.definitions.subgraphs) {
            if ($subgraph.nodes) { $nodes += $subgraph.nodes }
        }
    }
    if ($nodes.Count -eq 0) {
        foreach ($prop in $workflow.PSObject.Properties) {
            $value = $prop.Value
            if ($value -and $value.class_type) {
                $nodes += [pscustomobject]@{
                    id = $prop.Name
                    type = $value.class_type
                    widgets_values = @()
                    inputs = $value.inputs
                }
            }
        }
    }

    Write-ReportLine ("  Node count scanned: {0}" -f $nodes.Count)
    $interesting = $nodes | Where-Object { $_.type -match '(?i)Loader|Sampler|Decode|Preview|Save|Upscale|Lora|LoRA|VAE|CLIP|UNET|Checkpoint|ControlNet' }
    foreach ($node in $interesting) {
        $models = @()
        if ($node.widgets_values) {
            foreach ($value in $node.widgets_values) {
                if ($value -is [string] -and $value -match '(?i)\.(safetensors|ckpt|pt|bin)$|fp8|bf16|qwen|z_image|ae\.safetensors|lumina|res_multistep|euler|simple') {
                    $models += $value
                }
            }
        }
        if ($node.inputs) {
            foreach ($inputProp in $node.inputs.PSObject.Properties) {
                $value = [string]$inputProp.Value
                if ($value -match '(?i)\.(safetensors|ckpt|pt|bin)$|fp8|bf16|qwen|z_image|ae\.safetensors') {
                    $models += "$($inputProp.Name)=$value"
                }
            }
        }
        $modelText = if ($models.Count) { ($models | Sort-Object -Unique) -join ", " } else { "" }
        Write-ReportLine ("  Node {0}: {1} {2}" -f $node.id, $node.type, $modelText)
    }
}

function Write-SystemSummary {
    Write-ReportLine ""
    Write-ReportLine "Windows GPU and RAM"
    $videos = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
    if ($videos) {
        foreach ($video in $videos) {
            Write-ReportLine ("  GPU: {0}; AdapterRAM={1}; DriverVersion={2}" -f $video.Name, $video.AdapterRAM, $video.DriverVersion)
        }
    } else {
        Write-ReportLine "  GPU: unavailable from Win32_VideoController"
    }
    $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    if ($computer) {
        Write-ReportLine ("  TotalPhysicalMemory={0}" -f $computer.TotalPhysicalMemory)
    }
    Write-ReportLine "  dxdiag: not run by this script; Win32_VideoController summary above is included for a quick safe snapshot."
}

function Write-ProcessSummary {
    Write-ReportLine ""
    Write-ReportLine "ComfyUI-related processes"
    $processes = @(Get-ComfyProcesses)
    if ($processes.Count -eq 0) {
        Write-ReportLine "  No ComfyUI-like processes found."
    }
    foreach ($proc in $processes) {
        Write-ReportLine ("  PID {0} parent={1} name={2} safe={3} reason={4}" -f $proc.Pid, $proc.ParentPid, $proc.Name, $proc.SafeToStop, $proc.SafeReason)
        if ($proc.ExecutablePath) { Write-ReportLine ("    exe: {0}" -f $proc.ExecutablePath) }
        if ($proc.CommandLine) { Write-ReportLine ("    cmd: {0}" -f $proc.CommandLine) }
    }
    $serverProcesses = @($processes | Where-Object { $_.IsPythonServer })
    $serverPorts = @($serverProcesses | Where-Object { $_.ServerPort } | Select-Object -ExpandProperty ServerPort | Sort-Object -Unique)
    if ($serverProcesses.Count -gt 1) {
        Write-ReportLine ("  WARNING: {0} ComfyUI Python server-like processes were found. Listener ownership below tells which one is active." -f $serverProcesses.Count)
    }
    if ($serverPorts.Count -gt 1) {
        Write-ReportLine ("  WARNING: multiple ComfyUI server ports appear in command lines: {0}" -f ($serverPorts -join ", "))
    }
    if ($processes | Where-Object { $_.IsDesktop }) {
        Write-ReportLine "  NOTE: ComfyUI Desktop/Electron appears to be in use. On AMD, compare Desktop against a clean manual/portable ROCm launch before tuning StoryDriver resource modes."
    }

    $ports = @(8188, $ConfiguredComfyPort, 8000, 8001, 8002, 8003)
    $ports += $serverPorts
    Write-ReportLine ""
    Write-ReportLine "Relevant listening ports"
    $listeners = @(Get-Listeners $ports)
    if ($listeners.Count -eq 0) {
        Write-ReportLine "  No listeners on configured/default ComfyUI ports."
    }
    foreach ($listener in $listeners) {
        Write-ReportLine ("  {0}:{1} PID {2} {3}" -f $listener.Address, $listener.Port, $listener.Pid, $listener.ProcessName)
        if ($listener.CommandLine) { Write-ReportLine ("    cmd: {0}" -f $listener.CommandLine) }
    }
}

function Write-LogsSummary {
    $desktopUserDir = "C:\Users\wngar\Documents\ComfyUI\user"
    Write-ReportLine ""
    Write-ReportLine "ComfyUI logs"
    if (-not (Test-Path $desktopUserDir)) {
        Write-ReportLine "  Desktop user log folder not found at expected path."
        return
    }
    $logs = Get-ChildItem -Path $desktopUserDir -File -Filter "comfyui*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 6
    foreach ($log in $logs) {
        Write-ReportLine ("  {0} size={1} modified={2}" -f $log.FullName, $log.Length, $log.LastWriteTime)
    }
    $latestUseful = $logs | Where-Object { $_.Length -gt 0 } | Select-Object -First 1
    if ($latestUseful) {
        Write-ReportLine ""
        Write-ReportLine ("Recent memory/performance lines from {0}" -f $latestUseful.FullName)
        Get-Content -Path $latestUseful.FullName -Tail 220 -ErrorAction SilentlyContinue |
            Select-String -Pattern "Total VRAM|pytorch version|ROCm|Device:|Prompt executed|Requested to load|loaded completely|Unloaded partially|Using split attention|AOTriton|VAE load|CLIP/text encoder|queue|database|port|WARNING|IMPORT FAILED|No module named" |
            Select-Object -Last 80 |
            ForEach-Object { Write-ReportLine ("  {0}" -f $_.Line) }
    }
}

function Write-ConfigSummary {
    Write-ReportLine "StoryDriver ComfyUI configuration"
    Write-ReportLine ("  COMFYUI_BASE_URL={0}" -f $Config.COMFYUI_BASE_URL)
    Write-ReportLine ("  COMFYUI_WORKING_DIR={0}" -f $Config.COMFYUI_WORKING_DIR)
    Write-ReportLine ("  COMFYUI_START_COMMAND={0}" -f $Config.COMFYUI_START_COMMAND)
    Write-ReportLine ("  COMFYUI_LAUNCH_ARGS={0}" -f $Config.COMFYUI_LAUNCH_ARGS)
    Write-ReportLine ("  COMFYUI_EXPECTED_BACKEND={0}" -f $Config.COMFYUI_EXPECTED_BACKEND)
    Write-ReportLine ("  COMFYUI_PERF_NOTES={0}" -f $Config.COMFYUI_PERF_NOTES)
    Write-ReportLine ("  STORYDRIVER_BASE_DIR={0}" -f $Config.STORYDRIVER_BASE_DIR)
    Write-ReportLine ("  STORYDRIVER_DATA_DIR={0}" -f $Config.STORYDRIVER_DATA_DIR)
    Write-ReportLine ("  Workflow folder={0}" -f $WorkflowDir)
}

function Diagnose-ComfyUI {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $script:ReportLines.Clear()
    $script:CaptureReport = $true

    Write-ReportLine "StoryDriver ComfyUI Diagnostics"
    Write-ReportLine ("Timestamp: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"))
    Write-ReportLine ""
    Write-ConfigSummary

    Write-ReportLine ""
    Write-ReportLine "Configured ComfyUI endpoint checks"
    foreach ($path in @("/system_stats", "/queue", "/models")) {
        Write-EndpointSummary $ComfyBaseUrl $path
    }

    $activeUrl = Get-ActiveComfyBaseUrl
    if ($activeUrl -and $activeUrl -ne $ComfyBaseUrl) {
        Write-ReportLine ""
        Write-ReportLine ("Detected reachable ComfyUI endpoint outside configured URL: {0}" -f $activeUrl)
        foreach ($path in @("/system_stats", "/queue", "/models")) {
            Write-EndpointSummary $activeUrl $path
        }
    }

    Write-ReportLine ""
    Write-ReportLine "Free endpoint probe"
    $freeProbeBase = if ($activeUrl) { $activeUrl } else { $ComfyBaseUrl }
    $freeProbe = Invoke-Http "POST" "$freeProbeBase/free" '{"unload_models":false,"free_memory":false}'
    if ($freeProbe.Ok) {
        Write-ReportLine ("  /free responded HTTP {0} to no-op payload at {1}" -f $freeProbe.StatusCode, $freeProbeBase)
    } else {
        Write-ReportLine ("  /free unavailable at {0}: {1}" -f $freeProbeBase, $freeProbe.Error)
    }

    Write-ProcessSummary
    Write-SystemSummary

    Write-ReportLine ""
    Write-ReportLine "StoryDriver workflow files"
    if (Test-Path $WorkflowDir) {
        $storyWorkflows = Get-ChildItem -Path $WorkflowDir -File -Filter "*.json" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike "*.storydriver.json" }
        if ($storyWorkflows.Count -eq 0) {
            Write-ReportLine "  No exported API workflow JSON files are currently in the StoryDriver workflow folder."
        }
        foreach ($workflow in $storyWorkflows) {
            Write-WorkflowSummary $workflow.FullName
        }
    } else {
        Write-ReportLine "  StoryDriver workflow folder missing."
    }

    $desktopWorkflowDir = "C:\Users\wngar\Documents\ComfyUI\user\default\workflows"
    Write-ReportLine ""
    Write-ReportLine "Detected ComfyUI Desktop Z-Image workflows"
    if (Test-Path $desktopWorkflowDir) {
        $zWorkflows = Get-ChildItem -Path $desktopWorkflowDir -File -Filter "*.json" -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "z.?image|turbo" }
        if ($zWorkflows.Count -eq 0) {
            Write-ReportLine "  No Z-Image workflow names detected in Desktop workflow folder."
        }
        foreach ($workflow in $zWorkflows) {
            Write-WorkflowSummary $workflow.FullName
        }
    } else {
        Write-ReportLine "  ComfyUI Desktop workflow folder not found."
    }

    Write-LogsSummary

    $script:CaptureReport = $false
    $script:ReportLines | Set-Content -Path $ReportPath -Encoding UTF8
    Write-Host ""
    Write-Host "Saved report to $ReportPath"
}

function Stop-ComfySafe {
    $processes = @(Get-ComfyProcesses)
    $safe = @($processes | Where-Object { $_.SafeToStop })
    if ($safe.Count -eq 0) {
        Write-Host "No safely identifiable ComfyUI process found."
        Write-Host "Configured COMFYUI_WORKING_DIR=$($Config.COMFYUI_WORKING_DIR)"
        Write-Host "If ComfyUI is running, close it manually or configure COMFYUI_WORKING_DIR/COMFYUI_START_COMMAND in D:\StoryDriver\.env."
        $listeners = @(Get-Listeners @(8188, $ConfiguredComfyPort, 8000, 8001, 8002, 8003))
        foreach ($listener in $listeners) {
            Write-Host ("Port {0} listener PID {1} {2}" -f $listener.Port, $listener.Pid, $listener.ProcessName)
            if ($listener.CommandLine) { Write-Host ("  Command: {0}" -f $listener.CommandLine) }
        }
        return
    }
    Write-Host "Stopping safely identifiable ComfyUI processes only..."
    foreach ($proc in ($safe | Sort-Object Pid -Descending)) {
        Write-Host ("Stopping PID {0} {1} ({2})" -f $proc.Pid, $proc.Name, $proc.SafeReason)
        try {
            Stop-Process -Id $proc.Pid -Force -ErrorAction Stop
        } catch {
            Write-Host ("  Could not stop PID {0}: {1}" -f $proc.Pid, $_.Exception.Message)
        }
    }
}

function Start-ComfyClean {
    $workingDir = [string]$Config.COMFYUI_WORKING_DIR
    $startCommand = [string]$Config.COMFYUI_START_COMMAND
    $launchArgs = [string]$Config.COMFYUI_LAUNCH_ARGS

    if (-not $workingDir -or -not $startCommand) {
        Write-Host "ComfyUI clean start is not configured."
        Write-Host "Set these in D:\StoryDriver\.env after choosing the install you want StoryDriver to manage:"
        Write-Host "  COMFYUI_WORKING_DIR=..."
        Write-Host "  COMFYUI_START_COMMAND=..."
        Write-Host "  COMFYUI_LAUNCH_ARGS=..."
        Write-Host "Current COMFYUI_BASE_URL=$($Config.COMFYUI_BASE_URL)"
        return
    }
    if (-not (Test-Path $workingDir)) {
        Write-Host "Configured COMFYUI_WORKING_DIR does not exist: $workingDir"
        return
    }

    $state = Test-ComfyUrl $ComfyBaseUrl
    if ($state) {
        Write-Host "ComfyUI is already reachable at $ComfyBaseUrl. Not starting another instance."
        return
    }

    $commandText = $startCommand
    if ($launchArgs) { $commandText = "$commandText $launchArgs" }
    Write-Host "Starting ComfyUI with configured command..."
    Write-Host "Working directory: $workingDir"
    Write-Host "Command: $commandText"
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $commandText) -WorkingDirectory $workingDir -WindowStyle Hidden
}

function Free-ComfyMemory {
    $base = Get-ActiveComfyBaseUrl
    if (-not $base) {
        Write-Host "No reachable ComfyUI endpoint found from COMFYUI_BASE_URL or detected ComfyUI process ports."
        Write-Host "Configured COMFYUI_BASE_URL=$($Config.COMFYUI_BASE_URL)"
        return
    }

    $queueResult = Invoke-Http "GET" "$base/queue"
    if ($queueResult.Ok) {
        $queue = Get-JsonValue $queueResult.Content
        if ($queue -and (($queue.queue_running | Measure-Object).Count -gt 0 -or ($queue.queue_pending | Measure-Object).Count -gt 0)) {
            Write-Host "ComfyUI queue is not empty. Refusing to request memory free while a workflow may be active."
            Write-Host $queueResult.Content
            return
        }
    }

    $result = Invoke-Http "POST" "$base/free" '{"unload_models":true,"free_memory":true}'
    if ($result.Ok) {
        Write-Host "Requested ComfyUI model unload/free memory at $base/free"
        Write-Host "HTTP $($result.StatusCode)"
    } else {
        Write-Host "ComfyUI /free request failed at $base/free"
        Write-Host $result.Error
    }
}

switch ($Action) {
    "diagnose" { Diagnose-ComfyUI }
    "stop" { Stop-ComfySafe }
    "start" { Start-ComfyClean }
    "restart" { Stop-ComfySafe; Start-Sleep -Seconds 3; Start-ComfyClean }
    "free" { Free-ComfyMemory }
}
