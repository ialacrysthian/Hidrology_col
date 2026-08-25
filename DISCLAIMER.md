# Descargo de responsabilidad / Disclaimer

## Español

**TC Calculator** es un complemento (plugin) para QGIS de carácter
experimental, desarrollado con fines académicos, de investigación y de apoyo
a estudios preliminares de hidrología. El software se distribuye bajo
licencia MIT y se entrega "TAL CUAL" ("AS IS"), sin garantía de ningún tipo,
expresa o implícita.

1. **No es un producto oficial.** Este plugin no es un producto oficial ni
   está avalado, certificado o patrocinado por el IDEAM (Instituto de
   Hidrología, Meteorología y Estudios Ambientales de Colombia), la CIAT/CGIAR
   (fuente de los DEM SRTM), Corine Land Cover, ni por ninguna entidad
   gubernamental colombiana. Las consultas a servicios de IDEAM (Socrata /
   datos.gov.co) se realizan mediante sus APIs públicas y están sujetas a la
   disponibilidad, exactitud y términos de uso de dichas plataformas, fuera
   del control del autor.

2. **Resultados de referencia, no de diseño final.** Los resultados
   generados por este plugin —delimitación de cuencas, morfometría, tiempos
   de concentración (Kirpich, Ventura, Passini, SCS, Témez, Williams,
   Bransby-Williams, Giandotti, Haktanir-Sezen, SCS-Ranser, V.T. Chow,
   California, entre otros), rasters de Manning y de Número de Curva (CN)—
   se basan en modelos empíricos, datos de elevación de resolución media
   (SRTM ~30 m) y coberturas de suelo de escala nacional (Corine Land
   Cover). Estos métodos tienen limitaciones conocidas de aplicabilidad
   (tamaño de cuenca, pendiente, tipo de suelo, régimen climático) y **no
   sustituyen el criterio profesional, la calibración con datos locales, ni
   estudios hidrológicos/hidráulicos detallados** requeridos para diseño de
   obras de infraestructura, gestión de riesgo de inundación, ni cualquier
   decisión con implicaciones de seguridad, económicas o legales.

3. **Sin garantía de exactitud.** El autor no garantiza que los cálculos,
   las delimitaciones automáticas (GRASS GIS / pysheds) ni los valores de
   Manning/CN asignados por defecto sean exactos, completos o adecuados para
   un sitio o propósito específico. El usuario es responsable de validar
   todos los resultados con información de campo, series históricas
   verificadas y, cuando corresponda, con un profesional idóneo (ingeniero
   civil, hidrólogo) antes de tomar decisiones basadas en ellos.

4. **Limitación de responsabilidad.** En ningún caso el autor será
   responsable por daños directos, indirectos, incidentales o consecuentes
   que resulten del uso o la imposibilidad de uso de este software, incluidos
   —sin limitarse a— pérdidas económicas, daños a infraestructura, o
   decisiones de ingeniería adoptadas con base en sus resultados. Véase
   también los términos de la licencia MIT incluida en `LICENSE`.

5. **Datos y dependencias de terceros.** El plugin utiliza y/o depende de
   datos y software de terceros (SRTM/CGIAR-CSI, Corine Land Cover Colombia,
   IDEAM/datos.gov.co, GRASS GIS, pysheds, GDAL/rasterio, entre otros), cada
   uno sujeto a sus propias licencias y condiciones de uso, que el usuario
   debe revisar y respetar de forma independiente.

---

## English

**TC Calculator** is an experimental QGIS plugin developed for academic,
research, and preliminary hydrology study purposes. The software is
distributed under the MIT License and provided "AS IS", without warranty of
any kind, express or implied.

1. **Not an official product.** This plugin is not an official product of,
   and is not endorsed, certified, or sponsored by, IDEAM (Colombia's
   Institute of Hydrology, Meteorology and Environmental Studies), CIAT/CGIAR
   (source of the SRTM DEM tiles), Corine Land Cover, or any Colombian
   government entity. Queries to IDEAM services (Socrata / datos.gov.co) are
   made through their public APIs and are subject to the availability,
   accuracy, and terms of use of those platforms, which are outside the
   author's control.

2. **Reference results, not final design values.** Results produced by this
   plugin — watershed delineation, morphometry, times of concentration
   (Kirpich, Ventura, Passini, SCS, Témez, Williams, Bransby-Williams,
   Giandotti, Haktanir-Sezen, SCS-Ranser, V.T. Chow, California, among
   others), Manning and Curve Number (CN) rasters — rely on empirical
   models, medium-resolution elevation data (SRTM ~30 m), and national-scale
   land-cover data (Corine Land Cover). These methods have known
   applicability limitations (basin size, slope, soil type, climate regime)
   and **do not replace professional judgment, calibration with local data,
   or detailed hydrologic/hydraulic studies** required for infrastructure
   design, flood-risk management, or any decision with safety, economic, or
   legal implications.

3. **No accuracy guarantee.** The author does not warrant that the
   calculations, automated delineations (GRASS GIS / pysheds), or default
   Manning/CN values are accurate, complete, or fit for any particular site
   or purpose. Users are responsible for validating all outputs against
   field data, verified historical series, and, where appropriate, a
   qualified professional (civil engineer, hydrologist) before making
   decisions based on them.

4. **Limitation of liability.** In no event shall the author be liable for
   any direct, indirect, incidental, or consequential damages arising from
   the use or inability to use this software, including — without
   limitation — economic loss, infrastructure damage, or engineering
   decisions made based on its results. See also the MIT License terms in
   `LICENSE`.

5. **Third-party data and dependencies.** The plugin uses and/or depends on
   third-party data and software (SRTM/CGIAR-CSI, Corine Land Cover
   Colombia, IDEAM/datos.gov.co, GRASS GIS, pysheds, GDAL/rasterio, among
   others), each subject to its own license and terms of use, which users
   must independently review and comply with.
