"""
Gestor de proyecciones para el proyecto TC Calculator.

Usa la proyección del DEM para todo el proyecto.
"""
from __future__ import annotations
from typing import Callable

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsVectorLayer,
)
from osgeo import gdal, osr

from ..utils.logger import get_logger

log = get_logger(__name__)


class CRSManager:
    """
    Gestor de proyecciones para TC Calculator.

    Lee la proyección del DEM y la usa para todo el proyecto.
    """

    def __init__(
        self,
        dem_path: str,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.dem_path = dem_path
        self._cb = progress_cb or log.info

        # Leer proyección del DEM
        try:
            ds = gdal.Open(dem_path)
            wkt = ds.GetProjection()
            ds = None

            if not wkt:
                raise ValueError("DEM sin proyección definida")

            self.dem_crs = QgsCoordinateReferenceSystem(wkt)
            if not self.dem_crs.isValid():
                raise ValueError(f"Proyección inválida: {wkt}")

            auth = self.dem_crs.authid()
            self._cb(f"Proyección del DEM: {auth} ({self.dem_crs.description()})")

        except Exception as exc:
            log.error(f"Error leyendo proyección del DEM: {exc}")
            raise RuntimeError(f"No se pudo leer la proyección del DEM: {exc}")

    def set_project_crs(self) -> None:
        """Establece la proyección del proyecto QGIS a la del DEM."""
        project = QgsProject.instance()
        project.setCrs(self.dem_crs)
        self._cb(f"Proyección del proyecto: {self.dem_crs.authid()}")

    def get_project_crs_wkt(self) -> str:
        """Retorna la proyección del proyecto como WKT."""
        return self.dem_crs.toWkt()



def ensure_project_crs(dem_path: str) -> CRSManager:
    """
    Utilidad para asegurar que el proyecto tiene CRS configurado.

    Retorna el CRSManager configurado.
    """
    manager = CRSManager(dem_path)
    manager.set_project_crs()
    return manager
