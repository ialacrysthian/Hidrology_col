"""
Delimitación de cuencas con GRASS (via QGIS Processing).

Sigue el flujo clásico de delimitación automática:
    r.fill.dir  →  r.watershed  →  r.water.outlet  →  r.to.vect

Referencia: https://acolita.com/delimitar-automaticamente-una-cuenca-hidrografica-en-qgis-3/

Todas las coordenadas se manejan en el CRS del DEM (que es también el CRS
del proyecto — ver CRSManager). No hay conversiones a WGS84.
"""
from __future__ import annotations
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Callable

import numpy as np
from osgeo import gdal

from ..utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class WatershedResult:
    dem_copy:       str
    cuenca_shp:     str
    subcuencas_shp: str
    drenajes_shp:   str
    outlet_snapped: tuple


class WatershedDelineator:
    """
    Delimita cuencas hidrológicas desde un DEM usando GRASS.

    Productos FINALES en output_dir:
        dem.tif, cuenca.shp, subcuencas.shp, drenajes.shp

    Los rasters intermedios van a una carpeta temporal y se borran
    automáticamente al terminar.
    """

    def __init__(
        self,
        dem_path: str,
        output_dir: str,
        fat: int = 500,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.dem_path   = dem_path
        self.output_dir = output_dir
        self.fat        = fat
        self._cb        = progress_cb or log.info
        os.makedirs(output_dir, exist_ok=True)
        self._tmp = tempfile.mkdtemp(prefix="tc_ws_")

        # "grass7" en QGIS <= 3.34, "grass" en QGIS >= 3.36
        self._grass = self._detect_grass_prefix()

        # Cachés — se calculan una sola vez por instancia
        self._filled_path:   str | None = None
        self._drainage_path: str | None = None
        self._flow_acc_path: str | None = None
        self._basin_path:    str | None = None
        self._stream_path:   str | None = None
        self._ws_raster:     str | None = None

    # ------------------------------------------------------------------
    # Pipeline público — 2 pasos
    # ------------------------------------------------------------------

    def calculate_flow_accumulation(self) -> str:
        """
        r.fill.dir + r.watershed. Retorna ruta al raster de acumulación.
        Genera también drenaje (direcciones), subcuencas y cauces en un
        solo llamado a r.watershed.
        """
        self._cb("Paso 1/4  Copiando DEM a carpeta de resultados...")
        self._copy_dem()

        self._cb("Paso 2/4  Rellenando depresiones (r.fill.dir)...")
        filled = self._t("filled.tif")
        r = self._run(f"{self._grass}:r.fill.dir", {
            "input":     self.dem_path,
            "format":    0,
            "output":    filled,
            "direction": self._t("filldir.tif"),
            "areas":     self._t("fillareas.tif"),
        })
        self._filled_path = r["output"]

        self._cb("Paso 3/4  Calculando flujo (r.watershed)...")
        r = self._run(f"{self._grass}:r.watershed", {
            "elevation":    self._filled_path,
            "threshold":    self.fat,
            "accumulation": self._t("acc.tif"),
            "drainage":     self._t("drain.tif"),
            "basin":        self._t("basin.tif"),
            "stream":       self._t("stream.tif"),
            "-s":           True,   # single flow direction (D8)
            "-a":           True,   # acumulación en valores absolutos
        })
        self._flow_acc_path = r["accumulation"]   # sigue en temp: reuso interno (paso 3)
        self._drainage_path = r["drainage"]
        self._basin_path    = r["basin"]
        self._stream_path   = r["stream"]

        self._cb("Flujo acumulado listo")

        # Persistir una copia en output_dir: el temp puede desaparecer con
        # cualquier limpieza del SO o reinicio, y el cálculo distribuido de
        # Tc (paso 8) lo necesita más adelante en la sesión — incluso tras
        # cerrar y reabrir QGIS. self._flow_acc_path NO cambia (sigue
        # apuntando al temp, que es lo que reutiliza delineate_from_outlet).
        persisted = os.path.join(self.output_dir, "acumulacion_flujo.tif")
        try:
            shutil.copy2(self._flow_acc_path, persisted)
            return persisted
        except OSError as exc:
            self._cb(f"  Advertencia: no se pudo persistir la acumulación de flujo: {exc}")
            return self._flow_acc_path

    def delineate_from_outlet(self, outlet_x: float, outlet_y: float) -> WatershedResult:
        """
        Delimita la cuenca que drena al punto (x, y) — coordenadas en el
        CRS del DEM/proyecto.
        """
        try:
            if self._flow_acc_path is None:
                self.calculate_flow_accumulation()

            self._cb("Paso 3/4  Ajustando outlet al cauce más cercano...")
            snapped = self._snap_outlet(self._flow_acc_path, outlet_x, outlet_y)

            self._cb("Paso 4/4  Delimitando cuenca (r.water.outlet)...")
            cuenca, subcuencas = self._delineate(snapped[0], snapped[1])

            self._cb("Paso 4/4  Extrayendo red de drenajes...")
            drenajes = self._channels(cuenca)

            # GRASS a veces no escribe el .prj — garantizarlo con el CRS del DEM
            for shp in (cuenca, subcuencas, drenajes):
                self._ensure_prj(shp)

            self._cb(f"Completado. Resultados en: {self.output_dir}")
            return WatershedResult(
                dem_copy       = os.path.join(self.output_dir, "dem.tif"),
                cuenca_shp     = cuenca,
                subcuencas_shp = subcuencas,
                drenajes_shp   = drenajes,
                outlet_snapped = snapped,
            )
        finally:
            self._cleanup_tmp()
            self._cb("Temporales eliminados.")

    # ------------------------------------------------------------------
    # Wrapper de processing (seguro desde QgsTask)
    # ------------------------------------------------------------------

    def _run(self, alg_id: str, params: dict) -> dict:
        import processing
        from qgis.core import QgsProcessingContext, QgsProcessingFeedback

        ctx      = QgsProcessingContext()
        feedback = QgsProcessingFeedback()

        last_pct = [-1]

        def on_progress(pct: float) -> None:
            p = int(pct)
            if p != last_pct[0] and p % 20 == 0:
                self._cb(f"    {p}%")
                last_pct[0] = p

        feedback.progressChanged.connect(on_progress)

        short = alg_id.split(":")[-1]
        self._cb(f"  > {short}")
        try:
            result = processing.run(alg_id, params, context=ctx, feedback=feedback)
            self._cb(f"  > {short} OK")
            return result
        except Exception as exc:
            self._cb(f"  ERROR en {alg_id}: {exc}")
            raise

    @staticmethod
    def _detect_grass_prefix() -> str:
        """
        Retorna el prefijo del proveedor GRASS según la versión de QGIS:
        "grass7" (QGIS <= 3.34) o "grass" (QGIS >= 3.36).
        """
        from qgis.core import QgsApplication
        reg = QgsApplication.processingRegistry()
        for prefix in ("grass7", "grass"):
            if reg.algorithmById(f"{prefix}:r.watershed") is not None:
                return prefix
        raise RuntimeError(
            "El proveedor GRASS no está disponible en QGIS Processing. "
            "Actívelo en: Complementos → Administrar → GRASS GIS provider."
        )

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _t(self, name: str) -> str:
        return os.path.join(self._tmp, name)

    def _cleanup_tmp(self) -> None:
        """
        Borra los temporales EXCEPTO el raster de acumulación, que puede
        estar cargado como capa en el mapa de QGIS.
        """
        keep = {self._flow_acc_path}
        for f in os.listdir(self._tmp):
            path = os.path.join(self._tmp, f)
            if path in keep:
                continue
            try:
                os.remove(path)
            except OSError:
                pass

    def _copy_dem(self) -> str:
        dst = os.path.join(self.output_dir, "dem.tif")
        if not os.path.exists(dst):
            shutil.copy2(self.dem_path, dst)
        return dst

    def _validate_raster_not_empty(self, path: str, nombre: str) -> None:
        """Lanza RuntimeError descriptivo si el raster no tiene datos válidos."""
        ds     = gdal.Open(path)
        band   = ds.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        arr    = band.ReadAsArray().astype(float)
        ds     = None
        if nodata is not None:
            arr[arr == nodata] = np.nan
        valid = arr[~np.isnan(arr)]
        if valid.size == 0 or np.nanmax(np.abs(valid)) == 0:
            raise RuntimeError(
                f"El raster de {nombre} está vacío — r.water.outlet no encontró "
                f"celdas que drenen al punto marcado.\n"
                f"Verifique que el punto esté sobre un cauce visible en el "
                f"raster de acumulación de flujo."
            )
        self._cb(f"  Raster {nombre}: OK")

    def _snap_outlet(
        self, acc: str, x: float, y: float, rad: int = 15
    ) -> tuple[float, float]:
        """
        Mueve el outlet al píxel de CAUCE más cercano al clic (celda con
        acumulación >= FAT). NO usa el máximo de acumulación — eso movería
        el punto río abajo y delimitaría una cuenca más grande de lo pedido.

        Coordenadas en el CRS del DEM (sin conversiones).
        """
        try:
            ds     = gdal.Open(acc)
            gt     = ds.GetGeoTransform()
            band   = ds.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            arr    = band.ReadAsArray().astype(float)
            ds     = None

            if nodata is not None:
                arr[arr == nodata] = np.nan
            arr = np.abs(arr)   # r.watershed puede dar negativos en bordes

            rows, cols = arr.shape
            c0_ = int((x - gt[0]) / gt[1])
            r0_ = int((y - gt[3]) / gt[5])

            if not (0 <= r0_ < rows and 0 <= c0_ < cols):
                self._cb(
                    f"  ADVERTENCIA: el outlet ({x:.1f}, {y:.1f}) está fuera "
                    f"del DEM. Verifique el punto."
                )
                return x, y

            # Si el clic ya cayó sobre un cauce, no mover nada
            if not np.isnan(arr[r0_, c0_]) and arr[r0_, c0_] >= self.fat:
                self._cb(
                    f"  Outlet sobre cauce (acumulación="
                    f"{arr[r0_, c0_]:.0f}), sin ajuste"
                )
                return x, y

            # Buscar la celda de cauce MÁS CERCANA dentro del radio
            for radio in [rad, rad * 2]:
                r0 = max(0, r0_ - radio);  r1 = min(rows, r0_ + radio + 1)
                c0 = max(0, c0_ - radio);  c1 = min(cols, c0_ + radio + 1)
                win = arr[r0:r1, c0:c1]

                # Celdas de cauce: acumulación >= FAT
                stream_mask = ~np.isnan(win) & (win >= self.fat)
                if not stream_mask.any():
                    continue

                # Distancia de cada celda de cauce al clic
                rr, cc = np.nonzero(stream_mask)
                dist2  = (rr + r0 - r0_) ** 2 + (cc + c0 - c0_) ** 2
                k      = int(np.argmin(dist2))
                br, bc = rr[k] + r0, cc[k] + c0

                sx = gt[0] + (bc + 0.5) * gt[1]
                sy = gt[3] + (br + 0.5) * gt[5]
                self._cb(
                    f"  Outlet ajustado al cauce más cercano: "
                    f"{dist2[k] ** 0.5:.1f} celdas "
                    f"(acumulación={arr[br, bc]:.0f})"
                )
                return sx, sy

            self._cb(
                f"  ADVERTENCIA: ningún cauce (acum >= {self.fat}) en un "
                f"radio de {rad * 2} celdas. Usando el punto original — "
                f"marque más cerca de un cauce visible."
            )
            return x, y
        except Exception as exc:
            self._cb(f"  Snap error ({exc}), usando coords originales")
            return x, y

    # ------------------------------------------------------------------
    # Delimitación
    # ------------------------------------------------------------------

    def _delineate(self, x: float, y: float) -> tuple[str, str]:
        cuenca     = os.path.join(self.output_dir, "cuenca.shp")
        subcuencas = os.path.join(self.output_dir, "subcuencas.shp")

        # 1. Cuenca que drena al outlet — r.water.outlet sobre el raster
        #    de direcciones de drenaje (mismo CRS que el clic del mapa)
        ws_r = self._t("ws.tif")
        r = self._run(f"{self._grass}:r.water.outlet", {
            "input":       self._drainage_path,
            "coordinates": f"{x},{y}",
            "output":      ws_r,
        })
        ws_r = r["output"]
        self._validate_raster_not_empty(ws_r, "cuenca")
        self._ws_raster = ws_r   # para enmascarar subcuencas y cauces

        # 2. Vectorizar cuenca (área)
        self._run(f"{self._grass}:r.to.vect", {
            "input":  ws_r,
            "type":   2,         # area
            "column": "value",
            "output": cuenca,
        })

        # 3. Subcuencas: basin de r.watershed, enmascarado a la cuenca
        #    ANTES de vectorizar (r.to.vect sobre todo el DEM es muy lento)
        sub_masked = self._t("sub_masked.tif")
        self._mask_to_watershed(self._basin_path, ws_r, sub_masked)
        sub_raw = self._t("sub_raw.shp")
        self._run(f"{self._grass}:r.to.vect", {
            "input":  sub_masked,
            "type":   2,
            "column": "sub_id",
            "output": sub_raw,
        })
        try:
            self._run("native:clip", {
                "INPUT": sub_raw, "OVERLAY": cuenca, "OUTPUT": subcuencas,
            })
        except Exception:
            self._copy_shp(sub_raw, subcuencas)

        return cuenca, subcuencas

    def _channels(self, cuenca: str) -> str:
        drenajes = os.path.join(self.output_dir, "drenajes.shp")
        ch_raw   = self._t("ch_raw.shp")

        # Cauces: stream de r.watershed, enmascarado a la cuenca
        ch_masked = self._t("ch_masked.tif")
        try:
            self._mask_to_watershed(self._stream_path, self._ws_raster, ch_masked)
        except Exception:
            ch_masked = self._stream_path
        self._run(f"{self._grass}:r.to.vect", {
            "input":  ch_masked,
            "type":   0,         # line
            "column": "value",
            "output": ch_raw,
        })
        try:
            self._run("native:clip", {
                "INPUT": ch_raw, "OVERLAY": cuenca, "OUTPUT": drenajes,
            })
        except Exception:
            self._copy_shp(ch_raw, drenajes)

        return drenajes

    def _mask_to_watershed(self, src: str, ws: str, out: str) -> str:
        """
        Enmascara 'src' con la cuenca 'ws' (mismo grid): las celdas fuera
        de la cuenca quedan NoData. Acelera r.to.vect enormemente porque
        solo se vectoriza el área de interés.
        """
        ds_s  = gdal.Open(src)
        ds_w  = gdal.Open(ws)
        arr   = ds_s.GetRasterBand(1).ReadAsArray().astype(np.int32)
        wmask = ds_w.GetRasterBand(1).ReadAsArray()
        w_nd  = ds_w.GetRasterBand(1).GetNoDataValue()
        s_nd  = ds_s.GetRasterBand(1).GetNoDataValue()

        # Dentro de la cuenca: celdas con valor válido y distinto de 0
        wf = wmask.astype(float)
        inside = ~np.isnan(wf) & (wf != 0)
        if w_nd is not None:
            inside &= (wf != float(w_nd))
        if s_nd is not None:
            inside &= (arr != int(s_nd))
        arr[~inside] = 0

        drv = gdal.GetDriverByName("GTiff")
        ods = drv.Create(out, ds_s.RasterXSize, ds_s.RasterYSize, 1,
                         gdal.GDT_Int32)
        ods.SetGeoTransform(ds_s.GetGeoTransform())
        ods.SetProjection(ds_s.GetProjection())
        band = ods.GetRasterBand(1)
        band.WriteArray(arr)
        band.SetNoDataValue(0)
        ods = None; ds_s = None; ds_w = None
        return out

    def _ensure_prj(self, shp: str) -> None:
        """
        Si el shapefile no tiene .prj, lo escribe con la proyección del DEM.
        Sin esto, geopandas lee el SHP sin CRS y la selección de estaciones
        interpreta metros como grados (error 'Shell empty...').
        """
        prj = os.path.splitext(shp)[0] + ".prj"
        if os.path.exists(prj) or not os.path.exists(shp):
            return
        try:
            from osgeo import osr
            ds = gdal.Open(self.dem_path)
            wkt = ds.GetProjection()
            ds = None
            if not wkt:
                return
            sr = osr.SpatialReference()
            sr.ImportFromWkt(wkt)
            sr.MorphToESRI()
            with open(prj, "w") as f:
                f.write(sr.ExportToWkt())
            self._cb(f"  .prj escrito para {os.path.basename(shp)}")
        except Exception as exc:
            self._cb(f"  No se pudo escribir .prj: {exc}")

    @staticmethod
    def _copy_shp(src: str, dst: str) -> None:
        """Copia un shapefile completo (todos los archivos asociados)."""
        base_src = os.path.splitext(src)[0]
        base_dst = os.path.splitext(dst)[0]
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            if os.path.exists(base_src + ext):
                shutil.copy2(base_src + ext, base_dst + ext)
