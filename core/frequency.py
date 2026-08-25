"""
Análisis de frecuencia de extremos — ajuste GEV compartido.

Fuente única para estimar valores de precipitación asociados a un periodo de
retorno a partir de la serie de máximos anuales. Lo usan tanto
``RainfallStationImporter`` (raster Tr por estación) como ``IDFStation``
(curva IDF), de modo que ambas rutas de lluvia son estadísticamente coherentes.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import genextreme


def fit_gev(annual_max) -> tuple[float, float, float]:
    """
    Ajusta una GEV a la serie de máximos anuales.

    Retorna (shape, loc, scale) en la convención de ``scipy.stats.genextreme``.
    Lanza ValueError si no hay suficientes datos válidos.
    """
    values = np.asarray(annual_max, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        raise ValueError(
            "Se requieren al menos 2 máximos anuales válidos para ajustar la GEV."
        )
    shape, loc, scale = genextreme.fit(values)
    return float(shape), float(loc), float(scale)


def gev_quantile(params: tuple[float, float, float], return_period: float) -> float:
    """Cuantil de la GEV para un periodo de retorno dado, con params ya ajustados."""
    shape, loc, scale = params
    p = 1.0 - 1.0 / float(return_period)
    p = min(max(p, 0.001), 0.999)
    return float(genextreme.ppf(p, shape, loc=loc, scale=scale))


def gev_return_value(annual_max, return_period: float) -> float:
    """Ajusta la GEV a los máximos anuales y devuelve el valor del periodo de retorno."""
    return gev_quantile(fit_gev(annual_max), return_period)
