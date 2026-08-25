import os
import sys
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication

PLUGIN_DIR = os.path.dirname(__file__)
CNE_PATH   = os.path.join(PLUGIN_DIR, "data", "CNE_IDEAM.xls")


class TCCalculatorPlugin:
    def __init__(self, iface):
        self.iface         = iface
        self._actions: list[QAction] = []
        self._toolbar      = None
        self._main_dialog  = None

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self) -> None:
        self._ensure_dependencies()
        self._ensure_cne_shp()

        self._toolbar = self.iface.addToolBar("TC Calculator")
        self._toolbar.setObjectName("TCCalculatorToolbar")

        self._add_action(
            icon_path=os.path.join(PLUGIN_DIR, "ui", "resources", "icons", "plugin_icon.png"),
            text="TC Calculator",
            callback=self.run,
            tooltip="Abrir TC Calculator",
            add_to_toolbar=True,
            add_to_menu=True,
        )
        self._add_action(
            icon_path=None,
            text="Configuración",
            callback=self.open_settings,
            tooltip="Configurar parámetros",
            add_to_toolbar=False,
            add_to_menu=True,
        )

    def unload(self) -> None:
        for action in self._actions:
            self.iface.removePluginMenu("TC Calculator", action)
            if self._toolbar:
                self._toolbar.removeAction(action)
        if self._toolbar:
            del self._toolbar
            self._toolbar = None
        if self._main_dialog:
            self.iface.removeDockWidget(self._main_dialog)
            self._main_dialog.deleteLater()
            self._main_dialog = None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def run(self) -> None:
        if self._main_dialog is None:
            from .ui.main_dialog import MainDialog
            self._main_dialog = MainDialog(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(0x1, self._main_dialog)   # LeftDockWidgetArea
        self._main_dialog.show()
        self._main_dialog.raise_()

    def open_settings(self) -> None:
        from .ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.iface.mainWindow())
        dlg.exec_()

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    def _ensure_dependencies(self) -> None:
        required = ["requests", "pandas", "numpy", "scipy", "geopandas", "shapely",
                    "openpyxl", "xlrd", "rasterio", "matplotlib", "pysheds"]
        missing  = [p for p in required
                    if __import__("importlib.util").util.find_spec(p) is None]
        if missing:
            reply = QMessageBox.question(
                self.iface.mainWindow(),
                "TC Calculator — Dependencias faltantes",
                f"Paquetes necesarios:\n\n  {', '.join(missing)}\n\n¿Instalar ahora?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                import subprocess
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--user"] + missing,
                    check=True,
                )
                QMessageBox.information(
                    self.iface.mainWindow(), "Listo",
                    "Reinicia QGIS para activar los paquetes instalados.",
                )

    def _ensure_cne_shp(self) -> None:
        """Genera el SHP del CNE al primer uso si no existe."""
        shp_dir = os.path.join(PLUGIN_DIR, "data", "shp")
        shp_all = os.path.join(shp_dir, "CNE_IDEAM_estaciones.shp")
        if os.path.exists(shp_all):
            return
        if not os.path.exists(CNE_PATH):
            return
        try:
            import pandas as pd
            import geopandas as gpd
            from shapely.geometry import Point

            df = pd.read_excel(CNE_PATH, sheet_name="CNE", dtype=str)
            df.columns = df.columns.str.strip()
            for col in ["latitud", "longitud", "altitud"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["latitud", "longitud"])
            df["altitud"] = df["altitud"].fillna(0)
            df["CODIGO"]  = df["CODIGO"].str.strip()

            gdf = gpd.GeoDataFrame({
                "CODIGO":     df["CODIGO"],
                "NOMBRE":     df["nombre"].str.strip(),
                "CATEGORIA":  df["CATEGORIA"].str.strip(),
                "TECNOLOGIA": df["TECNOLOGIA"].str.strip(),
                "ESTADO":     df["ESTADO"].str.strip(),
                "ALTITUD_M":  df["altitud"].astype(float),
                "LATITUD":    df["latitud"],
                "LONGITUD":   df["longitud"],
                "DEPARTAMEN": df["DEPARTAMENTO"].str.strip(),
                "MUNICIPIO":  df["MUNICIPIO"].str.strip(),
                "AREA_HIDRO": df["AREA_HIDROGRAFICA"].str.strip(),
                "ZONA_HIDRO": df["ZONA_HIDROGRAFICA"].str.strip(),
                "SUBZONA":    df["SUBZONA_HIDROGRAFICA"].str.strip(),
                "CORRIENTE":  df["CORRIENTE"].str.strip(),
                "geometry":   [Point(lon, lat)
                               for lon, lat in zip(df["longitud"], df["latitud"])],
            }, crs="EPSG:4326")

            os.makedirs(shp_dir, exist_ok=True)
            gdf.to_file(shp_all, encoding="utf-8")
        except Exception as exc:
            pass   # fallo silencioso — el diálogo mostrará el error al usar

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add_action(self, icon_path, text, callback, tooltip="",
                    add_to_toolbar=True, add_to_menu=True) -> QAction:
        icon   = QIcon(icon_path) if icon_path and os.path.exists(icon_path) else QIcon()
        action = QAction(icon, text, self.iface.mainWindow())
        action.setToolTip(tooltip)
        action.triggered.connect(callback)
        if add_to_toolbar and self._toolbar:
            self._toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu("TC Calculator", action)
        self._actions.append(action)
        return action
