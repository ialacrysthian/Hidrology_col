from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsGeometry,
)


def to_epsg4326(geometry: QgsGeometry, source_crs: QgsCoordinateReferenceSystem) -> QgsGeometry:
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if source_crs == wgs84:
        return geometry
    transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
    geom = QgsGeometry(geometry)
    geom.transform(transform)
    return geom


def get_utm_crs_for_colombia(lon_center: float) -> QgsCoordinateReferenceSystem:
    """Returns the appropriate UTM zone CRS for Colombia (zones 17N–19N)."""
    zone = int((lon_center + 180) / 6) + 1
    epsg = 32600 + zone  # WGS84 / UTM zone N
    return QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
