import logging
from qgis.core import Qgis, QgsMessageLog

PLUGIN_TAG = "BalanceHidrico"

_LEVEL_MAP = {
    logging.DEBUG: Qgis.Info,
    logging.INFO: Qgis.Info,
    logging.WARNING: Qgis.Warning,
    logging.ERROR: Qgis.Critical,
    logging.CRITICAL: Qgis.Critical,
}


class _QgisHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = _LEVEL_MAP.get(record.levelno, Qgis.Info)
            QgsMessageLog.logMessage(msg, PLUGIN_TAG, level)
        except Exception:
            self.handleError(record)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"balance_hidrico.{name}")
    if not logger.handlers:
        logger.addHandler(_QgisHandler())
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger
