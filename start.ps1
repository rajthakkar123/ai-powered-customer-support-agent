$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$venvDir = Join-Path $backendDir '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$requirements = Join-Path $backendDir 'requirements.txt'
function Get-PythonCommand {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        & $pythonLauncher.Source -3 -c "import sys; assert sys.version_info >= (3, 11)"
        if ($LASTEXITCODE -eq 0) {
            return ,@($pythonLauncher.Source, '-3')
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Source -c "import sys; assert sys.version_info >= (3, 11)"
        if ($LASTEXITCODE -eq 0) {
            return ,@($pythonCommand.Source)
        }
    }
    throw 'Python 3.11 or newer is required. Install Python and rerun .\start.ps1.'
}

try {
    $basePython = Get-PythonCommand
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Creating backend virtual environment...'
        $basePythonPath = $basePython[0]
        $basePythonArgs = @()
        if ($basePython.Count -gt 1) {
            $basePythonArgs = $basePython[1..($basePython.Count - 1)]
        }
        & $basePythonPath @basePythonArgs -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }

    # Checked on every start so a newly added dependency is installed even if the venv is older.
    Push-Location $backendDir
    try {
        & $venvPython -m scripts.ensure_requirements $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Backend requirement installation failed.' }
    } finally {
        Pop-Location
    }

    $dotenvPath = Join-Path $backendDir '.env'
    if (Test-Path -LiteralPath $dotenvPath) {
        $dotenvJson = & $venvPython -c "import json,sys; from dotenv import dotenv_values; allowed=('SUPPORT_ADMIN_EMAIL','SUPPORT_ADMIN_PASSWORD','SUPPORT_HOST','SUPPORT_PORT','SUPPORT_DATABASE_PATH','SUPPORT_AS_OF_DATE','SUPPORT_ENVIRONMENT','OPENAI_API_KEY','OPENAI_MODEL'); values=dotenv_values(sys.argv[1]); print(json.dumps({key: values.get(key) for key in allowed if values.get(key) is not None}))" $dotenvPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not read backend/.env settings.' }
        $dotenvSettings = $dotenvJson | ConvertFrom-Json
        foreach ($name in @(
            'SUPPORT_ADMIN_EMAIL', 'SUPPORT_ADMIN_PASSWORD', 'SUPPORT_HOST', 'SUPPORT_PORT',
            'SUPPORT_DATABASE_PATH', 'SUPPORT_AS_OF_DATE', 'SUPPORT_ENVIRONMENT',
            'OPENAI_API_KEY', 'OPENAI_MODEL'
        )) {
            $value = $dotenvSettings.$name
            if (-not [Environment]::GetEnvironmentVariable($name, 'Process') -and $null -ne $value) {
                [Environment]::SetEnvironmentVariable($name, [string]$value, 'Process')
            }
        }
    }

    $databasePath = if ($env:SUPPORT_DATABASE_PATH) {
        $env:SUPPORT_DATABASE_PATH
    } else {
        Join-Path $backendDir 'data\support_agent.sqlite3'
    }
    $port = if ($env:SUPPORT_PORT) { $env:SUPPORT_PORT } else { '8000' }
    $url = "http://127.0.0.1:$port/"

    $env:SUPPORT_DATABASE_PATH = $databasePath
    $env:SUPPORT_PORT = $port
    # Local launches are demos: development mode enables the simulated tool-failure switch.
    if (-not $env:SUPPORT_ENVIRONMENT) {
        $env:SUPPORT_ENVIRONMENT = 'development'
    }
    if (-not $env:SUPPORT_AS_OF_DATE) {
        $env:SUPPORT_AS_OF_DATE = Get-Date -Format 'yyyy-MM-dd'
    }

    Write-Host 'Syncing SQLite schema and sample data...'
    Push-Location $backendDir
    try {
        & $venvPython -m support_agent.infrastructure.database
        if ($LASTEXITCODE -ne 0) { throw 'SQLite schema and sample-data sync failed.' }
    } finally {
        Pop-Location
    }

    Write-Host "Starting Customer Support Agent at $url"
    Write-Host 'Sign in with a staff email and password. On first run the server prints the'
    Write-Host 'generated administrator password once, unless SUPPORT_ADMIN_PASSWORD is set.'

    $browserJob = Start-Job -ArgumentList $url -ScriptBlock {
        param($applicationUrl)
        for ($attempt = 0; $attempt -lt 80; $attempt++) {
            try {
                Invoke-WebRequest -Uri $applicationUrl -Method Get -TimeoutSec 2 `
                    -UseBasicParsing | Out-Null
                Start-Process $applicationUrl
                return
            } catch {
                Start-Sleep -Milliseconds 250
            }
        }
    }

    Push-Location $backendDir
    try {
        & $venvPython -m support_agent
        if ($LASTEXITCODE -ne 0) { throw "Support Agent exited with code $LASTEXITCODE." }
    } finally {
        Pop-Location
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
} catch {
    Write-Error $_
    exit 1
}
