param([switch]$Demo)
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
# The interface is rebuilt whenever its sources are newer than the build: after an update an old dist
# would show last month's screens against today's server.
$builtPage = Join-Path $PSScriptRoot 'dist\index.html'
$sources = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'src'), (Join-Path $PSScriptRoot 'public') -Recurse -File)
$sources += @('index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json', 'tools\pwa.mjs') |
    ForEach-Object { Get-Item -LiteralPath (Join-Path $PSScriptRoot $_) -ErrorAction SilentlyContinue }
$newestSource = ($sources | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
if (-not (Test-Path -LiteralPath $builtPage) -or (Get-Item -LiteralPath $builtPage).LastWriteTimeUtc -lt $newestSource) {
    $installed = Join-Path $PSScriptRoot 'node_modules\.package-lock.json'
    $lock = Get-Item -LiteralPath (Join-Path $PSScriptRoot 'package-lock.json')
    if (-not (Test-Path -LiteralPath $installed)) {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Node dependencies could not be installed.' }
    } elseif ((Get-Item -LiteralPath $installed).LastWriteTimeUtc -lt $lock.LastWriteTimeUtc) {
        # Not `npm ci` here: it empties node_modules first and leaves it broken when a running tool holds a file.
        npm.cmd install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Node dependencies could not be updated.' }
    }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
# -Demo: the invented school on a temporary database (see CONTRIBUTING.md, the local demo section).
if ($Demo) {
    & $pythonExecutable -m tools.demo.local
    exit $LASTEXITCODE
}
Write-Host 'Atlas: http://127.0.0.1:8000'
& $pythonExecutable -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
