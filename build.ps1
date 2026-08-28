<#
.SYNOPSIS
    Build the Windows desktop app into dist\PDFTranslate and zip it for release.

.PARAMETER SkipAssets
    Skip downloading the layout model and font. The build still works, but the
    packaged app downloads them on its first translation instead of running offline.
#>
[CmdletBinding()]
param(
    [switch]$SkipAssets
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$output = Join-Path $root "dist_v4\PDFTranslate"

$running = Get-Process -Name "PDFTranslate" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "==> Closing running PDFTranslate app" -ForegroundColor Yellow
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
if (Test-Path $output) {
    Remove-Item -Path $output -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "==> Installing app and packaging dependencies" -ForegroundColor Cyan
& $python -m pip install -r (Join-Path $root "requirements-app.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }

if (-not $SkipAssets) {
    Write-Host "==> Fetching the layout model and font to bundle" -ForegroundColor Cyan
    & $python (Join-Path $root "scripts\fetch_assets.py")
    if ($LASTEXITCODE -ne 0) { throw "fetch_assets.py failed with exit code $LASTEXITCODE" }
}

Write-Host "==> Running PyInstaller" -ForegroundColor Cyan
$dist_dir = Join-Path $root "dist_v4"
& $python -m PyInstaller --noconfirm --clean --distpath $dist_dir (Join-Path $root "app.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

if (-not (Test-Path $output)) {
    throw "PyInstaller did not produce $output"
}

# PyInstaller can fail partway through COLLECT and still leave a folder behind, so
# check the payload rather than trusting that the folder exists. pdf2zh and scripts
# are not listed: they are frozen into the PYZ archive, not shipped as folders.
$required = @(
    "PDFTranslate.exe",
    "_internal\base_library.zip",
    "_internal\app\fonts\BeVietnamPro-Regular.ttf",
    "_internal\customtkinter",
    "_internal\tkinterdnd2",
    "_internal\cv2",
    "_internal\onnxruntime",
    "_internal\pymupdf"
)
if (-not $SkipAssets -or (Test-Path (Join-Path $root "app\assets\doclayout.onnx"))) {
    $required += "_internal\app\assets\doclayout.onnx"
    $required += "_internal\app\assets\GoNotoKurrent-Regular.ttf"
}
$missing = $required | Where-Object { -not (Test-Path (Join-Path $output $_)) }
if ($missing) {
    throw "Incomplete build, refusing to package. Missing:`n  " + ($missing -join "`n  ")
}

$archive = Join-Path $root "dist_v4\PDFTranslate-windows.zip"
Write-Host "==> Zipping to $archive" -ForegroundColor Cyan
& $python -c "import zipfile, os; src=r'$output'; dst=r'$archive'; zf=zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED); [zf.write(os.path.join(r, f), os.path.relpath(os.path.join(r, f), src)) for r, d, files in os.walk(src) for f in files]; zf.close()"

$size = (Get-ChildItem $output -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("==> Done. Folder {0:N0} MB, archive {1:N0} MB" -f $size, ((Get-Item $archive).Length / 1MB)) -ForegroundColor Green
Write-Host "Test on a machine with no Python before publishing." -ForegroundColor Yellow
