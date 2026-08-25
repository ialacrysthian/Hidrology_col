"""
Morfometría de cuenca y Tc adoptado (Kirpich) para iniciar la iteración
del cálculo distribuido de Tc (DistributedTcCalculator.solve()).

Parámetros calculados a partir de la cuenca delimitada (cuenca.shp), el DEM
y la red de drenaje vectorizada (drenajes.shp), todos productos del paso 3
(delimitación con GRASS):

  Área, perímetro, coeficiente de compacidad (Gravelius Kc), factor de
  forma (Kf), densidad de drenaje (Dd), longitud y pendiente del cauce
  principal, pendiente media de la cuenca, relieve, razón de relieve, y
  el Tc de Kirpich — adoptado como semilla de la iteración distribuida.

La fórmula de Kirpich usada (L en metros, S en m/m, Tc en minutos) es la
misma constante que ya usa DistributedTcCalculator._estimate_tc_initial_kirpich,
para que ambas estimaciones sean comparables:

    Tc = 0.01947 * L^0.77 * S^-0.385
"""
from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from ..utils.logger import get_logger
from .tc_empirical import TcEmpiricalResult, compute_all as _compute_tc_empirical, export_csv as _export_tc_empirical_csv

log = get_logger(__name__)

MIN_SLOPE = 1e-4   # misma cota que tc_distributed.py, evita división por 0


@dataclass
class MorphometryResult:
    area_km2: float
    perimetro_km: float
    longitud_cauce_principal_km: float
    pendiente_cauce_principal_pct: float
    pendiente_media_cuenca_pct: float
    elevacion_max_m: float
    elevacion_min_m: float
    elevacion_media_m: float
    relieve_m: float
    razon_relieve_m_km: float
    coef_compacidad_kc: float
    factor_forma_kf: float
    densidad_drenaje_km_km2: float
    tc_kirpich_min: float
    tc_adoptado_min: float

    def to_dict(self) -> dict:
        return asdict(self)


_LABELS: dict[str, str] = {
    "area_km2": "Área (km²)",
    "perimetro_km": "Perímetro (km)",
    "longitud_cauce_principal_km": "Longitud cauce principal (km)",
    "pendiente_cauce_principal_pct": "Pendiente cauce principal (%)",
    "pendiente_media_cuenca_pct": "Pendiente media de la cuenca (%)",
    "elevacion_max_m": "Elevación máxima (m)",
    "elevacion_min_m": "Elevación mínima (m)",
    "elevacion_media_m": "Elevación media (m)",
    "relieve_m": "Relieve (m)",
    "razon_relieve_m_km": "Razón de relieve (m/km)",
    "coef_compacidad_kc": "Coeficiente de compacidad Kc (Gravelius)",
    "factor_forma_kf": "Factor de forma Kf",
    "densidad_drenaje_km_km2": "Densidad de drenaje (km/km²)",
    "tc_kirpich_min": "Tc Kirpich (min)",
    "tc_adoptado_min": "Tc adoptado para iteración (min)",
}


class WatershedMorphometry:
    """
    Calcula parámetros morfométricos de la cuenca delimitada y el Tc de
    Kirpich adoptado como semilla para DistributedTcCalculator.

    Requiere que la cuenca ya esté delimitada (paso 3): usa cuenca.shp,
    drenajes.shp y el mismo dem.tif del proyecto.
    """

    def __init__(
        self,
        cuenca_shp: str,
        dem_path: str,
        drenajes_shp: str,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.cuenca_shp = cuenca_shp
        self.dem_path = dem_path
        self.drenajes_shp = drenajes_shp
        self._cb = progress_cb or log.info

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def compute(self) -> MorphometryResult:
        self._cb("Cargando cuenca y red de drenaje...")
        cuenca = gpd.read_file(self.cuenca_shp)
        drenajes = gpd.read_file(self.drenajes_shp)
        if cuenca.empty:
            raise RuntimeError("La cuenca está vacía.")
        if drenajes.empty:
            raise RuntimeError("La red de drenaje está vacía.")

        dem_crs, cellsize = self._dem_crs_and_cellsize()

        # Alinear CRS al del DEM (metros) — área/longitud se calculan en
        # ese CRS proyectado, nunca en grados.
        if cuenca.crs is not None and dem_crs is not None and cuenca.crs != dem_crs:
            cuenca = cuenca.to_crs(dem_crs)
        if drenajes.crs is not None and dem_crs is not None and drenajes.crs != dem_crs:
            drenajes = drenajes.to_crs(dem_crs)

        return self._compute_from_geoms(cuenca, drenajes, cellsize)

    def compute_subcuencas(
        self, subcuencas_shp: str, sub_id_field: str | None = None,
    ) -> list[tuple[str, MorphometryResult]]:
        """Morfometría de CADA subcuenca (subcuencas.shp, producto del paso
        3), con su propio cauce principal, área, pendiente, etc. — no
        hereda nada de la cuenca general, cada una se calcula de forma
        independiente y completa, igual que compute().

        Subcuencas sin cauce propio (por debajo del umbral FAT dentro de su
        extensión) se omiten con una advertencia, en vez de abortar el
        resto del cálculo.
        """
        dem_crs, cellsize = self._dem_crs_and_cellsize()

        subcuencas = gpd.read_file(subcuencas_shp)
        if subcuencas.empty:
            raise RuntimeError("El shapefile de subcuencas está vacío.")
        if subcuencas.crs is not None and dem_crs is not None and subcuencas.crs != dem_crs:
            subcuencas = subcuencas.to_crs(dem_crs)

        id_field = sub_id_field
        if id_field is None or id_field not in subcuencas.columns:
            for candidate in ("sub_id", "value", "DN", "cat", "id"):
                if candidate in subcuencas.columns:
                    id_field = candidate
                    break
        if id_field is None:
            id_field = "_fid"
            subcuencas[id_field] = subcuencas.index.astype(str)
        else:
            # r.to.vect emite un polígono por parte conexa: fusionar antes
            # de procesar, o una subcuenca fragmentada se calcularía varias
            # veces con solo una fracción de su área real.
            subcuencas = subcuencas.dissolve(by=id_field, as_index=False)
            subcuencas[id_field] = subcuencas[id_field].astype(str)

        drenajes_full = gpd.read_file(self.drenajes_shp)
        if drenajes_full.crs is not None and dem_crs is not None and drenajes_full.crs != dem_crs:
            drenajes_full = drenajes_full.to_crs(dem_crs)

        results: list[tuple[str, MorphometryResult]] = []
        for _, row in subcuencas.iterrows():
            sub_id = str(row[id_field])
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            sub_gdf = gpd.GeoDataFrame(geometry=[geom], crs=dem_crs)
            try:
                drenajes_sub = gpd.clip(drenajes_full, sub_gdf)
            except Exception:
                drenajes_sub = drenajes_full[drenajes_full.intersects(geom)]
            drenajes_sub = drenajes_sub[
                drenajes_sub.geometry.notna() & ~drenajes_sub.geometry.is_empty
            ]
            if drenajes_sub.empty:
                self._cb(
                    f"  Subcuenca {sub_id}: sin cauce propio dentro de su "
                    f"extensión — omitida."
                )
                continue
            try:
                self._cb(f"  Subcuenca {sub_id}: calculando morfometría...")
                result = self._compute_from_geoms(sub_gdf, drenajes_sub, cellsize)
                results.append((sub_id, result))
            except Exception as exc:
                self._cb(f"  Subcuenca {sub_id}: ERROR ({exc}) — omitida.")
                log.error(f"compute_subcuencas {sub_id}: {exc}", exc_info=True)

        if not results:
            raise RuntimeError("Ninguna subcuenca pudo calcularse (revise subcuencas.shp y drenajes.shp).")
        return results

    def _dem_crs_and_cellsize(self):
        with rasterio.open(self.dem_path) as src:
            return src.crs, abs(src.transform.a)

    def _compute_from_geoms(
        self, cuenca: gpd.GeoDataFrame, drenajes: gpd.GeoDataFrame, cellsize: float,
    ) -> MorphometryResult:
        """Núcleo de cálculo compartido por compute() (cuenca general) y
        compute_subcuencas() (cada subcuenca): recibe el polígono y la red
        de drenaje YA recortados/alineados al CRS del DEM."""
        # ---- Área y perímetro -----------------------------------------
        area_m2 = float(cuenca.geometry.area.sum())
        perimetro_m = float(cuenca.geometry.length.sum())
        area_km2 = area_m2 / 1e6
        perimetro_km = perimetro_m / 1000.0
        self._cb(f"Área = {area_km2:.3f} km²  |  Perímetro = {perimetro_km:.3f} km")

        # ---- Cauce principal (ruta más larga en el grafo de drenaje) ---
        self._cb("Determinando cauce principal (grafo de la red de drenaje)...")
        length_m, main_coords = self._main_channel_path(drenajes, cellsize)
        if length_m <= 0:
            raise RuntimeError("No se pudo determinar la longitud del cauce principal.")
        length_km = length_m / 1000.0

        # ---- Densidad de drenaje ----------------------------------------
        total_stream_km = float(drenajes.geometry.length.sum()) / 1000.0
        dd = total_stream_km / area_km2 if area_km2 > 0 else float("nan")

        # ---- Pendiente del cauce principal (muestreo sobre el DEM) -----
        self._cb("Muestreando elevaciones sobre el cauce principal...")
        elev_channel = self._sample_dem(main_coords)
        elev_channel = elev_channel[np.isfinite(elev_channel)]
        if elev_channel.size < 2:
            raise RuntimeError(
                "No se pudieron muestrear elevaciones válidas sobre el "
                "cauce principal (¿el DEM no cubre la cuenca?)."
            )
        drop_m = float(np.nanmax(elev_channel) - np.nanmin(elev_channel))
        slope_channel = max(drop_m / length_m, MIN_SLOPE)

        # ---- Elevación y pendiente media de la cuenca -------------------
        self._cb("Calculando estadísticas de elevación y pendiente de la cuenca...")
        elev_stats, mean_slope_pct = self._basin_raster_stats(cuenca, cellsize)

        relieve_m = elev_stats["max"] - elev_stats["min"]
        razon_relieve = relieve_m / length_km if length_km > 0 else float("nan")

        # ---- Índices morfométricos ---------------------------------------
        kc = 0.28 * perimetro_km / math.sqrt(area_km2) if area_km2 > 0 else float("nan")
        kf = area_km2 / length_km ** 2 if length_km > 0 else float("nan")

        # ---- Tc de Kirpich — adoptado como semilla de iteración ----------
        tc_kirpich_min = 0.01947 * length_m ** 0.77 * slope_channel ** -0.385
        self._cb(
            f"Tc Kirpich = {tc_kirpich_min:.2f} min "
            f"(L={length_km:.3f} km, S={slope_channel * 100:.2f}%)"
        )

        return MorphometryResult(
            area_km2=round(area_km2, 4),
            perimetro_km=round(perimetro_km, 4),
            longitud_cauce_principal_km=round(length_km, 4),
            pendiente_cauce_principal_pct=round(slope_channel * 100, 4),
            pendiente_media_cuenca_pct=round(mean_slope_pct, 4),
            elevacion_max_m=round(elev_stats["max"], 2),
            elevacion_min_m=round(elev_stats["min"], 2),
            elevacion_media_m=round(elev_stats["mean"], 2),
            relieve_m=round(relieve_m, 2),
            razon_relieve_m_km=round(razon_relieve, 4),
            coef_compacidad_kc=round(kc, 4),
            factor_forma_kf=round(kf, 4),
            densidad_drenaje_km_km2=round(dd, 4),
            tc_kirpich_min=round(tc_kirpich_min, 2),
            tc_adoptado_min=round(tc_kirpich_min, 2),
        )

    def compute_and_export(
        self,
        csv_path: str,
        curve_number: float | None = None,
        cn_raster_path: str | None = None,
    ) -> tuple[MorphometryResult, str, list[TcEmpiricalResult], str]:
        """Calcula la morfometría y, con los mismos parámetros (A, L, S,
        elevaciones), las 14 fórmulas empíricas de Tc de tc_empirical.py —
        exportadas en un CSV separado junto al de morfometría.

        CN para el método SCS(13): si se da ``curve_number`` explícito, ese
        manda (override manual); si no, y se da ``cn_raster_path`` (el
        raster de CN del paso 4, con cn_map poblado), se extrae la media
        del raster DENTRO de la cuenca automáticamente — no hay que
        teclear el CN a mano si ya se generó el raster.
        """
        result = self.compute()
        self.export_csv(result, csv_path)
        self._cb(f"Morfometría guardada en: {csv_path}")

        cn_final = curve_number
        if cn_final is None and cn_raster_path:
            cuenca = gpd.read_file(self.cuenca_shp)
            dem_crs, _ = self._dem_crs_and_cellsize()
            if cuenca.crs is not None and dem_crs is not None and cuenca.crs != dem_crs:
                cuenca = cuenca.to_crs(dem_crs)
            cn_final = self.extract_mean_raster_value(cn_raster_path, cuenca)
            if cn_final is not None:
                self._cb(f"CN extraído del raster (media en la cuenca) = {cn_final:.1f}")

        self._cb("Calculando métodos empíricos de Tc para comparación...")
        tc_results = _compute_tc_empirical(
            area_km2=result.area_km2,
            longitud_cauce_km=result.longitud_cauce_principal_km,
            pendiente_cauce_pct=result.pendiente_cauce_principal_pct,
            elevacion_media_m=result.elevacion_media_m,
            elevacion_min_m=result.elevacion_min_m,
            elevacion_max_m=result.elevacion_max_m,
            tc_kirpich_min=result.tc_kirpich_min,
            curve_number=cn_final,
        )
        tc_csv_path = str(Path(csv_path).with_name("comparacion_tc_metodos.csv"))
        _export_tc_empirical_csv(tc_results, tc_csv_path)
        self._cb(f"Comparación de métodos de Tc guardada en: {tc_csv_path}")

        return result, csv_path, tc_results, tc_csv_path

    def compute_and_export_subcuencas(
        self,
        subcuencas_shp: str,
        output_dir: str,
        cn_raster_path: str | None = None,
        sub_id_field: str | None = None,
    ) -> tuple[list[tuple[str, MorphometryResult, float | None]], str, list[tuple[str, TcEmpiricalResult]], str]:
        """Morfometría + comparación de métodos de Tc para CADA subcuenca,
        con su propio CN extraído del raster (media dentro de cada
        polígono — a diferencia de la cuenca general, aquí no hay opción
        de CN manual: cada subcuenca necesita el suyo propio).

        Exporta morfometria_subcuencas.csv (una fila por subcuenca) y
        comparacion_tc_metodos_subcuencas.csv (formato largo: sub_id +
        método + Tc), junto a los CSV de la cuenca general.
        """
        os.makedirs(output_dir, exist_ok=True)
        sub_results = self.compute_subcuencas(subcuencas_shp, sub_id_field=sub_id_field)

        dem_crs, _ = self._dem_crs_and_cellsize()
        subcuencas = gpd.read_file(subcuencas_shp)
        if subcuencas.crs is not None and dem_crs is not None and subcuencas.crs != dem_crs:
            subcuencas = subcuencas.to_crs(dem_crs)
        id_field = sub_id_field
        if id_field is None or id_field not in subcuencas.columns:
            for candidate in ("sub_id", "value", "DN", "cat", "id"):
                if candidate in subcuencas.columns:
                    id_field = candidate
                    break
        if id_field is not None:
            subcuencas = subcuencas.dissolve(by=id_field, as_index=False)
            subcuencas[id_field] = subcuencas[id_field].astype(str)
        else:
            id_field = "_fid"
            subcuencas[id_field] = subcuencas.index.astype(str)

        rows_morfo = []
        rows_tc: list[tuple[str, TcEmpiricalResult]] = []
        full_results: list[tuple[str, MorphometryResult, float | None]] = []

        for sub_id, result in sub_results:
            cn_sub = None
            if cn_raster_path:
                geom_rows = subcuencas.loc[subcuencas[id_field] == sub_id, "geometry"]
                if not geom_rows.empty:
                    poly_gdf = gpd.GeoDataFrame(geometry=[geom_rows.iloc[0]], crs=dem_crs)
                    cn_sub = self.extract_mean_raster_value(cn_raster_path, poly_gdf)
            full_results.append((sub_id, result, cn_sub))

            row = {"sub_id": sub_id, **result.to_dict()}
            row["cn_extraido"] = round(cn_sub, 2) if cn_sub is not None else ""
            rows_morfo.append(row)

            tc_results = _compute_tc_empirical(
                area_km2=result.area_km2,
                longitud_cauce_km=result.longitud_cauce_principal_km,
                pendiente_cauce_pct=result.pendiente_cauce_principal_pct,
                elevacion_media_m=result.elevacion_media_m,
                elevacion_min_m=result.elevacion_min_m,
                elevacion_max_m=result.elevacion_max_m,
                tc_kirpich_min=result.tc_kirpich_min,
                curve_number=cn_sub,
            )
            for tr in tc_results:
                rows_tc.append((sub_id, tr))
            self._cb(
                f"  Subcuenca {sub_id}: Tc Kirpich={result.tc_kirpich_min:.1f} min, "
                f"CN={cn_sub if cn_sub is not None else 'N/D'}"
            )

        morfo_csv = os.path.join(output_dir, "morfometria_subcuencas.csv")
        with open(morfo_csv, "w", newline="", encoding="utf-8-sig") as fh:
            fieldnames = ["sub_id"] + list(MorphometryResult.__dataclass_fields__.keys()) + ["cn_extraido"]
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_morfo)
        self._cb(f"Morfometría de subcuencas guardada en: {morfo_csv}")

        tc_csv = os.path.join(output_dir, "comparacion_tc_metodos_subcuencas.csv")
        with open(tc_csv, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["sub_id", "metodo", "tc_min", "tc_horas", "formula", "notas"])
            for sub_id, tr in rows_tc:
                tc_h = round(tr.tc_min / 60.0, 2) if tr.tc_min is not None else ""
                writer.writerow([sub_id, tr.metodo, tr.tc_min if tr.tc_min is not None else "N/D", tc_h, tr.formula, tr.notas])
        self._cb(f"Comparación de métodos de Tc por subcuenca guardada en: {tc_csv}")

        return full_results, morfo_csv, rows_tc, tc_csv

    @staticmethod
    def extract_mean_raster_value(raster_path: str, polygon_gdf: gpd.GeoDataFrame) -> float | None:
        """Media aritmética de un raster (ej. CN) dentro de un polígono.
        Retorna None si no hay superposición o el raster no tiene celdas
        válidas ahí — en vez de una excepción, para que el llamador pueda
        decidir omitir esa subcuenca sin abortar el resto."""
        try:
            with rasterio.open(raster_path) as src:
                geoms = [g.__geo_interface__ for g in polygon_gdf.geometry if g is not None]
                if not geoms:
                    return None
                nodata = src.nodata if src.nodata is not None else -9999.0
                out_image, _ = rio_mask(src, geoms, crop=True, nodata=nodata)
                data = out_image[0].astype(float)
            data[data == nodata] = np.nan
            valid = data[np.isfinite(data)]
            if valid.size == 0:
                return None
            return float(np.nanmean(valid))
        except Exception as exc:
            log.warning(f"extract_mean_raster_value({raster_path}): {exc}")
            return None

    @staticmethod
    def export_csv(result: MorphometryResult, csv_path: str) -> str:
        parent = os.path.dirname(csv_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["parametro", "valor"])
            for key, value in result.to_dict().items():
                writer.writerow([_LABELS.get(key, key), value])
        return csv_path

    # ------------------------------------------------------------------
    # Utilidades internas
    # ------------------------------------------------------------------

    @staticmethod
    def _main_channel_path(
        drenajes: gpd.GeoDataFrame, cellsize: float
    ) -> tuple[float, list[tuple[float, float]]]:
        """Cauce principal = ruta más larga (diámetro) del grafo de drenaje.

        r.to.vect emite un segmento de línea por tramo entre confluencias, y
        sus extremos rara vez coinciden EXACTAMENTE entre segmentos vecinos
        (artefacto de la rasterización). linemerge() exige coincidencia
        exacta y por eso fragmenta la red en decenas de componentes cortos
        — subestimando gravemente la longitud del cauce principal.

        Aquí los extremos se agrupan con una tolerancia de varias celdas del
        DEM (cKDTree + unión de conjuntos), se arma un grafo ponderado por
        longitud de cada tramo, y se calcula su diámetro (dos pasadas de
        caminos más cortos de Dijkstra) sobre la componente conexa más
        grande — el mismo principio que 'longest flow path' en hidrología
        de cuencas, aplicado sobre la red vectorizada en vez del raster de
        direcciones de flujo.
        """
        segments = []
        for g in drenajes.geometry.values:
            if g is None or g.is_empty:
                continue
            if g.geom_type == "LineString":
                segments.append(g)
            elif g.geom_type == "MultiLineString":
                segments.extend(list(g.geoms))
        if not segments:
            raise RuntimeError("La red de drenaje no contiene geometrías de línea válidas.")

        starts = np.array([s.coords[0] for s in segments])
        ends = np.array([s.coords[-1] for s in segments])
        all_pts = np.vstack([starts, ends])
        n_seg = len(segments)

        # 1) Agrupar extremos cercanos (tolerancia = 3 celdas del DEM)
        snap_tol = max(cellsize * 3.0, 5.0)
        tree = cKDTree(all_pts)
        pairs = tree.query_pairs(r=snap_tol)

        parent = list(range(len(all_pts)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, j in pairs:
            union(i, j)

        cluster_of = np.array([find(i) for i in range(len(all_pts))])
        remap = {c: idx for idx, c in enumerate(sorted(set(cluster_of.tolist())))}
        node_id = np.array([remap[c] for c in cluster_of.tolist()])
        n_nodes = len(remap)
        u_node = node_id[:n_seg]
        v_node = node_id[n_seg:]

        # 2) Grafo ponderado — un edge por par de nodos (el tramo más corto
        #    si hubiera varios segmentos entre el mismo par de nodos)
        edge_len: dict[tuple[int, int], float] = {}
        edge_seg: dict[tuple[int, int], int] = {}
        for k in range(n_seg):
            u, v = int(u_node[k]), int(v_node[k])
            if u == v:
                continue   # segmento degenerado (ambos extremos al mismo nodo)
            key = (u, v) if u < v else (v, u)
            length = float(segments[k].length)
            if key not in edge_len or length < edge_len[key]:
                edge_len[key] = length
                edge_seg[key] = k

        if not edge_len:
            raise RuntimeError("El grafo de drenaje no tiene tramos válidos entre nodos distintos.")

        rows = np.array([k[0] for k in edge_len], dtype=int)
        cols = np.array([k[1] for k in edge_len], dtype=int)
        weights = np.array(list(edge_len.values()), dtype=float)
        graph = coo_matrix(
            (np.concatenate([weights, weights]), (
                np.concatenate([rows, cols]), np.concatenate([cols, rows])
            )),
            shape=(n_nodes, n_nodes),
        ).tocsr()

        # 3) Componente conexa más grande (la red principal drenando al outlet)
        n_comp, labels = connected_components(graph, directed=False)
        if n_comp > 1:
            sizes = np.bincount(labels, minlength=n_comp)
            main_label = int(np.argmax(sizes))
        else:
            main_label = 0
        start_node = int(np.flatnonzero(labels == main_label)[0])

        # 4) Diámetro del grafo: 2 pasadas de Dijkstra (algoritmo estándar
        #    para la ruta más larga en un árbol/grafo disperso)
        dist0, pred0 = dijkstra(graph, directed=False, indices=start_node, return_predecessors=True)
        node_a = int(np.nanargmax(np.where(np.isfinite(dist0), dist0, -np.inf)))
        dist_a, pred_a = dijkstra(graph, directed=False, indices=node_a, return_predecessors=True)
        node_b = int(np.nanargmax(np.where(np.isfinite(dist_a), dist_a, -np.inf)))
        length_m = float(dist_a[node_b])

        # 5) Reconstruir la secuencia de nodos node_a -> node_b y mapearla
        #    a coordenadas concatenando los segmentos originales, en orden.
        path_nodes = [node_b]
        cur = node_b
        while cur != node_a:
            prev = int(pred_a[cur])
            if prev < 0:
                break   # no debería ocurrir: node_b es alcanzable desde node_a
            path_nodes.append(prev)
            cur = prev
        path_nodes.reverse()

        coords: list[tuple[float, float]] = []
        for n_from, n_to in zip(path_nodes[:-1], path_nodes[1:]):
            key = (n_from, n_to) if n_from < n_to else (n_to, n_from)
            seg = segments[edge_seg[key]]
            seg_coords = list(seg.coords)
            seg_u = int(u_node[edge_seg[key]])
            # Orientar el segmento para que empiece en n_from
            if seg_u != n_from:
                seg_coords = seg_coords[::-1]
            if coords and coords[-1] == seg_coords[0]:
                coords.extend(seg_coords[1:])
            else:
                coords.extend(seg_coords)

        return length_m, coords

    def _sample_dem(self, coords) -> np.ndarray:
        pts = list(coords)
        with rasterio.open(self.dem_path) as src:
            nodata = src.nodata
            values = np.array([v[0] for v in src.sample(pts)], dtype=float)
        if nodata is not None:
            values[values == nodata] = np.nan
        return values

    def _basin_raster_stats(
        self, cuenca: gpd.GeoDataFrame, cellsize: float
    ) -> tuple[dict, float]:
        with rasterio.open(self.dem_path) as src:
            geoms = [g.__geo_interface__ for g in cuenca.geometry if g is not None]
            nodata = src.nodata if src.nodata is not None else -9999.0
            out_image, _ = rio_mask(src, geoms, crop=True, nodata=nodata)
            dem = out_image[0].astype(float)
        dem[dem == nodata] = np.nan

        valid = dem[np.isfinite(dem)]
        if valid.size == 0:
            raise RuntimeError("El DEM no tiene celdas válidas dentro de la cuenca.")
        elev_stats = {
            "max": float(np.nanmax(valid)),
            "min": float(np.nanmin(valid)),
            "mean": float(np.nanmean(valid)),
        }

        dy, dx = np.gradient(dem, cellsize, cellsize)
        slope = np.hypot(dx, dy)
        slope_valid = slope[np.isfinite(dem) & np.isfinite(slope)]
        mean_slope_pct = (
            float(np.nanmean(slope_valid) * 100.0) if slope_valid.size else float("nan")
        )

        return elev_stats, mean_slope_pct
