import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()


def read_raster_as_array(raster_path: str) -> tuple[np.ndarray, list, str, float]:
    """Returns (array, geotransform, projection_wkt, nodata)."""
    ds = gdal.Open(raster_path, gdal.GA_ReadOnly)
    band = ds.GetRasterBand(1)
    array = band.ReadAsArray().astype(np.float64)
    nodata = band.GetNoDataValue()
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None
    return array, gt, proj, nodata


def write_raster(
    array: np.ndarray,
    output_path: str,
    geotransform: list,
    projection_wkt: str,
    nodata: float = -9999.0,
    dtype=gdal.GDT_Float32,
) -> str:
    rows, cols = array.shape
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(output_path, cols, rows, 1, dtype, options=["COMPRESS=LZW", "TILED=YES"])
    ds.SetGeoTransform(geotransform)
    ds.SetProjection(projection_wkt)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    out = np.where(np.isnan(array), nodata, array).astype(np.float32)
    band.WriteArray(out)
    band.FlushCache()
    ds = None
    return output_path


def make_grid_arrays(geotransform: list, rows: int, cols: int) -> tuple[np.ndarray, np.ndarray]:
    """Returns (grid_x, grid_y) meshgrid arrays in raster CRS coordinates."""
    x_origin, px_w, _, y_origin, _, px_h = geotransform
    x_coords = x_origin + px_w * (np.arange(cols) + 0.5)
    y_coords = y_origin + px_h * (np.arange(rows) + 0.5)
    return np.meshgrid(x_coords, y_coords)


def sample_raster_at_points(
    raster_path: str, lons: np.ndarray, lats: np.ndarray
) -> np.ndarray:
    """Samples raster values at (lon, lat) point arrays. Returns 1D array of values."""
    array, gt, _, nodata = read_raster_as_array(raster_path)
    x_origin, px_w, _, y_origin, _, px_h = gt
    cols_idx = ((lons - x_origin) / px_w).astype(int)
    rows_idx = ((lats - y_origin) / px_h).astype(int)

    nrows, ncols = array.shape
    valid = (rows_idx >= 0) & (rows_idx < nrows) & (cols_idx >= 0) & (cols_idx < ncols)

    values = np.full(len(lons), np.nan)
    values[valid] = array[rows_idx[valid], cols_idx[valid]]
    if nodata is not None:
        values[values == nodata] = np.nan
    return values
