"""Punto de entrada de la aplicación de escritorio.

Arranca el servidor FastAPI (definido en ``app.py``) con Uvicorn en un hilo
*daemon* y abre una ventana nativa con pywebview apuntando a la URL local.

Diseñado para empaquetarse con PyInstaller, por ejemplo:

    pyinstaller --noconsole --onefile \
        --add-data "static:static" \
        --add-binary "yt-dlp:bin" --add-binary "ffmpeg:bin" \
        main.py
"""

from __future__ import annotations

import sys
import threading
import time

import uvicorn
import webview

from app import app  # Instancia de FastAPI
from utils import find_free_port  # Usa socket internamente

HOST = "127.0.0.1"


def start_server(host: str, port: int) -> None:
    """Ejecuta Uvicorn en el hilo actual (se llamará como hilo daemon)."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Espera a que el servidor acepte conexiones TCP en host:port."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    # 1. Encontrar un puerto libre dinámicamente (socket.bind al puerto 0).
    port = find_free_port(HOST)
    url = f"http://{HOST}:{port}"

    # 2. Iniciar Uvicorn en un hilo daemon (se muere con el proceso principal).
    server_thread = threading.Thread(
        target=start_server,
        args=(HOST, port),
        daemon=True,
        name="uvicorn-server",
    )
    server_thread.start()

    # 3. Esperar a que el servidor esté listo antes de abrir la ventana.
    if not wait_for_server(HOST, port):
        print("Error: el servidor no llegó a iniciarse.", file=sys.stderr)
        sys.exit(1)

    # 4. Abrir la ventana nativa de pywebview.
    window = webview.create_window(
        title="YT-DLP Web GUI",
        url=url,
        width=1280,
        height=800,
        min_size=(900, 600),
    )

    # webview.start bloquea hasta que se cierra la ventana; al ser el hilo
    # principal, al terminar el proceso los hilos daemon mueren solos.
    webview.start()


if __name__ == "__main__":
    main()
