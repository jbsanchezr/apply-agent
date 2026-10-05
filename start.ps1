<#
.SYNOPSIS
    Start the apply-agent page against a real Gmail inbox, on this machine only.

.DESCRIPTION
    Sets the defaults for local use (Gmail, a local Ollama model, a database
    and an API token under ~/.config/apply_agent), starts the API and opens
    the applications page. Any APPLY_AGENT_* variable already set in the
    environment wins over these defaults. Stop it with Ctrl+C.

    One-time setup, see README: "Using a real Gmail inbox".

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Port 8080 -NoBrowser
#>
param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$configDir = Join-Path $HOME '.config\apply_agent'
New-Item -ItemType Directory -Force $configDir | Out-Null

function Set-Default([string]$Name, [string]$Value) {
    if (-not [Environment]::GetEnvironmentVariable($Name, 'Process')) {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    }
}

# The token protects the page. It is generated once and kept outside the repo.
$tokenPath = Join-Path $configDir 'api_token.txt'
if (-not (Test-Path $tokenPath)) {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $generated = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [IO.File]::WriteAllText($tokenPath, $generated)
}
$token = (Get-Content $tokenPath -Raw).Trim()

$database = (Join-Path $configDir 'applications.db').Replace('\', '/')
Set-Default 'APPLY_AGENT_API_TOKEN' $token
Set-Default 'APPLY_AGENT_DATABASE_URL' "sqlite:///$database"
Set-Default 'APPLY_AGENT_EMAIL_PROVIDER' 'gmail'
Set-Default 'APPLY_AGENT_LLM' 'ollama'
Set-Default 'APPLY_AGENT_INITIAL_LOOKBACK_DAYS' '30'

if ($env:APPLY_AGENT_EMAIL_PROVIDER -eq 'gmail' -and
    -not (Test-Path (Join-Path $configDir 'gmail_token.json'))) {
    Write-Host 'No Gmail token yet. Run this once, then start again:' -ForegroundColor Yellow
    Write-Host '  uv run python -m apply_agent.providers.gmail_auth'
    exit 1
}

if ($env:APPLY_AGENT_LLM -eq 'ollama') {
    try {
        Invoke-WebRequest 'http://localhost:11434/api/version' -UseBasicParsing -TimeoutSec 3 | Out-Null
    } catch {
        Write-Host 'Ollama is not answering on localhost:11434. The page will open,' -ForegroundColor Yellow
        Write-Host 'but "Sync now" will fail until Ollama is running.' -ForegroundColor Yellow
    }
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Installing dependencies (first run)...'
    uv sync --frozen
}

$url = "http://localhost:$Port/applications"
Set-Clipboard $env:APPLY_AGENT_API_TOKEN
Write-Host ''
Write-Host "  Page:      $url"
Write-Host '  User:      anything'
Write-Host "  Password:  the token in $tokenPath (copied to the clipboard)"
Write-Host '  Stop:      Ctrl+C'
Write-Host ''

if (-not $NoBrowser) {
    # Give the server a moment to bind before the browser asks for the page.
    Start-Job { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList $url | Out-Null
}

& $python -m uvicorn --factory apply_agent.api.app:create_app --host 127.0.0.1 --port $Port
