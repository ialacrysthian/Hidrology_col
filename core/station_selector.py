"""
Preselección espacial de estaciones IDEAM en función de un polígono de cuenca
y un buffer definido por el usuario.

Flujo:
  1. Cargar SHP de estaciones (CNE_IDEAM_estaciones.shp o _activas.shp)
  2. Aplicar buffer en km alrededor del polígono de cuenca
  3. Filtrar estaciones dentro del buffer
  4. Filtrar opcionalmente por categoría, estado o variable de interés
  5. Exportar SHP de preselección + CSV resumen

Uso desde línea de comandos:
  python -m tc_calculator.core.station_selector \\
      --cuenca ruta/cuenca.shp \\
      --buffer 20 \\
      --categoria "Climatica Principal,Climatica Ordinaria,Pluviometrica" \\
      --estado Activa \\
      --salida output/estaciones_preseleccion.shp

Uso desde Python:
  from tc_calculator.core.station_selector import StationSelector
  sel = StationSelector(shp_cne='data/shp/CNE_IDEAM_estaciones.shp')
  gdf = sel.select(cuenca_shp='mi_cuenca.shp', buffer_km=20)
  sel.export(gdf, 'salida/estaciones.shp')
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# CRS proyectado para Colombia — MAGNA-SIRGAS / CTM12 (EPSG:9377)
# Se usa internamente para calcular el buffer en metros con precisión.
_CRS_PROYECTADO = "EPSG:9377"

# Campos del SHP CNE que se incluyen en el resultado
_CAMPOS_SALIDA = [
    "CODIGO", "NOMBRE", "CATEGORIA", "TECNOLOGIA", "ESTADO",
    "ALTITUD_M", "LATITUD", "LONGITUD", "DEPARTAMEN", "MUNICIPIO",
    "AREA_HIDRO", "ZONA_HIDRO", "SUBZONA", "CORRIENTE", "geometry",
]

# Alias de categorías para búsqueda flexible
_ALIAS_CATEGORIA = {
    "climatica principal":   "Climática Principal",
    "climatica ordinaria":   "Climática Ordinaria",
    "pluviometrica":         "Pluviométrica",
    "pluviografica":         "Pluviográfica",
    "limnimetrica":          "Limnimétrica",
    "limnimetrica":          "Limnimétrica",
    "limnigrafica":          "Limnigráfica",
    "agrometerologica":      "Agrometeorológica",
    "meteorologica especial":"Meteorológica Especial",
    "sinoptica principal":   "Sinóptica Principal",
    "sinoptica secundaria":  "Sinóptica Secundaria",
}

# Variables de balance hídrico → categorías mínimas requeridas
VARIABLES_CATEGORIAS: dict[str, list[str]] = {
    "TEMPERATURA": [
        "Climática Principal", "Climática Ordinaria",
        "Agrometeorológica", "Sinóptica Principal",
        "Meteorológica Especial",
    ],
    "PRECIPITACION": [
        "Climática Principal", "Climática Ordinaria",
        "Pluviométrica", "Pluviográfica",
        "Agrometeorológica", "Sinóptica Principal",
        "Meteorológica Especial",
    ],
    "ETP": [
        "Climática Principal", "Climática Ordinaria",
        "Agrometeorológica",
    ],
    "CAUDAL": [
        "Limnigráfica", "Limnimétrica",
    ],
    "NIVEL": [
        "Limnigráfica", "Limnimétrica",
    ],
}


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class StationSelector:
    """
    Preselección espacial de estaciones CNE_IDEAM en función de un polígono
    de cuenca con buffer en kilómetros.

    Parámetros
    ----------
    shp_cne : str | Path
        Ruta al SHP de estaciones (generado desde CNE_IDEAM.xls).
        Por defecto busca 'data/shp/CNE_IDEAM_estaciones.shp' relativo
        al directorio del paquete.
    """

    def __init__(self, shp_cne: str | Path = "") -> None:
        if not shp_cne:
            shp_cne = Path(__file__).parent.parent / "data" / "shp" / "CNE_IDEAM_estaciones.shp"
        self._shp_cne = Path(shp_cne)
        if not self._shp_cne.exists():
            raise FileNotFoundError(f"SHP de estaciones no encontrado: {self._shp_cne}")
        self._gdf_cne: gpd.GeoDataFrame | None = None

    # ------------------------------------------------------------------
    # Carga lazy del catálogo
    # ------------------------------------------------------------------

    def _cne(self) -> gpd.GeoDataFrame:
        if self._gdf_cne is None:
            self._gdf_cne = gpd.read_file(self._shp_cne, encoding="utf-8")
            if self._gdf_cne.crs is None:
                self._gdf_cne = self._gdf_cne.set_crs("EPSG:4326")
        return self._gdf_cne

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def select(
        self,
        cuenca_shp: str | Path | None = None,
        cuenca_wkt: str | None = None,
        buffer_km: float = 10.0,
        estado: str = "Activa",
        categorias: list[str] | None = None,
        variable: str | None = None,
        tecnologia: list[str] | None = None,
        incluir_dentro: bool = True,
    ) -> gpd.GeoDataFrame:
        """
        Preselecciona estaciones dentro del buffer alrededor de la cuenca.

        Parámetros
        ----------
        cuenca_shp : str | Path, opcional
            SHP de la cuenca (toma la primera feature si hay varias).
        cuenca_wkt : str, opcional
            WKT del polígono de cuenca en EPSG:4326 (alternativa a cuenca_shp).
        buffer_km : float
            Distancia del buffer en kilómetros. 0 = solo dentro de la cuenca.
        estado : str
            Filtrar por estado: 'Activa', 'Suspendida', '' (todas).
        categorias : list[str], opcional
            Lista de categorías a incluir. Si None y variable está definida,
            usa VARIABLES_CATEGORIAS[variable]. Si ambos None, incluye todas.
        variable : str, opcional
            'TEMPERATURA' | 'PRECIPITACION' | 'ETP' | 'CAUDAL' | 'NIVEL'.
            Determina las categorías automáticamente si no se dan categorias.
        tecnologia : list[str], opcional
            Filtrar por tecnología: ['Convencional', 'Automática con Telemetría'].
            Si None, incluye todas.
        incluir_dentro : bool
            Si True, incluye estaciones dentro de la cuenca aunque estén fuera
            del buffer (redundante con buffer>0, útil con buffer=0).

        Retorna
        -------
        GeoDataFrame con las estaciones preseleccionadas en EPSG:4326,
        con columna adicional 'DENTRO_CUENCA' (bool) y 'DIST_KM' (float).
        """
        # --- Cargar polígono de cuenca ---
        poly_wgs84 = self._load_polygon(cuenca_shp, cuenca_wkt)

        # --- Proyectar a CTM12 para buffer métrico ---
        gdf_cuenca = gpd.GeoDataFrame(geometry=[poly_wgs84], crs="EPSG:4326")
        gdf_cuenca_proj = gdf_cuenca.to_crs(_CRS_PROYECTADO)
        poly_proj = gdf_cuenca_proj.geometry.iloc[0]

        buffer_m = buffer_km * 1000.0
        poly_buffer_proj = poly_proj.buffer(buffer_m)

        # --- Proyectar estaciones ---
        gdf = self._cne().copy()
        gdf_proj = gdf.to_crs(_CRS_PROYECTADO)

        # --- Filtro espacial: dentro del buffer ---
        mask_buffer = gdf_proj.geometry.within(poly_buffer_proj)
        mask_dentro = gdf_proj.geometry.within(poly_proj)

        if incluir_dentro:
            mask_espacial = mask_buffer | mask_dentro
        else:
            mask_espacial = mask_buffer

        result = gdf[mask_espacial].copy()
        result_proj = gdf_proj[mask_espacial].copy()

        # --- Columnas auxiliares ---
        result["DENTRO_CUE"] = mask_dentro[mask_espacial].values

        # Distancia al borde de la cuenca (negativa = dentro).
        # Se usa .boundary y no .exterior: la cuenca vectorizada por GRASS
        # puede ser MultiPolygon, que no tiene atributo .exterior.
        borde = poly_proj.boundary
        dist_m = result_proj.geometry.apply(
            lambda geom: borde.distance(geom)
            if not poly_proj.contains(geom) else -borde.distance(geom)
        )
        result["DIST_KM"] = (dist_m / 1000.0).round(2)

        # --- Filtros de atributo ---
        if estado:
            result = result[result["ESTADO"].str.strip() == estado]

        # Resolver categorías desde variable si no se dan explícitamente
        cats_efectivas = categorias
        if cats_efectivas is None and variable:
            cats_efectivas = VARIABLES_CATEGORIAS.get(variable.upper())

        if cats_efectivas:
            cats_norm = {self._normalizar(c) for c in cats_efectivas}
            mask_cat = result["CATEGORIA"].str.strip().apply(
                lambda c: self._normalizar(c) in cats_norm
                or c.strip() in cats_efectivas
            )
            result = result[mask_cat]

        if tecnologia:
            tec_norm = {t.lower() for t in tecnologia}
            result = result[
                result["TECNOLOGIA"].str.strip().str.lower().isin(tec_norm)
            ]

        # --- Ordenar por distancia ---
        result = result.sort_values("DIST_KM").reset_index(drop=True)

        return result[
            [c for c in _CAMPOS_SALIDA if c in result.columns]
            + ["DENTRO_CUE", "DIST_KM"]
        ]

    def select_by_variable(
        self,
        cuenca_shp: str | Path | None = None,
        cuenca_wkt: str | None = None,
        buffer_km: float = 10.0,
        estado: str = "Activa",
    ) -> dict[str, gpd.GeoDataFrame]:
        """
        Preselección separada por cada variable de balance hídrico.

        Retorna dict: {'TEMPERATURA': GDF, 'PRECIPITACION': GDF, ...}
        """
        return {
            var: self.select(
                cuenca_shp=cuenca_shp,
                cuenca_wkt=cuenca_wkt,
                buffer_km=buffer_km,
                estado=estado,
                variable=var,
            )
            for var in VARIABLES_CATEGORIAS
        }

    # ------------------------------------------------------------------
    # Exportación
    # ------------------------------------------------------------------

    def export(
        self,
        gdf: gpd.GeoDataFrame,
        salida_shp: str | Path,
        csv: bool = True,
    ) -> None:
        """
        Exporta el GeoDataFrame de preselección a SHP (y opcionalmente CSV).

        Parámetros
        ----------
        gdf : GeoDataFrame resultado de select()
        salida_shp : ruta de salida del SHP
        csv : si True, guarda también un CSV con el resumen de atributos
        """
        salida = Path(salida_shp)
        salida.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(salida, encoding="utf-8")
        print(f"SHP exportado: {salida}  ({len(gdf)} estaciones)")

        if csv:
            csv_path = salida.with_suffix(".csv")
            cols_csv = [c for c in gdf.columns if c != "geometry"]
            gdf[cols_csv].to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"CSV exportado: {csv_path}")

    def export_by_variable(
        self,
        resultados: dict[str, gpd.GeoDataFrame],
        directorio: str | Path,
        prefijo: str = "preseleccion",
    ) -> None:
        """
        Exporta los resultados de select_by_variable() a SHP individuales
        + un SHP consolidado con todas las variables (campo VAR).
        """
        dir_out = Path(directorio)
        dir_out.mkdir(parents=True, exist_ok=True)

        gdfs_todos = []
        for var, gdf in resultados.items():
            if gdf.empty:
                print(f"  {var}: 0 estaciones — omitido")
                continue
            shp = dir_out / f"{prefijo}_{var.lower()}.shp"
            self.export(gdf, shp, csv=True)
            g = gdf.copy()
            g["VARIABLE"] = var
            gdfs_todos.append(g)

        if gdfs_todos:
            consolidado = gpd.GeoDataFrame(
                pd.concat(gdfs_todos, ignore_index=True),
                crs="EPSG:4326",
            )
            # Deduplicar por CODIGO (una estación puede cubrir varias variables)
            shp_all = dir_out / f"{prefijo}_todas.shp"
            consolidado.to_file(shp_all, encoding="utf-8")
            n_uniq = consolidado["CODIGO"].nunique()
            print(f"\nConsolidado: {shp_all}  ({len(consolidado)} filas | {n_uniq} estaciones únicas)")

    # ------------------------------------------------------------------
    # Utilidades internas
    # ------------------------------------------------------------------

    @staticmethod
    def _load_polygon(shp_path, wkt_str):
        """Carga el polígono de cuenca desde SHP o WKT."""
        if shp_path is not None:
            gdf = gpd.read_file(shp_path)
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs("EPSG:4326")
            return gdf.geometry.iloc[0]
        if wkt_str is not None:
            from shapely.wkt import loads
            return loads(wkt_str)
        raise ValueError("Debe proveer cuenca_shp o cuenca_wkt.")

    @staticmethod
    def _normalizar(texto: str) -> str:
        """Normaliza texto para comparación: minúsculas sin tildes."""
        import unicodedata
        s = texto.lower().strip()
        return "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )


# ---------------------------------------------------------------------------
# Resumen estadístico
# ---------------------------------------------------------------------------

def resumen_preseleccion(
    resultados: dict[str, gpd.GeoDataFrame],
    buffer_km: float,
) -> pd.DataFrame:
    """
    Genera tabla resumen de la preselección por variable.

    Retorna DataFrame con columnas: variable, n_total, n_dentro, n_buffer,
    categorias, tecnologias.
    """
    filas = []
    for var, gdf in resultados.items():
        if gdf.empty:
            filas.append({
                "variable": var, "n_total": 0, "n_dentro": 0,
                "n_buffer": 0, "categorias": "", "tecnologias": "",
            })
            continue
        n_dentro = int(gdf["DENTRO_CUE"].sum())
        filas.append({
            "variable":    var,
            "n_total":     len(gdf),
            "n_dentro":    n_dentro,
            "n_buffer":    len(gdf) - n_dentro,
            "categorias":  " | ".join(sorted(gdf["CATEGORIA"].unique())),
            "tecnologias": " | ".join(sorted(gdf["TECNOLOGIA"].unique())),
        })
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    parser = argparse.ArgumentParser(
        description="Preselección de estaciones IDEAM por cuenca + buffer"
    )
    parser.add_argument("--cuenca",    required=True, help="SHP de la cuenca")
    parser.add_argument("--buffer",    type=float, default=10.0,
                        help="Buffer en kilómetros (default: 10)")
    parser.add_argument("--shp-cne",  default="",
                        help="SHP del CNE (default: data/shp/CNE_IDEAM_estaciones.shp)")
    parser.add_argument("--estado",   default="Activa",
                        help="Filtro de estado: Activa | Suspendida | '' (todas)")
    parser.add_argument("--variable", default="",
                        help="Variable: TEMPERATURA | PRECIPITACION | ETP | CAUDAL | NIVEL | ALL")
    parser.add_argument("--categoria", default="",
                        help="Categorías separadas por coma (sobreescribe --variable)")
    parser.add_argument("--salida",   default="output/preseleccion",
                        help="Directorio de salida (default: output/preseleccion)")
    args = parser.parse_args()

    sel = StationSelector(shp_cne=args.shp_cne or None)

    cats = [c.strip() for c in args.categoria.split(",")] if args.categoria else None
    var  = args.variable.upper() if args.variable and args.variable.upper() != "ALL" else None

    if args.variable.upper() == "ALL" or (not args.variable and not args.categoria):
        resultados = sel.select_by_variable(
            cuenca_shp=args.cuenca,
            buffer_km=args.buffer,
            estado=args.estado,
        )
        resumen = resumen_preseleccion(resultados, args.buffer)
        print("\n=== RESUMEN POR VARIABLE ===")
        print(resumen.to_string(index=False))
        sel.export_by_variable(resultados, args.salida)
    else:
        gdf = sel.select(
            cuenca_shp=args.cuenca,
            buffer_km=args.buffer,
            estado=args.estado,
            categorias=cats,
            variable=var,
        )
        print(f"\nEstaciones preseleccionadas: {len(gdf)}")
        print(gdf[["CODIGO", "NOMBRE", "CATEGORIA", "ESTADO", "DIST_KM"]].to_string(index=False))
        shp_out = Path(args.salida) / "preseleccion.shp"
        sel.export(gdf, shp_out)


if __name__ == "__main__":
    _cli()
