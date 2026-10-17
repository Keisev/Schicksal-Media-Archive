# =============================================================================
# build_windows.ps1
# Empaqueta Schicksal Media Archive (FastAPI + pywebview) en un .exe único
# para Windows usando PyInstaller. Descarga los binarios externos
# (yt-dlp.exe, ffmpeg.exe, ffprobe.exe) desde sus fuentes oficiales.
#
# Uso (en una máquina Windows, dentro de la raíz del proyecto):
#   powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
#
# Compatible con GitHub Actions: detecta la variable GITHUB_ACTIONS=true
# y ajusta el comportamiento (sin interactividad, logs más limpios).
# =============================================================================

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$IsCI = ($env:GITHUB_ACTIONS -eq "true")
$AppName = "SchicksalMediaArchive"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " $AppName - Build Windows EXE" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "==> Build iniciado en: $PSScriptRoot"
if ($IsCI) { Write-Host "==> Entorno CI detectado (GitHub Actions)" -ForegroundColor Yellow }

# -----------------------------------------------------------------------------
# 0. Localizar Python (py -> python -> python3)
# -----------------------------------------------------------------------------
$Python = $null
foreach ($cand in @("python", "python3", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        try {
            & $cand --version *> $null
            if ($LASTEXITCODE -eq 0) { $Python = $cand; break }
        } catch { continue }
    }
}
if (-not $Python) {
    throw "No se encontró Python en el PATH. Instala Python 3.x y reintenta."
}
Write-Host "==> Usando Python: $Python" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 1. Instalar dependencias Python
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[1/5] Instalando dependencias Python..." -ForegroundColor Cyan

foreach ($pkg in @("pyinstaller", "pywebview", "Pillow")) {
    # "pip show" escribe WARNINGs por stderr si el paquete no existe.
    # Con ErrorActionPreference=Stop, PowerShell 5.1 convierte ese stderr
    # en error FATAL (NativeCommandError). Bajamos la preferencia solo
    # durante la comprobacion y la restauramos despues.
    $ErrorActionPreference = "Continue"
    & $Python -m pip show $pkg *> $null
    $installed = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"

    if (-not $installed) {
        Write-Host "    Instalando $pkg..." -ForegroundColor Yellow
        & $Python -m pip install --upgrade $pkg
        if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de '$pkg' via pip." }
    } else {
        Write-Host "    $pkg ya esta instalado." -ForegroundColor Green
    }
}

$ErrorActionPreference = "Continue"
& $Python -m pip install -r requirements.txt | Out-Null
$reqOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $reqOk) { throw "Fallo la instalacion de requirements.txt" }

Write-Host "    Dependencias listas." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 2. Descargar binarios reales a build_bin/
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] Descargando binarios a build_bin/..." -ForegroundColor Cyan

$BinDir = Join-Path $PSScriptRoot "build_bin"
New-Item -ItemType Directory -Path $BinDir -Force | Out-Null

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- yt-dlp.exe ---
$YtdlpExe = Join-Path $BinDir "yt-dlp.exe"
if (Test-Path $YtdlpExe) {
    Write-Host "    yt-dlp.exe ya existe, se reutiliza." -ForegroundColor Green
} else {
    Write-Host "    Descargando yt-dlp.exe..." -ForegroundColor Gray
    try {
        $releaseApi = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        $release = Invoke-RestMethod -Uri $releaseApi -Headers @{ "User-Agent" = "build-windows-script" }
        $asset = $release.assets | Where-Object { $_.name -eq "yt-dlp.exe" } | Select-Object -First 1
        if ($asset) {
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $YtdlpExe -UseBasicParsing
        } else { throw "asset no encontrado" }
    } catch {
        Write-Host "    API falló, usando URL directa..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" `
                          -OutFile $YtdlpExe -UseBasicParsing
    }
    Write-Host "    yt-dlp.exe descargado." -ForegroundColor Green
}

# --- ffmpeg.exe y ffprobe.exe desde BtbN builds (estáticos, confiables) ---
$FfmpegExe = Join-Path $BinDir "ffmpeg.exe"
$FfprobeExe = Join-Path $BinDir "ffprobe.exe"

if ((Test-Path $FfmpegExe) -and (Test-Path $FfprobeExe)) {
    Write-Host "    ffmpeg/ffprobe ya existen, se reutilizan." -ForegroundColor Green
} else {
    Write-Host "    Descargando ffmpeg + ffprobe (BtbN builds)..." -ForegroundColor Gray
    $zipUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    $tmpDir = Join-Path $env:TEMP ("ffmpeg_extract_" + [Guid]::NewGuid().ToString("N"))
    $tmpZip = Join-Path $tmpDir "ffmpeg.zip"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
        $ffmpegFound = Get-ChildItem -Path $tmpDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        $ffprobeFound = Get-ChildItem -Path $tmpDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
        if (-not $ffmpegFound -or -not $ffprobeFound) {
            throw "No se encontraron ffmpeg.exe o ffprobe.exe dentro del ZIP"
        }
        Copy-Item $ffmpegFound.FullName $FfmpegExe -Force
        Copy-Item $ffprobeFound.FullName $FfprobeExe -Force
    } finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "    ffmpeg/ffprobe descargados." -ForegroundColor Green
}

Write-Host "    Binarios en build_bin/:" -ForegroundColor Gray
Get-ChildItem $BinDir -Filter "*.exe" | ForEach-Object {
    $sizeMB = [math]::Round($_.Length / 1MB, 2)
    Write-Host "      - $($_.Name) ($sizeMB MB)" -ForegroundColor Gray
}

# -----------------------------------------------------------------------------
# 3. Verificar parche _MEIPASS en main.py
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] Verificando parche _MEIPASS en main.py..." -ForegroundColor Cyan
$MainSrc = Get-Content -Raw -Path (Join-Path $PSScriptRoot "main.py")
if ($MainSrc -notmatch "_MEIPASS") {
    throw "main.py no contiene el parche sys._MEIPASS requerido para pywebview en modo onefile."
}
Write-Host "    Parche _MEIPASS presente." -ForegroundColor Green

# -----------------------------------------------------------------------------
# 4. Construir el ejecutable con PyInstaller
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Ejecutando PyInstaller (--onefile --noconsole)..." -ForegroundColor Cyan

# Limpiar builds anteriores
Remove-Item -Recurse -Force "build\$AppName" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "dist\$AppName*" -ErrorAction SilentlyContinue

$ErrorActionPreference = "Continue"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --noconsole `
    --onefile `
    --name $AppName `
    --add-binary "$BinDir\yt-dlp.exe;." `
    --add-binary "$BinDir\ffmpeg.exe;." `
    --add-binary "$BinDir\ffprobe.exe;." `
    --add-data "static;static" `
    --collect-all "webview" `
    main.py
$pyiOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"

if (-not $pyiOk) { throw "PyInstaller finalizó con errores." }

# -----------------------------------------------------------------------------
# 5. Verificación final
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Verificación final..." -ForegroundColor Cyan

$Exe = Join-Path $PSScriptRoot "dist\$AppName.exe"
if (-not (Test-Path $Exe)) {
    throw "PyInstaller terminó pero no se encontró dist\$AppName.exe"
}

$sizeMB = [math]::Round((Get-Item $Exe).Length / 1MB, 2)
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host " BUILD EXITOSO" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "    Ejecutable: dist\$AppName.exe ($sizeMB MB)" -ForegroundColor Green
Write-Host ""

if (-not $IsCI) {
    Write-Host "    Pruébalo con doble clic o desde consola:" -ForegroundColor Gray
    Write-Host "      .\dist\$AppName.exe" -ForegroundColor Gray
    Write-Host ""
}