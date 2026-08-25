from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import geopandas as gpd
import matplotlib
try:
    # Backend sin GUI: las figuras se generan en QgsTask (hilo secundario);
    # el backend Qt no es seguro fuera del hilo principal.
    matplotlib.use("Agg")
except Exception:
    pass
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from scipy.optimize import curve_fit
from scipy.spatial import cKDTree

try:
    from pysheds.grid import Grid
except ImportError:  # pragma: no cover
    Grid = None

from ..utils.logger import get_logger
from .frequency import fit_gev, gev_quantile

log = get_logger(__name__)


def _alternating_blocks(depths: np.ndarray) -> list[float]:
    """Reordena láminas de bloque en el patrón de bloques alternados (SCS):
    el mayor al centro, los siguientes alternan a cada lado. Cada valor se
    coloca UNA sola vez, de modo que sum(resultado) == sum(depths).
    """
    ordered: list[float] = []
    for i, depth in enumerate(sorted(depths, reverse=True)):
        if i % 2 == 0:
            ordered.append(depth)
        else:
            ordered.insert(0, depth)
    return ordered


def basin_mean_precipitation(raster_path: str) -> float:
    """Precipitación media de diseño de la cuenca (mm): media aritmética del
    raster de lámina Tr ya interpolado por IDW y recortado (paso de lluvia).

    Es la 'lámina interpolada' que alimenta el hietograma de bloques
    alternados del cálculo distribuido de Tc (P24h de diseño de la cuenca).
    """
    with rasterio.open(raster_path) as src:
        data = src.read(1, masked=True).astype(float)
    valid = data.compressed() if hasattr(data, "compressed") else data[np.isfinite(data)]
    if valid.size == 0:
        raise RuntimeError(f"El raster de precipitación no tiene celdas válidas: {raster_path}")
    return float(np.nanmean(valid))


# ------------------------------------------------------------
# Result objects
# ------------------------------------------------------------

@dataclass
class IDFResult:
    station_path: str
    gev_params: dict[str, float]
    idf_params: dict[str, float]
    idf_table: pd.DataFrame
    idf_plot: str
    storm_plot: str
    summary_csv: str


@dataclass
class StormResult:
    moments: pd.DataFrame
    peak_intensity_mm_h: float
    total_depth_mm: float
    dt_min: int
    plot_path: str


@dataclass
class TcResult:
    slope_raster: str
    depth_raster: str
    velocity_raster: str
    cell_time_raster: str
    accumulated_time_raster: str
    slowest_path_raster: str
    convergence_csv: str
    convergence_plot: str
    intensity_raster: str | None
    intensity_plot: str
    hyetogram_csv: str | None
    tc_seconds: float
    tc_minutes: float
    tc_hours: float
    iterations: int
    final_intensity_mm_h: float


# ------------------------------------------------------------
# IDF and storm generation
# ------------------------------------------------------------

class IDFStation:
    DURATIONS_MIN = np.array([5, 10, 15, 30, 60, 120, 360, 720, 1440], dtype=float)

    def __init__(
        self,
        file_path: str,
        output_dir: str,
        min_years: int = 20,
        beta: float = 0.40,
        return_period: float = 2.0,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.output_dir = Path(output_dir)
        self.min_years = min_years
        self.beta = beta
        self.return_period = return_period
        self._cb = progress_cb or log.info
        os.makedirs(self.output_dir, exist_ok=True)

    def process(self) -> IDFResult:
        self._cb(f"Cargando estación: {self.file_path}")
        series = self._load_precip_series(self.file_path)
        annual_max = self._annual_maxima(series)
        if len(annual_max) < self.min_years:
            raise RuntimeError(
                f"Se requieren al menos {self.min_years} años de datos válidos. "
                f"Se encontraron {len(annual_max)} años."
            )

        gev_params = self._fit_gev(annual_max)
        p24 = self._gev_quantile(self.return_period, gev_params)

        self._cb(f"P24({self.return_period} años) = {p24:.3f} mm")
        durations = self.DURATIONS_MIN
        intensities = self._disaggregate_intensities(p24, durations)

        a, b, c = self._fit_idf(durations, intensities)
        idf_table = pd.DataFrame({
            "duration_min": durations,
            "intensity_mm_h": intensities,
            "fitted_mm_h": self._idf_model(durations, a, b, c),
        })

        idf_plot = str(self.output_dir / f"{self.file_path.stem}_idf.png")
        self._plot_idf(durations, intensities, a, b, c, idf_plot)

        storm_plot = str(self.output_dir / f"{self.file_path.stem}_storm.png")
        summary_csv = str(self.output_dir / f"{self.file_path.stem}_idf_summary.csv")

        idf_table.to_csv(summary_csv, index=False, encoding="utf-8-sig")
        self._cb(f"Tabla IDF guardada en: {summary_csv}")

        return IDFResult(
            station_path=str(self.file_path),
            gev_params={"shape": float(gev_params[0]), "loc": float(gev_params[1]), "scale": float(gev_params[2]), "p24": float(p24)},
            idf_params={"a": float(a), "b": float(b), "c": float(c)},
            idf_table=idf_table,
            idf_plot=idf_plot,
            storm_plot=storm_plot,
            summary_csv=summary_csv,
        )

    def build_design_storm(self, a: float, b: float, c: float, tc_min: float, dt_min: int = 5) -> StormResult:
        durations = np.arange(1, max(1, int(np.ceil(tc_min / dt_min))) + 1, dtype=float) * dt_min
        cumulative_depth = self._idf_model(durations, a, b, c) * (durations / 60.0)
        block_depth = np.diff(np.concatenate([[0.0], cumulative_depth]))
        ordered_depth = _alternating_blocks(block_depth)
        intensity = np.array(ordered_depth) / (dt_min / 60.0)
        peak_intensity = float(np.nanmax(intensity))
        total_depth = float(np.nansum(ordered_depth))

        moments = pd.DataFrame({
            "block": np.arange(1, len(ordered_depth) + 1),
            "duration_min": np.full(len(ordered_depth), dt_min, dtype=float),
            "depth_mm": ordered_depth,
            "intensity_mm_h": intensity,
        })

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(moments["block"], moments["intensity_mm_h"], color="#4c72b0", edgecolor="#2a4d75")
        ax.set_xlabel("Bloque")
        ax.set_ylabel("Intensidad (mm/h)")
        ax.set_title(f"Hietograma Alternado  Tc={tc_min:.1f} min")
        ax.grid(True, linestyle="--", alpha=0.35)
        fig.tight_layout()
        plot_path = str(self.output_dir / f"{self.file_path.stem}_hietograma_{int(tc_min)}min.png")
        fig.savefig(plot_path, dpi=150)
        plt.close(fig)

        return StormResult(
            moments=moments,
            peak_intensity_mm_h=peak_intensity,
            total_depth_mm=total_depth,
            dt_min=dt_min,
            plot_path=plot_path,
        )

    def _load_precip_series(self, path: Path) -> pd.Series:
        if path.suffix.lower() in {".xls", ".xlsx"}:
            raw = pd.read_excel(path, sheet_name=0)
        else:
            raw = pd.read_csv(path)

        if raw.empty:
            raise RuntimeError("El archivo de precipitación está vacío.")

        date_col, precip_col = self._find_date_precip_columns(raw)
        df = raw[[date_col, precip_col]].copy()
        df.columns = ["date", "precip"]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["precip"] = pd.to_numeric(df["precip"], errors="coerce")
        df = df.dropna(subset=["date", "precip"]).sort_values("date")
        if df.empty:
            raise RuntimeError("No se encontraron fechas o precipitación válidas.")
        series = pd.Series(df["precip"].values, index=df["date"], name="precip")
        return series

    def _find_date_precip_columns(self, df: pd.DataFrame) -> tuple[str, str]:
        names = [str(c).strip().lower() for c in df.columns]
        date_candidates = {"fecha", "date", "dia", "fecha_hora", "date_time"}
        precip_candidates = {"precipitacion", "precip", "ppt", "mm", "rain", "rainfall", "valor"}

        date_col = next((c for c, n in zip(df.columns, names) if n in date_candidates), None)
        precip_col = next((c for c, n in zip(df.columns, names) if n in precip_candidates), None)
        if date_col is not None and precip_col is not None:
            return date_col, precip_col

        # Intentar adivinar encabezados dentro de las primeras 10 filas
        header_guess = self._guess_header_row(df)
        if header_guess is not None:
            raw = pd.read_excel(self.file_path, sheet_name=0, header=header_guess)
            names = [str(c).strip().lower() for c in raw.columns]
            date_col = next((c for c, n in zip(raw.columns, names) if n in date_candidates), None)
            precip_col = next((c for c, n in zip(raw.columns, names) if n in precip_candidates), None)
            if date_col is not None and precip_col is not None:
                return date_col, precip_col

        if len(df.columns) >= 2:
            return df.columns[0], df.columns[1]

        raise RuntimeError("No se pudo identificar las columnas de fecha y precipitación.")

    @staticmethod
    def _guess_header_row(df: pd.DataFrame) -> int | None:
        text = df.astype(str).apply(lambda col: col.str.strip().str.lower())
        candidates = {
            "fecha", "date", "dia", "fecha_hora",
            "precipitacion", "precip", "ppt", "mm", "rain", "rainfall", "valor",
        }
        for row in range(min(10, len(text))):
            row_values = set(text.iloc[row].tolist())
            if row_values & candidates:
                return row
        return None

    def _annual_maxima(self, series: pd.Series) -> pd.Series:
        years = series.index.year
        annual = series.groupby(years).max()
        return annual.dropna()

    def _fit_gev(self, annual_max: pd.Series) -> tuple[float, float, float]:
        return fit_gev(annual_max.values)

    def _gev_quantile(self, return_period: float, params: tuple[float, float, float]) -> float:
        return gev_quantile(params, return_period)

    def _disaggregate_intensities(self, p24: float, durations: np.ndarray) -> np.ndarray:
        depths = p24 * np.power(durations / 1440.0, self.beta)
        return np.divide(depths, durations / 60.0, out=np.zeros_like(depths), where=durations > 0)

    @staticmethod
    def _idf_model(t: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
        return a / np.power(t + b, c)

    def _fit_idf(self, durations: np.ndarray, intensities: np.ndarray) -> tuple[float, float, float]:
        guess = [float(intensities[0] * durations[0] ** 0.5), 1.0, 0.8]
        bounds = ([1e-6, 0.0, 0.05], [np.inf, np.inf, 5.0])
        params, _ = curve_fit(
            self._idf_model,
            durations,
            intensities,
            p0=guess,
            bounds=bounds,
            maxfev=10000,
        )
        return float(params[0]), float(params[1]), float(params[2])

    def _plot_idf(self, durations: np.ndarray, intensities: np.ndarray, a: float, b: float, c: float, path: str) -> None:
        duration_line = np.logspace(np.log10(durations.min()), np.log10(durations.max()), 200)
        fitted = self._idf_model(duration_line, a, b, c)

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.scatter(durations, intensities, label="Puntos Koutsoyiannis", color="#1f77b4", zorder=5)
        ax.plot(duration_line, fitted, label=f"Ajuste IDF: a={a:.3f}, b={b:.3f}, c={c:.3f}", color="#ff7f0e")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Duración (min)")
        ax.set_ylabel("Intensidad (mm/h)")
        ax.set_title(f"Curva IDF {self.file_path.stem}")
        ax.grid(True, which="both", linestyle="--", alpha=0.4)
        ax.legend()
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)


# ------------------------------------------------------------
# Distributed Tc solving
# ------------------------------------------------------------

class DistributedTcCalculator:
    D8_MOVES = {
        1: (0, 1),
        2: (1, 1),
        4: (1, 0),
        8: (1, -1),
        16: (0, -1),
        32: (-1, -1),
        64: (-1, 0),
        128: (-1, 1),
    }

    def __init__(
        self,
        dem_path: str,
        flow_acc_path: str,
        manning_path: str,
        output_dir: str,
        k_width: float = 1.0,
        n_min: float = 0.03,
        min_slope: float = 1e-4,
        dt_minutes: int = 5,
        max_iter: int = 100,
        tol_seconds: float = 60.0,
        sustain_iterations: int = 10,
        intensity_points_path: str | None = None,
        intensity_field: str = "intensity_mm_h",
        idw_power: float = 2.0,
        idw_neighbours: int = 8,
        idw_search_radius: float | None = None,
        idf_params: tuple[float, float, float] | None = None,
        p24h_mm: float | None = None,
        beta: float = 0.40,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        if Grid is None:
            raise ImportError(
                "PySheds no está instalado. Instale pysheds para habilitar el cálculo distribuido de Tc."
            )

        self.dem_path = Path(dem_path)
        self.flow_acc_path = Path(flow_acc_path)
        self.manning_path = Path(manning_path)
        self.output_dir = Path(output_dir)
        self.k_width = float(k_width)
        self.n_min = float(n_min)
        self.min_slope = float(min_slope)
        self.dt_minutes = int(dt_minutes)
        self.max_iter = int(max_iter)
        # Tolerancia de convergencia del Tc (por defecto 60 s = 1 minuto).
        self.tol_seconds = float(tol_seconds)
        # La convergencia debe sostenerse esta cantidad de iteraciones
        # consecutivas (evita detenerse en un cruce casual / solución local).
        self.sustain_iterations = max(1, int(sustain_iterations))
        self.intensity_points_path = Path(intensity_points_path) if intensity_points_path else None
        self.intensity_field = intensity_field
        self.idw_power = float(idw_power)
        self.idw_neighbours = int(idw_neighbours)
        self.idw_search_radius = float(idw_search_radius) if idw_search_radius is not None else None
        # Curva IDF i = a/(t+b)^c para realimentar la intensidad con el Tc.
        self.idf_params = tuple(float(v) for v in idf_params) if idf_params else None
        # Lámina de diseño P24h (mm, ya interpolada espacialmente para la
        # cuenca — ver basin_mean_precipitation) + exponente de Koutsoyiannis
        # para desagregar y construir el hietograma de bloques alternados en
        # cada iteración. Tiene prioridad sobre idf_params si ambos se dan.
        self.p24h_mm = float(p24h_mm) if p24h_mm is not None else None
        self.beta = float(beta)
        self._cb = progress_cb or log.info
        os.makedirs(self.output_dir, exist_ok=True)

        self.dem_profile = None
        self.dem_data = None
        self.flow_acc = None
        self.manning = None
        self.cellsize = None
        self._flowdir_cache: np.ndarray | None = None
        self._flowacc_pysheds_cache: np.ndarray | None = None
        self._load_rasters()

    def solve(
        self,
        peak_intensity_mm_h: float | None = None,
        initial_tc_min: float | None = None,
        output_prefix: str = "tc",
        use_kirpich_initial: bool = True,
    ) -> TcResult:
        """Itera el Tc distribuido hasta que se estabiliza.

        Criterio de convergencia: |Tc_asumido − Tc_calculado| ≤ tol_seconds
        (1 min por defecto) sostenido durante ``sustain_iterations``
        iteraciones CONSECUTIVAS (10 por defecto) — un solo cruce por debajo
        de la tolerancia no basta; si en algún punto la diferencia vuelve a
        superar la tolerancia, la racha se reinicia. Esto evita detenerse en
        una coincidencia casual o una oscilación local.

        Si se construyó con ``p24h_mm`` (lámina de diseño de la cuenca), la
        intensidad de cada iteración se obtiene reconstruyendo el hietograma
        de bloques alternados para la duración = Tc de esa iteración, y
        tomando su bloque pico (mm/h) como lluvia de diseño uniforme para el
        cálculo hidráulico (profundidad/velocidad de Manning). El Tc inicial
        (semilla) determina la duración del PRIMER hietograma.
        """
        self._cb("Iniciando cálculo distribuido de Tc...")
        slope_path = self._write_slope_raster(output_prefix)
        convergence_rows = []
        streak = 0
        last_moments: pd.DataFrame | None = None

        intensity_raster = None
        intensity_raster_path = None   # ruta para el resultado (no se anula en el bucle)
        if self.p24h_mm is None and self.intensity_points_path is not None:
            intensity_raster = self._write_intensity_raster(output_prefix)
            intensity_raster_path = intensity_raster
            if peak_intensity_mm_h is None:
                peak_intensity_mm_h = float(np.nanmax(self._read_raster(intensity_raster)))

        if use_kirpich_initial:
            tc_seconds = self._estimate_tc_initial_kirpich()
            self._cb(f"Tc inicial Kirpich estimado = {tc_seconds:.2f} s ({tc_seconds/60.0:.2f} min)")
        elif initial_tc_min is not None:
            tc_seconds = initial_tc_min * 60.0
            self._cb(
                f"Tc inicial adoptado (morfometría) = {tc_seconds:.2f} s "
                f"({initial_tc_min:.2f} min) — semilla del primer hietograma"
            )
        else:
            tc_seconds = 15.0 * 60.0

        # Hietograma de bloques alternados para la duración inicial (semilla)
        if self.p24h_mm is not None and peak_intensity_mm_h is None:
            peak_intensity_mm_h, last_moments = self._build_alternating_block_storm(tc_seconds)
            self._cb(
                f"Hietograma inicial ({len(last_moments)} bloques de {self.dt_minutes} min, "
                f"Tc={tc_seconds/60.0:.1f} min): pico = {peak_intensity_mm_h:.2f} mm/h"
            )

        for iteration in range(1, self.max_iter + 1):
            self._cb(
                f"Iteración {iteration}: Tc asumido {tc_seconds:.2f} s "
                f"({tc_seconds/60.0:.2f} min), i={peak_intensity_mm_h} mm/h"
                if peak_intensity_mm_h is not None else
                f"Iteración {iteration}: Tc asumido {tc_seconds:.2f} s"
            )
            if peak_intensity_mm_h is None and intensity_raster is None:
                raise RuntimeError(
                    "Debe proveer una intensidad de diseño para iniciar la iteración: "
                    "p24h_mm (bloques alternados), idf_params (curva IDF), un raster "
                    "IDW de estaciones, o un valor fijo de peak_intensity_mm_h."
                )

            if intensity_raster is not None:
                self._cb("Usando intensidad interpolada por IDW celda a celda para el cálculo hidráulico.")

            depth_raster = self._write_depth_raster(peak_intensity_mm_h, output_prefix, intensity_raster)
            velocity_raster = self._write_velocity_raster(output_prefix)
            cell_time_raster = self._write_cell_time_raster(output_prefix)
            accumulated_raster = self._write_accumulated_time_raster(output_prefix)

            accumulated = self._read_raster(accumulated_raster)
            if not np.isfinite(accumulated).any():
                raise RuntimeError(
                    "El raster de tiempo acumulado no tiene celdas válidas. "
                    "Revise que el DEM, el raster de Manning y la acumulación "
                    "de flujo se solapen y tengan datos en la cuenca."
                )
            # "Línea de píxeles" de mayor acumulación: la celda con el tiempo
            # acumulado máximo define el Tc de esta iteración; su trayectoria
            # D8 hacia el outlet es la ruta crítica (slowest_path_raster).
            target_tc_seconds = float(np.nanmax(accumulated))
            delta_seconds = abs(target_tc_seconds - tc_seconds)
            tc_minutes = target_tc_seconds / 60.0

            if delta_seconds <= self.tol_seconds:
                streak += 1
            else:
                streak = 0

            self._cb(
                f"Tc calculado: {target_tc_seconds:.2f} s ({tc_minutes:.2f} min), "
                f"diferencia = {delta_seconds:.4f} s — racha de convergencia = "
                f"{streak}/{self.sustain_iterations}"
            )

            convergence_rows.append({
                "iteration": iteration,
                "tc_seconds": target_tc_seconds,
                "tc_minutes": tc_minutes,
                "intensity_mm_h": peak_intensity_mm_h,
                "delta_seconds": delta_seconds,
                "convergence_streak": streak,
            })

            if streak >= self.sustain_iterations:
                self._cb(
                    f"Convergencia sostenida durante {streak} iteraciones consecutivas "
                    f"(tolerancia {self.tol_seconds:.0f} s) — Tc estabilizado."
                )
                break
            if iteration >= self.max_iter:
                self._cb(
                    f"ADVERTENCIA: se alcanzó max_iter={self.max_iter} sin sostener "
                    f"la convergencia {self.sustain_iterations} iteraciones seguidas. "
                    f"El Tc final puede no estar plenamente estabilizado."
                )
                break

            tc_seconds = target_tc_seconds
            if self.p24h_mm is not None:
                # Reconstruir el hietograma de bloques alternados para la
                # NUEVA duración (= Tc calculado) y tomar su bloque pico.
                peak_intensity_mm_h, last_moments = self._build_alternating_block_storm(tc_seconds)
                self._cb(
                    f"Hietograma recalculado ({len(last_moments)} bloques, "
                    f"Tc={tc_seconds/60.0:.1f} min): pico = {peak_intensity_mm_h:.2f} mm/h"
                )
            elif self.idf_params is not None:
                # Realimentación IDF: la nueva duración (= Tc) fija la intensidad
                peak_intensity_mm_h = self._intensity_from_tc(tc_seconds)
                intensity_raster = None   # la IDF gobierna las siguientes pasadas
                self._cb(
                    f"Intensidad IDF para {tc_seconds/60.0:.1f} min: "
                    f"{peak_intensity_mm_h:.2f} mm/h"
                )
            # Sin IDF ni P24h la intensidad es fija: la siguiente pasada
            # reproduce el mismo Tc y el bucle converge en pocas iteraciones.

        convergence_csv = str(self.output_dir / f"{output_prefix}_tc_convergence.csv")
        convergence_df = pd.DataFrame(convergence_rows)
        convergence_df.to_csv(convergence_csv, index=False, encoding="utf-8-sig")

        convergence_plot = str(self.output_dir / f"{output_prefix}_tc_convergence.png")
        self._plot_convergence(convergence_df, convergence_plot)

        intensity_plot = str(self.output_dir / f"{output_prefix}_intensity_iteration.png")
        self._plot_intensity(convergence_df, intensity_plot)

        hyetogram_csv = None
        if last_moments is not None:
            hyetogram_csv = str(self.output_dir / f"{output_prefix}_hietograma_final.csv")
            last_moments.to_csv(hyetogram_csv, index=False, encoding="utf-8-sig")

        final_tc = convergence_rows[-1]["tc_seconds"]
        return TcResult(
            slope_raster=slope_path,
            depth_raster=depth_raster,
            velocity_raster=velocity_raster,
            cell_time_raster=cell_time_raster,
            accumulated_time_raster=accumulated_raster,
            slowest_path_raster=self._write_slowest_path_raster(accumulated, output_prefix),
            intensity_raster=intensity_raster_path,
            convergence_csv=convergence_csv,
            convergence_plot=convergence_plot,
            intensity_plot=intensity_plot,
            hyetogram_csv=hyetogram_csv,
            tc_seconds=final_tc,
            tc_minutes=final_tc / 60.0,
            tc_hours=final_tc / 3600.0,
            iterations=len(convergence_rows),
            final_intensity_mm_h=peak_intensity_mm_h,
        )

    def _load_rasters(self) -> None:
        with rasterio.open(self.dem_path) as src:
            self.dem_profile = src.profile.copy()
            dem = src.read(1, masked=True).astype(float)
            self.cellsize = float(abs(src.transform.a))
            self.dem_data = dem.filled(np.nan)

        # flow_acc se calcula con PYSHEDS (misma librería y mismo objeto de
        # flujo que flowdir) — NO se lee de flow_acc_path (típicamente un
        # producto de GRASS r.watershed). Ver _compute_flow_direction_and_
        # accumulation() para la justificación: mezclar acumulación de una
        # librería con direcciones de otra desalinea ~6% de las celdas.
        # flow_acc_path se conserva como parámetro por compatibilidad de
        # interfaz (otros productos del plugin, como drenajes.shp, sí usan
        # la acumulación de GRASS para delimitar canales — son productos
        # independientes que no necesitan compartir el mismo array).
        _, self.flow_acc = self._compute_flow_direction_and_accumulation()
        if self.flow_acc.shape != self.dem_data.shape:
            raise RuntimeError("La acumulación de flujo (pysheds) no coincide en tamaño con el DEM.")

        self.manning = self._read_raster(self.manning_path, target_profile=self.dem_profile)
        if self.manning.shape != self.dem_data.shape:
            raise RuntimeError("El raster de Manning debe coincidir en resolución y extensión con el DEM.")

    def _read_raster(
        self, path: Path, target_profile: dict | None = None, resampling: Resampling = Resampling.bilinear,
    ) -> np.ndarray:
        with rasterio.open(path) as src:
            data = src.read(1, masked=True).astype(float)
            if target_profile and (src.transform != target_profile["transform"] or src.width != target_profile["width"] or src.height != target_profile["height"]):
                dest = np.empty((target_profile["height"], target_profile["width"]), dtype=np.float32)
                rasterio.warp.reproject(
                    source=data,
                    destination=dest,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=target_profile["transform"],
                    dst_crs=target_profile["crs"],
                    resampling=resampling,
                    src_nodata=src.nodata,
                    dst_nodata=np.nan,
                )
                return np.where(np.isfinite(dest), dest, np.nan)
            return data.filled(np.nan) if hasattr(data, "filled") else data

    def _write_raster(self, array: np.ndarray, path: str, nodata: float = np.nan) -> str:
        profile = self.dem_profile.copy()
        profile.update(dtype=rasterio.float32, count=1, compress="lzw", nodata=nodata)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.where(np.isfinite(array), array, nodata).astype(np.float32), 1)
        return path

    def _write_slope_raster(self, prefix: str) -> str:
        slope_path = str(self.output_dir / f"{prefix}_slope.tif")
        slope = self._compute_slope(self.dem_data)
        # Celdas válidas se acotan a min_slope; fuera del DEM quedan NoData.
        # El NoData debe ser un valor imposible (-9999), NUNCA min_slope:
        # si coincidieran, las celdas planas válidas se enmascararían como
        # NaN al releer el raster y anularían rutas completas de flujo.
        slope = np.where(np.isfinite(slope), np.maximum(slope, self.min_slope), np.nan)
        return self._write_raster(slope, slope_path, nodata=-9999.0)

    def _compute_slope(self, dem: np.ndarray) -> np.ndarray:
        dy, dx = np.gradient(dem, self.cellsize, self.cellsize)
        slope = np.hypot(dx, dy)
        return slope

    def _write_depth_raster(self, intensity_mm_h: float | None, prefix: str, intensity_raster: str | None = None) -> str:
        depth_path = str(self.output_dir / f"{prefix}_depth.tif")
        if intensity_raster is not None:
            intensity = self._read_raster(intensity_raster)
        elif intensity_mm_h is not None:
            intensity = np.full(self.flow_acc.shape, intensity_mm_h, dtype=float)
        else:
            raise RuntimeError("No hay intensidad disponible para calcular la profundidad.")

        q = (intensity / 1000.0) * self.flow_acc / 3600.0 * self.cellsize ** 2
        w = self.k_width * np.sqrt(np.maximum(self.flow_acc * self.cellsize ** 2, 0.0))
        slope = self._read_raster(self.output_dir / f"{prefix}_slope.tif")
        n = np.where(np.isfinite(self.manning), np.maximum(self.manning, self.n_min), self.n_min)
        h = np.power((q * n) / np.maximum(w * np.sqrt(np.maximum(slope, self.min_slope)), 1e-12), 3.0 / 5.0)
        return self._write_raster(h, depth_path, nodata=np.nan)

    def _write_velocity_raster(self, prefix: str) -> str:
        velocity_path = str(self.output_dir / f"{prefix}_velocity.tif")
        depth = self._read_raster(self.output_dir / f"{prefix}_depth.tif")
        slope = self._read_raster(self.output_dir / f"{prefix}_slope.tif")
        n = np.where(np.isfinite(self.manning), np.maximum(self.manning, self.n_min), self.n_min)
        v = np.where(
            np.isfinite(depth) & (depth > 0) & np.isfinite(slope),
            (1.0 / n) * np.power(depth, 2.0 / 3.0) * np.sqrt(np.maximum(slope, self.min_slope)),
            np.nan,
        )
        return self._write_raster(v, velocity_path, nodata=np.nan)

    @staticmethod
    def _resolve_field(columns, requested: str) -> str:
        """Resuelve el nombre real del campo de intensidad.

        Los shapefiles truncan los nombres de campo a 10 caracteres
        ('intensity_mm_h' → 'intensity_'), así que se acepta el nombre exacto,
        el truncado y una coincidencia sin distinguir mayúsculas. Un GeoPackage
        conserva el nombre completo y coincide de forma exacta.
        """
        cols = list(columns)
        truncated = requested[:10]
        for candidate in (requested, truncated):
            if candidate in cols:
                return candidate
        lower = {str(c).lower(): c for c in cols}
        for candidate in (requested.lower(), truncated.lower()):
            if candidate in lower:
                return lower[candidate]
        raise RuntimeError(
            f"El campo de intensidad '{requested}' no existe en el archivo de "
            f"estaciones. Columnas disponibles: {', '.join(map(str, cols))}."
        )

    def _write_intensity_raster(self, prefix: str) -> str:
        if self.intensity_points_path is None:
            raise RuntimeError("No se proporcionó shapefile de estaciones para interpolación IDW.")

        intensity_path = str(self.output_dir / f"{prefix}_intensity.tif")
        points = gpd.read_file(self.intensity_points_path)
        field = self._resolve_field(points.columns, self.intensity_field)
        points = points.dropna(subset=[field]).copy()
        if points.empty:
            raise RuntimeError("El shapefile de estaciones no contiene valores de intensidad válidos.")

        with rasterio.open(self.dem_path) as src:
            transform = src.transform
            width = src.width
            height = src.height
            crs = src.crs

        if points.crs is not None and points.crs != crs:
            points = points.to_crs(crs)

        coords = np.vstack([points.geometry.x.values, points.geometry.y.values]).T
        values = points[field].astype(float).values
        if coords.shape[0] == 0:
            raise RuntimeError("No hay puntos de estación con coordenadas válidas para interpolación.")

        x_coords = transform.c + np.arange(width) * transform.a + transform.a / 2.0
        y_coords = transform.f + np.arange(height) * transform.e + transform.e / 2.0
        xs, ys = np.meshgrid(x_coords, y_coords)
        flat_xy = np.column_stack([xs.ravel(), ys.ravel()])

        k = min(self.idw_neighbours, coords.shape[0])
        tree = cKDTree(coords)
        dists, inds = tree.query(flat_xy, k=k, n_jobs=-1)
        if k == 1:
            dists = dists[:, None]
            inds = inds[:, None]

        weights = np.where(
            dists <= 0.0,
            1e12,
            1.0 / np.power(dists, self.idw_power),
        )
        if self.idw_search_radius is not None:
            weights = np.where(dists <= self.idw_search_radius, weights, 0.0)

        numerator = np.sum(weights * values[inds], axis=1)
        denominator = np.sum(weights, axis=1)
        intensity = numerator / np.where(denominator == 0.0, np.nan, denominator)
        intensity_grid = intensity.reshape((height, width))

        self._write_raster(intensity_grid, intensity_path, nodata=np.nan)
        self._cb(f"Raster de intensidad IDW guardado en: {intensity_path}")
        return intensity_path

    def _write_cell_time_raster(self, prefix: str) -> str:
        time_path = str(self.output_dir / f"{prefix}_cell_time.tif")
        velocity = self._read_raster(self.output_dir / f"{prefix}_velocity.tif")
        flowdir = self._compute_flow_direction()
        factors = self._travel_factors(flowdir)
        t = np.where(np.isfinite(velocity) & (velocity > 0), (self.cellsize / velocity) * factors, np.nan)
        return self._write_raster(t, time_path, nodata=np.nan)

    def _write_accumulated_time_raster(self, prefix: str) -> str:
        accumulated_path = str(self.output_dir / f"{prefix}_accumulated_time.tif")
        cell_time = self._read_raster(self.output_dir / f"{prefix}_cell_time.tif")
        flowdir = self._compute_flow_direction()
        accumulated = self._accumulate_time(cell_time, flowdir)
        return self._write_raster(accumulated, accumulated_path, nodata=np.nan)

    def _compute_flow_direction(self) -> np.ndarray:
        """Direcciones D8 (convención ESRI) — ver _compute_flow_direction_and_accumulation."""
        flowdir, _ = self._compute_flow_direction_and_accumulation()
        return flowdir

    def _compute_flow_direction_and_accumulation(self) -> tuple[np.ndarray, np.ndarray]:
        """Direcciones D8 Y acumulación de flujo, ambas de PYSHEDS, sobre el
        MISMO objeto de flujo — cacheadas.

        No se usa la acumulación de flow_acc_path (típicamente un producto
        de GRASS r.watershed) precisamente porque flowdir se calcula con
        pysheds: dos algoritmos de enrutamiento independientes sobre el
        mismo DEM NO garantizan producir el mismo árbol de flujo (fill de
        depresiones, resolución de zonas planas y desempate en divisorias
        difieren entre librerías). Mezclarlos rompe el invariante D8 básico
        —el área acumulada nunca puede disminuir aguas abajo— en una
        fracción no despreciable de las celdas (~6% verificado en una
        cuenca real), corrompiendo todo el cálculo aguas abajo en silencio.
        Calcular flow_acc con el MISMO flowdir que enruta el tiempo de
        viaje garantiza que ambos sean, por construcción, consistentes.

        Soporta ambas APIs de pysheds: la 'sgrid' moderna (>=0.3, los
        métodos reciben y devuelven Raster) y la antigua 0.2.x
        (data_name/out_name).
        """
        if self._flowdir_cache is not None and self._flowacc_pysheds_cache is not None:
            return self._flowdir_cache, self._flowacc_pysheds_cache
        try:
            # pysheds >= 0.3
            grid = Grid.from_raster(str(self.dem_path))
            dem = grid.read_raster(str(self.dem_path))
            filled = grid.fill_depressions(dem)
            inflated = grid.resolve_flats(filled)
            flowdir = grid.flowdir(inflated)
            acc = grid.accumulation(flowdir)
        except TypeError:
            # pysheds 0.2.x
            grid = Grid.from_raster(str(self.dem_path), data_name="dem")
            grid.fill_depressions("dem", out_name="filled")
            grid.flowdir(data="filled", out_name="flowdir")
            flowdir = grid.view("flowdir")
            grid.accumulation(data="flowdir", out_name="acc")
            acc = grid.view("acc")

        fd_arr = np.asarray(flowdir, dtype=float)
        self._flowdir_cache = np.where(np.isfinite(fd_arr), fd_arr, -1).astype(int)
        acc_arr = np.asarray(acc, dtype=float)
        self._flowacc_pysheds_cache = np.where(np.isfinite(acc_arr), acc_arr, np.nan)
        return self._flowdir_cache, self._flowacc_pysheds_cache

    @classmethod
    def _build_downstream(cls, flowdir: np.ndarray) -> np.ndarray:
        """Índice plano de la celda aguas abajo de cada celda (-1 si no hay).

        Valida fila Y columna por separado: con aritmética plana
        (idx + dr*cols + dc) una celda del borde este que fluye al este
        'saltaría' a la primera columna de la fila siguiente.
        """
        rows, cols = flowdir.shape
        downstream = np.full(rows * cols, -1, dtype=int)
        rr, cc = np.indices((rows, cols))
        for code, (dr, dc) in cls.D8_MOVES.items():
            mask = flowdir == code
            nr = rr + dr
            nc = cc + dc
            ok = mask & (nr >= 0) & (nr < rows) & (nc >= 0) & (nc < cols)
            downstream[rr[ok] * cols + cc[ok]] = nr[ok] * cols + nc[ok]
        return downstream

    @staticmethod
    def _travel_factors(flowdir: np.ndarray) -> np.ndarray:
        factors = np.full(flowdir.shape, np.nan, dtype=float)
        diag = {2, 8, 32, 128}
        ortho = {1, 4, 16, 64}
        for code in ortho:
            factors[flowdir == code] = 1.0
        for code in diag:
            factors[flowdir == code] = math.sqrt(2.0)
        factors[np.isin(flowdir, [-1, 0])] = 1.0
        return factors

    def _accumulate_time(self, cell_time: np.ndarray, flowdir: np.ndarray) -> np.ndarray:
        rows, cols = cell_time.shape
        flat_time = np.full(rows * cols, np.nan, dtype=float)
        flat_time[:] = cell_time.flatten()
        downstream = self._build_downstream(flowdir)

        accumulated = np.full_like(flat_time, np.nan)
        for cell in range(rows * cols):
            if not np.isnan(accumulated[cell]):
                continue
            path = []
            current = cell
            visited = set()
            while True:
                if current in visited:
                    accumulated[path] = np.nan
                    break
                visited.add(current)
                path.append(current)
                if not np.isfinite(flat_time[current]):
                    accumulated[path] = np.nan
                    break
                downstream_idx = downstream[current]
                if downstream_idx < 0 or downstream_idx >= rows * cols:
                    value = 0.0
                    for idx_path in reversed(path):
                        value += flat_time[idx_path]
                        accumulated[idx_path] = value
                    break
                if np.isfinite(accumulated[downstream_idx]):
                    value = accumulated[downstream_idx]
                    for idx_path in reversed(path):
                        value += flat_time[idx_path]
                        accumulated[idx_path] = value
                    break
                current = downstream_idx
                if current in path:
                    accumulated[path] = np.nan
                    break

        return accumulated.reshape(rows, cols)

    def _write_slowest_path_raster(self, accumulated: np.ndarray, prefix: str) -> str:
        path_path = str(self.output_dir / f"{prefix}_slowest_path.tif")
        max_idx = np.nanargmax(accumulated)
        rows, cols = accumulated.shape
        r_arr = np.zeros_like(accumulated, dtype=float)
        flowdir = self._compute_flow_direction()
        current = max_idx
        while True:
            r_arr[np.unravel_index(current, (rows, cols))] = 1.0
            code = flowdir.flat[current]
            if code not in self.D8_MOVES:
                break
            dr, dc = self.D8_MOVES[code]
            row, col = np.unravel_index(current, (rows, cols))
            next_row, next_col = row + dr, col + dc
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                break
            current = next_row * cols + next_col
            if r_arr.flat[current] == 1.0:
                break
        return self._write_raster(r_arr, path_path, nodata=0.0)

    def _plot_convergence(self, df: pd.DataFrame, path: str) -> None:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(df["iteration"], df["tc_seconds"], marker="o", color="#2c7fb8")
        ax.set_xlabel("Iteración")
        ax.set_ylabel("Tc (s)")
        ax.set_title("Convergencia de Tc")
        ax.grid(True, linestyle="--", alpha=0.4)
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

    def _plot_intensity(self, df: pd.DataFrame, path: str) -> None:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(df["iteration"], df["intensity_mm_h"], marker="o", color="#d95f0e")
        ax.set_xlabel("Iteración")
        ax.set_ylabel("Intensidad pico (mm/h)")
        ax.set_title("Intensidad de diseño por iteración")
        ax.grid(True, linestyle="--", alpha=0.4)
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

    def _longest_path_cache(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Longitud del camino de drenaje D8 desde cada celda hasta su
        salida, calculada de forma ITERATIVA y memoizada (una sola pasada,
        O(n) amortizado — NO se repite por iteración, a diferencia de
        _accumulate_time). Base compartida de _estimate_tc_initial_kirpich
        y extract_critical_path.

        Retorna (length_cache, downstream, flowdir).
        """
        flowdir = self._compute_flow_direction()
        rows, cols = flowdir.shape
        downstream = self._build_downstream(flowdir)

        length_cache = np.full(rows * cols, np.nan, dtype=float)

        def step_len(cell: int) -> float:
            code = int(flowdir.flat[cell])
            if code not in self.D8_MOVES:
                return 0.0
            dr, dc = self.D8_MOVES[code]
            return self.cellsize if abs(dr) + abs(dc) == 1 else self.cellsize * math.sqrt(2.0)

        valid_cells = np.where(np.isfinite(self.flow_acc.flatten()))[0]
        for start_cell in valid_cells:
            if not np.isnan(length_cache[start_cell]):
                continue
            # Descender recolectando el camino hasta celda resuelta/terminal
            path: list[int] = []
            on_path: set[int] = set()
            cell = int(start_cell)
            base = 0.0
            while True:
                if cell < 0 or cell >= rows * cols or cell in on_path:
                    base = 0.0
                    break
                if not np.isnan(length_cache[cell]):
                    base = length_cache[cell]
                    break
                nxt = int(downstream[cell])
                if nxt < 0 or nxt == cell:
                    length_cache[cell] = 0.0
                    base = 0.0
                    break
                path.append(cell)
                on_path.add(cell)
                cell = nxt
            # Deshacer el camino acumulando longitudes hacia arriba
            for c in reversed(path):
                base = base + step_len(c)
                length_cache[c] = base

        return length_cache, downstream, flowdir

    def extract_critical_path(self) -> list[tuple[int, int]]:
        """Ruta crítica (cabecera → salida) como lista ordenada de (fila,
        columna), sin recorrer toda la grilla en cada iteración: es la
        misma celda de mayor longitud D8 que usa el Tc inicial de Kirpich,
        seguida aguas abajo hasta la salida.

        Es puramente TOPOLÓGICA (no depende de la intensidad ni de la
        velocidad), por eso se calcula UNA sola vez y basta: a diferencia
        de _accumulate_time (recalculado cada iteración sobre ~14M celdas
        en una cuenca grande), esto habilita iterar después solo sobre los
        píxeles de la ruta — cientos o miles, no millones.
        """
        length_cache, downstream, _ = self._longest_path_cache()
        if np.all(np.isnan(length_cache)):
            raise RuntimeError("No se pudo determinar la ruta crítica (longitud de camino vacía).")
        start = int(np.nanargmax(length_cache))

        rows, cols = self.dem_data.shape
        ordered = [start]
        visited = {start}
        current = start
        while True:
            nxt = int(downstream[current])
            if nxt < 0 or nxt >= rows * cols or nxt in visited:
                break
            ordered.append(nxt)
            visited.add(nxt)
            current = nxt
        return [divmod(idx, cols) for idx in ordered]

    def _estimate_tc_initial_kirpich(self) -> float:
        length_cache, downstream, _ = self._longest_path_cache()
        if np.all(np.isnan(length_cache)):
            raise RuntimeError("No se pudo estimar la longitud del canal para Tc Kirpich.")

        start = int(np.nanargmax(length_cache))
        total_length = length_cache[start]
        if total_length <= 0.0:
            raise RuntimeError("La longitud del canal estimada es nula.")

        outlet = int(start)
        while True:
            next_cell = downstream[outlet]
            if next_cell < 0 or next_cell == outlet:
                break
            outlet = next_cell

        elevations = self.dem_data.flatten()
        elev_drop = float(elevations[start] - elevations[outlet])
        slope = max(elev_drop / max(total_length, self.cellsize), self.min_slope)
        tc_min = 0.01947 * total_length ** 0.77 * slope ** -0.385
        return tc_min * 60.0

    def _build_alternating_block_storm(self, tc_seconds: float) -> tuple[float, pd.DataFrame]:
        """Hietograma de bloques alternados para duración total = Tc.

        La lámina de cada sub-duración d (min) se obtiene con la ley de
        escala de Koutsoyiannis P(d) = P24h·(d/1440)^beta — válida para
        cualquier duración por construcción, sin necesidad de ajustar una
        curva IDF completa (P24h ya viene de la interpolación espacial de
        las estaciones, ver basin_mean_precipitation).

        Retorna (intensidad_pico_mm_h, tabla_de_bloques). La intensidad
        pico es la que se usa como lluvia de diseño (uniforme en la cuenca)
        para el cálculo hidráulico (Manning) de la iteración.
        """
        tc_min = max(tc_seconds / 60.0, 1.0)
        dt_min = max(1, self.dt_minutes)
        n_blocks = max(1, int(math.ceil(tc_min / dt_min)))
        durations = np.arange(1, n_blocks + 1, dtype=float) * dt_min

        cumulative_depth = self.p24h_mm * np.power(durations / 1440.0, self.beta)
        block_depth = np.diff(np.concatenate([[0.0], cumulative_depth]))
        ordered_depth = _alternating_blocks(block_depth)
        intensity = np.array(ordered_depth) / (dt_min / 60.0)

        moments = pd.DataFrame({
            "block": np.arange(1, len(ordered_depth) + 1),
            "duration_min": np.full(len(ordered_depth), dt_min, dtype=float),
            "depth_mm": ordered_depth,
            "intensity_mm_h": intensity,
        })
        peak = float(np.max(intensity)) if len(intensity) else 0.0
        return peak, moments

    def _intensity_from_tc(self, tc_seconds: float) -> float:
        """Intensidad de la curva IDF i = a/(t+b)^c para duración t = Tc (min)."""
        if self.idf_params is None:
            raise RuntimeError(
                "No hay parámetros IDF (a, b, c). Provea idf_params al construir "
                "DistributedTcCalculator para iterar la intensidad con el Tc."
            )
        a, b, c = self.idf_params
        tc_min = max(tc_seconds / 60.0, 1.0)
        return float(a / (tc_min + b) ** c)
