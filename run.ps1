# Start AI Mentor (after setup.ps1): Ollama if needed, then the app.
# Stop with Ctrl+C.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [Environment]::GetEnvironmentVariable("Path", "User")

try { Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:11434/api/tags | Out-Null }
catch {
  if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Starting Ollama..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Minimized
    Start-Sleep -Seconds 3
  } else {
    Write-Host "Ollama is not installed; local AI replies are off (run setup.ps1)."
  }
}

& ai_service\.venv\Scripts\python.exe ai_service\start_app.py @args
