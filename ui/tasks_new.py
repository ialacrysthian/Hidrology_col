"""
Nuevas tareas para el flujo de 2 pasos: calcular flujo, luego delimitar.
"""
from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsTask, QgsApplication
from ..utils.logger import get_logger

log = get_logger(__name__)


class FlowAccumulationTask(QgsTask):
    """Calcula flujo acumulado sin delimitar cuenca."""
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(str)     # retorna ruta al raster
    finished_err     = pyqtSignal(str)

    def __init__(self, dem_path: str, output_dir: str, fat: int) -> None:
        super().__init__("TC — Calcular flujo acumulado", QgsTask.CanCancel)
        self.dem_path   = dem_path
        self.output_dir = output_dir
        self.fat        = fat
        # El delineator se conserva para que la delimitacion reutilice
        # el flujo ya calculado (sin recalcular fill sinks + r.watershed)
        self.delineator = None

    def run(self) -> bool:
        import traceback
        try:
            from ..core.watershed_delineator import WatershedDelineator

            self.progress_message.emit("Iniciando calculo de flujo...")
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


class WatershedDelimitationTask(QgsTask):
    """Delimita cuenca usando flujo ya calculado."""
    progress_message = pyqtSignal(str)
    finished_ok      = pyqtSignal(object)   # retorna WatershedResult
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
        self.delineator = delineator   # reutiliza flujo ya calculado

    def run(self) -> bool:
        import traceback
        try:
            from ..core.watershed_delineator import WatershedDelineator

            self.progress_message.emit("Iniciando delimitacion...")
            if self.delineator is not None:
                # Reutilizar flujo cacheado — redirigir mensajes a esta task
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
