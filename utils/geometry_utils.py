from qgis.core import QgsGeometry, QgsRectangle


def bbox_from_geometry(geom: QgsGeometry) -> tuple[float, float, float, float]:
    """Returns (min_lon, min_lat, max_lon, max_lat) from a geometry's bounding box."""
    bb = geom.boundingBox()
    return bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()


def geometry_to_geojson_dict(geom: QgsGeometry) -> dict:
    """Converts QgsGeometry to a GeoJSON-compatible dict (for GEE)."""
    import json
    return json.loads(geom.asJson())


def wkt_to_geometry(wkt: str) -> QgsGeometry:
    return QgsGeometry.fromWkt(wkt)
