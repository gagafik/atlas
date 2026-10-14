$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
$requirementsHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'requirements.lock.txt') -Algorithm SHA256).Hash
$dependencyStamp = Join-Path $PSScriptRoot '.venv\atlas-requirements.sha256'
if (-not (Test-Path -LiteralPath $dependencyStamp) -or (Get-Content -LiteralPath $dependencyStamp -Raw).Trim() -ne $requirementsHash) {
    & $pythonExecutable -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependencies could not be installed.' }
    Set-Content -LiteralPath $dependencyStamp -Value $requirementsHash
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Node dependencies could not be installed.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
Write-Host 'Atlas: http://127.0.0.1:8000'
& $pythonExecutable -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
