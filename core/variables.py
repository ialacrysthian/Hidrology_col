"""
Catálogo de variables IDEAM derivado de GlosarioVariables.xlsx.

Provee:
  - VariableInfo  — dataclass con metadatos de una variable
  - VARIABLES     — dict etiqueta → VariableInfo (todas las hojas)
  - BALANCE_HIDRICO_VARS — subconjunto relevante para balance hídrico
  - load_glossary()  — carga dinámica desde el XLSX si se necesita
  - get_variable()   — lookup por etiqueta o alias normalizado
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class VariableInfo:
    etiqueta: str           # clave única IDEAM (ej. 'TMN_MEDIA_M')
    parametro: str          # nombre del parámetro (ej. 'TEMPERATURA')
    descripcion: str        # descripción legible
    unidad: str             # °C, mm, %, m3/s, …
    periodo: str            # Horario | Diario | Mensual | Anual
    tipo_red: str           # Convencionales | Automáticas
    hoja: str               # Básicas | Derivadas-M | Derivadas-H
    serie_origen: str = ""  # variable fuente para derivadas
    publicar: bool = True


# ---------------------------------------------------------------------------
# Variables clave para balance hídrico — hardcoded para evitar dependencia
# de openpyxl en entornos donde no está disponible.
# Fuente: GlosarioVariables.xlsx / Derivadas-M (publicar=SI) +  Básicas
# ---------------------------------------------------------------------------

BALANCE_HIDRICO_VARS: dict[str, VariableInfo] = {
    # ── Temperatura convencional ──────────────────────────────────────────
    "TMN_MEDIA_M": VariableInfo(
        etiqueta="TMN_MEDIA_M", parametro="TEMPERATURA",
        descripcion="Temperatura mínima media mensual", unidad="°C",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="TMN_CON",
    ),
    "TMN_MEDIA_A": VariableInfo(
        etiqueta="TMN_MEDIA_A", parametro="TEMPERATURA",
        descripcion="Temperatura mínima media anual", unidad="°C",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="TMN_MEDIA_M",
    ),
    "TMX_MEDIA_M": VariableInfo(
        etiqueta="TMX_MEDIA_M", parametro="TEMPERATURA",
        descripcion="Temperatura máxima media mensual", unidad="°C",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="TMX_CON",
    ),
    "TMX_MEDIA_A": VariableInfo(
        etiqueta="TMX_MEDIA_A", parametro="TEMPERATURA",
        descripcion="Temperatura máxima media anual", unidad="°C",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="TMX_MEDIA_M",
    ),
    "TSSM_MEDIA_M": VariableInfo(
        etiqueta="TSSM_MEDIA_M", parametro="TEMPERATURA",
        descripcion="Temperatura seca media mensual", unidad="°C",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="TSSM_CON",
    ),
    "TSSM_MEDIA_A": VariableInfo(
        etiqueta="TSSM_MEDIA_A", parametro="TEMPERATURA",
        descripcion="Temperatura seca media anual", unidad="°C",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="TSSM_MEDIA_M",
    ),
    # ── Temperatura automática ─────────────────────────────────────────────
    "TAMN2_MEDIA_M": VariableInfo(
        etiqueta="TAMN2_MEDIA_M", parametro="TEMPERATURA",
        descripcion="Temperatura mínima del Aire a 2 m media mensual", unidad="°C",
        periodo="Mensual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="TAMN2_AUT_60",
    ),
    "TAMX2_MEDIA_M": VariableInfo(
        etiqueta="TAMX2_MEDIA_M", parametro="TEMPERATURA",
        descripcion="Temperatura máxima del Aire a 2 m media mensual", unidad="°C",
        periodo="Mensual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="TAMX2_AUT_60",
    ),
    "TA2_MEDIA_M": VariableInfo(
        etiqueta="TA2_MEDIA_M", parametro="TEMPERATURA",
        descripcion="Temperatura del Aire a 2 m media mensual", unidad="°C",
        periodo="Mensual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="TA2_AUT_60",
    ),
    "TA2_MEDIA_A": VariableInfo(
        etiqueta="TA2_MEDIA_A", parametro="TEMPERATURA",
        descripcion="Temperatura del Aire a 2 m media anual", unidad="°C",
        periodo="Anual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="TA2_MEDIA_M",
    ),
    # ── Precipitación convencional ─────────────────────────────────────────
    "PTPG_TT_M": VariableInfo(
        etiqueta="PTPG_TT_M", parametro="PRECIPITACION",
        descripcion="Precipitación total mensual (pluviógrafo)", unidad="mm",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="PTPG_TT_D",
    ),
    "PTPG_TT_A": VariableInfo(
        etiqueta="PTPG_TT_A", parametro="PRECIPITACION",
        descripcion="Precipitación total anual (pluviógrafo)", unidad="mm",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="PTPG_TT_M",
    ),
    "PTPM_TT_M": VariableInfo(
        etiqueta="PTPM_TT_M", parametro="PRECIPITACION",
        descripcion="Precipitación total mensual (pluviómetro)", unidad="mm",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="PTPM_CON",
    ),
    "PTPM_TT_A": VariableInfo(
        etiqueta="PTPM_TT_A", parametro="PRECIPITACION",
        descripcion="Precipitación total anual (pluviómetro)", unidad="mm",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="PTPM_TT_M",
    ),
    # ── Precipitación automática ───────────────────────────────────────────
    "PT_10_TT_M": VariableInfo(
        etiqueta="PT_10_TT_M", parametro="PRECIPITACION",
        descripcion="Precipitación total mensual (sensor 10 min)", unidad="mm",
        periodo="Mensual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="PT_10_TT_D",
    ),
    "PT_10_TT_A": VariableInfo(
        etiqueta="PT_10_TT_A", parametro="PRECIPITACION",
        descripcion="Precipitación total anual (sensor 10 min)", unidad="mm",
        periodo="Anual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="PT_10_TT_M",
    ),
    # ── Evaporación ────────────────────────────────────────────────────────
    "EVTE_TT_M": VariableInfo(
        etiqueta="EVTE_TT_M", parametro="EVAPORACION",
        descripcion="Evaporación total mensual (convencional)", unidad="mm",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="EVTE_CON",
    ),
    "EVTE_TT_A": VariableInfo(
        etiqueta="EVTE_TT_A", parametro="EVAPORACION",
        descripcion="Evaporación total anual (convencional)", unidad="mm",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="EVTE_TT_M",
    ),
    "EV_TT_M": VariableInfo(
        etiqueta="EV_TT_M", parametro="EVAPORACION",
        descripcion="Evaporación total mensual (automática)", unidad="mm",
        periodo="Mensual", tipo_red="Automáticas", hoja="Derivadas-M",
        serie_origen="EV_TT_D",
    ),
    # ── Humedad relativa ───────────────────────────────────────────────────
    "HRHG_MEDIA_M": VariableInfo(
        etiqueta="HRHG_MEDIA_M", parametro="HUM RELATIVA",
        descripcion="Humedad relativa media mensual (convencional)", unidad="%",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="HRHG_CON",
    ),
    "HRHG_MEDIA_A": VariableInfo(
        etiqueta="HRHG_MEDIA_A", parametro="HUM RELATIVA",
        descripcion="Humedad relativa media anual (convencional)", unidad="%",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="HRHG_MEDIA_M",
    ),
    # ── Brillo solar ───────────────────────────────────────────────────────
    "BSHG_TT_M": VariableInfo(
        etiqueta="BSHG_TT_M", parametro="BRILLO SOLAR",
        descripcion="Brillo solar total mensual", unidad="horas/sol",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="BSHG_TT_D",
    ),
    "BSHG_TT_A": VariableInfo(
        etiqueta="BSHG_TT_A", parametro="BRILLO SOLAR",
        descripcion="Brillo solar total anual", unidad="horas/sol",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-M",
        serie_origen="BSHG_TT_M",
    ),
    # ── Caudal ─────────────────────────────────────────────────────────────
    "Q_MEDIA_M": VariableInfo(
        etiqueta="Q_MEDIA_M", parametro="CAUDAL",
        descripcion="Caudal medio mensual", unidad="m3/s",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-H",
        serie_origen="Q_MEDIA_D",
    ),
    "Q_MEDIA_A": VariableInfo(
        etiqueta="Q_MEDIA_A", parametro="CAUDAL",
        descripcion="Caudal medio anual", unidad="m3/s",
        periodo="Anual", tipo_red="Convencionales", hoja="Derivadas-H",
        serie_origen="Q_MEDIA_M",
    ),
    # ── Nivel ──────────────────────────────────────────────────────────────
    "NV_MEDIA_M": VariableInfo(
        etiqueta="NV_MEDIA_M", parametro="NIVEL",
        descripcion="Nivel medio mensual", unidad="cm",
        periodo="Mensual", tipo_red="Convencionales", hoja="Derivadas-H",
        serie_origen="NV_MEDIA_D",
    ),
}

# Alias normalizados → etiqueta oficial
# Permite buscar por nombre semántico sin conocer la etiqueta exacta
_ALIASES: dict[str, str] = {
    "TEMPERATURA_MINIMA":  "TMN_MEDIA_M",
    "TEMPERATURA_MAXIMA":  "TMX_MEDIA_M",
    "TEMPERATURA_MEDIA":   "TSSM_MEDIA_M",
    "TEMPERATURA":         "TSSM_MEDIA_M",
    "PRECIPITACION":       "PTPG_TT_M",
    "PRECIPITACION_TOTAL": "PTPG_TT_M",
    "EVAPORACION":         "EVTE_TT_M",
    "HUMEDAD_RELATIVA":    "HRHG_MEDIA_M",
    "BRILLO_SOLAR":        "BSHG_TT_M",
    "CAUDAL":              "Q_MEDIA_M",
    "NIVEL":               "NV_MEDIA_M",
}

# Mapeo alias → nombre en nsz2-kzcq (normales climatológicas)
NORMAL_PARAM_MAP: dict[str, str] = {
    "TEMPERATURA_MINIMA":  "TEMPERATURA MÍNIMA",
    "TEMPERATURA_MAXIMA":  "TEMPERATURA MÁXIMA",
    "TEMPERATURA_MEDIA":   "TEMPERATURA MEDIA",
    "TEMPERATURA":         "TEMPERATURA MEDIA",
    "PRECIPITACION":       "PRECIPITACIÓN",
    "PRECIPITACION_TOTAL": "PRECIPITACIÓN",
    "BRILLO_SOLAR":        "BRILLO SOLAR",
    "HUMEDAD_RELATIVA":    "HUMEDAD RELATIVA",
    "DIAS_LLUVIA":         "No. DE DIAS CON LLUVIA",
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def get_variable(key: str) -> Optional[VariableInfo]:
    """
    Busca una variable por etiqueta exacta o alias normalizado.

    Ejemplos:
      get_variable('TMN_MEDIA_M')       -> VariableInfo(...)
      get_variable('TEMPERATURA_MINIMA') -> VariableInfo(etiqueta='TMN_MEDIA_M', ...)
    """
    key_up = key.strip().upper()
    if key_up in BALANCE_HIDRICO_VARS:
        return BALANCE_HIDRICO_VARS[key_up]
    resolved = _ALIASES.get(key_up)
    if resolved:
        return BALANCE_HIDRICO_VARS.get(resolved)
    return None


def list_variables(
    periodo: str = "",
    tipo_red: str = "",
    parametro: str = "",
) -> list[VariableInfo]:
    """Lista variables filtradas por período, tipo de red o parámetro."""
    result = list(BALANCE_HIDRICO_VARS.values())
    if periodo:
        result = [v for v in result if periodo.lower() in v.periodo.lower()]
    if tipo_red:
        result = [v for v in result if tipo_red.lower() in v.tipo_red.lower()]
    if parametro:
        result = [v for v in result if parametro.upper() in v.parametro.upper()]
    return result


def load_glossary(xlsx_path: str | Path | None = None) -> dict[str, VariableInfo]:
    """
    Carga el glosario completo desde GlosarioVariables.xlsx.
    Retorna dict etiqueta → VariableInfo con TODAS las variables del glosario.
    Si xlsx_path es None usa el archivo incluido en el plugin.
    """
    if xlsx_path is None:
        from ..data import GLOSSARY_PATH
        xlsx_path = GLOSSARY_PATH

    import pandas as pd

    result: dict[str, VariableInfo] = {}

    # Hoja Básicas
    df_basicas = pd.read_excel(xlsx_path, sheet_name="Básicas", header=1, dtype=str)
    df_basicas.columns = [
        "TEMATICA", "TIPO_RED", "PARAMETRO", "ETIQUETA", "UNIDAD",
        "INTERP", "METODO", "PERIODO", "PUBLICAR", "PORTAL_DD", "DESCRIPCION",
    ]
    for _, row in df_basicas.dropna(subset=["ETIQUETA"]).iterrows():
        etiq = str(row["ETIQUETA"]).strip()
        if not etiq:
            continue
        result[etiq] = VariableInfo(
            etiqueta=etiq,
            parametro=str(row.get("PARAMETRO", "")).strip(),
            descripcion=str(row.get("DESCRIPCION", "")).strip(),
            unidad=str(row.get("UNIDAD", "")).strip(),
            periodo=str(row.get("PERIODO", "")).strip(),
            tipo_red=str(row.get("TIPO_RED", "")).strip(),
            hoja="Básicas",
            publicar=str(row.get("PUBLICAR", "")).strip().upper() in ("SI", "S"),
        )

    # Hojas Derivadas
    for sheet, hoja_name in [("Derivadas-M", "Derivadas-M"), ("Derivadas-H", "Derivadas-H")]:
        df = pd.read_excel(xlsx_path, sheet_name=sheet, header=1, dtype=str)
        df.columns = [
            "TIPO_RED", "PARAMETRO", "ETIQUETA", "UNIDAD", "INTER",
            "PUBLICAR", "PORTAL", "PERIODO", "DESCRIPCION",
            "PROCESS_TYPE", "STATISTIC", "PCT_COV", "SERIE_ORIGEN", "DATOS_DESDE",
        ]
        for _, row in df.dropna(subset=["ETIQUETA"]).iterrows():
            etiq = str(row["ETIQUETA"]).strip()
            if not etiq:
                continue
            result[etiq] = VariableInfo(
                etiqueta=etiq,
                parametro=str(row.get("PARAMETRO", "")).strip(),
                descripcion=str(row.get("DESCRIPCION", "")).strip(),
                unidad=str(row.get("UNIDAD", "")).strip(),
                periodo=str(row.get("PERIODO", "")).strip(),
                tipo_red=str(row.get("TIPO_RED", "")).strip(),
                hoja=hoja_name,
                serie_origen=str(row.get("SERIE_ORIGEN", "")).strip(),
                publicar=str(row.get("PUBLICAR", "")).strip().upper() in ("SI", "S", "1"),
            )

    return result
