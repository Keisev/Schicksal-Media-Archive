"""Utilidades de rutas para ejecución normal y empaquetada (PyInstaller).

Este módulo resuelve la ubicación de los binarios externos (``yt-dlp``,
``ffmpeg``, ``ffprobe``...) tanto si el proyecto se ejecuta desde el código
fuente como si ha sido empaquetado con PyInstaller.

Cuando PyInstaller construye un ejecutable "onefile", descomprime todos los
recursos en un directorio temporal cuya ruta está disponible en el atributo
especial ``sys._MEIPASS``. Si ese atributo no existe, asumimos que estamos
en modo desarrollo y devolvemos una ruta relativa al script.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Nombre del directorio dentro del bundle donde se incluyen los binarios.
BIN_DIR_NAME = "bin"

# Directorio del script en modo desarrollo (este archivo utils.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def is_frozen() -> bool:
    """Devuelve True si la app se está ejecutando como binario PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_base_path() -> str:
    """Ruta base: ``sys._MEIPASS`` si está empaquetada, directorio del script si no."""
    if hasattr(sys, "_MEIPASS"):
        # App empaquetada con PyInstaller: recursos dentro del bundle temporal.
        return str(sys._MEIPASS)
    # Modo desarrollo: ruta relativa al script (este archivo utils.py).
    return os.path.dirname(os.path.abspath(__file__))


def _binary_filename(name: str) -> str:
    """Devuelve los nombres de archivo candidatos para el binario ``name``.

    En Windows se incluyen tanto la variante ``.exe`` como la variante sin
    extensión (p. ej. el *zipimport* oficial de yt-dlp se llama ``yt-dlp.exe``
    pero algunos builds usan ``yt-dlp``). En otros sistemas se devuelve solo el
    nombre tal cual. Se ordenan por prioridad de búsqueda.
    """
    if os.name == "nt":
        base = name[:-4] if name.lower().endswith(".exe") else name
        return [f"{base}.exe", base]
    return [name]


def resource_path(relative_path: str) -> str:
    """Ruta absoluta a un recurso estatico (ej. ``static/index.html``).

    Con PyInstaller (``sys._MEIPASS``) los recursos viven dentro del bundle
    temporal; en desarrollo relativo al directorio del proyecto.
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(str(sys._MEIPASS), relative_path)
    return os.path.join(_SCRIPT_DIR, relative_path)


def get_binary_path(name: str) -> str:
    """Devuelve la ruta correcta al binario ``name``.

    Orden de búsqueda:

    1. Si la app está empaquetada (``sys._MEIPASS``), se busca el binario
       dentro del bundle (directorio ``bin/`` y raíz del bundle).
    2. Si no está empaquetada, se busca junto al script (directorio ``bin/``
       local y raíz del proyecto).
    3. Como último recurso, se devuelve simplemente ``name`` para que el
       sistema lo resuelva a través del PATH.
    """
    filenames = _binary_filename(name)
    base_path = Path(get_base_path())

    candidates = []
    for fname in filenames:
        candidates.append(base_path / BIN_DIR_NAME / fname)  # bin/yt-dlp[.exe]
        candidates.append(base_path / fname)                 # raíz del bundle

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    # No está incluido en el bundle (o no existe): devolver solo el nombre
    # para que subprocess lo resuelva vía PATH. En modo empaquetado se avisa
    # por stderr porque normalmente indicaría un --add-binary ausente.
    if is_frozen():
        print(
            f"[utils] AVISO: binario '{filenames[0]}' no encontrado en el bundle "
            f"({base_path}); intentando resolverlo por PATH.",
            file=sys.stderr,
        )
    return name


def find_free_port(host: str = "127.0.0.1") -> int:
    """Usa ``socket`` para obtener un puerto TCP libre dinámicamente.

    Se enlaza al puerto 0 (el SO asigna uno efímero disponible), se lee el
    número de puerto y se cierra el socket inmediatamente.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
