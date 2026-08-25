"""
Panel principal — TC Calculator.

NUEVO FLUJO (5 pasos):
  1. DEM — cargar archivo GeoTIFF
  2. FLUJO — calcular acumulacion, ver corrientes en el mapa
  3. OUTLET — marcar punto de desembocadura (viendo las corrientes)
  4. DELIMITAR — generar cuenca.shp, subcuencas.shp, drenajes.shp
  5. ESTACIONES — seleccion IDEAM con buffer
"""
from __future__ import annotations

import os

from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QPushButton, QLabel, QToolButton, QShortcut,
    QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QLineEdit, QFileDialog, QSizePolicy, QMessageBox,
    QTableWidget, QTableWidgetItem, QScrollArea, QFrame,
)
from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QFont, QKeySequence
from .progress_dialog import ProgressDialog
from qgis.core import (
    QgsVectorLayer, QgsRasterLayer,
    QgsProject, QgsTask, QgsApplication,
    QgsSingleSymbolRenderer,
    QgsFillSymbol, QgsLineSymbol,
)

from .. import settings as cfg
from .. import i18n
from ..utils.logger import get_logger
from ..core import project_session as session

log = get_logger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
SHP_CNE    = os.path.join(PLUGIN_DIR, "data", "shp", "CNE_IDEAM_estaciones.shp")

# Tema Material cohesivo con ProgressDialog (azul #2196F3).
_STYLESHEET = """
QScrollArea#tcScroll { background: #F4F6F9; border: none; }
QWidget#tcRoot { background: #F4F6F9; }

QLabel#tcHeaderTitle { font-size: 15px; font-weight: 700; color: #12344D; }
QLabel#tcHeaderSub   { font-size: 11px; color: #5B7083; }

QWidget#stepCard {
    background: #FFFFFF;
    border: 1px solid #E3E8EF;
    border-radius: 10px;
}
QWidget#stepCard[state="active"] { border: 1px solid #2196F3; }

QLabel#stepTitle { font-size: 12px; font-weight: 600; color: #23303A; }
QLabel#stepArrow { color: #90A4AE; font-size: 11px; }

QLabel#stepBadge {
    background: #CFD8DC; color: #FFFFFF;
    border-radius: 12px; font-weight: 700; font-size: 11px;
}
QLabel#stepBadge[state="active"] { background: #2196F3; }
QLabel#stepBadge[state="done"]   { background: #2E7D32; }

QPushButton {
    background: #ECEFF3; color: #23303A;
    border: 1px solid #D3DAE2; border-radius: 7px;
    padding: 6px 12px;
}
QPushButton:hover    { background: #E0E5EC; }
QPushButton:disabled { background: #F0F2F5; color: #9AA7B2; border-color: #E7EBF0; }

QPushButton#primaryBtn {
    background: #2196F3; color: #FFFFFF; border: none; font-weight: 600;
}
QPushButton#primaryBtn:hover    { background: #1976D2; }
QPushButton#primaryBtn:disabled { background: #B0BEC5; color: #ECEFF1; }

QPushButton#langBtn {
    background: #FFFFFF; color: #2196F3;
    border: 1px solid #2196F3; border-radius: 7px;
    padding: 4px 8px; font-weight: 600;
}
QPushButton#langBtn:hover { background: #E3F2FD; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #FFFFFF; border: 1px solid #D3DAE2;
    border-radius: 6px; padding: 4px 6px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #2196F3;
}

QTableWidget { border: 1px solid #E3E8EF; border-radius: 6px; background: #FFFFFF; }
QGroupBox { border: none; margin: 0; padding: 0; }
"""


class StepCard(QWidget):
    """Tarjeta de paso: badge numerado + título + estado, contenido plegable.

    Mantiene la API mínima (addWidget/addLayout) que usaba CollapsibleSection.
    Emite ``toggled_open`` al expandirse, para el comportamiento de acordeón.
    """

    STATE_PENDING = "pending"
    STATE_ACTIVE = "active"
    STATE_DONE = "done"

    toggled_open = pyqtSignal(object)

    def __init__(self, number: int, title: str, parent=None) -> None:
        super().__init__(parent)
        self.number = number
        self._state = self.STATE_PENDING
        self.setObjectName("stepCard")
        # Sin este atributo, un QWidget plano IGNORA background/border del QSS
        self.setAttribute(Qt.WA_StyledBackground, True)

        # ── Encabezado clicable ──────────────────────────────────────
        self.header = QWidget()
        self.header.setObjectName("stepHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(10, 8, 10, 8)
        hl.setSpacing(10)

        self.badge = QLabel()
        self.badge.setObjectName("stepBadge")
        self.badge.setFixedSize(24, 24)
        self.badge.setAlignment(Qt.AlignCenter)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("stepTitle")

        self.arrow = QLabel("▾")   # ▾
        self.arrow.setObjectName("stepArrow")

        hl.addWidget(self.badge)
        hl.addWidget(self.title_lbl, 1)
        hl.addWidget(self.arrow)

        # ── Contenido ────────────────────────────────────────────────
        self.content = QWidget()
        self.content.setObjectName("stepContent")
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(12, 4, 12, 12)
        cl.setSpacing(6)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header)
        root.addWidget(self.content)

        self.header.mousePressEvent = self._on_header_click
        self._apply_state_style()

    # -- interacción --------------------------------------------------

    def _on_header_click(self, event) -> None:
        self.set_open(not self.content.isVisible())
        if self.content.isVisible():
            self.toggled_open.emit(self)

    def set_open(self, open_: bool) -> None:
        self.content.setVisible(open_)
        self.arrow.setText("▾" if open_ else "▸")   # ▾ / ▸

    def is_open(self) -> bool:
        return self.content.isVisible()

    def set_state(self, state: str) -> None:
        self._state = state
        self._apply_state_style()

    def _apply_state_style(self) -> None:
        if self._state == self.STATE_DONE:
            self.badge.setText("✓")   # ✓
        elif self.number <= 0:
            self.badge.setText("⚙")   # ⚙ (tarjeta de configuración)
        else:
            self.badge.setText(str(self.number))
        for w in (self, self.badge):
            w.setProperty("state", self._state)
            w.style().unpolish(w)
            w.style().polish(w)

    # -- API de contenido (compat) ------------------------------------

    def addWidget(self, widget) -> None:
        self.content.layout().addWidget(widget)

    def addLayout(self, layout) -> None:
        self.content.layout().addLayout(layout)


# ═══════════════════════════════════════════════════════════════════════
#  Tarea 2 — Calcular flujo acumulado
# ═══════════════════════════════════════════════════════════════════════

class FlowAccumulationTask(QgsTask):
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(str)
    finished_err     = pyqtSignal(str)

    def __init__(self, dem_path: str, output_dir: str, fat: int) -> None:
        super().__init__("TC — Calcular flujo", QgsTask.CanCancel)
        self.dem_path   = dem_path
        self.output_dir = output_dir
        self.fat        = fat
        # Se conserva para que la delimitación (paso 3) reutilice el flujo
        # ya calculado en vez de repetir fill + r.watershed.
        self.delineator = None

    def run(self) -> bool:
        import traceback
        try:
            from ..core.watershed_delineator import WatershedDelineator
            d = WatershedDelineator(
                dem_path    = self.dem_path,
                output_dir  = self.output_dir,
                fat         = self.fat,
                progress_cb = self.progress_message.emit,
            )
            self.delineator = d
            acc_path = d.calculate_flow_accumulation()
            self.finished_ok.emit(acc_path)
            return True
        except Exception as exc:
            detail = traceback.format_exc()
            log.error(f"FlowAccumulationTask:\n{detail}")
            self.finished_err.emit(f"{exc}\n\nDetalle:\n{detail}")
            return False

    def finished(self, result: bool) -> None:
        pass

# ═══════════════════════════════════════════════════════════════════════
#  Tarea 4 — Delimitar cuenca
# ═══════════════════════════════════════════════════════════════════════

class WatershedDelimitationTask(QgsTask):
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(object)
    finished_err     = pyqtSignal(str)

    def __init__(
        self,
        dem_path: str,
        outlet_lon: float,
        outlet_lat: float,
        output_dir: str,
        fat: int,
        delineator=None,
    ) -> None:
        super().__init__("TC — Delimitar cuenca", QgsTask.CanCancel)
        self.dem_path   = dem_path
        self.outlet_lon = outlet_lon
        self.outlet_lat = outlet_lat
        self.output_dir = output_dir
        self.fat        = fat
        self.delineator = delineator

    def run(self) -> bool:
        import traceback
        try:
            from ..core.watershed_delineator import WatershedDelineator
            if self.delineator is not None:
                d = self.delineator
                d._cb = self.progress_message.emit
            else:
                d = WatershedDelineator(
                    dem_path    = self.dem_path,
                    output_dir  = self.output_dir,
                    fat         = self.fat,
                    progress_cb = self.progress_message.emit,
                )
            result = d.delineate_from_outlet(self.outlet_lon, self.outlet_lat)
            self.finished_ok.emit(result)
            return True
        except Exception as exc:
            detail = traceback.format_exc()
            log.error(f"WatershedDelimitationTask:\n{detail}")
            self.finished_err.emit(f"{exc}\n\nDetalle:\n{detail}")
            return False

    def finished(self, result: bool) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════
#  Tarea 3 — Seleccion de estaciones
# ═══════════════════════════════════════════════════════════════════════

class StationsTask(QgsTask):
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(str, dict)
    finished_err     = pyqtSignal(str)

    def __init__(
        self,
        watershed_wkt: str,
        buffer_km: float,
        estado: str,
        variables: list[str],
        output_dir: str,
    ) -> None:
        super().__init__("TC — Seleccionar estaciones", QgsTask.CanCancel)
        self.watershed_wkt = watershed_wkt
        self.buffer_km     = buffer_km
        self.estado        = estado
        self.variables     = variables
        self.output_dir    = output_dir

    def run(self) -> bool:
        try:
            from ..core.station_selector import StationSelector, resumen_preseleccion
            import geopandas as gpd
            import pandas as pd

            self.progress_message.emit(
                f"Buscando estaciones (buffer {self.buffer_km} km)..."
            )
            sel = StationSelector(shp_cne=SHP_CNE)
            resultados = {}
            for var in self.variables:
                self.progress_message.emit(f"  {var}")
                gdf = sel.select(
                    cuenca_wkt = self.watershed_wkt,
                    buffer_km  = self.buffer_km,
                    estado     = "" if self.estado == "Todas" else self.estado,
                    variable   = var,
                )
                resultados[var] = gdf

            resumen = resumen_preseleccion(resultados, self.buffer_km)
            os.makedirs(self.output_dir, exist_ok=True)

            gdfs = [g.assign(VARIABLE=v)
                    for v, g in resultados.items() if not g.empty]
            shp_out = ""
            if gdfs:
                cons = gpd.GeoDataFrame(
                    pd.concat(gdfs, ignore_index=True), crs="EPSG:4326"
                )
                shp_out = os.path.join(self.output_dir, "estaciones.shp")
                cons.to_file(shp_out, encoding="utf-8")
                for var, gdf in resultados.items():
                    if not gdf.empty:
                        cols = [c for c in gdf.columns if c != "geometry"]
                        gdf[cols].to_csv(
                            os.path.join(
                                self.output_dir,
                                f"estaciones_{var.lower()}.csv"
                            ),
                            index=False, encoding="utf-8-sig",
                        )

            self.finished_ok.emit(
                shp_out, {"resumen": resumen.to_dict("records")}
            )
            return True
        except Exception as exc:
            log.error(f"StationsTask: {exc}", exc_info=True)
            self.finished_err.emit(str(exc))
            return False

    def finished(self, result: bool) -> None:
        pass


class LandCoverTask(QgsTask):
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(str, str, str)   # (clipped_shp, raster_manning, raster_cn|"")
    finished_err     = pyqtSignal(str)

    def __init__(
        self,
        land_cover_shp: str,
        cover_field: str,
        manning_map: dict[str, float],
        watershed_shp: str,
        dem_path: str,
        output_dir: str,
        cn_map: dict[str, float] | None = None,
    ) -> None:
        super().__init__("TC — Procesar cobertura de tierra", QgsTask.CanCancel)
        self.land_cover_shp = land_cover_shp
        self.cover_field = cover_field
        self.manning_map = manning_map
        self.watershed_shp = watershed_shp
        self.dem_path = dem_path
        self.output_dir = output_dir
        self.cn_map = cn_map

    def run(self) -> bool:
        import traceback
        try:
            from ..core.land_cover import LandCoverProcessor

            self.progress_message.emit("Procesando cobertura de tierra...")
            processor = LandCoverProcessor(
                land_cover_shp = self.land_cover_shp,
                cover_field    = self.cover_field,
                manning_map    = self.manning_map,
                watershed_shp  = self.watershed_shp,
                dem_path       = self.dem_path,
                output_dir     = self.output_dir,
                cn_map         = self.cn_map,
                progress_cb    = self.progress_message.emit,
            )
            result = processor.process()
            self.finished_ok.emit(
                result["clipped_shp"], result["raster"], result.get("raster_cn", "")
            )
            return True
        except Exception as exc:
            detail = traceback.format_exc()
            log.error(f"LandCoverTask:\n{detail}")
            self.finished_err.emit(f"{exc}\n\nDetalle:\n{detail}")
            return False

    def finished(self, result: bool) -> None:
        pass


class LandCoverClipTask(QgsTask):
    """Recorta la cobertura a la cuenca UNA sola vez, en segundo plano.

    El resultado (GeoPackage + GeoDataFrame en memoria) se cachea para que
    cambiar de campo despliegue las categorías al instante, sin re-leer el
    shapefile nacional ni congelar la UI.
    """
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(str, object)   # (gpkg_path, [campos])
    finished_err     = pyqtSignal(str)

    def __init__(
        self,
        land_cover_shp: str,
        watershed_shp: str,
        watershed_crs_wkt: str | None,
        output_gpkg: str,
    ) -> None:
        super().__init__("TC — Recortar cobertura", QgsTask.CanCancel)
        self.land_cover_shp = land_cover_shp
        self.watershed_shp = watershed_shp
        self.watershed_crs_wkt = watershed_crs_wkt
        self.output_gpkg = output_gpkg
        # Se conserva para que el panel lo cachee (patrón _active_task)
        self.clipped_gdf = None

    def run(self) -> bool:
        import traceback
        try:
            from ..core.land_cover import LandCoverProcessor

            self.progress_message.emit("Leyendo y recortando la cobertura a la cuenca...")
            clipped = LandCoverProcessor.clip_to_watershed(
                self.land_cover_shp, self.watershed_shp, self.watershed_crs_wkt,
            )
            if clipped.empty:
                raise RuntimeError(
                    "La cobertura no intersecta con la cuenca. "
                    "Verifique el shapefile y su CRS."
                )
            os.makedirs(os.path.dirname(self.output_gpkg), exist_ok=True)
            clipped.to_file(self.output_gpkg, driver="GPKG", layer="cobertura")
            self.clipped_gdf = clipped
            fields = [c for c in clipped.columns if c != "geometry"]
            self.progress_message.emit(
                f"Recorte listo: {len(clipped)} polígonos, {len(fields)} campos"
            )
            self.finished_ok.emit(self.output_gpkg, fields)
            return True
        except Exception as exc:
            detail = traceback.format_exc()
            log.error(f"LandCoverClipTask:\n{detail}")
            self.finished_err.emit(f"{exc}\n\nDetalle:\n{detail}")
            return False

    def finished(self, result: bool) -> None:
        pass


class DistributedTcTask(QgsTask):
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(object)
    finished_err     = pyqtSignal(str)

    def __init__(
        self,
        dem_path: str,
        flow_acc_path: str,
        manning_path: str,
        intensity_points_path: str,
        output_dir: str,
        use_kirpich_initial: bool = True,
    ) -> None:
        super().__init__("TC — Calcular Tc distribuido", QgsTask.CanCancel)
        self.dem_path = dem_path
        self.flow_acc_path = flow_acc_path
        self.manning_path = manning_path
        self.intensity_points_path = intensity_points_path
        self.output_dir = output_dir
        self.use_kirpich_initial = use_kirpich_initial

    def run(self) -> bool:
        import traceback
        try:
            from ..core.tc_distributed import DistributedTcCalculator

            calculator = DistributedTcCalculator(
                dem_path=self.dem_path,
                flow_acc_path=self.flow_acc_path,
                manning_path=self.manning_path,
                output_dir=self.output_dir,
                intensity_points_path=self.intensity_points_path,
                intensity_field="intensity_mm_h",
                progress_cb=self.progress_message.emit,
            )
            result = calculator.solve(use_kirpich_initial=self.use_kirpich_initial)
            self.finished_ok.emit({
                "slope_raster": result.slope_raster,
                "depth_raster": result.depth_raster,
                "velocity_raster": result.velocity_raster,
                "cell_time_raster": result.cell_time_raster,
                "accumulated_time_raster": result.accumulated_time_raster,
                "slowest_path_raster": result.slowest_path_raster,
                "intensity_raster": result.intensity_raster,
                "convergence_csv": result.convergence_csv,
                "convergence_plot": result.convergence_plot,
                "intensity_plot": result.intensity_plot,
                "tc_seconds": result.tc_seconds,
                "tc_minutes": result.tc_minutes,
                "tc_hours": result.tc_hours,
                "iterations": result.iterations,
                "final_intensity_mm_h": result.final_intensity_mm_h,
            })
            return True
        except Exception as exc:
            detail = traceback.format_exc()
            log.error(f"DistributedTcTask:\n{detail}")
            self.finished_err.emit(f"{exc}\n\nDetalle:\n{detail}")
            return False

    def finished(self, result: bool) -> None:
        pass


class MorphometryTask(QgsTask):
    """Paso 6 — morfometría de la cuenca GENERAL y de cada SUBCUENCA + Tc de
    Kirpich adoptado + comparación con métodos empíricos de Tc (Ventura,
    Passini, SCS, Témez, Williams, Bransby-Williams, Giandotti,
    Haktanir-Sezen, SCS-Ranser, V.T. Chow, California). El CN para SCS se
    extrae automáticamente del raster de CN (paso 4) si existe — por
    cuenca y por cada subcuenca de forma independiente."""
    progress_message = pyqtSignal(str)
    # (MorphometryResult, csv_path, [TcEmpiricalResult], tc_csv_path,
    #  [(sub_id, MorphometryResult, cn)], subcuencas_csv, [(sub_id, TcEmpiricalResult)], subcuencas_tc_csv)
    finished_ok      = pyqtSignal(object, str, object, str, object, str, object, str)
    finished_err     = pyqtSignal(str)

    def __init__(
        self, cuenca_shp: str, dem_path: str, drenajes_shp: str, csv_path: str,
        subcuencas_shp: str | None = None,
        cn_raster_path: str | None = None,
        curve_number: float | None = None,
    ) -> None:
        super().__init__("TC — Morfometría de la cuenca", QgsTask.CanCancel)
        self.cuenca_shp = cuenca_shp
        self.dem_path = dem_path
        self.drenajes_shp = drenajes_shp
        self.csv_path = csv_path
        self.subcuencas_shp = subcuencas_shp
        self.cn_raster_path = cn_raster_path
        self.curve_number = curve_number

    def run(self) -> bool:
        import traceback
        try:
            from ..core.morphometry import WatershedMorphometry

            calc = WatershedMorphometry(
                cuenca_shp=self.cuenca_shp,
                dem_path=self.dem_path,
                drenajes_shp=self.drenajes_shp,
                progress_cb=self.progress_message.emit,
            )
            result, csv_path, tc_results, tc_csv_path = calc.compute_and_export(
                self.csv_path,
                curve_number=self.curve_number,
                cn_raster_path=self.cn_raster_path,
            )

            sub_full, sub_csv, sub_tc, sub_tc_csv = [], "", [], ""
            if self.subcuencas_shp and os.path.exists(self.subcuencas_shp):
                self.progress_message.emit("Calculando morfometría por subcuenca...")
                out_dir = os.path.dirname(self.csv_path)
                sub_full, sub_csv, sub_tc, sub_tc_csv = calc.compute_and_export_subcuencas(
                    self.subcuencas_shp, out_dir, cn_raster_path=self.cn_raster_path,
                )

            self.finished_ok.emit(
                result, csv_path, tc_results, tc_csv_path,
                sub_full, sub_csv, sub_tc, sub_tc_csv,
            )
            return True
        except Exception as exc:
            detail = traceback.format_exc()
            log.error(f"MorphometryTask:\n{detail}")
            self.finished_err.emit(f"{exc}\n\nDetalle:\n{detail}")
            return False

    def finished(self, result: bool) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════
#  Panel principal
# ═══════════════════════════════════════════════════════════════════════

class MainDialog(QDockWidget):

    def __init__(self, iface, parent=None) -> None:
        super().__init__("TC Calculator", parent)
        self.iface   = iface
        self.canvas  = iface.mapCanvas()

        self._dem_path:   str | None   = None
        self._crs_manager              = None   # gestor de proyecciones
        self._flow_acc_path: str | None = None   # raster de acumulacion
        self._outlet_lon: float | None = None
        self._outlet_lat: float | None = None
        self._ws_result                = None
        self._ws_wkt:     str | None   = None
        self._land_cover_path: str | None = None
        self._land_cover_loaded: bool = False
        self._land_cover_raster_path: str | None = None
        self._land_cover_cn_raster_path: str | None = None
        self._land_cover_clip_path: str | None = None   # GPKG del recorte
        self._land_cover_clip_gdf = None                # cache en memoria
        self._morphometry_csv: str | None = None
        self._tc_adopted_min: float | None = None   # Tc Kirpich — semilla p/ iteración
        self._delineator               = None   # cache de flujo entre pasos
        self._outlet_tool              = None
        self._active_task              = None   # evita garbage collection
        self._progress_dialog           = None
        self._log_messages: list[str] = []
        self._mode = cfg.get(cfg.Keys.PROGRESS_MODE) or "professional"

        # Estado del asistente por pasos (para badges y acordeón)
        self._step_cards: dict[int, StepCard] = {}
        self._auto_step: int | None = None
        self._stations_done = False
        # Callables de retraducción (i18n)
        self._i18n: list = []

        self._build_ui()
        self._connect_signals()
        self._update_buttons()
        self._set_status(i18n.tr("status_ready_title"), i18n.tr("status_ready_text"))

    # ------------------------------------------------------------------
    # Construccion de la UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("tcRoot")
        root.setAttribute(Qt.WA_StyledBackground, True)
        lay  = QVBoxLayout(root)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # ── Encabezado + selector de idioma ───────────────────────────
        header = QWidget()
        hb = QHBoxLayout(header)
        hb.setContentsMargins(4, 2, 4, 4)
        hb.setSpacing(6)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        lbl_title = QLabel("TC Calculator")
        lbl_title.setObjectName("tcHeaderTitle")
        self.lbl_header_sub = QLabel()
        self.lbl_header_sub.setObjectName("tcHeaderSub")
        self._reg(self.lbl_header_sub, "header_sub")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_header_sub)
        hb.addLayout(title_col, 1)

        self.btn_lang = QPushButton()
        self.btn_lang.setObjectName("langBtn")
        self.btn_lang.setFixedWidth(84)
        self.btn_lang.setToolTip("Español / English")
        self.btn_lang.clicked.connect(self._on_toggle_language)
        self._reg(self.btn_lang, "lang_switch")
        hb.addWidget(self.btn_lang, 0, Qt.AlignTop)
        lay.addWidget(header)

        # ── Carpeta de resultados ─────────────────────────────────────
        grp_out = QGroupBox()
        lo = QHBoxLayout(grp_out)
        self.edit_output = QLineEdit(cfg.get(cfg.Keys.OUTPUT_DIR) or "")
        self._reg(self.edit_output, "ph_output", "setPlaceholderText")
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(32)
        btn_browse.clicked.connect(self._on_browse_output)
        self.btn_load_session = QPushButton()
        self._reg(self.btn_load_session, "btn_load_session")
        self._reg(self.btn_load_session, "tip_load_session", "setToolTip")
        self.btn_load_session.clicked.connect(self._on_load_session)
        lo.addWidget(self.edit_output, 1)
        lo.addWidget(btn_browse)
        lo.addWidget(self.btn_load_session)
        lay.addWidget(self._wrap_config("card_output", grp_out))

        # ── Paso 1: DEM ───────────────────────────────────────────────
        grp1 = QGroupBox()
        l1   = QVBoxLayout(grp1)

        row_dem = QHBoxLayout()
        self.edit_dem = QLineEdit(cfg.get(cfg.Keys.DEM_USER_PATH) or "")
        self._reg(self.edit_dem, "ph_dem", "setPlaceholderText")
        btn_dem_browse = QPushButton("...")
        btn_dem_browse.setFixedWidth(32)
        btn_dem_browse.clicked.connect(self._on_browse_dem)
        self.btn_load_dem = QPushButton()
        self._reg(self.btn_load_dem, "btn_load")
        self.btn_load_dem.setFixedWidth(70)
        row_dem.addWidget(self.edit_dem, 1)
        row_dem.addWidget(btn_dem_browse)
        row_dem.addWidget(self.btn_load_dem)
        l1.addLayout(row_dem)

        self.lbl_dem = QLabel()
        self._reg(self.lbl_dem, "lbl_dem_empty")
        self.lbl_dem.setStyleSheet("color:#888;font-style:italic;")
        l1.addWidget(self.lbl_dem)
        lay.addWidget(self._wrap_step(1, "step1_title", grp1))

        # ── Paso 2: Calcular flujo acumulado ──────────────────────────
        grp_flow = QGroupBox()
        lf = QVBoxLayout(grp_flow)
        self.btn_calc_flow = QPushButton()
        self._reg(self.btn_calc_flow, "btn_calc_flow")
        self._reg(self.btn_calc_flow, "tip_calc_flow", "setToolTip")
        self.btn_calc_flow.setEnabled(False)
        self.lbl_flow = QLabel()
        self._reg(self.lbl_flow, "lbl_flow_empty")
        self.lbl_flow.setStyleSheet("color:#888;font-style:italic;")
        lf.addWidget(self.btn_calc_flow)
        lf.addWidget(self.lbl_flow)
        lay.addWidget(self._wrap_step(2, "step2_title", grp_flow))

        # ── Paso 3: Outlet + Delimitacion ─────────────────────────────
        grp2 = QGroupBox()
        l2   = QVBoxLayout(grp2)

        row_out = QHBoxLayout()
        self.btn_outlet = QPushButton()
        self._reg(self.btn_outlet, "btn_outlet")
        self._reg(self.btn_outlet, "tip_outlet", "setToolTip")
        self.btn_outlet.setEnabled(False)
        self.lbl_outlet = QLabel()
        self._reg(self.lbl_outlet, "lbl_outlet_empty")
        self.lbl_outlet.setStyleSheet("color:#888;font-style:italic;")
        row_out.addWidget(self.btn_outlet)
        row_out.addWidget(self.lbl_outlet, 1)
        l2.addLayout(row_out)

        row_fat = QHBoxLayout()
        lbl_fat = QLabel()
        self._reg(lbl_fat, "lbl_fat")
        row_fat.addWidget(lbl_fat)
        self.spin_fat = QSpinBox()
        self.spin_fat.setRange(50, 50000)
        self.spin_fat.setValue(cfg.get_int(cfg.Keys.WATERSHED_FAT))
        self.spin_fat.setSingleStep(50)
        self._reg(self.spin_fat, "tip_fat", "setToolTip")
        row_fat.addWidget(self.spin_fat)
        row_fat.addStretch()
        l2.addLayout(row_fat)

        self.btn_delineate = QPushButton()
        self._reg(self.btn_delineate, "btn_delineate")
        self.btn_delineate.setEnabled(False)
        l2.addWidget(self.btn_delineate)
        lay.addWidget(self._wrap_step(3, "step3_title", grp2))

        # ── Paso 4: Cobertura de tierra ───────────────────────────────
        grp_cover = QGroupBox()
        lc = QVBoxLayout(grp_cover)

        row_cover = QHBoxLayout()
        self.edit_land_cover = QLineEdit("")
        self._reg(self.edit_land_cover, "ph_landcover", "setPlaceholderText")
        btn_cover_browse = QPushButton("...")
        btn_cover_browse.setFixedWidth(32)
        btn_cover_browse.clicked.connect(self._on_browse_land_cover)
        self.btn_load_land_cover = QPushButton()
        self._reg(self.btn_load_land_cover, "btn_load")
        self.btn_load_land_cover.setFixedWidth(70)
        self.btn_load_land_cover.setEnabled(False)
        row_cover.addWidget(self.edit_land_cover, 1)
        row_cover.addWidget(btn_cover_browse)
        row_cover.addWidget(self.btn_load_land_cover)
        lc.addLayout(row_cover)

        row_field = QHBoxLayout()
        lbl_field = QLabel()
        self._reg(lbl_field, "lbl_cover_field")
        row_field.addWidget(lbl_field)
        self.combo_cover_field = QComboBox()
        self.combo_cover_field.setEnabled(False)
        row_field.addWidget(self.combo_cover_field, 1)
        lc.addLayout(row_field)

        self.table_manning = QTableWidget(0, 3)
        _cov_headers = lambda: [i18n.tr("col_category"), "Manning", "CN"]
        self.table_manning.setHorizontalHeaderLabels(_cov_headers())
        self._i18n.append((
            self.table_manning, "headers",
            lambda: self.table_manning.setHorizontalHeaderLabels(_cov_headers()),
        ))
        self.table_manning.setFixedHeight(140)
        lc.addWidget(self.table_manning)

        row_manning = QHBoxLayout()
        self.btn_add_manning = QPushButton()
        self._reg(self.btn_add_manning, "btn_add_row")
        self.btn_remove_manning = QPushButton()
        self._reg(self.btn_remove_manning, "btn_remove_row")
        self.btn_add_manning.setFixedWidth(110)
        self.btn_remove_manning.setFixedWidth(110)
        row_manning.addWidget(self.btn_add_manning)
        row_manning.addWidget(self.btn_remove_manning)
        row_manning.addStretch()
        lc.addLayout(row_manning)

        self.btn_process_cover = QPushButton()
        self._reg(self.btn_process_cover, "btn_process_cover")
        self.btn_process_cover.setEnabled(False)
        lc.addWidget(self.btn_process_cover)

        self.lbl_cover = QLabel()
        self._reg(self.lbl_cover, "lbl_cover_empty")
        self.lbl_cover.setStyleSheet("color:#888;font-style:italic;")
        lc.addWidget(self.lbl_cover)

        lay.addWidget(self._wrap_step(4, "step4_title", grp_cover))

        # ── Paso 5: Estaciones ────────────────────────────────────────
        grp3 = QGroupBox()
        l3   = QVBoxLayout(grp3)

        row_buf = QHBoxLayout()
        lbl_buffer = QLabel()
        self._reg(lbl_buffer, "lbl_buffer")
        row_buf.addWidget(lbl_buffer)
        self.spin_buf = QDoubleSpinBox()
        self.spin_buf.setRange(0, 200)
        self.spin_buf.setValue(float(cfg.get(cfg.Keys.STATIONS_BUFFER_KM)))
        self.spin_buf.setSuffix(" km")
        self.spin_buf.setDecimals(1)
        row_buf.addWidget(self.spin_buf)
        row_buf.addStretch()
        l3.addLayout(row_buf)

        row_est = QHBoxLayout()
        lbl_estado = QLabel()
        self._reg(lbl_estado, "lbl_status_word")
        row_est.addWidget(lbl_estado)
        self.combo_estado = QComboBox()
        # userData = valor canónico usado por la lógica (el dato NO se traduce)
        self.combo_estado.addItem(i18n.tr("estado_activa"), "Activa")
        self.combo_estado.addItem(i18n.tr("estado_suspendida"), "Suspendida")
        self.combo_estado.addItem(i18n.tr("estado_todas"), "Todas")
        self._i18n.append((self.combo_estado, "items", self._retranslate_estado))
        idx = self.combo_estado.findData(cfg.get(cfg.Keys.STATIONS_ESTADO))
        if idx >= 0:
            self.combo_estado.setCurrentIndex(idx)
        row_est.addWidget(self.combo_estado)
        row_est.addStretch()
        l3.addLayout(row_est)

        row_vars = QHBoxLayout()
        self.chk_precip = QCheckBox()
        self.chk_temp   = QCheckBox()
        self.chk_caudal = QCheckBox()
        self._reg(self.chk_precip, "chk_precip")
        self._reg(self.chk_temp, "chk_temp")
        self._reg(self.chk_caudal, "chk_caudal")
        self.chk_precip.setChecked(cfg.get_bool(cfg.Keys.STATIONS_PRECIP))
        self.chk_temp.setChecked(cfg.get_bool(cfg.Keys.STATIONS_TEMP))
        self.chk_caudal.setChecked(cfg.get_bool(cfg.Keys.STATIONS_CAUDAL))
        row_vars.addWidget(self.chk_precip)
        row_vars.addWidget(self.chk_temp)
        row_vars.addWidget(self.chk_caudal)
        row_vars.addStretch()
        l3.addLayout(row_vars)

        self.btn_stations = QPushButton()
        self._reg(self.btn_stations, "btn_stations")
        self.btn_stations.setEnabled(False)
        l3.addWidget(self.btn_stations)
        lay.addWidget(self._wrap_step(5, "step5_title", grp3))

        # ── Paso 6: Morfometría, Tc adoptado y comparación de métodos ──
        grp_morpho = QGroupBox()
        lm = QVBoxLayout(grp_morpho)

        row_cn = QHBoxLayout()
        lbl_cn = QLabel()
        self._reg(lbl_cn, "lbl_curve_number")
        row_cn.addWidget(lbl_cn)
        self.spin_curve_number = QDoubleSpinBox()
        self.spin_curve_number.setRange(0, 100)
        self.spin_curve_number.setSpecialValueText(" ")   # 0 = "no aplicar"
        self.spin_curve_number.setDecimals(0)
        self._reg(self.spin_curve_number, "tip_curve_number", "setToolTip")
        row_cn.addWidget(self.spin_curve_number)
        row_cn.addStretch()
        lm.addLayout(row_cn)

        self.btn_morphometry = QPushButton()
        self._reg(self.btn_morphometry, "btn_morphometry")
        self._reg(self.btn_morphometry, "tip_morphometry", "setToolTip")
        self.btn_morphometry.setEnabled(False)
        lm.addWidget(self.btn_morphometry)

        self.lbl_morphometry = QLabel()
        self._reg(self.lbl_morphometry, "lbl_morphometry_empty")
        self.lbl_morphometry.setWordWrap(True)
        self.lbl_morphometry.setStyleSheet("color:#888;font-style:italic;")
        lm.addWidget(self.lbl_morphometry)

        lay.addWidget(self._wrap_step(6, "step6_title", grp_morpho))

        # ── Estado y vista previa de la cuenca ────────────────────────
        grp_preview = QGroupBox()
        lp = QHBoxLayout(grp_preview)
        lp.setContentsMargins(8, 8, 8, 8)
        lp.setSpacing(10)

        status_col = QVBoxLayout()
        self.status_label = QLabel()
        self.status_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.status_label.setWordWrap(True)
        self.status_text = QLabel()
        self.status_text.setWordWrap(True)
        status_col.addWidget(self.status_label)
        status_col.addWidget(self.status_text)
        status_col.addStretch()
        lp.addLayout(status_col, 1)

        lay.addWidget(self._wrap_config("card_status", grp_preview))
        lay.addStretch()

        # ── Envolver todo en un área desplazable ──────────────────────
        scroll = QScrollArea()
        scroll.setObjectName("tcScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(root)
        self.setWidget(scroll)

        self._mark_primary_buttons()
        self._apply_theme()
        self._refresh_step_states()

    def _connect_signals(self) -> None:
        self.btn_load_dem.clicked.connect(self._on_load_dem)
        self.btn_calc_flow.clicked.connect(self._on_calc_flow)
        self.btn_outlet.clicked.connect(self._on_pick_outlet)
        self.btn_delineate.clicked.connect(self._on_delineate)
        self.btn_load_land_cover.clicked.connect(self._on_load_land_cover)
        self.combo_cover_field.currentIndexChanged.connect(self._on_cover_field_selected)
        self.btn_add_manning.clicked.connect(self._on_add_manning_row)
        self.btn_remove_manning.clicked.connect(self._on_remove_manning_row)
        self.btn_process_cover.clicked.connect(self._on_process_land_cover)
        self.btn_stations.clicked.connect(self._on_find_stations)
        self.btn_morphometry.clicked.connect(self._on_calc_morphometry)
        self._shortcut_toggle_mode = QShortcut(QKeySequence("Ctrl+Shift+W"), self)
        self._shortcut_toggle_mode.activated.connect(self._toggle_progress_mode)

    # ------------------------------------------------------------------
    # Paso 1 — DEM
    # ------------------------------------------------------------------

    def _on_browse_dem(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar DEM",
            self.edit_dem.text() or os.path.expanduser("~"),
            "GeoTIFF (*.tif *.tiff);;Todos los archivos (*)",
        )
        if path:
            self.edit_dem.setText(path)

    def _on_load_dem(self) -> None:
        path = self.edit_dem.text().strip()
        if not path or not os.path.exists(path):
            self._log("Seleccione un archivo DEM valido.")
            return
        cfg.set_value(cfg.Keys.DEM_USER_PATH, path)
        self._set_dem(path)


    def _on_browse_land_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar cobertura de tierra",
            self.edit_land_cover.text() or os.path.expanduser("~"),
            "SHP (*.shp);;Todos los archivos (*)",
        )
        if path:
            self.edit_land_cover.setText(path)
            self.btn_load_land_cover.setEnabled(True)

    def _on_load_land_cover(self) -> None:
        path = self.edit_land_cover.text().strip()
        if not path or not os.path.exists(path):
            self._log("Seleccione un shapefile de cobertura valido.")
            return
        if not self._ws_result or not os.path.exists(self._ws_result.cuenca_shp):
            self._log(
                "Delimite la cuenca primero (paso 3): las categorías se "
                "obtienen del recorte de la cobertura a la cuenca."
            )
            return
        out = self._output_dir()
        if not out:
            return

        self._land_cover_path = path
        self._land_cover_loaded = True
        # Invalidar recortes de cargas anteriores
        self._land_cover_clip_path = None
        self._land_cover_clip_gdf = None
        self.combo_cover_field.clear()
        self.combo_cover_field.setEnabled(False)
        self.table_manning.setRowCount(0)
        self._log(f"Cobertura de tierra: {path}")

        crs_wkt = (
            self._crs_manager.dem_crs.toWkt()
            if self._crs_manager is not None else None
        )
        self.btn_load_land_cover.setEnabled(False)
        self._active_task = LandCoverClipTask(
            land_cover_shp    = path,
            watershed_shp     = self._ws_result.cuenca_shp,
            watershed_crs_wkt = crs_wkt,
            output_gpkg       = os.path.join(out, "cobertura", "landcover_clip.gpkg"),
        )
        self._active_task.progress_message.connect(self._on_land_cover_progress)
        self._active_task.finished_ok.connect(self._on_land_cover_clip_ready)
        self._active_task.finished_err.connect(
            lambda e: self._show_progress_error("Recorte de cobertura", e)
        )
        self._show_progress_dialog("Recortar cobertura a la cuenca", step=1, total=1)
        QgsApplication.taskManager().addTask(self._active_task)

    def _on_land_cover_clip_ready(self, gpkg_path: str, fields: list) -> None:
        # Cachear el GeoDataFrame recortado ANTES de soltar la tarea
        self._land_cover_clip_gdf = getattr(self._active_task, "clipped_gdf", None)
        self._active_task = None
        self._land_cover_clip_path = gpkg_path
        if not self._alive():
            return
        try:
            self._close_progress_dialog()
            self.btn_load_land_cover.setEnabled(True)
            n = len(self._land_cover_clip_gdf) if self._land_cover_clip_gdf is not None else 0
            self._unreg(self.lbl_cover)
            self.lbl_cover.setText(
                f"Cobertura recortada: {n} polígonos en la cuenca"
            )
            self.lbl_cover.setStyleSheet("color:#2a7a2a;font-weight:bold;")

            self.combo_cover_field.clear()
            self.combo_cover_field.addItems(fields)
            self.combo_cover_field.setEnabled(bool(fields))
            if fields:
                self.combo_cover_field.setCurrentIndex(0)
                self._log(f"Campos disponibles: {', '.join(fields)}")
                self._on_cover_field_selected(0)
            else:
                self._log("La cobertura recortada no contiene campos de atributo.")
            self._update_buttons()
            self._save_session()
        except RuntimeError:
            pass

    def _on_add_manning_row(self) -> None:
        row = self.table_manning.rowCount()
        self.table_manning.insertRow(row)
        self.table_manning.setItem(row, 0, QTableWidgetItem(""))
        self.table_manning.setItem(row, 1, QTableWidgetItem(""))
        self.table_manning.setItem(row, 2, QTableWidgetItem(""))

    def _on_remove_manning_row(self) -> None:
        row = self.table_manning.currentRow()
        if row >= 0:
            self.table_manning.removeRow(row)

    def _read_coverage_table(self) -> tuple[dict[str, float], dict[str, float]]:
        """Lee la tabla Categoría/Manning/CN. CN es opcional: si TODAS las
        celdas de esa columna están vacías, no se genera raster de CN
        (retorna dict vacío); si al menos una tiene valor, se exige a
        todas las categorías presentes (LandCoverProcessor ya valida esto
        con un error claro por categoría faltante)."""
        manning: dict[str, float] = {}
        cn: dict[str, float] = {}
        for row in range(self.table_manning.rowCount()):
            item_cat = self.table_manning.item(row, 0)
            item_val = self.table_manning.item(row, 1)
            item_cn = self.table_manning.item(row, 2)
            if item_cat is None or item_val is None:
                continue
            cat = item_cat.text().strip()
            val = item_val.text().strip()
            if not cat or not val:
                continue
            try:
                manning[cat] = float(val.replace(',', '.'))
            except ValueError:
                raise ValueError(
                    f"Valor de Manning inválido en la fila {row + 1}: '{val}'"
                )
            cn_text = item_cn.text().strip() if item_cn is not None else ""
            if cn_text:
                try:
                    cn[cat] = float(cn_text.replace(',', '.'))
                except ValueError:
                    raise ValueError(
                        f"Valor de CN inválido en la fila {row + 1}: '{cn_text}'"
                    )
        if not manning:
            raise ValueError("Defina al menos una categoría de Manning.")
        return manning, cn

    def _on_process_land_cover(self) -> None:
        if not self._ws_result or not self._land_cover_path:
            self._log("Delimite la cuenca y cargue la cobertura antes de procesar.")
            return

        cover_field = self.combo_cover_field.currentText().strip()
        if not cover_field:
            self._log("Seleccione el campo de cobertura a usar.")
            return

        try:
            manning_map, cn_map = self._read_coverage_table()
        except Exception as exc:
            self._log(f"Error en valores de Manning/CN: {exc}")
            return

        out = self._output_dir()
        if not out:
            return

        self.btn_process_cover.setEnabled(False)
        msg = "Procesando cobertura de tierra para generar raster de Manning"
        msg += " y CN..." if cn_map else "..."
        self._log(msg)

        self._active_task = LandCoverTask(
            # Reutiliza el recorte cacheado (rápido); el original solo como fallback
            land_cover_shp = self._land_cover_clip_path or self._land_cover_path,
            cover_field    = cover_field,
            manning_map    = manning_map,
            watershed_shp  = self._ws_result.cuenca_shp,
            dem_path       = self._dem_path,
            output_dir     = os.path.join(out, "cobertura"),
            cn_map         = cn_map or None,
        )
        self._active_task.progress_message.connect(self._on_land_cover_progress)
        self._active_task.finished_ok.connect(self._on_land_cover_ready)
        self._active_task.finished_err.connect(
            lambda e: self._show_progress_error("Cobertura", e)
        )
        self._show_progress_dialog("Procesar cobertura", step=1, total=1)
        QgsApplication.taskManager().addTask(self._active_task)

    def _on_land_cover_ready(self, clipped_shp: str, raster_path: str, raster_cn_path: str) -> None:
        self._active_task = None
        self._land_cover_raster_path = raster_path
        self._land_cover_cn_raster_path = raster_cn_path or None
        if not self._alive():
            return
        try:
            # Cerrar el diálogo modal — sin esto queda abierto bloqueando
            # todo QGIS y parece que el proceso nunca terminó.
            self._close_progress_dialog()
            self.btn_process_cover.setEnabled(True)
            self._set_status("Completado", "Cobertura procesada y raster(es) generado(s).")
            self._log(f"Cobertura procesada: {clipped_shp}")
            self._log(f"Raster de Manning generado: {raster_path}")
            if os.path.exists(clipped_shp):
                self._load_shp(clipped_shp, "Cobertura recortada", fill="220,160,0,50", stroke="180,120,0", poly=True)
            if os.path.exists(raster_path):
                rl = QgsRasterLayer(raster_path, "Manning cobertura")
                if rl.isValid():
                    QgsProject.instance().addMapLayer(rl)
                else:
                    self._log("No se pudo cargar el raster de Manning al mapa.")
            if raster_cn_path and os.path.exists(raster_cn_path):
                self._log(f"Raster de CN generado: {raster_cn_path}")
                rl_cn = QgsRasterLayer(raster_cn_path, "CN cobertura")
                if rl_cn.isValid():
                    QgsProject.instance().addMapLayer(rl_cn)
                else:
                    self._log("No se pudo cargar el raster de CN al mapa.")
            self._update_buttons()
            self._save_session()
        except RuntimeError:
            pass

    def _set_dem(self, path: str) -> None:
        self._dem_path = path
        nombre = os.path.basename(path)
        self._unreg(self.lbl_dem)
        self.lbl_dem.setText(f"DEM cargado: {nombre}")
        self.lbl_dem.setStyleSheet("color:#2a7a2a;font-weight:bold;")
        self._log(f"DEM: {path}")

        # Configurar CRS del proyecto automáticamente
        try:
            from ..core.crs_manager import CRSManager
            self._log("Configurando proyección del proyecto...")
            self._crs_manager = CRSManager(path, self._log)
            self._crs_manager.set_project_crs()
            self._log(f"Proyección establecida: {self._crs_manager.dem_crs.authid()}")
        except Exception as exc:
            self._log(f"  Advertencia al configurar CRS: {exc}")
            self._crs_manager = None

        # Agregar al mapa si no existe ya
        nombres_cargados = [
            lyr.source() for lyr in QgsProject.instance().mapLayers().values()
        ]
        if path not in nombres_cargados:
            rl = QgsRasterLayer(path, "DEM")
            if rl.isValid():
                QgsProject.instance().addMapLayer(rl)
            else:
                self._log("  Advertencia: el DEM no se pudo cargar como capa raster.")
        self._update_buttons()
        self._save_session()

    # ------------------------------------------------------------------
    # Paso 2 — Calcular flujo acumulado
    # ------------------------------------------------------------------

    def _on_calc_flow(self) -> None:
        out = self._output_dir()
        if not out:
            return
        fat = self.spin_fat.value()
        cfg.set_value(cfg.Keys.WATERSHED_FAT, fat)
        self.btn_calc_flow.setEnabled(False)
        self._log(f"Calculando flujo acumulado (FAT = {fat} celdas)...")
        self._set_status("Calculando flujo", "Iniciando cálculo de flujo acumulado...")

        self._active_task = FlowAccumulationTask(
            dem_path   = self._dem_path,
            output_dir = out,
            fat        = fat,
        )
        self._active_task.progress_message.connect(self._on_flow_progress)
        self._active_task.finished_ok.connect(self._on_flow_ready)
        self._active_task.finished_err.connect(
            lambda e: self._show_progress_error("Flujo acumulado", e)
        )
        self._show_progress_dialog("Calcular flujo acumulado", step=1, total=1)
        QgsApplication.taskManager().addTask(self._active_task)

    def _on_flow_progress(self, msg: str) -> None:
        self._log(msg)
        if not self._alive():
            return
        value, subtitle = self._parse_task_progress(msg, task="flow")
        self._update_progress(value, subtitle or msg)

    def _on_land_cover_progress(self, msg: str) -> None:
        self._log(msg)
        if not self._alive():
            return
        value, subtitle = self._parse_task_progress(msg, task="land_cover")
        self._update_progress(value, subtitle or msg)

    def _on_flow_ready(self, acc_path: str) -> None:
        """Se llama cuando el flujo acumulado termina."""
        # Conservar el delineator para que la delimitacion reutilice
        # el flujo ya calculado (la barra continua desde 25%)
        self._delineator = getattr(self._active_task, "delineator", None)
        self._active_task = None
        self._flow_acc_path = acc_path
        if not self._alive():
            return
        try:
            self._close_progress_dialog()
            self.btn_calc_flow.setEnabled(True)
            self._unreg(self.lbl_flow)
            self.lbl_flow.setText("Flujo calculado")
            self.lbl_flow.setStyleSheet("color:#2a7a2a;font-weight:bold;")
            self._log("Cargando raster de acumulacion al mapa...")

            # Cargar el raster de acumulación con un colormap
            rl = QgsRasterLayer(acc_path, "Acumulacion de flujo")
            if rl.isValid():
                # Simple pseudocolor: valores bajos = azul, altos = rojo
                self._apply_accumulation_symbology(rl)
                QgsProject.instance().addMapLayer(rl)
                self._log("Acumulacion visible en el mapa — ahora marque el outlet")
            else:
                self._log("No se pudo cargar el raster de acumulacion")
            self._update_buttons()
            self._save_session()
        except RuntimeError:
            pass

    def _apply_accumulation_symbology(self, layer: QgsRasterLayer) -> None:
        """Se puede mejorar para usar colormap, pero por ahora solo lo carga con estiramiento automático."""
        # El raster se carga con su simbología por defecto (grayscale con estiramiento)
        # Las áreas de mayor acumulación se ven más claras
        pass

    # ------------------------------------------------------------------
    # Paso 3 — Outlet + Delimitacion
    # ------------------------------------------------------------------

    def _on_pick_outlet(self) -> None:
        from .outlet_tool import OutletTool
        if self._outlet_tool is None:
            self._outlet_tool = OutletTool(self.canvas)
            self._outlet_tool.outlet_picked.connect(self._on_outlet_picked)
        self.canvas.setMapTool(self._outlet_tool)
        self._log("Haga clic sobre el cauce para marcar la desembocadura.")

    def _on_outlet_picked(self, x: float, y: float) -> None:
        # Coordenadas en el CRS del proyecto (= CRS del DEM)
        self._outlet_lon = x
        self._outlet_lat = y
        self._unreg(self.lbl_outlet)
        self.lbl_outlet.setText(f"({x:.1f}, {y:.1f})")
        self.lbl_outlet.setStyleSheet("color:#2a7a2a;font-weight:bold;")
        self._log(f"Outlet: ({x:.1f}, {y:.1f})")
        self._update_buttons()

    def _on_delineate(self) -> None:
        out = self._output_dir()
        if not out:
            return
        fat = self.spin_fat.value()
        cfg.set_value(cfg.Keys.WATERSHED_FAT, fat)
        self.btn_delineate.setEnabled(False)
        self._log(f"Iniciando delimitacion (FAT = {fat} celdas)...")

        self._ws_step = 0
        self._set_status("Delimitando", "Procesando delimitación de la cuenca...")

        # Guardar referencia — sin esto Python destruye el task
        # antes de que el hilo lo ejecute (garbage collection)
        self._active_task = WatershedDelimitationTask(
            dem_path   = self._dem_path,
            outlet_lon = self._outlet_lon,
            outlet_lat = self._outlet_lat,
            output_dir = out,
            fat        = fat,
            delineator = self._delineator,   # reutiliza flujo calculado
        )
        self._active_task.progress_message.connect(self._on_ws_progress)
        self._active_task.finished_ok.connect(self._on_ws_ready)
        self._active_task.finished_err.connect(
            lambda e: self._err("Delimitacion", e)
        )
        self._show_progress_dialog("Delimitar cuenca", step=1, total=1)
        QgsApplication.taskManager().addTask(self._active_task)

    def _on_ws_progress(self, msg: str) -> None:
        self._log(msg)
        if not self._alive():
            return
        value, subtitle = self._parse_task_progress(msg, task="watershed")
        self._update_progress(value, subtitle or msg)

    def _on_ws_ready(self, result) -> None:
        self._ws_result = result
        self._active_task = None   # liberar referencia
        if not self._alive():
            return
        try:
            self._close_progress_dialog()
            self._set_status("Completado", "La cuenca se ha delimitado correctamente.")
            self._log(f"Resultados guardados en: {self.edit_output.text()}")
        except RuntimeError:
            return

        # WKT de la cuenca para seleccion de estaciones
        try:
            import geopandas as gpd
            from shapely.ops import unary_union
            gdf = gpd.read_file(result.cuenca_shp)
            # Si el SHP no trae CRS (GRASS a veces omite el .prj),
            # asignar el del DEM antes de reproyectar
            if gdf.crs is None and self._crs_manager is not None:
                gdf = gdf.set_crs(self._crs_manager.dem_crs.toWkt())
            # El selector de estaciones espera WKT en WGS84
            if gdf.crs is not None:
                gdf = gdf.to_crs("EPSG:4326")
            self._ws_wkt = unary_union(gdf.geometry).wkt
            lon0, lat0, lon1, lat1 = gpd.GeoSeries(
                [unary_union(gdf.geometry)]).total_bounds
            self._log(f"  Cuenca en WGS84: ({lon0:.3f},{lat0:.3f}) - ({lon1:.3f},{lat1:.3f})")
        except Exception as exc:
            self._log(f"  Error leyendo cuenca.shp: {exc}")

        # Cargar capas al mapa
        self._load_shp(
            result.cuenca_shp, "Cuenca",
            fill="0,160,0,50", stroke="0,120,0", poly=True
        )
        self._load_shp(
            result.subcuencas_shp, "Subcuencas",
            fill="0,80,200,35", stroke="0,60,160", poly=True
        )
        self._load_shp(
            result.drenajes_shp, "Drenajes",
            fill=None, stroke="0,160,220", poly=False
        )
        self._update_buttons()
        self._save_session()

    # ------------------------------------------------------------------
    # Paso 3 — Estaciones
    # ------------------------------------------------------------------

    def _on_find_stations(self) -> None:
        out = self._output_dir()
        if not out:
            return
        if not self._ws_wkt:
            self._log("Delimite la cuenca primero.")
            return

        variables = []
        if self.chk_precip.isChecked():
            variables.append("PRECIPITACION")
        if self.chk_temp.isChecked():
            variables.append("TEMPERATURA")
        if self.chk_caudal.isChecked():
            variables += ["CAUDAL", "NIVEL"]
        if not variables:
            self._log("Seleccione al menos una variable.")
            return

        buffer = self.spin_buf.value()
        estado = self.combo_estado.currentData() or "Activa"
        cfg.set_value(cfg.Keys.STATIONS_BUFFER_KM, buffer)
        cfg.set_value(cfg.Keys.STATIONS_ESTADO, estado)
        cfg.set_value(cfg.Keys.STATIONS_PRECIP, self.chk_precip.isChecked())
        cfg.set_value(cfg.Keys.STATIONS_TEMP,   self.chk_temp.isChecked())
        cfg.set_value(cfg.Keys.STATIONS_CAUDAL, self.chk_caudal.isChecked())

        self.btn_stations.setEnabled(False)
        self._log(f"Buscando estaciones ({', '.join(variables)}) buffer {buffer} km...")

        self._active_task = StationsTask(
            watershed_wkt = self._ws_wkt,
            buffer_km     = buffer,
            estado        = estado,
            variables     = variables,
            output_dir    = os.path.join(out, "estaciones"),
        )
        self._active_task.progress_message.connect(self._log)
        self._active_task.finished_ok.connect(self._on_stations_ready)
        self._active_task.finished_err.connect(
            lambda e: self._err("Estaciones", e)
        )
        QgsApplication.taskManager().addTask(self._active_task)

    def _on_stations_ready(self, shp_path: str, data: dict) -> None:
        self._active_task = None
        if not self._alive():
            return
        try:
            self.btn_stations.setEnabled(True)
            total = sum(r.get("n_total", 0) for r in data.get("resumen", []))
            self._log(f"Estaciones encontradas: {total}")
            for row in data.get("resumen", []):
                self._log(
                    f"  {row['variable']:14s}: {row['n_total']:3d} total  "
                    f"({row['n_dentro']} dentro / {row['n_buffer']} en buffer)"
                )
            if shp_path and os.path.exists(shp_path):
                lyr = QgsVectorLayer(shp_path, "Estaciones IDEAM", "ogr")
                if lyr.isValid():
                    self._apply_stations_symbology(lyr)
                    QgsProject.instance().addMapLayer(lyr)
            self._stations_done = True
            self._save_session()
        except (RuntimeError, Exception) as exc:
            log.info(f"_on_stations_ready: {exc}")

    # ------------------------------------------------------------------
    # Paso 6 — Morfometría, Tc adoptado (Kirpich) y comparación de métodos
    # ------------------------------------------------------------------

    def _on_calc_morphometry(self) -> None:
        if not self._ws_result or not os.path.exists(self._ws_result.cuenca_shp):
            self._log("Delimite la cuenca primero (paso 3).")
            return
        drenajes = getattr(self._ws_result, "drenajes_shp", None)
        if not drenajes or not os.path.exists(drenajes):
            self._log("No se encontró la red de drenaje (drenajes.shp) del paso 3.")
            return
        out = self._output_dir()
        if not out:
            return

        self.btn_morphometry.setEnabled(False)
        self._log("Calculando morfometría de la cuenca...")

        # 0 = "no aplicar" (setSpecialValueText); None -> SCS (13) queda sin calcular
        cn_val = self.spin_curve_number.value()
        curve_number = cn_val if cn_val > 0 else None

        subcuencas = getattr(self._ws_result, "subcuencas_shp", None)
        if not subcuencas or not os.path.exists(subcuencas):
            subcuencas = None
            self._log("  Sin subcuencas.shp — solo se calculará la cuenca general.")

        if self._land_cover_cn_raster_path:
            self._log(f"  CN se extraerá automáticamente de: {self._land_cover_cn_raster_path}")
        elif curve_number:
            self._log(f"  CN manual = {curve_number:g} (sin raster de CN generado en el paso 4).")

        self._active_task = MorphometryTask(
            cuenca_shp     = self._ws_result.cuenca_shp,
            dem_path       = self._dem_path,
            drenajes_shp   = drenajes,
            csv_path       = os.path.join(out, "morfometria", "morfometria.csv"),
            subcuencas_shp = subcuencas,
            cn_raster_path = self._land_cover_cn_raster_path,
            curve_number   = curve_number,
        )
        self._active_task.progress_message.connect(self._on_morphometry_progress)
        self._active_task.finished_ok.connect(self._on_morphometry_ready)
        self._active_task.finished_err.connect(
            lambda e: self._show_progress_error("Morfometría", e)
        )
        self._show_progress_dialog("Calcular morfometría", step=1, total=1)
        QgsApplication.taskManager().addTask(self._active_task)

    def _on_morphometry_progress(self, msg: str) -> None:
        self._log(msg)
        if not self._alive():
            return
        value, subtitle = self._parse_task_progress(msg, task="morphometry")
        self._update_progress(value, subtitle or msg)

    def _on_morphometry_ready(
        self, result, csv_path: str, tc_results, tc_csv_path: str,
        sub_full, sub_csv: str, sub_tc, sub_tc_csv: str,
    ) -> None:
        self._active_task = None
        if not self._alive():
            return
        try:
            self._close_progress_dialog()
            self.btn_morphometry.setEnabled(True)
            self._morphometry_csv = csv_path
            self._tc_adopted_min = float(result.tc_adoptado_min)
            self._set_status("Completado", "Morfometría y comparación de métodos de Tc calculadas.")

            d = result.to_dict()
            self._log(f"Morfometría (cuenca general) guardada en: {csv_path}")
            self._log(
                f"  Área={d['area_km2']} km²  Perímetro={d['perimetro_km']} km  "
                f"L cauce={d['longitud_cauce_principal_km']} km  "
                f"S cauce={d['pendiente_cauce_principal_pct']}%"
            )
            self._log(
                f"  Kc={d['coef_compacidad_kc']}  Kf={d['factor_forma_kf']}  "
                f"Dd={d['densidad_drenaje_km_km2']} km/km²"
            )
            self._log(f"  Tc Kirpich (adoptado) = {d['tc_kirpich_min']} min")

            self._log(f"Comparación de métodos de Tc guardada en: {tc_csv_path}")
            for r in tc_results:
                tc_txt = f"{r.tc_min:.1f} min" if r.tc_min is not None else "N/D"
                self._log(f"  {r.metodo}: {tc_txt}")

            if sub_full:
                self._log(f"Morfometría de {len(sub_full)} subcuencas guardada en: {sub_csv}")
                self._log(f"Comparación de métodos de Tc por subcuenca guardada en: {sub_tc_csv}")
                for sub_id, sub_result, cn_sub in sub_full:
                    cn_txt = f"{cn_sub:.1f}" if cn_sub is not None else "N/D"
                    self._log(
                        f"  Subcuenca {sub_id}: Área={sub_result.area_km2} km²  "
                        f"Tc Kirpich={sub_result.tc_kirpich_min} min  CN={cn_txt}"
                    )

            sub_txt = f" · {len(sub_full)} subcuencas" if sub_full else ""
            self._unreg(self.lbl_morphometry)
            self.lbl_morphometry.setText(
                f"Área {d['area_km2']:.1f} km² · L cauce {d['longitud_cauce_principal_km']:.1f} km · "
                f"Tc adoptado (Kirpich) = {d['tc_kirpich_min']:.1f} min · "
                f"{len(tc_results)} métodos comparados{sub_txt}"
            )
            self.lbl_morphometry.setStyleSheet("color:#2a7a2a;font-weight:bold;")
            self._update_buttons()
            self._save_session()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _on_browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Carpeta de resultados",
            self.edit_output.text() or os.path.expanduser("~"),
        )
        if folder:
            self.edit_output.setText(folder)
            cfg.set_value(cfg.Keys.OUTPUT_DIR, folder)
            self._maybe_offer_restore(folder)

    # ------------------------------------------------------------------
    # Persistencia de sesión (proyecto QGIS + estado del asistente)
    # ------------------------------------------------------------------

    def _maybe_offer_restore(self, folder: str) -> None:
        """Si la carpeta tiene una sesión previa, ofrece cargarla."""
        if not session.has_session(folder):
            return
        reply = QMessageBox.question(
            self, "TC Calculator",
            "Se encontró una sesión previa en esta carpeta.\n"
            "¿Desea cargarla y continuar desde el último paso?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._load_session(folder)

    def _on_load_session(self) -> None:
        folder = self.edit_output.text().strip()
        if not folder:
            folder = QFileDialog.getExistingDirectory(
                self, "Carpeta con la sesión a cargar",
                os.path.expanduser("~"),
            )
            if not folder:
                return
            self.edit_output.setText(folder)
        if not session.has_session(folder):
            QMessageBox.information(
                self, "TC Calculator",
                "No se encontró una sesión (tc_session.json) en esa carpeta.",
            )
            return
        self._load_session(folder)

    def _load_session(self, folder: str) -> None:
        state = session.load_state(folder)
        if not state:
            self._log("No se pudo leer la sesión.")
            return
        try:
            if session.load_qgis_project(folder):
                self._log("Proyecto QGIS restaurado.")
        except Exception as exc:
            self._log(f"No se pudo cargar el proyecto QGIS: {exc}")
        self._restore_state(state)

    def _collect_state(self) -> dict:
        ws = None
        if self._ws_result is not None:
            snapped = self._ws_result.outlet_snapped
            ws = {
                "dem_copy":       self._ws_result.dem_copy,
                "cuenca_shp":     self._ws_result.cuenca_shp,
                "subcuencas_shp": self._ws_result.subcuencas_shp,
                "drenajes_shp":   self._ws_result.drenajes_shp,
                "outlet_snapped": list(snapped) if snapped else None,
            }
        return {
            "output_dir":             self.edit_output.text().strip(),
            "dem_path":               self._dem_path,
            "flow_acc_path":          self._flow_acc_path,
            "outlet_lon":             self._outlet_lon,
            "outlet_lat":             self._outlet_lat,
            "ws_result":              ws,
            "ws_wkt":                 self._ws_wkt,
            "land_cover_path":        self._land_cover_path,
            "land_cover_clip_path":   self._land_cover_clip_path,
            "land_cover_raster_path": self._land_cover_raster_path,
            "land_cover_cn_raster_path": self._land_cover_cn_raster_path,
            "stations_done":          self._stations_done,
            "morphometry_csv":        self._morphometry_csv,
            "tc_adopted_min":         self._tc_adopted_min,
            "fat":                    self.spin_fat.value(),
            "buffer_km":              self.spin_buf.value(),
            "estado":                 self.combo_estado.currentData() or "Activa",
        }

    def _save_session(self) -> None:
        """Guarda proyecto QGIS + estado tras cada paso. Nunca interrumpe el flujo."""
        out = self.edit_output.text().strip()
        if not out:
            return
        try:
            session.save_state(out, self._collect_state())
            session.save_qgis_project(out)
            self._log(f"Sesión guardada en: {out}")
        except Exception as exc:
            self._log(f"No se pudo guardar la sesión: {exc}")

    def _restore_state(self, state: dict) -> None:
        out = state.get("output_dir") or self.edit_output.text().strip()
        if out:
            self.edit_output.setText(out)

        self._dem_path = state.get("dem_path")
        if self._dem_path:
            self.edit_dem.setText(self._dem_path)
            self._unreg(self.lbl_dem)
            self.lbl_dem.setText(f"DEM cargado: {os.path.basename(self._dem_path)}")
            self.lbl_dem.setStyleSheet("color:#2a7a2a;font-weight:bold;")
            try:
                from ..core.crs_manager import CRSManager
                self._crs_manager = CRSManager(self._dem_path, self._log)
            except Exception:
                self._crs_manager = None

        self._flow_acc_path = state.get("flow_acc_path")
        if self._flow_acc_path:
            self._unreg(self.lbl_flow)
            self.lbl_flow.setText("Flujo calculado")
            self.lbl_flow.setStyleSheet("color:#2a7a2a;font-weight:bold;")

        self._outlet_lon = state.get("outlet_lon")
        self._outlet_lat = state.get("outlet_lat")
        if self._outlet_lon is not None and self._outlet_lat is not None:
            self._unreg(self.lbl_outlet)
            self.lbl_outlet.setText(f"({self._outlet_lon:.1f}, {self._outlet_lat:.1f})")
            self.lbl_outlet.setStyleSheet("color:#2a7a2a;font-weight:bold;")

        ws = state.get("ws_result")
        if ws:
            from ..core.watershed_delineator import WatershedResult
            snapped = ws.get("outlet_snapped")
            self._ws_result = WatershedResult(
                dem_copy       = ws.get("dem_copy", ""),
                cuenca_shp     = ws.get("cuenca_shp", ""),
                subcuencas_shp = ws.get("subcuencas_shp", ""),
                drenajes_shp   = ws.get("drenajes_shp", ""),
                outlet_snapped = tuple(snapped) if snapped else (None, None),
            )
        self._ws_wkt = state.get("ws_wkt")

        self._land_cover_path = state.get("land_cover_path")
        if self._land_cover_path:
            self.edit_land_cover.setText(self._land_cover_path)
            self.btn_load_land_cover.setEnabled(True)
            self._unreg(self.lbl_cover)
            self.lbl_cover.setText(
                f"Cobertura cargada: {os.path.basename(self._land_cover_path)}"
            )
            self.lbl_cover.setStyleSheet("color:#2a7a2a;font-weight:bold;")
        self._land_cover_raster_path = state.get("land_cover_raster_path")
        self._land_cover_cn_raster_path = state.get("land_cover_cn_raster_path")

        # Recorte de cobertura cacheado: repoblar campos y categorías
        clip_path = state.get("land_cover_clip_path")
        if clip_path and os.path.exists(clip_path):
            self._land_cover_clip_path = clip_path
            try:
                gdf = self._clip_gdf()   # lee el GPKG (pequeño) y lo cachea
                fields = [c for c in gdf.columns if c != "geometry"]
                self.combo_cover_field.clear()
                self.combo_cover_field.addItems(fields)
                self.combo_cover_field.setEnabled(bool(fields))
                if fields:
                    self.combo_cover_field.setCurrentIndex(0)
                    self._on_cover_field_selected(0)
            except Exception as exc:
                self._log(f"No se pudo restaurar el recorte de cobertura: {exc}")

        self._stations_done = bool(state.get("stations_done"))

        self._morphometry_csv = state.get("morphometry_csv")
        self._tc_adopted_min = state.get("tc_adopted_min")
        if self._morphometry_csv and os.path.exists(self._morphometry_csv):
            self._unreg(self.lbl_morphometry)
            tc_txt = f"{self._tc_adopted_min:.1f} min" if self._tc_adopted_min else "?"
            self.lbl_morphometry.setText(
                f"Morfometría restaurada · Tc adoptado (Kirpich) = {tc_txt}"
            )
            self.lbl_morphometry.setStyleSheet("color:#2a7a2a;font-weight:bold;")

        if state.get("fat") is not None:
            self.spin_fat.setValue(int(state["fat"]))
        if state.get("buffer_km") is not None:
            self.spin_buf.setValue(float(state["buffer_km"]))
        est = state.get("estado")
        if est:
            idx = self.combo_estado.findData(est)
            if idx >= 0:
                self.combo_estado.setCurrentIndex(idx)

        self._update_buttons()
        self._set_status("Sesión restaurada", "Continúe desde el último paso completado.")
        self._log("Sesión restaurada — puede continuar el flujo.")

    def _output_dir(self) -> str | None:
        folder = self.edit_output.text().strip()
        if not folder:
            self._log("Defina la carpeta de resultados primero.")
            return None
        os.makedirs(folder, exist_ok=True)
        cfg.set_value(cfg.Keys.OUTPUT_DIR, folder)
        return folder

    def _update_buttons(self) -> None:
        has_dem    = self._dem_path is not None
        has_flow   = self._flow_acc_path is not None
        has_outlet = self._outlet_lon is not None
        has_ws     = self._ws_result is not None
        has_cover  = self._land_cover_path is not None
        self.btn_calc_flow.setEnabled(has_dem)
        self.btn_outlet.setEnabled(has_flow)      # solo si hay flujo
        self.btn_delineate.setEnabled(has_flow and has_outlet)
        self.btn_load_land_cover.setEnabled(bool(self.edit_land_cover.text().strip()))
        self.btn_process_cover.setEnabled(has_ws and has_cover)
        self.btn_stations.setEnabled(has_ws)
        has_drenajes = has_ws and bool(getattr(self._ws_result, "drenajes_shp", None))
        self.btn_morphometry.setEnabled(has_drenajes)
        self._refresh_step_states()

    def _show_progress_dialog(self, title: str, step: int = 1, total: int = 1) -> None:
        if self._progress_dialog is not None:
            try:
                self._progress_dialog.close()
            except Exception:
                pass
        self._progress_dialog = ProgressDialog(self._mode, parent=self)
        self._progress_dialog.set_step(step, total, title)
        self._progress_dialog.set_progress(0, "Iniciando...")
        self._progress_dialog.canceled.connect(self._on_progress_canceled)
        self._progress_dialog.show()

    def _on_progress_canceled(self) -> None:
        if self._active_task is None:
            return
        try:
            self._log("Cancelando tarea en segundo plano...")
            self._set_status("Cancelando", "Intentando detener el proceso...")
            if hasattr(self._active_task, "cancel"):
                self._active_task.cancel()
            elif hasattr(self._active_task, "setCanceled"):
                self._active_task.setCanceled(True)
        except Exception as exc:
            self._log(f"No se pudo cancelar la tarea: {exc}")

    def _toggle_progress_mode(self) -> None:
        self._mode = "fun" if self._mode == "professional" else "professional"
        cfg.set_value(cfg.Keys.PROGRESS_MODE, self._mode)
        self._log(f"Modo de progreso cambiado a: {self._mode}")
        if self._progress_dialog is not None:
            self._close_progress_dialog()
            self._show_progress_dialog("Cambiando modo", step=1, total=1)
            self._progress_dialog.set_progress(0, "Modo actualizado")

    def _update_progress(self, value: int, subtitle: str | None = None) -> None:
        if self._progress_dialog is None:
            return
        try:
            self._progress_dialog.set_progress(value, subtitle)
            if subtitle:
                self._set_status("En progreso", subtitle)
        except Exception:
            pass

    def _parse_task_progress(self, msg: str, task: str) -> tuple[int, str | None]:
        value = 0
        subtitle = msg
        if "%" in msg:
            try:
                value = int(msg.strip().split("%", 1)[0].strip())
            except ValueError:
                value = 0
        elif "Paso" in msg and "/" in msg:
            try:
                parts = msg.split("Paso", 1)[1].strip().split()
                nums = parts[0].split("/")
                current = int(nums[0])
                total = int(nums[1])
                value = int(((current - 1) / total) * 100)
            except Exception:
                value = 0
        elif msg.lower().startswith("completado") or "listo" in msg.lower():
            value = 100
        return value, subtitle

    def _show_progress_error(self, title: str, error: str) -> None:
        self._active_task = None
        # Recalcular botones desde el estado real: sin esto, el botón del
        # paso fallido quedaba deshabilitado y no se podía reintentar.
        if self._alive():
            try:
                self._update_buttons()
                self._set_status(f"Error en {title}", error.splitlines()[0] if error else "")
            except RuntimeError:
                pass
        if self._progress_dialog is None:
            self._log(f"{title}: {error}")
            return
        try:
            self._progress_dialog.append_log(f"ERROR: {error}")
            self._progress_dialog.show_error(f"Error en {title}")
        except Exception:
            pass

    def _close_progress_dialog(self) -> None:
        if self._progress_dialog is None:
            return
        try:
            self._progress_dialog.close()
        except Exception:
            pass
        finally:
            self._progress_dialog = None

    def _set_status(self, title: str, text: str) -> None:
        if not self._alive():
            return
        try:
            self.status_label.setText(f"{i18n.tr('lbl_status_word')} {title}")
            self.status_text.setText(text)
        except RuntimeError:
            pass

    def _wrap_config(self, key: str, widget: QWidget) -> QWidget:
        """Tarjeta de configuración (badge ⚙), fuera del acordeón de pasos."""
        if isinstance(widget, QGroupBox):
            widget.setTitle("")
        card = StepCard(0, i18n.tr(key))
        self._reg(card.title_lbl, key)
        card.addWidget(widget)
        card.set_open(True)
        return card

    def _wrap_step(self, number: int, key: str, widget: QWidget) -> QWidget:
        """Tarjeta de paso numerada, integrada en el acordeón."""
        if isinstance(widget, QGroupBox):
            widget.setTitle("")
        card = StepCard(number, i18n.tr(key))
        self._reg(card.title_lbl, key)
        card.addWidget(widget)
        card.toggled_open.connect(self._on_step_opened)
        card.set_open(number == 1)   # solo el primer paso abierto al inicio
        self._step_cards[number] = card
        return card

    # ------------------------------------------------------------------
    # Idioma (i18n)
    # ------------------------------------------------------------------

    def _reg(self, widget, key: str, setter: str = "setText"):
        """Registra un widget traducible y le aplica el texto del idioma actual.

        Idempotente por (widget, setter): re-registrar reemplaza la clave.
        """
        self._unreg(widget, setter)

        def apply() -> None:
            getattr(widget, setter)(i18n.tr(key))
        self._i18n.append((widget, setter, apply))
        apply()
        return widget

    def _unreg(self, widget, setter: str = "setText") -> None:
        """Quita el registro i18n de un widget cuyo texto pasó a ser dinámico.

        Sin esto, cambiar de idioma re-escribiría 'Sin DEM cargado' sobre
        una etiqueta que ya muestra 'DEM cargado: x.tif'.
        """
        self._i18n = [
            entry for entry in self._i18n
            if not (entry[0] is widget and entry[1] == setter)
        ]

    def _retranslate_estado(self) -> None:
        for i, canon in enumerate(("Activa", "Suspendida", "Todas")):
            self.combo_estado.setItemText(i, i18n.tr(f"estado_{canon.lower()}"))

    def _retranslate(self) -> None:
        for _widget, _setter, fn in list(self._i18n):
            try:
                fn()
            except Exception:
                pass

    def _on_toggle_language(self) -> None:
        i18n.set_language("en" if i18n.get_language() == "es" else "es")
        self._retranslate()

    def _on_step_opened(self, card: "StepCard") -> None:
        """Acordeón: al abrir un paso, colapsa los demás."""
        for other in self._step_cards.values():
            if other is not card:
                other.set_open(False)

    def _open_step(self, number: int) -> None:
        for n, card in self._step_cards.items():
            card.set_open(n == number)

    def _refresh_step_states(self) -> None:
        """Actualiza los badges (pendiente/activo/hecho) y auto-abre el paso activo."""
        done = {
            1: self._dem_path is not None,
            2: self._flow_acc_path is not None,
            3: self._ws_result is not None,
            4: self._land_cover_raster_path is not None,
            5: self._stations_done,
            6: self._morphometry_csv is not None,
        }
        active = next((n for n in range(1, 7) if not done.get(n)), None)
        for n, card in self._step_cards.items():
            if done.get(n):
                card.set_state(StepCard.STATE_DONE)
            elif n == active:
                card.set_state(StepCard.STATE_ACTIVE)
            else:
                card.set_state(StepCard.STATE_PENDING)
        # Auto-abrir el paso activo solo cuando cambia (respeta clics manuales)
        if active is not None and active != self._auto_step:
            self._auto_step = active
            self._open_step(active)

    def _mark_primary_buttons(self) -> None:
        """Resalta la acción principal de cada paso."""
        for btn in (
            self.btn_load_dem, self.btn_calc_flow, self.btn_delineate,
            self.btn_process_cover, self.btn_stations,
            self.btn_morphometry,
        ):
            btn.setObjectName("primaryBtn")

    def _apply_theme(self) -> None:
        self.setStyleSheet(_STYLESHEET)

    def _clip_gdf(self):
        """GeoDataFrame del recorte cobertura∩cuenca (memoria o GPKG en disco)."""
        if self._land_cover_clip_gdf is not None:
            return self._land_cover_clip_gdf
        if self._land_cover_clip_path and os.path.exists(self._land_cover_clip_path):
            import geopandas as gpd
            self._land_cover_clip_gdf = gpd.read_file(self._land_cover_clip_path)
            return self._land_cover_clip_gdf
        return None

    def _on_cover_field_selected(self, index: int) -> None:
        field_name = self.combo_cover_field.currentText().strip()
        if not field_name:
            self.table_manning.setRowCount(0)
            return

        categories = []
        try:
            gdf = self._clip_gdf()
            if gdf is None:
                self._log("Cargue la cobertura primero (se recorta a la cuenca).")
                self.table_manning.setRowCount(0)
                return

            from ..core.land_cover import LandCoverProcessor
            categories = LandCoverProcessor.categories(gdf, field_name)
            self._log(
                f"Campo '{field_name}': {len(categories)} categorías dentro de la cuenca"
            )
        except Exception as exc:
            self._log(f"Error al obtener categorías desde la cobertura: {exc}")
            categories = []

        self._populate_manning_table(categories)

    def _populate_manning_table(self, categories: list[str]) -> None:
        self.table_manning.setRowCount(0)
        for cat in categories:
            if not cat:
                continue
            row = self.table_manning.rowCount()
            self.table_manning.insertRow(row)
            self.table_manning.setItem(row, 0, QTableWidgetItem(cat))
            self.table_manning.setItem(row, 1, QTableWidgetItem(""))
            self.table_manning.setItem(row, 2, QTableWidgetItem(""))

    def _alive(self) -> bool:
        """Verifica que el widget no haya sido destruido por Qt."""
        try:
            import sip
            return not sip.isdeleted(self)
        except Exception:
            return True

    def _log(self, msg: str) -> None:
        if not self._alive():
            log.info(msg)
            return
        try:
            if self._progress_dialog is not None:
                self._progress_dialog.append_log(msg)
            log.info(msg)
        except RuntimeError:
            log.info(msg)

    def _err(self, step: str, msg: str) -> None:
        self._log(f"ERROR en {step}: {msg}")
        try:
            if self._alive():
                self._set_status(f"Error en {step}", msg)
                self._update_buttons()
        except RuntimeError:
            pass

    def _load_shp(
        self, path: str, name: str,
        fill: str | None, stroke: str,
        poly: bool = True,
    ) -> None:
        if not path or not os.path.exists(path):
            self._log(f"  No encontrado: {name}")
            return
        try:
            lyr = QgsVectorLayer(path, name, "ogr")
            if not lyr.isValid():
                self._log(f"  Capa no valida: {path}")
                return
            if poly and fill:
                sym = QgsFillSymbol.createSimple({
                    "color": fill,
                    "outline_color": stroke,
                    "outline_width": "0.6",
                })
            else:
                sym = QgsLineSymbol.createSimple({"color": stroke, "width": "0.5"})
            lyr.setRenderer(QgsSingleSymbolRenderer(sym))
            QgsProject.instance().addMapLayer(lyr)
            self._log(f"  Capa cargada: {name}")
        except Exception as exc:
            self._log(f"  Error cargando {name}: {exc}")

    def _apply_stations_symbology(self, layer: QgsVectorLayer) -> None:
        try:
            from qgis.core import (
                QgsCategorizedSymbolRenderer,
                QgsRendererCategory,
                QgsMarkerSymbol,
            )
            colors = {
                "PRECIPITACION": "0,100,220",
                "TEMPERATURA":   "220,60,0",
                "CAUDAL":        "0,160,120",
                "NIVEL":         "0,140,100",
            }
            cats = []
            for var, color in colors.items():
                sym = QgsMarkerSymbol.createSimple({
                    "color": color, "size": "4",
                    "outline_color": "255,255,255", "outline_width": "0.4",
                    "name": "circle",
                })
                cats.append(QgsRendererCategory(var, sym, var))
            layer.setRenderer(QgsCategorizedSymbolRenderer("VARIABLE", cats))
        except Exception:
            pass
