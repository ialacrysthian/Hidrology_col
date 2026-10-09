"""
Condición hidrológica del suelo (A/B/C/D, método SCS) y Número de Curva (CN)
final por intersección suelo × cobertura.

Flujo (ver docs/manual_tecnico_ES.ipynb, paso 4 del asistente):

    1. Cargar capa de suelos (vectorial, aportada por el usuario).
    2. clip_to_watershed()            -> suelos recortados a la cuenca.
    3. available_fields() / unique_values() -> exponer atributos y valores
       únicos para que el usuario los clasifique.
    4. assign_hydrologic_condition()  -> columna 'Condicion_Hidrologica'
       (A/B/C/D) en la capa de suelos recortada. NO se asigna un CN aquí:
       la condición hidrológica es solo el vínculo suelo -> cobertura.
    5. La capa de cobertura (ya recortada por LandCoverProcessor) debe traer,
       o el usuario debe indicar, los campos CN_A/CN_B/CN_C/CN_D -- el CN de
       cada categoría de cobertura para cada una de las 4 condiciones.
    6-7. intersect_with_coverage()    -> overlay suelo × cobertura; cada
       polígono resultante conserva los atributos de ambas capas y recibe
       'CN_final' = el CN_<condición> que corresponde a su propia condición
       hidrológica.
    8. rasterize CN_final (vía land_cover.rasterize_attribute) -> mapa de CN.

Sin dependencia de QGIS: solo geopandas/GDAL, igual que land_cover.py, para
poder probarse con pytest sin la API de PyQGIS.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from typing import Callable

import geopandas as gpd

from ..utils.logger import get_logger
from .land_cover import LandCoverProcessor, rasterize_attribute

log = get_logger(__name__)

VALID_CONDITIONS = ("A", "B", "C", "D")


@dataclass
class SoilCnResult:
    soil_shp: str            # suelos recortados + Condicion_Hidrologica
    cn_final_shp: str        # intersección suelo x cobertura (nombres truncados, Shapefile)
    cn_final_gpkg: str       # misma intersección, GeoPackage (nombres completos)
    cn_final_raster: str
    n_polygons: int
    n_unclassified_soil: int
    unclassified_values: list[str]
    n_sin_cn: int
    area_suelo_km2: float
    area_cobertura_km2: float
    area_interseccion_km2: float


class SoilHydrologicProcessor:
    """Asigna condición hidrológica del suelo y calcula el CN final.

    Los métodos estáticos ``available_fields``/``clip_to_watershed``/
    ``unique_values`` delegan en ``LandCoverProcessor``: el recorte de una
    capa vectorial a la cuenca y la lectura de categorías únicas es
    exactamente el mismo problema para suelos que para cobertura, por lo que
    no se duplica esa lógica (astillas no-poligonales del borde, CRS de la
    cuenca sin .prj, etc. -- ver land_cover.py).
    """

    CONDITION_FIELD = "Condicion_Hidrologica"
    CN_FIELD = "CN_final"

    @staticmethod
    def _ascii_safe_columns(gdf: "gpd.GeoDataFrame") -> "gpd.GeoDataFrame":
        """Devuelve una copia con nombres de columna solo-ASCII, únicos.

        Capas de suelos oficiales (IGAC/CTM12) suelen traer nombres de
        campo con tildes ('LITOLOGÍA', 'CARACTERÍSTICAS'). El driver
        Shapefile los trunca a 10 caracteres de todos modos, pero además
        el manejador de errores de GDAL/fiona asume ASCII/UTF-8 estricto al
        formatear sus propios mensajes de log durante la escritura, y un
        byte no-ASCII colado en un nombre de campo hace que la escritura
        entera falle con un UnicodeDecodeError ajeno al dato en sí. Se
        renombra a ASCII (con sufijo numérico si hay colisión) SOLO para el
        Shapefile de salida -- el GPKG conserva los nombres originales."""
        def _ascii(name: str) -> str:
            norm = unicodedata.normalize("NFKD", str(name))
            ascii_name = norm.encode("ascii", "ignore").decode("ascii")
            ascii_name = "".join(c if c.isalnum() or c == "_" else "_" for c in ascii_name)
            return ascii_name or "FIELD"

        gdf = gdf.copy()
        seen: dict[str, int] = {}
        rename: dict[str, str] = {}
        for col in gdf.columns:
            if col == "geometry":
                continue
            new_name = _ascii(col)
            if new_name in seen:
                seen[new_name] += 1
                new_name = f"{new_name}_{seen[new_name]}"
            else:
                seen[new_name] = 0
            rename[col] = new_name
        return gdf.rename(columns=rename)

    def __init__(
        self,
        soil_shp: str,
        watershed_shp: str,
        dem_path: str,
        output_dir: str,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.soil_shp = soil_shp
        self.watershed_shp = watershed_shp
        self.dem_path = dem_path
        self.output_dir = output_dir
        self._cb = progress_cb or log.info
        os.makedirs(output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Pasos 1-3: cargar, recortar, listar atributos/valores
    # ------------------------------------------------------------------

    @staticmethod
    def available_fields(shp_path: str) -> list[str]:
        return LandCoverProcessor.available_fields(shp_path)

    @staticmethod
    def clip_to_watershed(
        soil_shp: str,
        watershed_shp: str,
        watershed_crs_wkt: str | None = None,
    ) -> "gpd.GeoDataFrame":
        """Recorta la capa de suelos a la cuenca. Ver
        ``LandCoverProcessor.clip_to_watershed`` (misma lógica: CRS de la
        cuenca reproyectado al de los suelos, solo geometrías poligonales
        tras el recorte)."""
        return LandCoverProcessor.clip_to_watershed(soil_shp, watershed_shp, watershed_crs_wkt)

    @staticmethod
    def unique_values(gdf: "gpd.GeoDataFrame", field: str) -> list[str]:
        """Valores únicos y válidos (sin nulos) de un campo de suelos."""
        return LandCoverProcessor.categories(gdf, field)

    # ------------------------------------------------------------------
    # Paso 4: asignar condición hidrológica A/B/C/D
    # ------------------------------------------------------------------

    @staticmethod
    def assign_hydrologic_condition(
        gdf: "gpd.GeoDataFrame",
        field: str,
        mapping: dict[str, str],
    ) -> tuple["gpd.GeoDataFrame", list[str], int]:
        """Agrega la columna ``Condicion_Hidrologica`` según ``mapping``
        (valor de suelo -> 'A'/'B'/'C'/'D').

        No asigna directamente un CN: la condición hidrológica es solo el
        vínculo entre suelo y cobertura (paso 6-7).

        Retorna (gdf_con_columna, valores_sin_clasificar, n_polígonos_sin_clasificar).
        Lanza ValueError si algún valor del mapeo no es A/B/C/D.
        """
        if field not in gdf.columns:
            raise ValueError(f"El campo '{field}' no existe en la capa de suelos.")

        norm_mapping: dict[str, str] = {}
        invalid: dict[str, str] = {}
        for k, v in mapping.items():
            v_str = str(v).strip().upper()
            if not v_str:
                continue   # sin clasificar todavía -- válido, se reporta como faltante
            if v_str not in VALID_CONDITIONS:
                invalid[str(k)] = v_str
            else:
                norm_mapping[str(k)] = v_str
        if invalid:
            raise ValueError(
                "Condición hidrológica inválida (solo se aceptan A, B, C o D): "
                + ", ".join(f"'{k}' -> '{v}'" for k, v in invalid.items())
            )

        gdf = gdf.copy()
        vals = gdf[field].astype(str).str.strip()
        gdf[SoilHydrologicProcessor.CONDITION_FIELD] = vals.map(norm_mapping)

        sin_clasificar_mask = gdf[SoilHydrologicProcessor.CONDITION_FIELD].isna()
        unclassified_values = sorted(vals[sin_clasificar_mask].unique().tolist())
        n_unclassified = int(sin_clasificar_mask.sum())
        return gdf, unclassified_values, n_unclassified

    # ------------------------------------------------------------------
    # Pasos 6-7: intersección suelo (condición) x cobertura (CN_A..D)
    # ------------------------------------------------------------------

    @staticmethod
    def intersect_with_coverage(
        soil_gdf: "gpd.GeoDataFrame",
        coverage_gdf: "gpd.GeoDataFrame",
        cn_field_map: dict[str, str],
        progress_cb: Callable[[str], None] | None = None,
    ) -> tuple["gpd.GeoDataFrame", dict]:
        """Intersecta suelos (con ``Condicion_Hidrologica`` ya asignada, sin
        nulos -- filtrar antes de llamar) con la cobertura (con los campos
        de CN indicados en ``cn_field_map``, p.ej. {'A': 'CN_A', 'B': 'CN_B',
        ...}). Para cada polígono resultante, selecciona el CN del campo que
        corresponde a SU PROPIA condición hidrológica:

            Condicion_Hidrologica == 'A' -> usa cn_field_map['A']
            Condicion_Hidrologica == 'B' -> usa cn_field_map['B']
            ... etc.

        Conserva todos los atributos originales de ambas capas (overlay
        estándar de geopandas). Retorna (gdf_interseccion, info) donde
        ``info`` resume polígonos sin CN y áreas sin contraparte.
        """
        cb = progress_cb or log.info

        if SoilHydrologicProcessor.CONDITION_FIELD not in soil_gdf.columns:
            raise ValueError(
                f"La capa de suelos no tiene la columna "
                f"'{SoilHydrologicProcessor.CONDITION_FIELD}' -- "
                "asigne la condición hidrológica antes de intersectar."
            )

        present_conditions = set(
            soil_gdf[SoilHydrologicProcessor.CONDITION_FIELD].dropna().unique().tolist()
        )
        missing_for_present = sorted(c for c in present_conditions if c not in cn_field_map)
        if missing_for_present:
            raise ValueError(
                "Faltan campos de CN para condiciones hidrológicas presentes en "
                f"el suelo: {missing_for_present}. Seleccione el campo CN_"
                f"{'/CN_'.join(missing_for_present)} de la cobertura."
            )
        for cond, field in cn_field_map.items():
            if field not in coverage_gdf.columns:
                raise ValueError(
                    f"El campo '{field}' (CN de la condición {cond}) no existe "
                    "en la capa de cobertura."
                )

        if soil_gdf.crs is not None and coverage_gdf.crs is not None and soil_gdf.crs != coverage_gdf.crs:
            coverage_gdf = coverage_gdf.to_crs(soil_gdf.crs)

        cb("Intersectando suelos (condición hidrológica) con cobertura (CN por condición)...")
        try:
            try:
                inter = gpd.overlay(soil_gdf, coverage_gdf, how="intersection", keep_geom_type=True)
            except TypeError:   # geopandas antiguo sin keep_geom_type
                inter = gpd.overlay(soil_gdf, coverage_gdf, how="intersection")
        except Exception as exc:
            raise RuntimeError(f"Error al intersectar suelos y cobertura: {exc}") from exc

        inter = LandCoverProcessor._polygons_only(inter)
        if inter.empty:
            raise RuntimeError(
                "La intersección de suelos y cobertura resultó vacía -- "
                "verifique que ambas capas cubran la misma zona de la cuenca."
            )

        def _pick_cn(row):
            cond = row.get(SoilHydrologicProcessor.CONDITION_FIELD)
            field = cn_field_map.get(cond)
            if not field:
                return None
            return row.get(field)

        inter[SoilHydrologicProcessor.CN_FIELD] = inter.apply(_pick_cn, axis=1)

        n_sin_cn = int(inter[SoilHydrologicProcessor.CN_FIELD].isna().sum())
        if n_sin_cn:
            cb(
                f"  Advertencia: {n_sin_cn} de {len(inter)} polígonos de la "
                "intersección quedaron sin valor de CN (celda de CN vacía en "
                "la cobertura para esa condición)."
            )

        area_suelo_km2 = float(soil_gdf.geometry.area.sum()) / 1e6
        area_cobertura_km2 = float(coverage_gdf.geometry.area.sum()) / 1e6
        area_inter_km2 = float(inter.geometry.area.sum()) / 1e6
        area_sin_pareja_km2 = max(area_suelo_km2, area_cobertura_km2) - area_inter_km2
        if area_sin_pareja_km2 > 1e-3:
            cb(
                f"  Advertencia: ~{area_sin_pareja_km2:.2f} km² de suelo o "
                "cobertura no tuvieron contraparte en la intersección "
                "(zonas sin cobertura o sin suelo clasificado dentro de la cuenca)."
            )

        info = {
            "n_polygons": len(inter),
            "n_sin_cn": n_sin_cn,
            "area_suelo_km2": area_suelo_km2,
            "area_cobertura_km2": area_cobertura_km2,
            "area_interseccion_km2": area_inter_km2,
            "area_sin_pareja_km2": max(area_sin_pareja_km2, 0.0),
        }
        return inter, info

    # ------------------------------------------------------------------
    # Orquestador: pasos 2, 4, 6-8 en un solo llamado (usado por la UI)
    # ------------------------------------------------------------------

    def compute_cn_final(
        self,
        soil_field: str,
        condition_map: dict[str, str],
        coverage_gdf: "gpd.GeoDataFrame",
        cn_field_map: dict[str, str],
        watershed_crs_wkt: str | None = None,
    ) -> SoilCnResult:
        """Pipeline completo: recorta suelos a la cuenca, asigna condición
        hidrológica, intersecta con la cobertura ya recortada (``coverage_gdf``,
        con los campos CN_A/B/C/D indicados en ``cn_field_map``) y exporta
        CN_final como Shapefile + GeoPackage + raster.

        Los pasos 1 (cargar SHP) y 3 (listar valores únicos para que el
        usuario los clasifique) ocurren antes, en la UI.
        """
        self._cb(f"Recortando suelos a la cuenca: {self.soil_shp}")
        soil = self.clip_to_watershed(self.soil_shp, self.watershed_shp, watershed_crs_wkt)
        if soil.empty:
            raise RuntimeError("Los suelos no intersectan con la cuenca. Verifique el shapefile y su CRS.")
        self._cb(f"  {len(soil)} polígonos de suelo dentro de la cuenca.")

        self._cb("Asignando condición hidrológica (A/B/C/D)...")
        soil, unclassified_values, n_unclassified = self.assign_hydrologic_condition(
            soil, soil_field, condition_map
        )
        if n_unclassified:
            self._cb(
                f"  Advertencia: {n_unclassified} polígonos con valores de suelo sin "
                f"clasificar ({', '.join(map(str, unclassified_values))}) -- "
                "excluidos de la intersección."
            )
            soil = soil[soil[self.CONDITION_FIELD].notna()].copy()
        if soil.empty:
            raise RuntimeError(
                "Ningún polígono de suelo quedó clasificado con A/B/C/D -- "
                "asigne la condición hidrológica a al menos un valor."
            )

        soil_shp_out = os.path.join(self.output_dir, "suelos_condicion_hidrologica.shp")
        self._ascii_safe_columns(soil).to_file(soil_shp_out, encoding="utf-8")
        self._cb(f"Suelos con condición hidrológica guardados en: {soil_shp_out}")

        inter, info = self.intersect_with_coverage(
            soil, coverage_gdf, cn_field_map, progress_cb=self._cb
        )

        cn_shp_out = os.path.join(self.output_dir, "CN_final.shp")
        cn_gpkg_out = os.path.join(self.output_dir, "CN_final.gpkg")
        # Shapefile trunca nombres de campo a 10 caracteres -- se exporta
        # también a GeoPackage para conservar los nombres completos de
        # ambas capas de origen (mismo patrón que land_cover.py/rainfall.py).
        self._ascii_safe_columns(inter).to_file(cn_shp_out, encoding="utf-8")
        inter.to_file(cn_gpkg_out, driver="GPKG", layer="CN_final")
        self._cb(f"Capa CN_final guardada en: {cn_shp_out}")
        self._cb(f"  (y en {cn_gpkg_out}, con los nombres de campo completos)")

        raster_path = os.path.join(self.output_dir, "CN_final.tif")
        rasterize_attribute(self.dem_path, cn_shp_out, raster_path, self.CN_FIELD)
        self._cb(f"Raster de CN final generado: {raster_path}")

        return SoilCnResult(
            soil_shp=soil_shp_out,
            cn_final_shp=cn_shp_out,
            cn_final_gpkg=cn_gpkg_out,
            cn_final_raster=raster_path,
            n_polygons=info["n_polygons"],
            n_unclassified_soil=n_unclassified,
            unclassified_values=unclassified_values,
            n_sin_cn=info["n_sin_cn"],
            area_suelo_km2=info["area_suelo_km2"],
            area_cobertura_km2=info["area_cobertura_km2"],
            area_interseccion_km2=info["area_interseccion_km2"],
        )
