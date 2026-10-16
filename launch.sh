#!/usr/bin/env bash

# Añadir el venv al PATH por si yt-dlp está instalado ahí
export PATH="/home/kei/venv/bin:$PATH"

cd "/home/kei/ytdlp-webgui" || exit 1

# Abrir el navegador después de unos segundos
(sleep 2; xdg-open "http://127.0.0.1:8000" >/dev/null 2>&1) &

# Ejecutar la aplicación
exec "/home/kei/venv/bin/python" app.py