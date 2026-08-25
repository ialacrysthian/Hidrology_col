"""
Proveedor de DEM SRTM para el área de interés.
Estrategia default: descarga tiles CGIAR-CSI via GDAL /vsicurl/ con caché local.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Optional

from osgeo import gdal

from ..utils.logger import get_logger

gdal.UseExceptions()
log = get_logger(__name__)

# CGIAR-CSI SRTM v4.1 — tiles de 5x5 grados
_CGIAR_URL = (
    "https://srtm.csi.cgiar.org/wp-content/uploads/files/srtm_5x5/tiff/"
    "srtm_{col:02d}_{row:02d}.zip"
)
_OPENTOPO_URL = (
    "https://portal.opentopography.org/API/globaldem"
    "?demtype=SRTMGL3&south={south}&north={north}&west={west}&east={east}"
    "&outputFormat=GTiff&API_Key={api_key}"
)


class DemProvider:
    def __init__(self, cache_dir: str = "", opentopo_api_key: str = "") -> None:
        self._cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "bhc_dem_cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._opentopo_key = opentopo_api_key

    def get_dem_for_aoi(
        self,
        bbox: tuple[float, float, float, float],  # (min_lon, min_lat, max_lon, max_lat)
        resolution_m: float = 90.0,
        user_dem_path: str = "",
        target_epsg: int = 4326,
    ) -> str:
        """Devuelve ruta a GeoTIFF DEM recortado al AOI. Usa caché cuando es posible."""
        cache_key = self._compute_cache_key(bbox, resolution_m)
        cached_path = os.path.join(self._cache_dir, f"dem_{cache_key}.tif")

        if os.path.exists(cached_path):
            log.info(f"DEM desde caché: {cached_path}")
            return cached_path

        if user_dem_path and os.path.exists(user_dem_path):
            log.info("DEM desde archivo de usuario.")
            return self._clip_to_bbox(user_dem_path, bbox, cached_path, target_epsg, resolution_m)

        log.info("Descargando DEM SRTM...")
        return self._download_and_clip(bbox, cached_path, target_epsg, resolution_m)

    # ------------------------------------------------------------------
    # Download strategies
    # ------------------------------------------------------------------

    def _download_and_clip(
        self,
        bbox: tuple,
        output_path: str,
        target_epsg: int,
        resolution_m: float,
    ) -> str:
        min_lon, min_lat, max_lon, max_lat = bbox

        # Intentar OpenTopography primero (más confiable para Colombia)
        if self._opentopo_key:
            try:
                return self._download_opentopo(bbox, output_path, target_epsg, resolution_m)
            except Exception as exc:
                log.warning(f"OpenTopography falló ({exc}), intentando tiles CGIAR...")

        # Fallback: tiles CGIAR via /vsicurl/
        tile_paths = self._get_cgiar_tile_vsipaths(min_lon, min_lat, max_lon, max_lat)
        return self._merge_and_clip(tile_paths, bbox, output_path, target_epsg, resolution_m)

    def _download_opentopo(
        self,
        bbox: tuple,
        output_path: str,
        target_epsg: int,
        resolution_m: float,
    ) -> str:
        import urllib.request
        min_lon, min_lat, max_lon, max_lat = bbox
        url = _OPENTOPO_URL.format(
            south=min_lat, north=max_lat, west=min_lon, east=max_lon,
            api_key=self._opentopo_key,
        )
        tmp = output_path + "_raw.tif"
        log.info(f"Descargando DEM de OpenTopography...")
        urllib.request.urlretrieve(url, tmp)
        result = self._clip_to_bbox(tmp, bbox, output_path, target_epsg, resolution_m)
        os.remove(tmp)
        return result

    def _get_cgiar_tile_vsipaths(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> list[str]:
        """Genera rutas /vsicurl/ para los tiles CGIAR que cubren el bbox."""
        tile_cols = self._lon_to_cgiar_cols(min_lon, max_lon)
        tile_rows = self._lat_to_cgiar_rows(min_lat, max_lat)
        paths = []
        for col in tile_cols:
            for row in tile_rows:
                zip_url = _CGIAR_URL.format(col=col, row=row)
                vsi_path = f"/vsicurl/{zip_url}/srtm_{col:02d}_{row:02d}.tif"
                paths.append(vsi_path)
                log.info(f"Tile CGIAR: srtm_{col:02d}_{row:02d}")
        return paths

    def _merge_and_clip(
        self,
        tile_paths: list[str],
        bbox: tuple,
        output_path: str,
        target_epsg: int,
        resolution_m: float,
    ) -> str:
        vrt_path = output_path + ".vrt"
        gdal.BuildVRT(vrt_path, tile_paths)
        return self._clip_to_bbox(vrt_path, bbox, output_path, target_epsg, resolution_m)

    def _clip_to_bbox(
        self,
        src_path: str,
        bbox: tuple,
        output_path: str,
        target_epsg: int,
        resolution_m: float,
    ) -> str:
        min_lon, min_lat, max_lon, max_lat = bbox
        # Resolución en grados ≈ resolution_m / 111320
        res_deg = resolution_m / 111320.0

        warp_opts = gdal.WarpOptions(
            outputBounds=(min_lon, min_lat, max_lon, max_lat),
            dstSRS=f"EPSG:{target_epsg}",
            xRes=res_deg,
            yRes=res_deg,
            resampleAlg=gdal.GRA_Bilinear,
            creationOptions=["COMPRESS=LZW", "TILED=YES"],
        )
        ds = gdal.Warp(output_path, src_path, options=warp_opts)
        if ds is None:
            raise RuntimeError(f"gdal.Warp falló para {src_path}")
        ds = None
        log.info(f"DEM guardado: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_cache_key(bbox: tuple, resolution_m: float) -> str:
        key = f"{bbox}_{resolution_m}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    @staticmethod
    def _lon_to_cgiar_cols(min_lon: float, max_lon: float) -> list[int]:
        # CGIAR col = floor((lon + 180) / 5) + 1
        col_min = int((min_lon + 180) / 5) + 1
        col_max = int((max_lon + 180) / 5) + 1
        return list(range(col_min, col_max + 1))

    @staticmethod
    def _lat_to_cgiar_rows(min_lat: float, max_lat: float) -> list[int]:
        # CGIAR row = floor((60 - lat) / 5) + 1
        row_min = int((60 - max_lat) / 5) + 1
        row_max = int((60 - min_lat) / 5) + 1
        return list(range(row_min, row_max + 1))
