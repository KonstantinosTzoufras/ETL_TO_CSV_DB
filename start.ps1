param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$etlPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $etlPython)) {
    throw 'Create the environment first: python -m venv .venv. Then install requirements.txt. See README.md.'
}
& $etlPython -m etl serve --port $Port
exit $LASTEXITCODE
