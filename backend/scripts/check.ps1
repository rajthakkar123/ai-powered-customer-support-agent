# Quality gate: the same steps as scripts/check.sh and CI. Run from anywhere:
#   & .\backend\scripts\check.ps1
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $backendRoot

$venvPython = Join-Path $backendRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python).Source }

function Invoke-Step([string]$name, [scriptblock]$command) {
    Write-Host "==> $name"
    # Exit codes decide success. With 'Stop', Windows PowerShell 5.1 would treat a tool's normal
    # stderr output (unittest progress, for example) as fatal whenever output is redirected.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $command
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) {
        Write-Host "FAILED: $name" -ForegroundColor Red
        exit $code
    }
}

Invoke-Step 'Dev tools are installed' { & $python -m scripts.ensure_requirements requirements-dev.txt }
Invoke-Step 'Python syntax' { & $python -m compileall -q support_agent tests scripts }
Invoke-Step 'Unit, workflow, and security tests with coverage' { & $python -m coverage run -m unittest discover }
Invoke-Step 'Coverage threshold' { & $python -m coverage report }
Invoke-Step 'Offline scenario evals' { & $python -m scripts.run_evals }
Invoke-Step 'Ruff lint' { & $python -m ruff check support_agent tests scripts }
Invoke-Step 'Ruff format' { & $python -m ruff format --check support_agent tests scripts }
Invoke-Step 'Mypy type check' { & $python -m mypy }
Invoke-Step 'Secret scan' { & $python -m scripts.scan_secrets }
$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -ne $node) {
    Invoke-Step 'Frontend JavaScript syntax' { & $node.Source --check (Join-Path $backendRoot '..\frontend\assets\support.js') }
} else {
    Write-Host '==> Frontend JavaScript syntax: skipped (Node.js not installed)'
}

Write-Host 'All checks passed.' -ForegroundColor Green
