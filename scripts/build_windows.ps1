param(
    [string]$Python = "$env:LOCALAPPDATA\JHRChiffrage\venv\Scripts\python.exe",
    [string]$ReleaseName = ('v0.1.0-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not (Test-Path -LiteralPath $Python)) { throw "Python introuvable : $Python" }
if ($ReleaseName -notmatch '^[a-zA-Z0-9._-]+$' -or $ReleaseName -in @('.', '..')) { throw 'Nom de release invalide.' }
$ReleaseRoot = Join-Path $ProjectRoot "dist\$ReleaseName"
if (Test-Path -LiteralPath $ReleaseRoot) { throw "La release existe déjà, choisir un nouveau nom : $ReleaseRoot" }
$StagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('jhr-build-' + [guid]::NewGuid().ToString('N'))
$OriginalPath = $env:PATH
# PyInstaller resolves native dependencies from PATH. Do not capture ICU/DLLs
# from unrelated tools such as Poppler, Git or other desktop applications.
$env:PATH = @((Split-Path -Parent $Python), "$env:SystemRoot\System32", "$env:SystemRoot") -join ';'
Push-Location $ProjectRoot
try {
    $Common = @('-m', 'PyInstaller', '--noconfirm', '--onedir', '--paths', 'src', '--distpath', (Join-Path $StagingRoot 'dist'), '--workpath', (Join-Path $StagingRoot 'build'), '--specpath', 'packaging', '--copy-metadata', 'platformdirs')
    & $Python @Common --name JHRChiffrage --windowed --icon (Join-Path $ProjectRoot 'src/jhr_chiffrage/assets/chiffrage.ico') --add-data "$ProjectRoot/src/jhr_chiffrage/assets;jhr_chiffrage/assets" packaging/desktop_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Échec du build bureau.' }
    & $Python @Common --name JHRChiffrageMCP --console --copy-metadata mcp packaging/mcp_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Échec du build MCP.' }
    New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $StagingRoot 'dist\JHRChiffrage') -Destination $ReleaseRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $StagingRoot 'dist\JHRChiffrageMCP') -Destination $ReleaseRoot -Recurse
    Write-Output "Bureau : $ReleaseRoot\JHRChiffrage\JHRChiffrage.exe"
    Write-Output "MCP : $ReleaseRoot\JHRChiffrageMCP\JHRChiffrageMCP.exe"
    Write-Output "Staging conservé : $StagingRoot"
} finally {
    Pop-Location
    $env:PATH = $OriginalPath
}
