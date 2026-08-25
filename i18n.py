"""
Internacionalización ligera para TC Calculator (Español / English).

No usa el sistema .ts/.qm de Qt para mantener el plugin autocontenido: es un
diccionario de cadenas por idioma + una función ``tr(key)``. El idioma se
guarda en QSettings vía ``settings.Keys.LANGUAGE``.

Uso:
    from .. import i18n
    label.setText(i18n.tr("btn_load"))

Las claves faltantes hacen fallback a español y, si tampoco existe, devuelven
la propia clave (útil para detectar cadenas sin traducir).
"""
from __future__ import annotations

from . import settings as cfg

DEFAULT_LANG = "es"

_STRINGS: dict[str, dict[str, str]] = {
    "es": {
        # Encabezado
        "header_sub": "Tiempo de concentración distribuido · IDEAM Colombia",
        "lang_switch": "English",
        # Tarjetas de configuración
        "card_output": "Carpeta de resultados",
        "card_status": "Estado del proceso",
        "ph_output": "Seleccionar carpeta...",
        "btn_load_session": "Cargar sesión",
        "tip_load_session": ("Carga el proyecto QGIS y el estado guardados en la carpeta de\n"
                             "resultados para continuar sin empezar de cero."),
        # Títulos de pasos
        "step1_title": "Modelo de Elevación Digital (DEM)",
        "step2_title": "Calcular flujo acumulado",
        "step3_title": "Marcar outlet y delimitar cuenca",
        "step4_title": "Cobertura de tierra",
        "step5_title": "Selección de estaciones IDEAM",
        "step6_title": "Morfometría y comparación de métodos de Tc",
        # Comunes
        "btn_load": "Cargar",
        # Paso 1
        "ph_dem": "Ruta al archivo .tif / .tiff...",
        "lbl_dem_empty": "Sin DEM cargado",
        # Paso 2
        "btn_calc_flow": "Calcular acumulación de flujo",
        "tip_calc_flow": ("Calcula el flujo acumulado (corrientes/cauces).\n"
                          "Se mostrará en el mapa para que vea dónde está cada cauce."),
        "lbl_flow_empty": "Sin flujo calculado",
        # Paso 3
        "btn_outlet": "Marcar punto de desembocadura",
        "tip_outlet": ("Haga clic sobre el cauce en el DEM para definir\n"
                       "el punto de cierre de la cuenca."),
        "lbl_outlet_empty": "Sin outlet",
        "lbl_fat": "Umbral FAT (celdas):",
        "tip_fat": ("Número mínimo de celdas aguas arriba para iniciar un cauce.\n"
                    "Valor típico Colombia: 200-2000 celdas.\n"
                    "Menor valor = red más densa = más subcuencas."),
        "btn_delineate": "Delimitar cuenca",
        # Paso 4
        "ph_landcover": "Seleccionar shapefile de cobertura...",
        "lbl_cover_field": "Campo de cobertura:",
        "col_category": "Categoría",
        "btn_add_row": "Agregar fila",
        "btn_remove_row": "Eliminar fila",
        "btn_process_cover": "Procesar cobertura",
        "lbl_cover_empty": "Sin cobertura cargada",
        # Paso 5
        "lbl_buffer": "Buffer alrededor de la cuenca:",
        "lbl_status_word": "Estado:",
        "estado_activa": "Activa",
        "estado_suspendida": "Suspendida",
        "estado_todas": "Todas",
        "chk_precip": "Precipitación",
        "chk_temp": "Temperatura",
        "chk_caudal": "Caudal/Nivel",
        "btn_stations": "Buscar estaciones",
        # Paso 6
        "btn_morphometry": "Calcular morfometría",
        "tip_morphometry": ("Calcula área, perímetro, longitud y pendiente del cauce\n"
                            "principal, coeficientes de forma, el Tc de Kirpich —\n"
                            "adoptado como referencia — y lo compara contra 14 métodos\n"
                            "empíricos de Tc (Ventura, Passini, SCS, Témez, Williams,\n"
                            "Bransby-Williams, Giandotti, Haktanir-Sezen, SCS-Ranser,\n"
                            "V.T. Chow, California). Se exporta a morfometria.csv y\n"
                            "comparacion_tc_metodos.csv."),
        "lbl_morphometry_empty": "Sin morfometría calculada",
        "lbl_curve_number": "Curva número CN (opcional, para SCS):",
        "tip_curve_number": ("Curva número SCS de la cuenca (0-100). Si se define,\n"
                             "habilita el cálculo del método SCS (13). Déjelo en blanco\n"
                             "si no la conoce — los demás métodos no la requieren."),
        # Estado inicial
        "status_ready_title": "Listo",
        "status_ready_text": "Pulse 'Calcular acumulación de flujo' para comenzar.",
    },
    "en": {
        "header_sub": "Distributed time of concentration · IDEAM Colombia",
        "lang_switch": "Español",
        "card_output": "Results folder",
        "card_status": "Process status",
        "ph_output": "Select folder...",
        "btn_load_session": "Load session",
        "tip_load_session": ("Loads the QGIS project and state saved in the results\n"
                             "folder to continue without starting from scratch."),
        "step1_title": "Digital Elevation Model (DEM)",
        "step2_title": "Compute flow accumulation",
        "step3_title": "Mark outlet and delineate watershed",
        "step4_title": "Land cover",
        "step5_title": "IDEAM station selection",
        "step6_title": "Morphometry and Tc methods comparison",
        "btn_load": "Load",
        "ph_dem": "Path to .tif / .tiff file...",
        "lbl_dem_empty": "No DEM loaded",
        "btn_calc_flow": "Compute flow accumulation",
        "tip_calc_flow": ("Computes flow accumulation (streams/channels).\n"
                          "It is shown on the map so you can see each channel."),
        "lbl_flow_empty": "No flow computed",
        "btn_outlet": "Mark outlet point",
        "tip_outlet": ("Click on the channel in the DEM to define\n"
                       "the watershed outlet point."),
        "lbl_outlet_empty": "No outlet",
        "lbl_fat": "FAT threshold (cells):",
        "tip_fat": ("Minimum number of upstream cells to start a channel.\n"
                    "Typical value for Colombia: 200-2000 cells.\n"
                    "Lower value = denser network = more subbasins."),
        "btn_delineate": "Delineate watershed",
        "ph_landcover": "Select land cover shapefile...",
        "lbl_cover_field": "Cover field:",
        "col_category": "Category",
        "btn_add_row": "Add row",
        "btn_remove_row": "Remove row",
        "btn_process_cover": "Process land cover",
        "lbl_cover_empty": "No land cover loaded",
        "lbl_buffer": "Buffer around watershed:",
        "lbl_status_word": "Status:",
        "estado_activa": "Active",
        "estado_suspendida": "Suspended",
        "estado_todas": "All",
        "chk_precip": "Precipitation",
        "chk_temp": "Temperature",
        "chk_caudal": "Discharge/Level",
        "btn_stations": "Search stations",
        "btn_morphometry": "Compute morphometry",
        "tip_morphometry": ("Computes area, perimeter, main channel length and slope,\n"
                            "shape coefficients, the Kirpich Tc — adopted as reference —\n"
                            "and compares it against 14 empirical Tc methods (Ventura,\n"
                            "Passini, SCS, Témez, Williams, Bransby-Williams, Giandotti,\n"
                            "Haktanir-Sezen, SCS-Ranser, V.T. Chow, California). Exported\n"
                            "to morfometria.csv and comparacion_tc_metodos.csv."),
        "lbl_morphometry_empty": "No morphometry computed",
        "lbl_curve_number": "Curve number CN (optional, for SCS):",
        "tip_curve_number": ("SCS curve number for the basin (0-100). If set, enables\n"
                             "the SCS (13) method. Leave blank if unknown — the other\n"
                             "methods don't need it."),
        "status_ready_title": "Ready",
        "status_ready_text": "Press 'Compute flow accumulation' to begin.",
    },
}


def get_language() -> str:
    lang = cfg.get(cfg.Keys.LANGUAGE) or DEFAULT_LANG
    return lang if lang in _STRINGS else DEFAULT_LANG


def set_language(lang: str) -> None:
    if lang in _STRINGS:
        cfg.set_value(cfg.Keys.LANGUAGE, lang)


def tr(key: str) -> str:
    lang = get_language()
    return _STRINGS[lang].get(key) or _STRINGS[DEFAULT_LANG].get(key, key)
