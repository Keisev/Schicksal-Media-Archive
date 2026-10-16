# =============================================================================
# build_windows.ps1
# Empaqueta la aplicacion FastAPI + pywebview en un .exe unico para Windows
# usando PyInstaller. Descarga los binarios externos (yt-dlp.exe y ffmpeg.exe)
# desde sus fuentes oficiales antes de construir.
#
# Uso (en una maquina Windows, dentro de la raiz del proyecto):
#   powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Build de Windows iniciado en: $PSScriptRoot" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# 0. Localizar Python (py -> python -> python3)
# -----------------------------------------------------------------------------
$Python = $null
foreach ($cand in @("py", "python", "python3")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        try {
            & $cand --version *> $null
            if ($LASTEXITCODE -eq 0) { $Python = $cand; break }
        } catch { continue }
    }
}
if (-not $Python) {
    throw "No se encontro Python en el PATH. Instala Python 3.x y reintenta."
}
Write-Host "==> Usando Python: $Python" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 1. Verificar dependencias: pyinstaller y pywebview (instalar via pip si falta)
# -----------------------------------------------------------------------------
foreach ($pkg in @("pyinstaller", "pywebview")) {
    & $Python -m pip show $pkg *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "==> '$pkg' no esta instalado. Instalando via pip..." -ForegroundColor Yellow
        & $Python -m pip install --upgrade $pkg
        if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de '$pkg' via pip." }
    } else {
        Write-Host "==> '$pkg' ya esta instalado." -ForegroundColor Green
    }
}

# Asegurar tambien las dependencias de la app (fastapi, uvicorn, etc.)
& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de requirements.txt" }

# -----------------------------------------------------------------------------
# 2. Descargar el ultimo yt-dlp.exe desde GitHub Releases
# -----------------------------------------------------------------------------
$YtdlpExe = Join-Path $PSScriptRoot "yt-dlp.exe"
if (Test-Path $YtdlpExe) {
    Write-Host "==> yt-dlp.exe ya existe, se conservara: $YtdlpExe" -ForegroundColor Green
} else {
    Write-Host "==> Descargando ultimo yt-dlp.exe desde GitHub Releases..." -ForegroundColor Cyan
    $releaseApi = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        $release = Invoke-RestMethod -Uri $releaseApi -Headers @{ "User-Agent" = "build-windows-script" }
        $asset = $release.assets | Where-Object { $_.name -eq "yt-dlp.exe" } | Select-Object -First 1
        if (-not $asset) { throw "No se encontro el asset 'yt-dlp.exe' en la release." }
        Write-Host "    Version: $($release.tag_name)" -ForegroundColor Gray
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $YtdlpExe -UseBasicParsing
    } catch {
        # Fallback al enlace estable directo de la release latest
        Write-Host "    API fallo ($_), usando URL fija de 'latest'..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" `
                          -OutFile $YtdlpExe -UseBasicParsing
    }
    if (-not (Test-Path $YtdlpExe)) { throw "No se pudo descargar yt-dlp.exe" }
    Write-Host "==> yt-dlp.exe descargado correctamente." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# 3. Descargar ffmpeg.exe desde gyan.dev (fuente confiable oficial de builds)
#    Se usa el build "essentials" shared: extraemos solo ffmpeg.exe del zip.
# -----------------------------------------------------------------------------
$FfmpegExe = Join-Path $PSScriptRoot "ffmpeg.exe"
if (Test-Path $FfmpegExe) {
    Write-Host "==> ffmpeg.exe ya existe, se conservara: $FfmpegExe" -ForegroundColor Green
} else {
    Write-Host "==> Descargando ffmpeg.exe desde gyan.dev..." -ForegroundColor Cyan
    $zipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $tmpDir = Join-Path $env:TEMP ("ffmpeg_extract_" + [Guid]::NewGuid().ToString("N"))
    $tmpZip = Join-Path $tmpDir "ffmpeg.zip"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing
        Write-Host "    Extrayendo ffmpeg.exe del archivo ZIP..." -ForegroundColor Gray
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
        $found = Get-ChildItem -Path $tmpDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if (-not $found) { throw "ffmpeg.exe no encontrado dentro del ZIP de gyan.dev" }
        Copy-Item $found.FullName $FfmpegExe -Force
    } finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path $FfmpegExe)) { throw "No se pudo obtener ffmpeg.exe" }
    Write-Host "==> ffmpeg.exe descargado correctamente." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# 4. Truco de compatibilidad para PyInstaller --onefile con pywebview:
#    los .dll del bundle (p.ej. WebView2Loader.dll) viven en sys._MEIPASS y
#    ctypes.windll no los encuentra si no estan en el PATH. main.py ya incluye
#    ese parche, pero lo verificamos aqui antes de construir.
# -----------------------------------------------------------------------------
Write-Host "==> Verificando parche _MEIPASS/PATH en main.py..." -ForegroundColor Cyan
$MainSrc = Get-Content -Raw -Path (Join-Path $PSScriptRoot "main.py")
if ($MainSrc -notmatch "_MEIPASS") {
    throw "main.py no contiene el parche sys._MEIPASS -> PATH requerido para pywebview en modo one-file."
}

# -----------------------------------------------------------------------------
# 5. Construir el ejecutable con PyInstaller
# -----------------------------------------------------------------------------
Write-Host "==> Ejecutando PyInstaller (--noconsole --onefile)..." -ForegroundColor Cyan
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --noconsole `
    --onefile `
    --name "YTDP-WebGUI" `
    --add-binary "yt-dlp.exe;." `
    --add-binary "ffmpeg.exe;." `
    --add-data "static;static" `
    --hidden-import "clr" `
    --collect-all "webview" `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller finalizo con errores." }

# -----------------------------------------------------------------------------
# 5. Resultado
# -----------------------------------------------------------------------------
$Exe = Join-Path $PSScriptRoot "dist\YTDP-WebGUI.exe"
if (Test-Path $Exe) {
    $sizeMB = [math]::Round((Get-Item $Exe).Length / 1MB, 2)
    Write-Host ""
    Write-Host "==> BUILD EXITOSO" -ForegroundColor Green
    Write-Host "    Ejecutable: $Exe (${sizeMB} MB)" -ForegroundColor Green
    Write-Host "    Pruebalo con doble clic o desde la consola: `"$Exe`"" -ForegroundColor Gray
} else {
    throw "El build termino pero no se encontro dist\YTDP-WebGUI.exe"
}
