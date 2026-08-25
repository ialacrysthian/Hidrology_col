import os

# openpyxl usa lxml si está instalado. El libxml2 que trae lxml (pip) choca
# con el libxml2 de QGIS/GDAL dentro del mismo proceso y provoca un
# "access violation" (xmlDictFree) al leer .xlsx dentro de un QgsTask.
# Forzar el parser ElementTree puro evita el conflicto de DLLs.
os.environ.setdefault("OPENPYXL_LXML", "False")


def classFactory(iface):
    from .plugin import TCCalculatorPlugin
    return TCCalculatorPlugin(iface)
