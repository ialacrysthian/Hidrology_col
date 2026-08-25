"""
Herramienta de mapa para dibujar el polígono AOI directamente sobre el canvas de QGIS.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsWkbTypes,
)
from qgis.gui import (
    QgsMapCanvas,
    QgsMapTool,
    QgsMapMouseEvent,
    QgsRubberBand,
)


class AoiMapTool(QgsMapTool):
    aoi_selected = pyqtSignal(QgsGeometry, QgsCoordinateReferenceSystem)
    cancelled = pyqtSignal()

    def __init__(self, canvas: QgsMapCanvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._points: list[QgsPointXY] = []
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(QColor(255, 100, 0, 128))
        self._rubber_band.setWidth(2)

    # ------------------------------------------------------------------
    # QgsMapTool overrides
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event: QgsMapMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self._points.append(pt)
            self._rubber_band.addPoint(pt, True)

    def canvasMoveEvent(self, event: QgsMapMouseEvent) -> None:
        if self._points:
            pt = self.toMapCoordinates(event.pos())
            self._rubber_band.movePoint(pt)

    def canvasDoubleClickEvent(self, event: QgsMapMouseEvent) -> None:
        if len(self._points) >= 3:
            self._finish_polygon()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if len(self._points) >= 3:
                self._finish_polygon()
        elif event.key() == Qt.Key_Escape:
            self._reset()
            self.cancelled.emit()

    def deactivate(self) -> None:
        self._reset()
        super().deactivate()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _finish_polygon(self) -> None:
        polygon = QgsGeometry.fromPolygonXY([self._points])
        crs = self._canvas.mapSettings().destinationCrs()
        self._reset()
        self.aoi_selected.emit(polygon, crs)
        self._canvas.unsetMapTool(self)

    def _reset(self) -> None:
        self._points.clear()
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
