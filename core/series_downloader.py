"""
Descarga de series históricas de estaciones IDEAM desde datos.gov.co (Socrata).

Variables objetivo:
  - Temperatura máxima   (TMX_CON convencional / cualquier T máx automática)
  - Temperatura mínima   (TMN_CON convencional / cualquier T mín automática)
  - Temperatura media    (TSSM_CON / TSTG_CON convencional / T media automática)
  - Precipitación diaria (PTPM_CON / PTPG_CON / PT_AUT)
  - Precipitación 10 min (PT_AUT_10 / PT_AUT_2) — si existe
  - Caudal diario        (Q_MEDIA_D / CAUDAL_H)

Estrategia por estación:
  1. Consulta cada dataset Socrata filtrando por codigoestacion
  2. Filtra filas por descripción de sensor (keywords)
  3. Pagina hasta agotar registros (limit=1000, offset)
  4. Une resultados de distintos datasets, deduplica por (sensor, fecha)
  5. Guarda CSV por estación + Excel consolidado (pivot fecha × estación)
"""
from __future__ import annotations

import concurrent.futures
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests

from ..utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constantes HTTP
# ---------------------------------------------------------------------------
_BASE      = "https://www.datos.gov.co/resource"
_PAGE      = 1000
_MAX_PAGES = 500        # máx 500k registros por consulta
_WORKERS   = 6
_RETRY     = 3
_BACKOFF   = 2.0

# ---------------------------------------------------------------------------
# Definición de variables objetivo
# Cada entrada: dataset_id → lista de fragmentos que deben aparecer en
# descripcionsensor (case-insensitive) para aceptar la fila.
# ---------------------------------------------------------------------------
TARGET: dict[str, dict] = {
    "TMAX": {
        "label": "Temperatura máxima",
        "datasets": {
            # ccvq-rp9s = dataset específico T máx automáticas
            "ccvq-rp9s": ["temp"],
            # 57sv-p2fu = todas las variables; captura TMX_CON y automáticas
            "57sv-p2fu": ["temp max", "temperatura max", "tmx", "t max",
                          "temperatura máxima"],
        },
        # excluir T mínima, T media, T húmeda y T suelo
        "exclude": ["min", "media", "húmeda", "seca", "suelo", "rocio",
                    "punto de rocio", "bulbo"],
    },
    "TMIN": {
        "label": "Temperatura mínima",
        "datasets": {
            # afdg-3zpb = dataset específico T mín automáticas
            "afdg-3zpb": ["temp"],
            "57sv-p2fu": ["temp min", "temperatura min", "tmn", "t min",
                          "temperatura mínima"],
        },
        "exclude": ["max", "media", "húmeda", "seca", "suelo", "rocio",
                    "punto de rocio", "bulbo"],
    },
    "TMEDIA": {
        "label": "Temperatura media",
        "datasets": {
            # sbwg-7ju4 = T ambiente automáticas (proxy de T media)
            "sbwg-7ju4": ["temp"],
            "57sv-p2fu": ["temp seca", "temperatura seca", "tssm", "tstg",
                          "temp media", "temperatura media", "t media",
                          "temp aire 2"],
        },
        "exclude": ["min", "max", "húmeda", "suelo", "rocio",
                    "punto de rocio", "bulbo"],
    },
    "PRECIP_DIARIA": {
        "label": "Precipitación diaria",
        "datasets": {
            "s54a-sgyg": ["precipit"],
            "57sv-p2fu": ["precipit"],
        },
        "exclude": ["10 minuto", "2 minuto", "intensidad"],
    },
    "PRECIP_10MIN": {
        "label": "Precipitación 10 minutos",
        "datasets": {
            "57sv-p2fu": ["10 minuto", "acumulada 10", "2 minuto", "acumulada 2"],
        },
        "exclude": [],
    },
    "CAUDAL": {
        "label": "Caudal",
        "datasets": {
            "57sv-p2fu": ["caudal", "q_media", "q media"],
        },
        "exclude": [],
    },
}

# Columnas estandarizadas de salida
_COL_MAP = {
    "codigoestacion":    "codigo",
    "nombreestacion":    "nombre",
    "codigosensor":      "sensor_cod",
    "descripcionsensor": "sensor_desc",
    "fechaobservacion":  "fecha",
    "valorobservado":    "valor",
    "unidadmedida":      "unidad",
    "departamento":      "departamento",
    "municipio":         "municipio",
    "latitud":           "latitud",
    "longitud":          "longitud",
}
_COLS_OUT = list(_COL_MAP.values())


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(session: requests.Session, url: str, params: dict, timeout: int = 30) -> list[dict]:
    for attempt in range(1, _RETRY + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == _RETRY:
                raise
            time.sleep(_BACKOFF ** attempt)
    return []


# ---------------------------------------------------------------------------
# Filtro de sensor por keywords
# ---------------------------------------------------------------------------

def _match_sensor(desc: str, include: list[str], exclude: list[str]) -> bool:
    d = str(desc).lower()
    if any(kw in d for kw in exclude):
        return False
    return any(kw in d for kw in include)


# ---------------------------------------------------------------------------
# Descarga paginada de una (estación, dataset, variable)
# ---------------------------------------------------------------------------

def _fetch_pages(
    session: requests.Session,
    dataset_id: str,
    station_code: str,
    include_kws: list[str],
    exclude_kws: list[str],
    progress_cb: Callable[[str], None],
) -> pd.DataFrame:
    url = f"{_BASE}/{dataset_id}.json"
    all_rows: list[dict] = []

    # Probar código con y sin ceros iniciales
    for code in [station_code.zfill(10), station_code]:
        rows_this: list[dict] = []
        for page in range(_MAX_PAGES):
            params = {
                "$where":  f"codigoestacion='{code}'",
                "$limit":  _PAGE,
                "$offset": page * _PAGE,
                "$order":  "fechaobservacion ASC",
            }
            try:
                batch = _get(session, url, params)
            except Exception as exc:
                log.debug(f"  {dataset_id}/{code} p{page}: {exc}")
                break

            if not batch:
                break

            # Filtrar por sensor antes de acumular
            for row in batch:
                desc = row.get("descripcionsensor", "") or ""
                if _match_sensor(desc, include_kws, exclude_kws):
                    rows_this.append(row)

            if len(batch) < _PAGE:
                break

            if page % 10 == 9:
                progress_cb(f"  {station_code}/{dataset_id}: {len(rows_this):,} reg (pág {page+1})")

        if rows_this:
            all_rows = rows_this
            break   # encontró datos con este formato de código

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})
    df["fecha"] = pd.to_datetime(df.get("fecha"), errors="coerce")
    df["valor"] = pd.to_numeric(df.get("valor"), errors="coerce")
    if "codigo" in df.columns:
        df["codigo"] = df["codigo"].str.lstrip("0")

    for col in _COLS_OUT:
        if col not in df.columns:
            df[col] = None

    return df[_COLS_OUT].copy()


# ---------------------------------------------------------------------------
# Descarga de una estación — todas las variables objetivo
# ---------------------------------------------------------------------------

@dataclass
class StationResult:
    code: str
    name: str = ""
    records_by_var: dict = field(default_factory=dict)  # var -> n_records
    csv_path: str = ""
    error: str = ""

    @property
    def total_records(self) -> int:
        return sum(self.records_by_var.values())


def _download_station(
    session: requests.Session,
    code: str,
    variables: list[str],
    var_dirs: dict[str, Path],      # var -> carpeta de salida
    progress_cb: Callable[[str], None],
) -> StationResult:
    """
    Descarga todas las variables de una estación y guarda un CSV por variable
    en la carpeta correspondiente: <output>/<VARIABLE>/<codigo>.csv
    """
    dfs_var: dict[str, pd.DataFrame] = {}

    for var in variables:
        cfg_var = TARGET[var]
        inc_kws: list[str] = []
        for kws in cfg_var["datasets"].values():
            inc_kws.extend(kws)
        inc_kws = list(set(inc_kws))
        exc_kws = cfg_var["exclude"]

        parts: list[pd.DataFrame] = []
        for ds_id in cfg_var["datasets"]:
            try:
                df = _fetch_pages(session, ds_id, code, inc_kws, exc_kws, progress_cb)
                if not df.empty:
                    df["variable"] = var
                    df["dataset"]  = ds_id
                    parts.append(df)
            except Exception as exc:
                log.debug(f"  {code}/{var}/{ds_id}: {exc}")

        if parts:
            merged = pd.concat(parts, ignore_index=True)
            merged = merged.drop_duplicates(subset=["sensor_cod", "fecha"])
            merged = merged.sort_values("fecha").reset_index(drop=True)
            dfs_var[var] = merged

            # Guardar CSV: <output>/<VARIABLE>/<codigo>.csv
            csv_var = var_dirs[var] / f"{code}.csv"
            merged.to_csv(csv_var, index=False, encoding="utf-8-sig")

    if not dfs_var:
        return StationResult(code=code, error="Sin datos en Socrata")

    # Nombre de la estación
    name = ""
    for df in dfs_var.values():
        col = df["nombre"].dropna()
        if not col.empty:
            name = col.iloc[0]
            break

    records_by_var = {var: len(df) for var, df in dfs_var.items()}
    return StationResult(
        code=code,
        name=str(name),
        records_by_var=records_by_var,
    )


# ---------------------------------------------------------------------------
# Clase pública
# ---------------------------------------------------------------------------

class SeriesDownloader:
    """
    Descarga series históricas de estaciones IDEAM para variables específicas.

    Parámetros
    ----------
    output_dir : str | Path
        Carpeta raíz de salida.
    variables : list[str]
        Subconjunto de ['TMAX','TMIN','PRECIP_DIARIA','PRECIP_10MIN','CAUDAL'].
        Por defecto todas menos PRECIP_10MIN.
    max_workers : int
        Hilos paralelos entre estaciones.
    app_token : str
        Socrata application token (evita throttling, registro gratuito).
    progress_cb : callable
        Función que recibe mensajes de progreso (str).
    """

    def __init__(
        self,
        output_dir: str | Path,
        variables: list[str] | None = None,
        max_workers: int = _WORKERS,
        app_token: str = "",
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.output_dir  = Path(output_dir)
        self.variables   = variables or ["TMAX", "TMIN", "PRECIP_DIARIA", "CAUDAL"]
        self.max_workers = max_workers
        self.progress_cb = progress_cb or (lambda m: log.info(m))

        # Validar variables
        invalid = set(self.variables) - set(TARGET)
        if invalid:
            raise ValueError(f"Variables no reconocidas: {invalid}. "
                             f"Válidas: {list(TARGET)}")

        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        if app_token:
            self._session.headers["X-App-Token"] = app_token

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def download(self, station_codes: list[str]) -> list[StationResult]:
        """
        Descarga todas las estaciones en paralelo.
        Estructura de salida:
          <output_dir>/
            TMAX/           ← una carpeta por variable
              15085020.csv
              25027750.csv
              ...
            TMIN/
              ...
            PRECIP_DIARIA/
              ...
            series_consolidadas.xlsx
            resumen_descarga.csv
        """
        # Crear carpetas por variable
        var_dirs: dict[str, Path] = {}
        for var in self.variables:
            d = self.output_dir / var
            d.mkdir(parents=True, exist_ok=True)
            var_dirs[var] = d

        labels = [f"{v} ({TARGET[v]['label']})" for v in self.variables]
        self.progress_cb(
            f"Variables:\n" + "\n".join(f"  · {l}" for l in labels) +
            f"\nEstaciones: {len(station_codes)} | Hilos: {self.max_workers}"
        )

        results: list[StationResult] = []
        done = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {
                ex.submit(
                    _download_station,
                    self._session, code, self.variables, var_dirs, self.progress_cb
                ): code
                for code in station_codes
            }
            for future in concurrent.futures.as_completed(futures):
                code = futures[future]
                done += 1
                try:
                    res = future.result()
                    results.append(res)
                    resumen = " | ".join(
                        f"{v}: {n:,}" for v, n in res.records_by_var.items() if n > 0
                    )
                    self.progress_cb(
                        f"[{done}/{len(station_codes)}] {code} {res.name[:25]}"
                        + (f" → {resumen}" if resumen else " → sin datos")
                    )
                except Exception as exc:
                    results.append(StationResult(code=code, error=str(exc)))
                    log.warning(f"Error {code}: {exc}")

        self._export_consolidated(var_dirs)
        self._export_summary(results)
        return results

    # ------------------------------------------------------------------
    # Exportación
    # ------------------------------------------------------------------

    def _export_consolidated(self, var_dirs: dict[str, Path]) -> None:
        """
        Excel consolidado: una hoja por variable.
        Lee los CSVs de cada carpeta de variable y hace pivot fecha × estación.
        """
        excel_path = self.output_dir / "series_consolidadas.xlsx"
        try:
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                for var, vdir in var_dirs.items():
                    csvs = list(vdir.glob("*.csv"))
                    if not csvs:
                        continue

                    dfs = []
                    for csv in csvs:
                        try:
                            df = pd.read_csv(csv, parse_dates=["fecha"])
                            dfs.append(df)
                        except Exception:
                            pass

                    if not dfs:
                        continue

                    full = pd.concat(dfs, ignore_index=True)
                    pivot = (
                        full.groupby(["fecha", "codigo"])["valor"]
                        .mean()
                        .unstack("codigo")
                        .sort_index()
                    )
                    sheet = TARGET[var]["label"][:31]
                    pivot.to_excel(writer, sheet_name=sheet)

            self.progress_cb(f"Excel consolidado: {excel_path}")
        except Exception as exc:
            log.warning(f"Error exportando Excel: {exc}")

    def _export_summary(self, results: list[StationResult]) -> None:
        rows = []
        for r in results:
            row = {"codigo": r.code, "nombre": r.name, "error": r.error}
            for var in self.variables:
                row[f"n_{var.lower()}"] = r.records_by_var.get(var, 0)
            row["total"] = r.total_records
            row["csv"]   = r.csv_path
            rows.append(row)

        df = pd.DataFrame(rows)
        out = self.output_dir / "resumen_descarga.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")

        ok    = sum(1 for r in results if r.total_records > 0)
        total = sum(r.total_records for r in results)
        self.progress_cb(
            f"\nResumen final:\n"
            f"  Estaciones con datos : {ok} / {len(results)}\n"
            f"  Registros totales    : {total:,}\n"
            f"  Carpeta salida       : {self.output_dir}"
        )


# ---------------------------------------------------------------------------
# Variables disponibles (para la UI)
# ---------------------------------------------------------------------------

AVAILABLE_VARIABLES = {k: v["label"] for k, v in TARGET.items()}
