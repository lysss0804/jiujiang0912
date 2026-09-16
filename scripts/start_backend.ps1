$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
& $python -m pip install -e ".[api,dev]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host "Backend: http://127.0.0.1:8000"
Write-Host "Swagger: http://127.0.0.1:8000/docs"
& $python -m app.main serve
