from qgis.PyQt.QtCore import QSettings

_PREFIX = "TCCalculator"


class Keys:
    # IDEAM / Socrata
    IDEAM_APP_TOKEN     = f"{_PREFIX}/ideam_app_token"
    # DEM (archivo provisto por el usuario)
    DEM_USER_PATH       = f"{_PREFIX}/dem_user_path"
    # Carpeta de resultados del proyecto
    OUTPUT_DIR          = f"{_PREFIX}/output_dir"
    # Delimitación
    WATERSHED_FAT       = f"{_PREFIX}/watershed_fat"       # celdas (int)
    # Selección de estaciones
    STATIONS_BUFFER_KM  = f"{_PREFIX}/stations_buffer_km"  # km (float)
    STATIONS_ESTADO     = f"{_PREFIX}/stations_estado"      # Activa|Suspendida|Todas
    STATIONS_PRECIP     = f"{_PREFIX}/stations_precip"      # bool
    STATIONS_TEMP       = f"{_PREFIX}/stations_temp"        # bool
    STATIONS_CAUDAL     = f"{_PREFIX}/stations_caudal"      # bool
    PROGRESS_MODE       = f"{_PREFIX}/progress_mode"
    # Idioma de la interfaz (es | en)
    LANGUAGE            = f"{_PREFIX}/language"
    # Log
    LOG_LEVEL           = f"{_PREFIX}/log_level"


_DEFAULTS = {
    Keys.IDEAM_APP_TOKEN:    "",
    Keys.DEM_USER_PATH:      "",
    Keys.OUTPUT_DIR:         "",
    Keys.WATERSHED_FAT:      500,
    Keys.STATIONS_BUFFER_KM: 20.0,
    Keys.STATIONS_ESTADO:    "Activa",
    Keys.STATIONS_PRECIP:    True,
    Keys.STATIONS_TEMP:      True,
    Keys.STATIONS_CAUDAL:    True,
    Keys.LANGUAGE:           "es",
    Keys.LOG_LEVEL:          "INFO",
}


def get(key: str, default=None):
    return QSettings().value(key, _DEFAULTS.get(key, default))


def set_value(key: str, value) -> None:
    QSettings().setValue(key, value)


def get_bool(key: str) -> bool:
    val = get(key)
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")


def get_float(key: str) -> float:
    return float(get(key))


def get_int(key: str) -> int:
    return int(get(key))
