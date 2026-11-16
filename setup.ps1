<#
.SYNOPSIS
  Install AI Mentor on Windows: tools, the app, a local AI model, and optionally
  lip sync and voice cloning. Safe to run again: finished parts are skipped.

.EXAMPLE
  # From anywhere (downloads the project into .\Ai-mentor):
  irm https://raw.githubusercontent.com/Sankey001/Ai-mentor/main/setup.ps1 | iex

  # From inside a downloaded copy:
  powershell -ExecutionPolicy Bypass -File setup.ps1 -All
#>
param(
  [string]$Dir = "",            # where to put the project when downloading it
  [string]$Model = "gemma3",    # Ollama model for replies
  [switch]$LipSync,             # install lip sync (NVIDIA GPU, ~10 GB)
  [switch]$Voice,               # install voice cloning (~12 GB)
  [switch]$All,                 # both of the above
  [switch]$ApiOnly,             # skip Ollama and the local model (use a Gemini/Groq key)
  [switch]$Yes,                 # do not ask; install only what the switches name
  [switch]$SkipSystem           # do not install tools with winget (only check them)
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$RepoUrl = "https://github.com/Sankey001/Ai-mentor.git"
# Trust the Windows certificate store (works behind antivirus HTTPS scanning).
$env:UV_SYSTEM_CERTS = "true"
# Large GPU libraries (hundreds of MB each) need time on slower connections.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = "600" }
if (-not $env:UV_HTTP_RETRIES) { $env:UV_HTTP_RETRIES = "5" }

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Info($text) { Write-Host "    $text" }
function Warn($text) { Write-Host "    ! $text" -ForegroundColor Yellow }
function Has($command) { [bool](Get-Command $command -ErrorAction SilentlyContinue) }
function Run {
  # Run a program and stop the setup if it fails.
  & $args[0] @($args | Select-Object -Skip 1)
  if ($LASTEXITCODE -ne 0) { throw "this command failed (exit $LASTEXITCODE): $($args -join ' ')" }
}
function Quiet {
  # Run a program ignoring its stderr; Windows PowerShell would otherwise stop
  # on any warning a program prints there.
  $ErrorActionPreference = "Continue"
  & $args[0] @($args | Select-Object -Skip 1) 2>$null
}
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}
function Ask($question) {
  if ($Yes) { return $false }
  return (Read-Host "    $question [y/N]") -match '^(y|yes)$'
}
function Python-Works($python, $code) {
  # True when the environment exists and can run the check.
  if (-not (Test-Path $python)) { return $false }
  Quiet $python -c $code | Out-Null
  return $LASTEXITCODE -eq 0
}
function Ollama-Up {
  try { Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:11434/api/tags | Out-Null; return $true }
  catch { return $false }
}

trap {
  Write-Host "`nSetup stopped: $_" -ForegroundColor Red
  Write-Host "Fix the problem above and run setup again; finished parts are skipped." -ForegroundColor Red
  exit 1
}

# ---------------------------------------------------------------- tools
Step "Checking tools"
Refresh-Path  # tools installed since this terminal opened
$tools = @(
  @{ Command = "git";    Id = "Git.Git" },
  @{ Command = "uv";     Id = "astral-sh.uv" },
  @{ Command = "ffmpeg"; Id = "Gyan.FFmpeg" },
  @{ Command = "node";   Id = "OpenJS.NodeJS.LTS" }
)
if (-not $ApiOnly) { $tools += @{ Command = "ollama"; Id = "Ollama.Ollama" } }
foreach ($tool in $tools) {
  if (Has $tool.Command) { Info "$($tool.Command): found"; continue }
  if ($SkipSystem) { throw "$($tool.Command) is missing. Install it (winget install $($tool.Id)) and run setup again." }
  Info "$($tool.Command): installing $($tool.Id) with winget"
  # winget's exit code also signals "already installed"; what matters is the command afterwards.
  Quiet winget install --id $tool.Id -e --source winget --accept-source-agreements --accept-package-agreements
  Refresh-Path
  if (-not (Has $tool.Command)) { throw "$($tool.Command) is still missing after installing $($tool.Id). Open a new terminal and run setup again." }
}
$nodeVersion = [version]((node --version).TrimStart("v"))
if ($nodeVersion.Major -lt 20) { throw "Node.js 20 or newer is needed (found $nodeVersion). Update it: winget upgrade OpenJS.NodeJS.LTS" }
# The website's build tool (Vite) asks for 20.19+ or 22.13+; older ones work but warn.
if (($nodeVersion.Major -eq 20 -and $nodeVersion.Minor -lt 19) -or ($nodeVersion.Major -eq 22 -and $nodeVersion.Minor -lt 13)) {
  Warn "Node.js $nodeVersion is a little old for the website; consider: winget upgrade OpenJS.NodeJS.LTS"
}

# ---------------------------------------------------------------- project
Step "Getting the project"
$here = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if ($here -and (Test-Path (Join-Path $here "ai_service\start_app.py"))) {
  $Root = $here
} else {
  $Root = if ($Dir) { $Dir } else { Join-Path (Get-Location) "Ai-mentor" }
  if (Test-Path (Join-Path $Root "ai_service\start_app.py")) {
    Info "using the copy in $Root"
  } else {
    # schannel: Git uses the Windows certificate store, like a browser.
    Run git -c http.sslBackend=schannel clone --depth 1 $RepoUrl $Root
  }
}
Set-Location $Root
Info "project: $Root"

# ---------------------------------------------------------------- what to install
$gpu = $false
if (Has "nvidia-smi") {
  # Collect all output first: stopping the pipeline early would kill nvidia-smi.
  $gpuInfo = @(Quiet nvidia-smi '--query-gpu=name,memory.total,driver_version' '--format=csv,noheader')[0]
  if ($LASTEXITCODE -eq 0 -and $gpuInfo) {
    $gpu = $true
    Info "NVIDIA GPU: $gpuInfo"
    $driver = [double](($gpuInfo -split ',')[-1].Trim() -replace '^(\d+\.\d+).*', '$1')
    if ($driver -lt 550) { Warn "voice cloning needs NVIDIA driver 550 or newer; update your driver first" }
  }
}
if ($All) { $LipSync = $true; $Voice = $true }
if (-not $gpu -and $LipSync) { Warn "lip sync needs an NVIDIA GPU; skipping it"; $LipSync = $false }
if (-not $All) {
  if (-not $LipSync -and $gpu) { $LipSync = Ask "Install lip sync (moving mouth, about 10 GB)?" }
  if (-not $Voice) {
    $note = if ($gpu) { "" } else { "; slow without an NVIDIA GPU" }
    $Voice = Ask "Install voice cloning (mentors speak in a recorded voice, about 12 GB$note)?"
  }
}

# ---------------------------------------------------------------- app
Step "Installing the AI service (Python)"
$appPython = "ai_service\.venv\Scripts\python.exe"
if (-not (Test-Path $appPython)) { Run uv venv ai_service\.venv --python 3.13 }
Run uv pip install --python $appPython -r ai_service\backend\requirements.txt

Step "Installing the website (Node)"
# npm.cmd, not the npm.ps1 wrapper: under strict errors its warnings would stop setup.
if (Test-Path "frontend\node_modules") { Info "already installed" } else { Run npm.cmd ci --prefix frontend }

Step "Settings"
if (-not (Test-Path "frontend\.env")) { Copy-Item frontend\mentor-preview.env.example frontend\.env; Info "created frontend\.env" }
$envFile = "ai_service\backend\.env"
$modelTag = if ($Model -match ':') { $Model } else { "${Model}:latest" }
$newEnv = -not (Test-Path $envFile)
if ($newEnv) { Copy-Item ai_service\backend\.env.example $envFile; Info "created $envFile" }
$lines = Get-Content $envFile
if ($newEnv -and $ApiOnly) {
  $lines = $lines -replace '^AI_PROVIDER=.*', 'AI_PROVIDER=gemini'
  Info "replies use Gemini: add your key at http://localhost:5173/mentor-settings"
}
# Keep a default model chosen later in Settings, unless -Model was given.
if ($newEnv -or $PSBoundParameters.ContainsKey("Model")) {
  $lines = $lines -replace '^OLLAMA_MODEL=.*', "OLLAMA_MODEL=$modelTag"
} else {
  $configured = ($lines | Select-String -Pattern '^OLLAMA_MODEL=(.+)$' | Select-Object -First 1)
  if ($configured) { $modelTag = $configured.Matches[0].Groups[1].Value.Trim() }
}
[IO.File]::WriteAllLines((Join-Path $Root $envFile), [string[]]$lines)  # UTF-8 without a BOM

# ---------------------------------------------------------------- local AI model
if (-not $ApiOnly) {
  Step "Local AI model ($modelTag)"
  if (-not (Ollama-Up)) {
    Info "starting Ollama"
    Start-Process ollama -ArgumentList "serve" -WindowStyle Minimized
    foreach ($i in 1..15) { if (Ollama-Up) { break }; Start-Sleep -Seconds 1 }
  }
  if (-not (Ollama-Up)) { throw "Ollama did not start. Start the Ollama app, then run setup again." }
  $installed = (Invoke-RestMethod -TimeoutSec 10 http://127.0.0.1:11434/api/tags).models.name
  if ($installed -contains $modelTag) { Info "already downloaded" } else { Run ollama pull $modelTag }
}

# ---------------------------------------------------------------- lip sync
if ($LipSync) {
  Step "Lip sync (MuseTalk)"
  $py = "local\lip_sync\.venv\Scripts\python.exe"
  if (-not (Test-Path "local\lip_sync\MuseTalk\musetalk")) {
    Run git -c http.sslBackend=schannel clone --depth 1 https://github.com/TMElyralab/MuseTalk.git local\lip_sync\MuseTalk
  }
  if (Python-Works $py "import torch, mmcv, mmpose, diffusers, truststore; assert torch.cuda.is_available()") {
    Info "environment already works"
  } else {
    if (-not (Test-Path $py)) { Run uv venv local\lip_sync\.venv --python 3.10 }
    Run uv pip install --python $py --index-strategy unsafe-best-match -r ai_service\workers\requirements-lipsync.txt
    Run uv pip install --python $py --no-deps mmpose==1.1.0 json-tricks munkres xtcocotools
  }
  Run $py ai_service\workers\download_models.py lipsync
}

# ---------------------------------------------------------------- voice
if ($Voice) {
  Step "Voice cloning (Chatterbox)"
  $py = "local\voice_clone\.venv\Scripts\python.exe"
  $requirements = if ($gpu) { "requirements-voice.txt" } else { "requirements-voice-cpu.txt" }
  if (Python-Works $py "import torch, truststore, perth; from chatterbox.tts_turbo import ChatterboxTurboTTS; assert perth.PerthImplicitWatermarker") {
    Info "environment already works"
  } else {
    if (-not (Test-Path $py)) { Run uv venv local\voice_clone\.venv --python 3.10 }
    Run uv pip install --python $py --index-strategy unsafe-best-match -r "ai_service\workers\$requirements"
  }
  Run $py ai_service\workers\download_models.py voice
}

# ---------------------------------------------------------------- done
Step "Done"
Info "Start AI Mentor:   .\run.cmd        (in $Root)"
Info "Then open:         http://localhost:5173/mentor"
Info "Keys and models:   http://localhost:5173/mentor-settings"
