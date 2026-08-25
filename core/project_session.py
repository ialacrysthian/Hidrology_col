"""
Persistencia de la sesión de trabajo de TC Calculator.

Guarda y restaura dos cosas en la carpeta de resultados para poder reanudar el
trabajo sin empezar de cero:

  - El proyecto QGIS (``tc_calculator.qgz``): todas las capas y el CRS.
  - Un JSON (``tc_session.json``): el estado del asistente (rutas de los
    productos generados, outlet, cuenca, parámetros) que el proyecto QGIS por
    sí solo no conoce.
"""
from __future__ import annotations

import json
import os
from typing import Any

from qgis.core import QgsProject

from ..utils.logger import get_logger

log = get_logger(__name__)

STATE_FILENAME = "tc_session.json"
PROJECT_FILENAME = "tc_calculator.qgz"
STATE_VERSION = 1


def state_path(output_dir: str) -> str:
    return os.path.join(output_dir, STATE_FILENAME)


def project_path(output_dir: str) -> str:
    return os.path.join(output_dir, PROJECT_FILENAME)


def has_session(output_dir: str) -> bool:
    """True si la carpeta contiene una sesión previa que se puede reanudar."""
    return bool(output_dir) and os.path.exists(state_path(output_dir))


def save_state(output_dir: str, state: dict[str, Any]) -> str:
    """Escribe el estado del asistente como JSON. Retorna la ruta."""
    os.makedirs(output_dir, exist_ok=True)
    payload = dict(state)
    payload["version"] = STATE_VERSION
    path = state_path(output_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load_state(output_dir: str) -> dict[str, Any] | None:
    """Lee el estado del asistente. Retorna None si no existe o está corrupto."""
    path = state_path(output_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(f"No se pudo leer la sesión {path}: {exc}")
        return None


def save_qgis_project(output_dir: str) -> str:
    """Guarda el proyecto QGIS actual (.qgz) en la carpeta de resultados."""
    os.makedirs(output_dir, exist_ok=True)
    path = project_path(output_dir)
    QgsProject.instance().write(path)
    return path


def load_qgis_project(output_dir: str) -> bool:
    """Carga el proyecto QGIS guardado (reemplaza el proyecto actual)."""
    path = project_path(output_dir)
    if not os.path.exists(path):
        return False
    return QgsProject.instance().read(path)
