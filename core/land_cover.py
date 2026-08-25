from __future__ import annotations
import os
from typing import Callable

import geopandas as gpd
from osgeo import gdal, ogr

from ..utils.logger import get_logger

log = get_logger(__name__)

gdal.UseExceptions()


class LandCoverProcessor:
    def __init__(
        self,
        land_cover_shp: str,
        cover_field: str,
        manning_map: dict[str, float],
        watershed_shp: str,
        dem_path: str,
        output_dir: str,
        cn_map: dict[str, float] | None = None,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.land_cover_shp = land_cover_shp
        self.cover_field = cover_field
        self.manning_map = {str(k): float(v) for k, v in manning_map.items()}
        # CN (curva número, SCS) opcional — si se provee, process() también
        # genera un raster de CN además del de Manning, desde la MISMA
        # cobertura recortada, sin recortar/reproyectar dos veces.
        self.cn_map = {str(k): float(v) for k, v in cn_map.items()} if cn_map else None
        self.watershed_shp = watershed_shp
        self.dem_path = dem_path
        self.output_dir = output_dir
        self._cb = progress_cb or log.info
        os.makedirs(output_dir, exist_ok=True)

    @staticmethod
    def available_fields(shp_path: str) -> list[str]:
        # rows=1: solo se necesitan las columnas — leer el SHP completo
        # (la cobertura nacional pesa cientos de MB) congelaba la UI.
        gdf = gpd.read_file(shp_path, rows=1)
        return [col for col in gdf.columns if col != "geometry"]

    @staticmethod
    def _polygons_only(gdf: "gpd.GeoDataFrame") -> "gpd.GeoDataFrame":
        """Conserva solo geometrías poligonales tras un clip.

        Recortar contra el borde de la cuenca genera astillas LINESTRING /
        POINT (y GeometryCollection) donde un polígono toca exactamente el
        límite. El driver Shapefile de tipo POLYGON no puede escribirlas
        ('Attempt to write non-polygon (LINESTRING) geometry...').
        """
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        try:
            gdf = gdf.explode(index_parts=False, ignore_index=True)
        except TypeError:   # geopandas antiguo sin index_parts/ignore_index
            gdf = gdf.explode().reset_index(drop=True)
        return gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]

    @staticmethod
    def clip_to_watershed(
        land_cover_shp: str,
        watershed_shp: str,
        watershed_crs_wkt: str | None = None,
    ) -> "gpd.GeoDataFrame":
        """Recorta la cobertura a la cuenca. Retorna el GeoDataFrame recortado
        en el CRS original de la cobertura.

        watershed_crs_wkt: CRS a asignar si la cuenca no trae .prj (debe ser
        el CRS del DEM). Asignarle el CRS de la cobertura sería incorrecto:
        la cuenca está en metros del DEM y la cobertura suele venir en 4326,
        el clip caería en otro lugar y saldría vacío.
        """
        gdf = gpd.read_file(land_cover_shp)
        watershed = gpd.read_file(watershed_shp)
        if watershed.crs is None and watershed_crs_wkt:
            watershed = watershed.set_crs(watershed_crs_wkt)
        if gdf.crs is not None and watershed.crs is not None and watershed.crs != gdf.crs:
            watershed = watershed.to_crs(gdf.crs)
        try:
            try:
                clipped = gpd.clip(gdf, watershed, keep_geom_type=True)
            except TypeError:   # geopandas antiguo sin keep_geom_type
                clipped = gpd.clip(gdf, watershed)
        except Exception:
            clipped = gpd.overlay(gdf, watershed, how="intersection")
        return LandCoverProcessor._polygons_only(clipped)

    @staticmethod
    def categories(gdf: "gpd.GeoDataFrame", field: str) -> list[str]:
        """Categorías únicas y válidas de un campo (sin nulos ni 'nan')."""
        if field not in gdf.columns:
            raise ValueError(f"El campo '{field}' no existe en la cobertura.")
        # dropna ANTES de astype(str): después, los nulos ya son el texto 'nan'
        vals = gdf[field].dropna().astype(str).str.strip()
        vals = vals[(vals != "") & (~vals.str.lower().isin({"nan", "none", "null"}))]
        return sorted(vals.unique().tolist())

    def process(self) -> dict[str, str]:
        self._cb(f"Cargando cobertura: {self.land_cover_shp}")
        gdf = gpd.read_file(self.land_cover_shp)
        if self.cover_field not in gdf.columns:
            raise ValueError(f"El campo '{self.cover_field}' no existe en el shapefile")
        if gdf.crs is None:
            raise ValueError(
                "El shapefile de cobertura no contiene un CRS válido. "
                "Asigne un CRS antes de procesar."
            )

        project_crs = self._project_crs()
        if gdf.crs != project_crs:
            self._cb(f"Reproyectando cobertura a {project_crs}...")
            gdf = gdf.to_crs(project_crs)

        self._cb("Cargando cuenca para recorte...")
        watershed = gpd.read_file(self.watershed_shp)
        if watershed.crs is None:
            watershed = watershed.set_crs(project_crs)
        if watershed.crs != project_crs:
            watershed = watershed.to_crs(project_crs)

        if watershed.empty:
            raise RuntimeError("El shapefile de cuenca está vacío")

        self._cb("Recortando cobertura al área de la cuenca...")
        try:
            try:
                clipped = gpd.clip(gdf, watershed, keep_geom_type=True)
            except TypeError:   # geopandas antiguo sin keep_geom_type
                clipped = gpd.clip(gdf, watershed)
        except Exception:
            clipped = gpd.overlay(gdf, watershed, how="intersection")

        # Solo polígonos: el clip deja astillas LINESTRING/POINT en el borde
        # de la cuenca que el shapefile POLYGON no puede almacenar.
        n_antes = len(clipped)
        clipped = self._polygons_only(clipped)
        if len(clipped) < n_antes:
            self._cb(
                f"  {n_antes - len(clipped)} astillas no poligonales del borde — excluidas"
            )

        if clipped.empty:
            raise RuntimeError(
                "La cobertura no intersecta con la cuenca. Verifique el shapefile y el CRS."
            )

        clipped[self.cover_field] = clipped[self.cover_field].astype(str).str.strip()
        sin_categoria = clipped[self.cover_field].str.lower().isin({"", "nan", "none", "null"})
        if sin_categoria.any():
            self._cb(
                f"  {int(sin_categoria.sum())} polígonos sin categoría — excluidos"
            )
            clipped = clipped[~sin_categoria]
            if clipped.empty:
                raise RuntimeError(
                    "Todos los polígonos de la cobertura carecen de categoría "
                    f"en el campo '{self.cover_field}'."
                )

        clipped["MANNING"] = clipped[self.cover_field].map(self.manning_map)
        if clipped["MANNING"].isna().any():
            missing = clipped.loc[clipped["MANNING"].isna(), self.cover_field].unique()
            raise RuntimeError(
                "No se encontró valor de Manning para las categorías: "
                f"{', '.join(map(str, missing))}"
            )

        if self.cn_map is not None:
            clipped["CN"] = clipped[self.cover_field].map(self.cn_map)
            if clipped["CN"].isna().any():
                missing = clipped.loc[clipped["CN"].isna(), self.cover_field].unique()
                raise RuntimeError(
                    "No se encontró valor de CN para las categorías: "
                    f"{', '.join(map(str, missing))}"
                )

        clipped_shp = os.path.join(self.output_dir, "landcover_clipped.shp")
        clipped.to_file(clipped_shp, encoding="utf-8")
        self._cb(f"Cobertura recortada guardada en: {clipped_shp}")

        raster_path = os.path.join(self.output_dir, "landcover_manning.tif")
        self._rasterize(clipped_shp, raster_path, "MANNING")
        self._cb(f"Raster de Manning generado: {raster_path}")

        result = {"clipped_shp": clipped_shp, "raster": raster_path}

        if self.cn_map is not None:
            cn_raster_path = os.path.join(self.output_dir, "landcover_cn.tif")
            self._rasterize(clipped_shp, cn_raster_path, "CN")
            self._cb(f"Raster de CN generado: {cn_raster_path}")
            result["raster_cn"] = cn_raster_path

        return result

    def _project_crs(self):
        from qgis.core import QgsProject

        project_crs = QgsProject.instance().crs()
        if not project_crs.isValid():
            raise RuntimeError("El CRS del proyecto no está definido")
        return project_crs.authid()

    def _rasterize(
        self,
        input_shp: str,
        output_raster: str,
        attribute: str,
    ) -> str:
        self._cb(f"Rasterizando cobertura con valores de {attribute}...")
        ds = gdal.Open(self.dem_path, gdal.GA_ReadOnly)
        if ds is None:
            raise RuntimeError(f"No se puede abrir el DEM: {self.dem_path}")

        geotransform = ds.GetGeoTransform()
        projection = ds.GetProjection()
        cols = ds.RasterXSize
        rows = ds.RasterYSize
        ds = None

        driver = gdal.GetDriverByName("GTiff")
        out = driver.Create(
            output_raster,
            cols,
            rows,
            1,
            gdal.GDT_Float32,
            options=["COMPRESS=LZW", "TILED=YES"],
        )
        out.SetGeoTransform(geotransform)
        out.SetProjection(projection)
        band = out.GetRasterBand(1)
        nodata = -9999.0
        band.SetNoDataValue(nodata)
        band.Fill(nodata)

        vector_ds = ogr.Open(input_shp)
        if vector_ds is None:
            raise RuntimeError(f"No se puede abrir el shapefile: {input_shp}")
        layer = vector_ds.GetLayer()
        gdal.RasterizeLayer(
            out,
            [1],
            layer,
            options=[f"ATTRIBUTE={attribute}", "ALL_TOUCHED=TRUE"],
        )
        band.FlushCache()
        # Estadísticas embebidas (excluyen NoData). Sin ellas QGIS no puede
        # estimar min/max y estira la simbología a ±3.4e38 (rango Float32),
        # mostrando el raster como si tuviera valores absurdos.
        try:
            band.ComputeStatistics(False)
        except Exception:
            pass
        out = None
        vector_ds = None
        return output_raster
