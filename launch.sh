#!/usr/bin/env bash

export PATH="/home/kei/venv/bin:$PATH"
cd "/home/kei/ytdlp-webgui" || exit 1
(sleep 2; xdg-open "http://127.0.0.1:8000" >/dev/null 2>&1) &
exec "/home/kei/venv/bin/python" app.py
