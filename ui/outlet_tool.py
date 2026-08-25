"""
QgsMapTool para que el usuario haga clic en el mapa y defina
el punto de cierre (outlet) de la cuenca.

Uso:
    tool = OutletTool(canvas)
    tool.outlet_picked.connect(lambda lon, lat: ...)
    canvas.setMapTool(tool)
"""
from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QCursor, QColor
from qgis.core import QgsPointXY
from qgis.gui import QgsMapTool, QgsMapCanvas, QgsVertexMarker


class OutletTool(QgsMapTool):
    """
    Herramienta de mapa: un clic izquierdo captura el punto de cierre
    y emite outlet_picked(x, y) en el CRS del proyecto (= CRS del DEM).

    Muestra un marcador rojo en el punto seleccionado hasta que
    se llame a clear_marker() o se cambie la herramienta.
    """

    outlet_picked = pyqtSignal(float, float)   # x, y en CRS del proyecto

    def __init__(self, canvas: QgsMapCanvas) -> None:
        super().__init__(canvas)
        self._canvas  = canvas
        self._marker  = None   # QgsVertexMarker
        self.setCursor(QCursor(Qt.CrossCursor))

    # ------------------------------------------------------------------
    # Eventos del mapa
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return

        point_canvas = self.toMapCoordinates(event.pos())

        self._place_marker(point_canvas)
        # El proyecto usa el CRS del DEM — emitir coordenadas tal cual
        self.outlet_picked.emit(point_canvas.x(), point_canvas.y())

    def canvasMoveEvent(self, event) -> None:
        pass   # sin previsualización para mantener la UI simple

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.clear_marker()
            self._canvas.unsetMapTool(self)

    # ------------------------------------------------------------------
    # Marcador visual
    # ------------------------------------------------------------------

    def _place_marker(self, point: QgsPointXY) -> None:
        self.clear_marker()
        marker = QgsVertexMarker(self._canvas)
        marker.setCenter(point)
        marker.setColor(QColor(220, 30, 30))
        marker.setFillColor(QColor(220, 30, 30, 180))
        marker.setIconSize(14)
        marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        marker.setPenWidth(3)
        self._marker = marker

    def clear_marker(self) -> None:
        if self._marker is not None:
            self._canvas.scene().removeItem(self._marker)
            self._marker = None

    # ------------------------------------------------------------------
    # Deactivation cleanup
    # ------------------------------------------------------------------

    def deactivate(self) -> None:
        self.clear_marker()
        super().deactivate()
