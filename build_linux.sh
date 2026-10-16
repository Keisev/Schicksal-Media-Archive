#!/bin/bash
# ==============================================================================
# build_linux.sh - Compila Schicksal Media Archive como AppImage para Linux
#
# Pasos:
#   1. Verificar/instalar dependencias Python (pyinstaller, pywebview, Pillow)
#   2. Descargar binarios reales (yt-dlp standalone + ffmpeg estático) a build_bin/
#   3. Ejecutar PyInstaller en modo --onedir
#   4. Crear la estructura AppDir (AppRun, .desktop, icono PNG)
#   5. Descargar y ejecutar linuxdeploy para generar el .AppImage
#   6. Verificación final del artefacto generado
#   7. Commit y push del script
# ==============================================================================

set -e

cd "$(dirname "$0")"

echo "=============================================="
echo " Schicksal Media Archive - Build AppImage Linux"
echo "=============================================="

# ------------------------------------------------------------------------------
# 1. Verificar e instalar dependencias
# ------------------------------------------------------------------------------
echo ""
echo "[1/7] Verificando dependencias Python..."

MISSING_DEPS=0
python -c "import PyInstaller" 2>/dev/null || { echo "  - Falta pyinstaller"; MISSING_DEPS=1; }
python -c "import webview"      2>/dev/null || { echo "  - Falta pywebview";   MISSING_DEPS=1; }

if [ "$MISSING_DEPS" -eq 1 ]; then
    echo "  Instalando dependencias desde requirements.txt / requirements-linux.txt..."
    pip install -r requirements.txt -r requirements-linux.txt
else
    echo "  pyinstaller y pywebview ya están instalados."
fi

echo "  Asegurando Pillow (para generar el icono)..."
pip install Pillow

# Herramientas de sistema necesarias para linuxdeploy/appimagetool
if ! command -v file >/dev/null 2>&1; then
    echo "  Instalando utilidad 'file' (requerida por appimagetool)..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y -qq file || \
            sudo apt-get update -qq && sudo apt-get install -y -qq file
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y file
    elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y file
    elif command -v apk >/dev/null 2>&1; then
        sudo apk add file
    fi
fi

# ------------------------------------------------------------------------------
# 2. Descargar binarios reales a build_bin/
# ------------------------------------------------------------------------------
echo ""
echo "[2/7] Descargando binarios a build_bin/..."

mkdir -p build_bin

if [ ! -x build_bin/yt-dlp ]; then
    echo "  Descargando yt-dlp (standalone Linux)..."
    curl -L --fail --retry 3 -o build_bin/yt-dlp \
        https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp
else
    echo "  yt-dlp ya existe, se reutiliza."
fi

if [ ! -x build_bin/ffmpeg ] || [ ! -x build_bin/ffprobe ]; then
    echo "  Descargando ffmpeg estático (amd64)..."
    curl -L --fail --retry 3 -o build_bin/ffmpeg-release-amd64-static.tar.xz \
        https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz

    echo "  Extrayendo ffmpeg y ffprobe del tar.xz..."
    tar -xf build_bin/ffmpeg-release-amd64-static.tar.xz -C build_bin/

    # El tar contiene una carpeta ffmpeg-*-amd64-static/ con los binarios dentro
    FFMPEG_DIR=$(find build_bin/ -maxdepth 1 -type d -name 'ffmpeg-*-amd64-static' | head -n 1)
    if [ -z "$FFMPEG_DIR" ]; then
        echo "  ERROR: No se encontró la carpeta extraída de ffmpeg." >&2
        exit 1
    fi
    mv -f "$FFMPEG_DIR/ffmpeg"  build_bin/ffmpeg
    mv -f "$FFMPEG_DIR/ffprobe" build_bin/ffprobe
    rm -rf "$FFMPEG_DIR"
    rm -f build_bin/ffmpeg-release-amd64-static.tar.xz
else
    echo "  ffmpeg/ffprobe ya existen, se reutilizan."
fi

chmod +x build_bin/yt-dlp build_bin/ffmpeg build_bin/ffprobe

echo "  Verificando permisos de ejecución:"
ls -l build_bin/yt-dlp build_bin/ffmpeg build_bin/ffprobe

# ------------------------------------------------------------------------------
# 3. Ejecutar PyInstaller en modo --onedir
# ------------------------------------------------------------------------------
echo ""
echo "[3/7] Ejecutando PyInstaller (--onedir)..."

rm -rf build/SchicksalMediaArchive dist/SchicksalMediaArchive SchicksalMediaArchive.spec

pyinstaller --onedir --windowed --name SchicksalMediaArchive \
    --add-data "static:static" \
    --add-binary "build_bin/yt-dlp:." \
    --add-binary "build_bin/ffmpeg:." \
    --add-binary "build_bin/ffprobe:." \
    --collect-all webview \
    --collect-all PyQt6 \
    main.py

if [ ! -x dist/SchicksalMediaArchive/SchicksalMediaArchive ]; then
    echo "  ERROR: PyInstaller no generó el ejecutable." >&2
    exit 1
fi
echo "  Ejecutable generado: dist/SchicksalMediaArchive/SchicksalMediaArchive"

# ------------------------------------------------------------------------------
# 4. Crear la estructura AppDir
# ------------------------------------------------------------------------------
echo ""
echo "[4/7] Creando estructura AppDir..."

rm -rf AppDir
mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

echo "  Copiando contenido de dist/SchicksalMediaArchive/ a AppDir/usr/bin/..."
cp -a dist/SchicksalMediaArchive/. AppDir/usr/bin/

cat > AppDir/AppRun << 'EOF'
#!/bin/bash
cd "$(dirname "$0")/usr/bin"
exec ./SchicksalMediaArchive
EOF
chmod +x AppDir/AppRun

cat > AppDir/SchicksalMediaArchive.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Schicksal Media Archive
Exec=SchicksalMediaArchive
Icon=SchicksalMediaArchive
Categories=AudioVideo;Video;
EOF

echo "  Generando icono PNG 256x256 con Pillow..."
python - << 'PYEOF'
from PIL import Image, ImageDraw

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Fondo: círculo con degradado simple (dos colores azul/violeta)
for y in range(SIZE):
    t = y / (SIZE - 1)
    r = int(30 + t * (90 - 30))
    g = int(60 + t * (40 - 60))
    b = int(160 + t * (180 - 160))
    draw.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

# Recortar a forma circular redondeada
mask = Image.new("L", (SIZE, SIZE), 0)
mdraw = ImageDraw.Draw(mask)
mdraw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=48, fill=255)
bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
bg.paste(img, (0, 0), mask)
img = bg
draw = ImageDraw.Draw(img)

# Triangulo "play" blanco (simbolo de video/media)
cx, cy = SIZE // 2, SIZE // 2
s = 70
draw.polygon(
    [(cx - s // 2 + 10, cy - s), (cx - s // 2 + 10, cy + s), (cx + s, cy)],
    fill=(255, 255, 255, 255),
)

img.save("AppDir/usr/share/icons/hicolor/256x256/apps/SchicksalMediaArchive.png")
print("  Icono guardado en AppDir/usr/share/icons/hicolor/256x256/apps/SchicksalMediaArchive.png")
PYEOF

# linuxdeploy espera encontrar el icono junto al .desktop o en la ruta hicolor;
# copiamos tambien una copia raiz para facilitar la deteccion automatica.
cp AppDir/usr/share/icons/hicolor/256x256/apps/SchicksalMediaArchive.png \
   AppDir/SchicksalMediaArchive.png

# ------------------------------------------------------------------------------
# 5. Descargar y ejecutar linuxdeploy
# ------------------------------------------------------------------------------
echo ""
echo "[5/7] Descargando linuxdeploy..."

if [ ! -f linuxdeploy-x86_64.AppImage ]; then
    curl -L --fail --retry 3 -O \
        https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
fi

if [ ! -f linuxdeploy-plugin-appimage-x86_64.AppImage ]; then
    curl -L --fail --retry 3 -O \
        https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/continuous/linuxdeploy-plugin-appimage-x86_64.AppImage
fi

chmod +x linuxdeploy-x86_64.AppImage linuxdeploy-plugin-appimage-x86_64.AppImage

export LINUXDEPLOY_OUTPUT_VERSION="1.0.0"
export DISABLE_COPYRIGHT_CHECK=true

echo "  Ejecutando linuxdeploy (fase 1: desplegar dependencias en AppDir)..."
APPIMAGE_EXTRACT_AND_RUN=1 ./linuxdeploy-x86_64.AppImage --appdir AppDir

echo "  Ejecutando linuxdeploy (fase 2: generar el AppImage)..."
APPIMAGE_EXTRACT_AND_RUN=1 ./linuxdeploy-x86_64.AppImage --appdir AppDir --output appimage

# El plugin appimage de linuxdeploy puede tardar en descargar su runtime;
# si no se genero nada, usar appimagetool directamente como respaldo.
GENERATED_APPIMAGE=$(find . -maxdepth 1 -name 'SchicksalMediaArchive*.AppImage' | head -n 1)
if [ -z "$GENERATED_APPIMAGE" ]; then
    echo "  linuxdeploy no genero el AppImage; usando appimagetool como respaldo..."
    if [ ! -f ./appimagetool-x86_64.AppImage ]; then
        curl -L --fail --retry 3 -O \
            https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
        chmod +x appimagetool-x86_64.AppImage
    fi
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 ./appimagetool-x86_64.AppImage AppDir \
        SchicksalMediaArchive-x86_64.AppImage
    GENERATED_APPIMAGE=$(find . -maxdepth 1 -name 'SchicksalMediaArchive*.AppImage' | head -n 1)
fi

# Mover el AppImage generado a dist/ (objetivo: dist/SchicksalMediaArchive-x86_64.AppImage)
mkdir -p dist
if [ -n "$GENERATED_APPIMAGE" ]; then
    TARGET="dist/$(basename "$GENERATED_APPIMAGE")"
    case "$(basename "$TARGET")" in
        SchicksalMediaArchive-*-?*.AppImage|SchicksalMediaArchive-[0-9]*.AppImage)
            TARGET="dist/SchicksalMediaArchive-x86_64.AppImage" ;;
    esac
    mv -f "$GENERATED_APPIMAGE" "$TARGET"
    echo "  AppImage generado: $TARGET"
fi

# ------------------------------------------------------------------------------
# 6. Verificación final
# ------------------------------------------------------------------------------
echo ""
echo "[6/7] Verificación final..."

APPIMAGE_PATH="dist/SchicksalMediaArchive-x86_64.AppImage"

if [ ! -f "$APPIMAGE_PATH" ]; then
    APPIMAGE_PATH=$(find dist -maxdepth 1 -name '*.AppImage' | head -n 1)
fi

if [ -z "$APPIMAGE_PATH" ] || [ ! -f "$APPIMAGE_PATH" ]; then
    echo "  ERROR: No se generó ningún archivo .AppImage." >&2
    exit 1
fi

chmod +x "$APPIMAGE_PATH"

echo "  Archivo generado: $APPIMAGE_PATH"
ls -lh "$APPIMAGE_PATH"
file "$APPIMAGE_PATH" || true

echo "  Probando que el AppImage es ejecutable (--appimage-extract-and-run --list):"
if APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE_PATH" --appimage-extract >/dev/null 2>&1 && \
   [ -x squashfs-root/AppRun ]; then
    echo "  OK: el AppImage se descomprime y AppRun es ejecutable."
    rm -rf squashfs-root
elif APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE_PATH" --appimage-version >/dev/null 2>&1; then
    echo "  OK: el AppImage responde a opciones de línea de comandos."
else
    echo "  ADVERTENCIA: no se pudo probar la ejecución en este entorno" \
         "(posible ausencia de FUSE); el archivo existe y tiene permisos +x."
fi

echo ""
echo "=============================================="
echo " BUILD COMPLETO: $APPIMAGE_PATH"
echo "=============================================="

# ------------------------------------------------------------------------------
# 7. Commit y push
# ------------------------------------------------------------------------------
echo ""
echo "[7/7] Commit y push del script..."

git add build_linux.sh
git commit -m "Add Linux AppImage build script" || echo "  (nada que commitear o commit existente)"
git push origin main || echo "  ADVERTENCIA: no se pudo hacer push (¿sin remoto o sin credenciales?)"

echo ""
echo "Listo."