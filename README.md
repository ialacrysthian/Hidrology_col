# TC Calculator

Plugin de **QGIS 3.x** para Colombia que delimita cuencas y subcuencas a
partir de un DEM, extrae la red de drenaje, selecciona estaciones
hidrometeorológicas del IDEAM y calcula el **Tiempo de Concentración (Tc)**
de una cuenca mediante métodos empíricos y morfometría distribuida
celda-a-celda.

> ⚠️ Proyecto experimental, con fines académicos y de estudio preliminar.
> Lee el [DISCLAIMER](DISCLAIMER.md) antes de usar los resultados en
> proyectos de diseño o de ingeniería real.

## Características

- **Delimitación de cuencas** desde un DEM (GRASS GIS `r.fill.dir` +
  `r.watershed`, algoritmo D8) a partir de un punto de salida (*outlet*)
  seleccionado en el mapa.
- **Extracción automática de subcuencas** y de la red de drenaje.
- **Morfometría** de la cuenca general y de cada subcuenca (área, perímetro,
  longitud y pendiente del cauce principal, pendiente media, relieve,
  coeficiente de compacidad, factor de forma, densidad de drenaje).
- **14 métodos empíricos de Tiempo de Concentración** para comparar
  resultados: Kirpich, Ventura, Passini, SCS (Lag), Témez, Williams,
  Bransby-Williams, Giandotti, Haktanir-Sezen, SCS-Ranser, Ventura-Heras,
  V.T. Chow y California.
- **Cobertura de tierra (Corine Land Cover)**: genera rasters de rugosidad
  de Manning y de Número de Curva (CN) a partir de una tabla editable de
  equivalencias por categoría.
- **Extracción automática de CN** desde el raster generado para alimentar
  el método SCS sin necesidad de ingresarlo manualmente.
- **Selección de estaciones IDEAM** (precipitación, temperatura, caudal)
  mediante buffer configurable alrededor de la cuenca, usando el catálogo
  CNE y los servicios Socrata de datos.gov.co.
- Interfaz como panel acoplable (`QgsDockWidget`) no modal, con un asistente
  guiado de 6 pasos; cada paso pesado corre en segundo plano (`QgsTask`).

## Flujo del asistente

```
1. DEM        → fija el CRS del proyecto al CRS nativo del DEM
2. FLUJO      → cálculo de dirección y acumulación de flujo (D8)
3. CUENCA     → delimitación de cuenca/subcuencas/drenajes desde el outlet
4. COBERTURA  → raster de Manning y raster de CN desde Corine Land Cover
5. ESTACIONES → selección de estaciones IDEAM dentro de un buffer
6. MORFOMETRÍA→ morfometría + comparación de los 14 métodos de Tc
                (cuenca general y cada subcuenca)
```

## Requisitos

- QGIS 3.16 o superior, con el proveedor **GRASS** activo en Processing.
- Dependencias Python (instalables por el propio plugin vía `pip --user`):
  `requests`, `pandas`, `numpy`, `scipy`, `geopandas`, `shapely`,
  `openpyxl`, `xlrd`, `rasterio`, `matplotlib`.
- [`pysheds`](https://github.com/mdbartos/pysheds) (opcional, recomendado):
  necesario para el cálculo distribuido del Tc y la corrección de
  inconsistencias de dirección de flujo.

## Instalación

No requiere compilación. Para instalar:

1. Comprime la carpeta `tc_calculator/` en un archivo `.zip`.
2. En QGIS: **Complementos → Administrar e instalar complementos → Instalar
   desde ZIP**.
3. Al activarlo, el plugin verifica las dependencias Python y ofrece
   instalarlas automáticamente si faltan.

## Fuentes de datos

- **DEM**: SRTM (CGIAR-CSI), tiles 5×5° vía `/vsicurl/` (o DEM propio del
  usuario).
- **Cobertura del suelo**: Corine Land Cover Colombia (nivel 3).
- **Estaciones hidrometeorológicas**: catálogo CNE del IDEAM + servicios
  Socrata (`datos.gov.co`).

## Licencia

Distribuido bajo licencia [MIT](LICENSE).

## Aviso legal

Este software no está avalado por el IDEAM ni por ninguna entidad oficial
colombiana. Los resultados son de referencia y no sustituyen estudios
hidrológicos detallados ni el criterio de un profesional idóneo. Ver
[DISCLAIMER.md](DISCLAIMER.md) para el texto completo (ES/EN).
