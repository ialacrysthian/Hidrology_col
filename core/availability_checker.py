"""
Verificación de disponibilidad de datos en Socrata para una lista de estaciones.

Por cada estación consulta cada dataset con:
  SELECT descripcionsensor, count(*), min(fechaobservacion), max(fechaobservacion)
  WHERE codigoestacion = '<code>'
  GROUP BY descripcionsensor

Retorna qué variables tiene cada estación, cuántos registros y qué período.
Se ejecuta en paralelo para procesar muchas estaciones rápido.
"""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd
import requests

from .series_downloader import TARGET, _match_sensor
from ..utils.logger import get_logger

log = get_logger(__name__)

_BASE    = "https://www.datos.gov.co/resource"
_RETRY   = 2
_BACKOFF = 1.5
_WORKERS = 10   # más hilos porque son queries livianas (count)
_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Resultado por estación
# ---------------------------------------------------------------------------

@dataclass
class VarAvailability:
    variable:   str
    label:      str
    n_records:  int
    fecha_ini:  str   # "YYYY-MM-DD" o ""
    fecha_fin:  str
    datasets:   list[str] = field(default_factory=list)


@dataclass
class StationAvailability:
    code:      str
    name:      str = ""
    has_data:  bool = False
    variables: dict[str, VarAvailability] = field(default_factory=dict)
    error:     str = ""

    def summary_line(self) -> str:
        if not self.has_data:
            return "sin datos en Socrata"
        parts = []
        for var, av in self.variables.items():
            parts.append(f"{var}:{av.n_records:,} ({av.fecha_ini[:7]}→{av.fecha_fin[:7]})")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(session: requests.Session, url: str, params: dict) -> list[dict]:
    for attempt in range(1, _RETRY + 1):
        try:
            r = session.get(url, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == _RETRY:
                log.debug(f"  {url}: {exc}")
                return []
            time.sleep(_BACKOFF ** attempt)
    return []


# ---------------------------------------------------------------------------
# Consulta de disponibilidad de una estación en un dataset
# ---------------------------------------------------------------------------

def _check_dataset(
    session: requests.Session,
    dataset_id: str,
    station_code: str,
) -> list[dict]:
    """
    Retorna lista de filas con:
      descripcionsensor, n_records, fecha_ini, fecha_fin
    para la estación dada.
    """
    url = f"{_BASE}/{dataset_id}.json"

    for code in [station_code.zfill(10), station_code]:
        params = {
            "$select": ("descripcionsensor,"
                        "count(*) as n_records,"
                        "min(fechaobservacion) as fecha_ini,"
                        "max(fechaobservacion) as fecha_fin"),
            "$where":  f"codigoestacion='{code}'",
            "$group":  "descripcionsensor",
            "$limit":  200,
        }
        rows = _get(session, url, params)
        if rows:
            for r in rows:
                r["_dataset"] = dataset_id
                r["_code_used"] = code
            return rows

    return []


# ---------------------------------------------------------------------------
# Verificación de una estación completa
# ---------------------------------------------------------------------------

def _check_station(
    session: requests.Session,
    code: str,
    variables: list[str],
) -> StationAvailability:
    """
    Consulta todos los datasets relevantes para las variables pedidas
    y agrupa por variable TARGET.
    """
    # Datasets únicos a consultar
    datasets_needed: set[str] = set()
    for var in variables:
        datasets_needed.update(TARGET[var]["datasets"].keys())

    # Consultar en paralelo los datasets (hilos ligeros dentro del hilo de estación)
    all_rows: list[dict] = []
    for ds_id in datasets_needed:
        rows = _check_dataset(session, ds_id, code)
        all_rows.extend(rows)

    if not all_rows:
        return StationAvailability(code=code, has_data=False)

    # Clasificar filas por variable TARGET
    var_data: dict[str, dict] = {}   # var -> {n, ini, fin, datasets}

    for row in all_rows:
        desc     = str(row.get("descripcionsensor") or "")
        n        = int(row.get("n_records", 0) or 0)
        ini      = str(row.get("fecha_ini", "") or "")[:10]
        fin      = str(row.get("fecha_fin", "") or "")[:10]
        ds_id    = row.get("_dataset", "")

        for var in variables:
            cfg     = TARGET[var]
            inc_kws = [kw for kws in cfg["datasets"].values() for kw in kws]
            exc_kws = cfg["exclude"]

            if not _match_sensor(desc, inc_kws, exc_kws):
                continue

            if var not in var_data:
                var_data[var] = {"n": 0, "ini": ini, "fin": fin, "datasets": set()}

            var_data[var]["n"] += n
            var_data[var]["datasets"].add(ds_id)
            # ampliar rango de fechas
            if ini and (not var_data[var]["ini"] or ini < var_data[var]["ini"]):
                var_data[var]["ini"] = ini
            if fin and fin > var_data[var]["fin"]:
                var_data[var]["fin"] = fin

    if not var_data:
        return StationAvailability(code=code, has_data=False)

    variables_result = {
        var: VarAvailability(
            variable  = var,
            label     = TARGET[var]["label"],
            n_records = info["n"],
            fecha_ini = info["ini"],
            fecha_fin = info["fin"],
            datasets  = list(info["datasets"]),
        )
        for var, info in var_data.items()
    }

    return StationAvailability(
        code      = code,
        has_data  = True,
        variables = variables_result,
    )


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class AvailabilityChecker:
    """
    Verifica disponibilidad de datos en Socrata para una lista de estaciones.

    Parámetros
    ----------
    variables : list[str]
        Subconjunto de TARGET keys a verificar.
    max_workers : int
        Hilos paralelos (queries de count son ligeras, se pueden usar muchos).
    app_token : str
        Socrata application token.
    progress_cb : callable
        Función que recibe mensajes de progreso (str).
    """

    def __init__(
        self,
        variables: list[str] | None = None,
        max_workers: int = _WORKERS,
        app_token: str = "",
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.variables   = variables or list(TARGET.keys())
        self.max_workers = max_workers
        self.progress_cb = progress_cb or (lambda m: log.info(m))

        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        if app_token:
            self._session.headers["X-App-Token"] = app_token

    def check(self, station_codes: list[str]) -> list[StationAvailability]:
        """
        Verifica disponibilidad para todas las estaciones en paralelo.
        Retorna lista de StationAvailability ordenada por código.
        """
        results: list[StationAvailability] = []
        done = 0
        total = len(station_codes)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {
                ex.submit(_check_station, self._session, code, self.variables): code
                for code in station_codes
            }
            for future in concurrent.futures.as_completed(futures):
                code = futures[future]
                done += 1
                try:
                    av = future.result()
                    results.append(av)
                    if done % 5 == 0 or done == total:
                        n_ok = sum(1 for r in results if r.has_data)
                        self.progress_cb(
                            f"Verificando disponibilidad... [{done}/{total}] "
                            f"con datos: {n_ok}"
                        )
                except Exception as exc:
                    results.append(StationAvailability(code=code, error=str(exc)))

        return sorted(results, key=lambda r: r.code)

    def summary(self, results: list[StationAvailability]) -> pd.DataFrame:
        """
        Genera DataFrame resumen:
          codigo | con_datos | TMAX_n | TMAX_ini | TMAX_fin | TMIN_n | ...
        """
        rows = []
        for av in results:
            row: dict = {"codigo": av.code, "con_datos": av.has_data}
            for var in self.variables:
                v = av.variables.get(var)
                row[f"{var}_n"]   = v.n_records if v else 0
                row[f"{var}_ini"] = v.fecha_ini if v else ""
                row[f"{var}_fin"] = v.fecha_fin if v else ""
            rows.append(row)
        return pd.DataFrame(rows)

    def global_summary(self, results: list[StationAvailability]) -> dict:
        """
        Resumen global por variable:
          {var: {"n_stations": X, "total_records": Y, "fecha_ini": Z, "fecha_fin": W}}
        """
        out: dict = {}
        for var in self.variables:
            avs = [av.variables[var] for av in results if var in av.variables]
            if not avs:
                out[var] = {"n_stations": 0, "total_records": 0,
                            "fecha_ini": "", "fecha_fin": ""}
                continue
            fechas_ini = [a.fecha_ini for a in avs if a.fecha_ini]
            fechas_fin = [a.fecha_fin for a in avs if a.fecha_fin]
            out[var] = {
                "n_stations":    len(avs),
                "total_records": sum(a.n_records for a in avs),
                "fecha_ini":     min(fechas_ini) if fechas_ini else "",
                "fecha_fin":     max(fechas_fin) if fechas_fin else "",
            }
        return out
