"""
Cliente para datos climáticos IDEAM via datos.gov.co (Socrata API).

Fuente de estaciones
--------------------
  CNE_IDEAM.xls  Catálogo Nacional de Estaciones (4497 estaciones). Fuente
                 primaria para localización, categoría y cuenca. Se entrega
                 con el plugin o se descarga desde el portal IDEAM.

Datasets Socrata (datos.gov.co)
--------------------------------
  nsz2-kzcq  Normales Climatológicas 1961-1990, 1981-2010, 1991-2020
  afdg-3zpb  Temperatura Mínima del Aire (automáticas, tiempo real)
  ccvq-rp9s  Temperatura Máxima del Aire (automáticas, tiempo real)
  sbwg-7ju4  Temperatura Ambiente del Aire (automáticas, tiempo real)
  s54a-sgyg  Precipitación (automáticas, tiempo real)
  57sv-p2fu  Todas las variables IDEAM en tiempo real
"""
from __future__ import annotations

import concurrent.futures
import os
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from shapely.geometry import Point
from shapely.wkt import loads as wkt_loads

from ..utils.logger import get_logger
from .variables import NORMAL_PARAM_MAP as _NORMAL_PARAM_MAP  # noqa: F401 (re-export)

log = get_logger(__name__)

_SOCRATA_BASE = "https://www.datos.gov.co/resource"
_PAGE_SIZE = 1000

# Columnas CNE → campos Station
_CNE_COLS = {
    "CODIGO": "code",
    "nombre": "name",
    "latitud": "lat",
    "longitud": "lon",
    "altitud": "elevation_m",
    "CATEGORIA": "category",
    "ESTADO": "status",
    "DEPARTAMENTO": "department",
    "MUNICIPIO": "municipality",
    "TECNOLOGIA": "technology",
    "AREA_HIDROGRAFICA": "hydrographic_area",
    "ZONA_HIDROGRAFICA": "hydrographic_zone",
    "SUBZONA_HIDROGRAFICA": "hydrographic_subzone",
    "CORRIENTE": "stream",
    "FECHA_INSTALACION": "install_date",
}
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds
_MAX_WORKERS = 5     # hilos para descarga paralela

# Dataset IDs en datos.gov.co
_DS_NORMALS = "nsz2-kzcq"       # Normales climatológicas (convencionales)
_DS_TMIN    = "afdg-3zpb"       # T mínima automáticas
_DS_TMAX    = "ccvq-rp9s"       # T máxima automáticas
_DS_TAMB    = "sbwg-7ju4"       # T ambiente automáticas
_DS_PRECIP  = "s54a-sgyg"       # Precipitación automáticas
_DS_ALL     = "57sv-p2fu"       # Todas las variables (incluye convencionales)

# Normales: nombres de columnas de mes
_MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun",
               "jul", "ago", "sep", "oct", "nov", "dic"]
_MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec"]


@dataclass
class Station:
    code: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    category: str
    status: str
    department: str
    municipality: str
    technology: str = ""
    hydrographic_area: str = ""
    hydrographic_zone: str = ""
    hydrographic_subzone: str = ""
    stream: str = ""
    install_date: str = ""


@dataclass
class StationRecord:
    station_code: str
    date: date
    value: float
    variable: str


def load_cne(cne_path: str | Path) -> pd.DataFrame:
    """
    Carga el Catálogo Nacional de Estaciones CNE_IDEAM.xls/.xlsx.

    Retorna DataFrame con todos los campos del CNE, código como string sin
    espacios, coordenadas como float.  Útil para filtrado local sin API.
    """
    path = Path(cne_path)
    if not path.exists():
        raise FileNotFoundError(f"CNE no encontrado: {path}")

    suffix = path.suffix.lower()
    if suffix in (".xls", ".xlsx"):
        df = pd.read_excel(path, sheet_name="CNE", dtype=str)
    else:
        df = pd.read_csv(path, dtype=str)

    df.columns = df.columns.str.strip()
    df["CODIGO"] = df["CODIGO"].str.strip().str.lstrip("0")
    df["latitud"] = pd.to_numeric(df["latitud"], errors="coerce")
    df["longitud"] = pd.to_numeric(df["longitud"], errors="coerce")
    df["altitud"] = pd.to_numeric(df["altitud"], errors="coerce").fillna(0)
    df = df.dropna(subset=["latitud", "longitud"])
    return df


def cne_to_stations(df: pd.DataFrame, active_only: bool = True) -> list[Station]:
    """Convierte un DataFrame CNE a lista de Station."""
    if active_only:
        df = df[df["ESTADO"].str.strip().str.lower() == "activa"]
    stations = []
    for _, row in df.iterrows():
        try:
            stations.append(Station(
                code=str(row["CODIGO"]).strip(),
                name=str(row.get("nombre", "")).strip(),
                lat=float(row["latitud"]),
                lon=float(row["longitud"]),
                elevation_m=float(row.get("altitud") or 0),
                category=str(row.get("CATEGORIA", "")).strip(),
                status=str(row.get("ESTADO", "")).strip(),
                department=str(row.get("DEPARTAMENTO", "")).strip(),
                municipality=str(row.get("MUNICIPIO", "")).strip(),
                technology=str(row.get("TECNOLOGIA", "")).strip(),
                hydrographic_area=str(row.get("AREA_HIDROGRAFICA", "")).strip(),
                hydrographic_zone=str(row.get("ZONA_HIDROGRAFICA", "")).strip(),
                hydrographic_subzone=str(row.get("SUBZONA_HIDROGRAFICA", "")).strip(),
                stream=str(row.get("CORRIENTE", "")).strip(),
                install_date=str(row.get("FECHA_INSTALACION", "")).strip(),
            ))
        except (ValueError, KeyError) as exc:
            log.warning(f"Fila CNE ignorada: {exc}")
    return stations


class IdeamClient:
    """
    Accede a datos climáticos IDEAM.

    Estaciones: vía CNE_IDEAM.xls (catálogo local) o vía Socrata API.
    Datos:      Normales Climatológicas (nsz2-kzcq) + datasets tiempo real.

    Parámetros
    ----------
    cne_path : str | Path, opcional
        Ruta al archivo CNE_IDEAM.xls.  Si se provee, get_stations_in_bbox /
        get_stations_in_polygon usan el CNE local (más rápido y completo).
    app_token : str, opcional
        Socrata application token (dev.socrata.com — registro gratuito).
    timeout : int
        Timeout HTTP en segundos por petición.
    """

    def __init__(
        self,
        cne_path: str | Path = "",
        app_token: str = "",
        timeout: int = 30,
    ) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        if app_token:
            self._session.headers["X-App-Token"] = app_token

        self._cne_df: pd.DataFrame | None = None
        if cne_path and Path(cne_path).exists():
            self._cne_df = load_cne(cne_path)
            log.info(f"CNE cargado: {len(self._cne_df)} estaciones desde {cne_path}")

    # ------------------------------------------------------------------
    # Public — estaciones
    # ------------------------------------------------------------------

    def get_stations_in_bbox(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        active_only: bool = True,
    ) -> list[Station]:
        """
        Estaciones en la bounding box.

        Si se inicializó con cne_path usa el CNE local (preferido — más
        completo y sin llamadas a red).  Si no, consulta nsz2-kzcq en Socrata.
        """
        if self._cne_df is not None:
            return self._bbox_from_cne(min_lon, min_lat, max_lon, max_lat, active_only)
        return self._bbox_from_socrata(min_lon, min_lat, max_lon, max_lat)

    def get_stations_in_polygon(
        self,
        polygon_wkt: str,
        active_only: bool = True,
    ) -> list[Station]:
        """Estaciones dentro del polígono WKT (recorte exacto con shapely)."""
        poly = wkt_loads(polygon_wkt)
        b = poly.bounds
        candidates = self.get_stations_in_bbox(b[0], b[1], b[2], b[3], active_only)
        return [s for s in candidates if poly.contains(Point(s.lon, s.lat))]

    def get_cne_dataframe(self) -> pd.DataFrame | None:
        """Retorna el DataFrame CNE completo (o None si no se cargó)."""
        return self._cne_df

    def _bbox_from_cne(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        active_only: bool,
    ) -> list[Station]:
        df = self._cne_df
        mask = (
            (df["latitud"] >= min_lat) & (df["latitud"] <= max_lat) &
            (df["longitud"] >= min_lon) & (df["longitud"] <= max_lon)
        )
        subset = df[mask].copy()
        return cne_to_stations(subset, active_only=active_only)

    def _bbox_from_socrata(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> list[Station]:
        where = (
            f"latitud >= '{min_lat}' AND latitud <= '{max_lat}' "
            f"AND longitud >= '{min_lon}' AND longitud <= '{max_lon}'"
        )
        rows = self._socrata_get(
            _DS_NORMALS, where=where,
            select="distinct c_digo,estaci_n,municipio,departamento,altitud_m,latitud,longitud,categoria",
        )
        stations: list[Station] = []
        seen: set[str] = set()
        for row in rows:
            try:
                s = self._parse_normals_station(row)
                if s.code not in seen:
                    seen.add(s.code)
                    stations.append(s)
            except (KeyError, ValueError) as exc:
                log.warning(f"Fila de estación ignorada: {exc}")
        return stations

    # ------------------------------------------------------------------
    # Public — normales climatológicas (estaciones convencionales)
    # ------------------------------------------------------------------

    def get_annual_normals(
        self,
        station_codes: list[str],
        variable: str,
        period: str = "1991-2020",
    ) -> pd.DataFrame:
        """
        Retorna normales climatológicas de nsz2-kzcq para una lista de estaciones.

        Parámetros
        ----------
        station_codes : list[str]
            Códigos IDEAM (ej. ['15085020', '25027750']).
        variable : str
            Clave normalizada: 'TEMPERATURA_MINIMA', 'TEMPERATURA_MAXIMA',
            'TEMPERATURA_MEDIA', 'PRECIPITACION', 'BRILLO_SOLAR', etc.
        period : str
            '1961-1990' | '1981-2010' | '1991-2020'

        Retorna
        -------
        DataFrame con columnas: station_code, mean_annual, jan..dec
        """
        param_name = _NORMAL_PARAM_MAP.get(variable.upper(), variable)

        def _fetch_one(code: str) -> list[dict]:
            return self._socrata_get(
                _DS_NORMALS,
                where=f"c_digo='{code}' AND periodo='{period}'",
                q=param_name,
                limit=50,
            )

        rows: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {ex.submit(_fetch_one, c): c for c in station_codes}
            for f in concurrent.futures.as_completed(futures):
                try:
                    rows.extend(f.result())
                except Exception as exc:
                    log.warning(f"Error descargando normales para {futures[f]}: {exc}")

        if not rows:
            return pd.DataFrame(columns=["station_code", "mean_annual"] + _MONTHS_EN)

        records = []
        for row in rows:
            try:
                rec = {"station_code": str(row["c_digo"]).strip()}
                vals = [float(row.get(m) or "nan") for m in _MONTHS_ES]
                rec["mean_annual"] = float(row.get("anual") or "nan")
                for en, val in zip(_MONTHS_EN, vals):
                    rec[en] = val
                records.append(rec)
            except (KeyError, ValueError) as exc:
                log.warning(f"Fila de normal ignorada: {exc}")

        return pd.DataFrame(records)

    def get_all_normals_for_stations(
        self,
        station_codes: list[str],
        period: str = "1991-2020",
    ) -> pd.DataFrame:
        """
        Descarga todas las variables disponibles en normales para las estaciones
        indicadas. Retorna DataFrame con columnas: station_code, par_metro, mean_annual, jan..dec
        """
        def _fetch_one(code: str) -> list[dict]:
            return self._socrata_get(
                _DS_NORMALS,
                where=f"c_digo='{code}' AND periodo='{period}'",
                limit=50,
            )

        rows: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {ex.submit(_fetch_one, c): c for c in station_codes}
            for f in concurrent.futures.as_completed(futures):
                try:
                    rows.extend(f.result())
                except Exception as exc:
                    log.warning(f"Error descargando normales para {futures[f]}: {exc}")

        if not rows:
            return pd.DataFrame()

        records = []
        for row in rows:
            try:
                rec = {
                    "station_code": str(row["c_digo"]).strip(),
                    "par_metro": str(row.get("par_metro", "")).strip(),
                    "mean_annual": float(row.get("anual") or "nan"),
                }
                for es, en in zip(_MONTHS_ES, _MONTHS_EN):
                    rec[en] = float(row.get(es) or "nan")
                records.append(rec)
            except (KeyError, ValueError) as exc:
                log.warning(f"Fila ignorada: {exc}")

        return pd.DataFrame(records)

    # ------------------------------------------------------------------
    # Public — datos en tiempo real (estaciones automáticas)
    # ------------------------------------------------------------------

    def fetch_realtime_data(
        self,
        station_codes: list[str],
        variable: str,
        limit_per_station: int = 5000,
    ) -> pd.DataFrame:
        """
        Descarga datos en tiempo real de estaciones automáticas.

        variable: 'TEMPERATURA_MINIMA' | 'TEMPERATURA_MAXIMA' |
                  'TEMPERATURA_MEDIA'  | 'PRECIPITACION' | 'ALL'
        """
        ds_map = {
            "TEMPERATURA_MINIMA": _DS_TMIN,
            "TEMPERATURA_MAXIMA": _DS_TMAX,
            "TEMPERATURA_MEDIA":  _DS_TAMB,
            "PRECIPITACION":      _DS_PRECIP,
            "ALL":                _DS_ALL,
        }
        dataset = ds_map.get(variable.upper(), _DS_ALL)

        def _fetch_one(code: str) -> list[dict]:
            # códigos en automáticas tienen cero adelante
            padded = code.zfill(10)
            rows = self._socrata_get(
                dataset,
                where=f"codigoestacion='{padded}'",
                order="fechaobservacion DESC",
                limit=limit_per_station,
            )
            if not rows:
                # intentar sin relleno
                rows = self._socrata_get(
                    dataset,
                    where=f"codigoestacion='{code}'",
                    order="fechaobservacion DESC",
                    limit=limit_per_station,
                )
            return rows

        all_rows: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {ex.submit(_fetch_one, c): c for c in station_codes}
            for f in concurrent.futures.as_completed(futures):
                code = futures[f]
                try:
                    rows = f.result()
                    all_rows.extend(rows)
                    log.debug(f"  {code}: {len(rows)} registros")
                except Exception as exc:
                    log.warning(f"Error descargando {code}: {exc}")

        if not all_rows:
            return pd.DataFrame(columns=["station_code", "fecha", "value", "sensor"])

        df = pd.DataFrame(all_rows)
        df = df.rename(columns={
            "codigoestacion": "station_code",
            "fechaobservacion": "fecha",
            "valorobservado": "value",
            "descripcionsensor": "sensor",
        })
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["fecha", "value"])
        df["station_code"] = df["station_code"].str.lstrip("0")
        return df

    def fetch_monthly_data(
        self,
        station_codes: list[str],
        variable: str,
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        """
        Agrega datos en tiempo real a nivel mensual.
        Para estaciones convencionales prefiere get_annual_normals().
        """
        df = self.fetch_realtime_data(station_codes, variable)
        if df.empty:
            return pd.DataFrame(columns=["station_code", "year", "month", "value"])

        df = df[df["fecha"].dt.year.between(year_start, year_end)]
        df["year"] = df["fecha"].dt.year
        df["month"] = df["fecha"].dt.month

        agg = "mean" if "TEMP" in variable.upper() else "sum"
        monthly = (
            df.groupby(["station_code", "year", "month"])["value"]
            .agg(agg)
            .reset_index()
        )
        return monthly

    # ------------------------------------------------------------------
    # Public — catálogo completo de estaciones (para discover)
    # ------------------------------------------------------------------

    def discover_resource_ids(self) -> dict[str, str]:
        """Retorna los dataset IDs hardcodeados (datos.gov.co es Socrata, no CKAN)."""
        return {
            "normals":     _DS_NORMALS,
            "t_min":       _DS_TMIN,
            "t_max":       _DS_TMAX,
            "t_amb":       _DS_TAMB,
            "precip":      _DS_PRECIP,
            "all_sensors": _DS_ALL,
        }

    # ------------------------------------------------------------------
    # Internal HTTP — Socrata SoQL
    # ------------------------------------------------------------------

    def _socrata_get(
        self,
        dataset_id: str,
        where: str = "",
        select: str = "",
        q: str = "",
        order: str = "",
        limit: int = _PAGE_SIZE,
        offset: int = 0,
    ) -> list[dict]:
        url = f"{_SOCRATA_BASE}/{dataset_id}.json"
        params: dict = {"$limit": limit, "$offset": offset}
        if where:
            params["$where"] = where
        if select:
            params["$select"] = select
        if q:
            params["$q"] = q
        if order:
            params["$order"] = order
        return self._get(url, params)

    def _socrata_get_all(
        self,
        dataset_id: str,
        where: str = "",
        select: str = "",
        order: str = "",
    ) -> list[dict]:
        """Descarga paginada completa con paralelismo cuando hay muchas páginas."""
        # Primera página para saber cuántos registros hay
        first = self._socrata_get(dataset_id, where=where, select=select,
                                  order=order, limit=_PAGE_SIZE, offset=0)
        if len(first) < _PAGE_SIZE:
            return first

        # Hay más páginas — descargar en paralelo (offsets adicionales)
        all_rows = list(first)
        offsets = [_PAGE_SIZE * i for i in range(1, 20)]  # máx 20 páginas = 20k reg

        def _page(off: int) -> list[dict]:
            return self._socrata_get(dataset_id, where=where, select=select,
                                     order=order, limit=_PAGE_SIZE, offset=off)

        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {ex.submit(_page, off): off for off in offsets}
            for f in concurrent.futures.as_completed(futures):
                batch = f.result()
                all_rows.extend(batch)
                if len(batch) < _PAGE_SIZE:
                    # Cancelar futuros pendientes — no hay más datos
                    for pending in futures:
                        pending.cancel()
                    break

        return all_rows

    def _get(self, url: str, params: dict) -> list[dict]:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                r = self._session.get(url, params=params, timeout=self._timeout)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as exc:
                if attempt == _MAX_RETRIES:
                    raise
                wait = _BACKOFF_BASE ** attempt
                log.warning(f"Intento {attempt} falló ({exc}), reintentando en {wait}s")
                time.sleep(wait)
        return []

    @staticmethod
    def _parse_normals_station(row: dict) -> Station:
        return Station(
            code=str(row["c_digo"]).strip(),
            name=str(row.get("estaci_n", "")).strip(),
            lat=float(row["latitud"]),
            lon=float(row["longitud"]),
            elevation_m=float(row.get("altitud_m") or 0),
            category=str(row.get("categoria", "")).strip(),
            status="ACTIVA",
            department=str(row.get("departamento", "")).strip(),
            municipality=str(row.get("municipio", "")).strip(),
        )
