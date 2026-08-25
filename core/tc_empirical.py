"""
Métodos empíricos de Tc para comparar contra el Kirpich morfométrico
(módulo 7) y el Tc distribuido/segmentado (módulo 8).

Fórmulas (11)-(24) tal como fueron provistas por el usuario, con sus
unidades explícitas de entrada — se implementan literalmente, sin
reinterpretar constantes. Todas usan solo parámetros morfométricos ya
calculados por WatershedMorphometry (área, longitud y pendiente del cauce
principal, elevaciones) — salvo SCS (13), que requiere curva número (CN),
no disponible en el plugin, por lo que queda marcada como "requiere CN"
mientras no se provea explícitamente.

(15) US Corps of Engineers no se implementa: la ecuación no llegó completa
(solo las unidades) y no hay forma de garantizar la constante correcta sin
inventar una cifra — se reporta como "fórmula no disponible".

(11) Ventura y (22) Ventura-Heras son la MISMA fórmula (mismas unidades y
constantes) — se calculan una sola vez y se listan bajo ambos nombres tal
como aparecen en el documento fuente. Lo mismo para (23) V.T. Chow y
(24) California.
"""
from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass


@dataclass
class TcEmpiricalResult:
    metodo: str
    tc_min: float | None
    formula: str
    notas: str = ""


# ---------------------------------------------------------------------
# Fórmulas — cada una retorna Tc en las unidades ORIGINALES del enunciado
# (horas o minutos, según el caso); compute_all normaliza todo a minutos.
# ---------------------------------------------------------------------

def tc_ventura(L_km: float, S_pct: float) -> float:
    """(11) Ventura / (22) Ventura-Heras.
    Tc(h) = 0.30 * (L / S^0.25)^0.75      L en km, S en %.
    """
    S = max(S_pct, 1e-6)
    return 0.30 * (L_km / S ** 0.25) ** 0.75


def tc_passini(A_km2: float, L_km: float, S_m_m: float) -> float:
    """(12) Passini.
    Tc(h) = 0.108 * (A*L)^(1/3) / sqrt(S)   A en km², L en km, S en m/m.
    """
    S = max(S_m_m, 1e-9)
    return 0.108 * (max(A_km2, 0.0) * L_km) ** (1.0 / 3.0) / math.sqrt(S)


def tc_scs(L_km: float, S_pct: float, curve_number: float) -> float:
    """(13) SCS.
    Tc(h) = 100*L(ft)^0.8*[(1000/CN)-9]^0.7 / (1900*sqrt(S))
    L en PIES (se convierte desde km), S en %, CN = curva número.
    """
    L_ft = L_km * 1000.0 / 0.3048
    S = max(S_pct, 1e-6)
    return 100.0 * L_ft ** 0.8 * ((1000.0 / curve_number) - 9.0) ** 0.7 / (1900.0 * math.sqrt(S))


def tc_temez_doc(L_km: float, S_m_m: float) -> float:
    """(14) Témez [forma dada por el usuario].
    Tc(min) = 18 * [L*(100S)^-0.25]^0.75    L en km, S en m/m.
    """
    S = max(S_m_m, 1e-9)
    bracket = L_km * (100.0 * S) ** -0.25
    return 18.0 * bracket ** 0.75


def tc_williams(L_km: float, A_km2: float, S_pct: float) -> float:
    """(16) Williams.
    Tc(h) = 0.683 * (L*A^0.40) / (D*S^0.25)
    D = diámetro de una cuenca circular equivalente = 2*sqrt(A/pi), km.
    S en %.
    """
    S = max(S_pct, 1e-6)
    D = 2.0 * math.sqrt(max(A_km2, 0.0) / math.pi)
    if D <= 0:
        return float("nan")
    return 0.683 * (L_km * A_km2 ** 0.40) / (D * S ** 0.25)


def tc_bransby_williams(L_km: float, A_km2: float, S_m_km: float) -> float:
    """(17) Bransby-Williams.
    Tc(min) = 14.6 * L / (A^0.1 * S^0.2)
    L en km, A en km², S en m/km (pendiente entre cota máx. y mín. del cauce).
    """
    S = max(S_m_km, 1e-6)
    return 14.6 * L_km / (max(A_km2, 1e-9) ** 0.1 * S ** 0.2)


def tc_giandotti_doc(A_km2: float, L_km: float, S_m_m: float) -> float:
    """(18) Giandotti [forma dada por el usuario].
    Tc(h) = (4*sqrt(A) + 1.5*L) / (25.3*sqrt(L*S))
    A en km², L en km, S en m/m (pendiente total del cauce principal).
    """
    S = max(S_m_m, 1e-9)
    denom = 25.3 * math.sqrt(max(L_km, 1e-9) * S)
    if denom <= 0:
        return float("nan")
    return (4.0 * math.sqrt(max(A_km2, 0.0)) + 1.5 * L_km) / denom


def tc_haktanir_sezen(L_km: float) -> float:
    """(19) Haktanir-Sezen.
    Tc(min) = 44.75 * L^0.841      L en km.
    """
    return 44.75 * L_km ** 0.841


def tc_kirpich_doc(L_km: float, S_m_m: float) -> float:
    """(20) Kirpich [misma fórmula ya usada en morphometry.py, reexpresada
    en L(km)/S(m/m)/Tc(h) para consistencia dimensional con el resto de
    esta tabla — matemáticamente idéntica a
    Tc(min) = 0.01947*L(m)^0.77*S^-0.385].
    Tc(h) = 0.06626 * L^0.77 * S^-0.385     L en km, S en m/m.
    """
    S = max(S_m_m, 1e-9)
    return 0.06626 * L_km ** 0.77 * S ** -0.385


def tc_scs_ranser(L_km: float, H_m: float) -> float:
    """(21) SCS-Ranser (equivalente algebraico de Kirpich reparametrizado
    por desnivel en vez de pendiente — se verificó: la constante 0.947
    coincide con la derivación directa desde Kirpich, S=H/L).
    Tc(h) = 0.947 * (L^3/H)^0.385
    L en km, H = diferencia de cotas entre los extremos del cauce (m).
    """
    H = max(H_m, 1e-6)
    return 0.947 * (L_km ** 3 / H) ** 0.385


def tc_chow(L_km: float, S_m_m: float) -> float:
    """(23) V.T. Chow / (24) California [misma fórmula].
    Tc(h) = 0.273 * (L/S^0.5)^0.64     L en km, S en m/m.
    """
    S = max(S_m_m, 1e-9)
    return 0.273 * (L_km / S ** 0.5) ** 0.64


# ---------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------

def compute_all(
    area_km2: float,
    longitud_cauce_km: float,
    pendiente_cauce_pct: float,
    elevacion_media_m: float,
    elevacion_min_m: float,
    elevacion_max_m: float | None = None,
    tc_kirpich_min: float | None = None,
    curve_number: float | None = None,
    tc_referencia_extra: dict[str, float] | None = None,
) -> list[TcEmpiricalResult]:
    """Calcula las 14 fórmulas (11)-(24) provistas por el usuario más el
    Kirpich ya adoptado en morfometría, para comparación directa.

    Todas las entradas de pendiente se derivan de pendiente_cauce_pct (la
    misma columna que exporta morfometria.csv) hacia las unidades que pide
    cada fórmula (%, m/m, m/km) — sin pedir datos nuevos al usuario.
    """
    L = longitud_cauce_km
    A = area_km2
    S_pct = pendiente_cauce_pct
    S_mm = S_pct / 100.0
    S_m_km = S_mm * 1000.0
    # Desnivel a lo largo del cauce principal, reconstruido desde L y S%
    # (mismo par usado internamente por morphometry.py: S = drop/length).
    H_channel_m = S_mm * (L * 1000.0)
    Hm_sobre_salida = None
    if elevacion_max_m is not None:
        Hm_sobre_salida = max(elevacion_media_m - elevacion_min_m, 0.0)

    results: list[TcEmpiricalResult] = []

    if tc_kirpich_min is not None:
        results.append(TcEmpiricalResult(
            "Kirpich (1940) — adoptado en morfometría", round(tc_kirpich_min, 2),
            "Tc(min) = 0.01947 * L(m)^0.77 * S^-0.385",
            "Valor oficial del módulo 7 (semilla de la iteración distribuida).",
        ))
    results.append(TcEmpiricalResult(
        "Kirpich (20)", round(tc_kirpich_doc(L, S_mm) * 60.0, 2),
        "Tc(h) = 0.06626 * L^0.77 * S^-0.385",
        "Misma fórmula que el Kirpich adoptado, reexpresada en L(km)/S(m/m) "
        "— debe coincidir de cerca con la fila anterior (verificación cruzada).",
    ))

    results.append(TcEmpiricalResult(
        "Ventura (11)", round(tc_ventura(L, S_pct) * 60.0, 2),
        "Tc(h) = 0.30 * (L/S^0.25)^0.75", "S en %.",
    ))
    results.append(TcEmpiricalResult(
        "Ventura-Heras (22)", round(tc_ventura(L, S_pct) * 60.0, 2),
        "Tc(h) = 0.30 * (L/S^0.25)^0.75",
        "Misma fórmula que Ventura (11) — el documento fuente la repite con otro nombre.",
    ))

    results.append(TcEmpiricalResult(
        "Passini (12)", round(tc_passini(A, L, S_mm) * 60.0, 2),
        "Tc(h) = 0.108 * (A*L)^(1/3) / sqrt(S)", "S en m/m.",
    ))

    if curve_number is not None:
        scs_min = round(tc_scs(L, S_pct, curve_number) * 60.0, 2)
        scs_note = f"CN = {curve_number:g}. L convertida de km a pies."
        # La ecuación SCS-Lag se calibró para microcuencas (longitudes de
        # flujo de cientos a pocos miles de pies) — con cauces largos (L en
        # decenas de km) el término L^0.8 domina y da resultados sin
        # sentido físico (verificado: L=54.8 km, CN=75 -> ~171,000 min).
        # Se advierte explícitamente en vez de presentarlo sin contexto.
        if tc_kirpich_min and scs_min > tc_kirpich_min * 10:
            scs_note += (
                " ADVERTENCIA: resultado muy por encima de Kirpich — la fórmula "
                "SCS-Lag se calibró para microcuencas con L corto; con cauces "
                "largos (decenas de km) se extrapola fuera de su rango válido "
                "y el resultado no es físicamente confiable."
            )
        results.append(TcEmpiricalResult(
            "SCS (13)", scs_min,
            "Tc(h) = 100*L(ft)^0.8*[(1000/CN)-9]^0.7 / (1900*sqrt(S))",
            scs_note,
        ))
    else:
        results.append(TcEmpiricalResult(
            "SCS (13)", None,
            "Tc(h) = 100*L(ft)^0.8*[(1000/CN)-9]^0.7 / (1900*sqrt(S))",
            "Requiere curva número (CN) — no disponible; provea curve_number para calcularla.",
        ))

    results.append(TcEmpiricalResult(
        "Témez (14)", round(tc_temez_doc(L, S_mm), 2),
        "Tc(min) = 18*[L*(100S)^-0.25]^0.75", "S en m/m.",
    ))

    results.append(TcEmpiricalResult(
        "US Corps of Engineers (15)", None, "—",
        "Ecuación no disponible en el documento fuente (solo llegaron las "
        "unidades) — no se calcula para evitar asumir una constante no verificada.",
    ))

    results.append(TcEmpiricalResult(
        "Williams (16)", round(tc_williams(L, A, S_pct) * 60.0, 2),
        "Tc(h) = 0.683 * (L*A^0.40) / (D*S^0.25)",
        f"D (diámetro cuenca circular equivalente) = {2*math.sqrt(max(A,0.0)/math.pi):.2f} km. S en %.",
    ))

    results.append(TcEmpiricalResult(
        "Bransby-Williams (17)", round(tc_bransby_williams(L, A, S_m_km), 2),
        "Tc(min) = 14.6 * L / (A^0.1 * S^0.2)", f"S = {S_m_km:.2f} m/km.",
    ))

    results.append(TcEmpiricalResult(
        "Giandotti (18)", round(tc_giandotti_doc(A, L, S_mm) * 60.0, 2),
        "Tc(h) = (4*sqrt(A) + 1.5*L) / (25.3*sqrt(L*S))", "S en m/m.",
    ))
    if Hm_sobre_salida is not None:
        results.append(TcEmpiricalResult(
            "Giandotti (clásico, Hm)", round(
                (4.0 * math.sqrt(max(A, 0.0)) + 1.5 * L) / (0.8 * math.sqrt(max(Hm_sobre_salida, 1e-3))) * 60.0, 2
            ),
            "Tc(h) = (4*sqrt(A) + 1.5*L) / (0.8*sqrt(Hm))",
            f"Variante clásica con Hm = elevación media sobre la salida = "
            f"{Hm_sobre_salida:.1f} m (no forma parte de la lista (11)-(24), "
            f"se incluye como referencia adicional).",
        ))

    results.append(TcEmpiricalResult(
        "Haktanir-Sezen (19)", round(tc_haktanir_sezen(L), 2),
        "Tc(min) = 44.75 * L^0.841", "",
    ))

    results.append(TcEmpiricalResult(
        "SCS-Ranser (21)", round(tc_scs_ranser(L, H_channel_m) * 60.0, 2),
        "Tc(h) = 0.947 * (L^3/H)^0.385",
        f"H (desnivel del cauce, reconstruido de L y S%) = {H_channel_m:.1f} m.",
    ))

    tc_chow_min = round(tc_chow(L, S_mm) * 60.0, 2)
    results.append(TcEmpiricalResult(
        "V.T. Chow (23)", tc_chow_min,
        "Tc(h) = 0.273 * (L/S^0.5)^0.64", "S en m/m.",
    ))
    results.append(TcEmpiricalResult(
        "California (24)", tc_chow_min,
        "Tc(h) = 0.273 * (L/S^0.5)^0.64",
        "Misma fórmula que V.T. Chow (23) en el documento fuente.",
    ))

    if tc_referencia_extra:
        for nombre, tc_min in tc_referencia_extra.items():
            results.append(TcEmpiricalResult(
                nombre, round(float(tc_min), 2), "—",
                "Resultado del cálculo distribuido (no una fórmula empírica).",
            ))

    return results


def export_csv(results: list[TcEmpiricalResult], csv_path: str) -> str:
    parent = os.path.dirname(csv_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["metodo", "tc_min", "tc_horas", "formula", "notas"])
        for r in results:
            tc_h = round(r.tc_min / 60.0, 2) if r.tc_min is not None else ""
            writer.writerow([r.metodo, r.tc_min if r.tc_min is not None else "N/D", tc_h, r.formula, r.notas])
    return csv_path
