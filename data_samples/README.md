# Datos de muestra

`buildings_la_guaira.json` — muestra aleatoria de **500 edificios** de La Guaira
(de un total de 46,889 reales cargados en el proyecto original), en el mismo
formato que devuelve Overpass API (`out center tags`).

Es solo para poder probar `scripts/load_buildings.py` sin depender de la
disponibilidad del servidor público de Overpass. Contiene únicamente
metadatos públicos de OpenStreetMap (geometría, tipo de edificio, pisos,
material) — **cero datos de personas**.

Para el inventario completo (96,634 edificios entre La Guaira y Caracas),
usa `scripts/download_overpass.py` — ver `docs/INSTALACION.md`.

Licencia de los datos: [ODbL de OpenStreetMap](https://www.openstreetmap.org/copyright).
