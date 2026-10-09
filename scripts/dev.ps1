# One-command local run (no Docker): sets up the venv, migrates the DB, starts API + UI.
#   powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example - set ADMIN_PASSWORD in it, then re-run." -ForegroundColor Yellow
    exit 1
}

Push-Location backend
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt -c constraints.txt
.\.venv\Scripts\alembic.exe upgrade head
$api = Start-Process -PassThru -NoNewWindow .\.venv\Scripts\uvicorn.exe -ArgumentList "app.main:app", "--port", "8000"
Pop-Location

Push-Location frontend
if (-not (Test-Path node_modules)) { npm ci }
try { npm run dev } finally { Stop-Process -Id $api.Id -ErrorAction SilentlyContinue }
Pop-Location
