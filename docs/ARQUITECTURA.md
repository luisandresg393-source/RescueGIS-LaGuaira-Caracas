# Arquitectura

```
                    ┌─────────────────────┐
                    │   OpenStreetMap      │
                    │   (Overpass API)     │
                    └──────────┬───────────┘
                               │ download_overpass.py
                               ▼
                    ┌─────────────────────┐
                    │  JSON crudo (data/)  │
                    └──────────┬───────────┘
                               │ load_buildings.py / load_infra.py / load_vias.py
                               ▼
        ┌──────────────────────────────────────────────┐
        │            PostgreSQL 17 + PostGIS 3.5         │
        │                                                │
        │  buildings (96,634)   infraestructura (1,406)  │
        │  vias (6,530)         incidentes               │
        │                       evidencias                │
        │                                                │
        │  Triggers automáticos:                          │
        │   - matching GPS → edificio (KNN, radio 150m)  │
        │   - recálculo de prioridad por componentes      │
        └───────────────┬────────────────────────────────┘
                         │
             ┌───────────┴────────────┐
             ▼                        ▼
    ┌─────────────────┐     ┌──────────────────────┐
    │  Bot Telegram    │     │  Panel de coordinación │
    │  (fase 3, WIP)   │     │  Leaflet + OSM (WIP)   │
    └─────────────────┘     └──────────────────────┘
```

## Capas de datos

| Capa | Tabla | Fuente | Contenido |
|---|---|---|---|
| Edificios | `buildings` | OSM `building=*` | Centroide, tipo, pisos, material, código corto (`LG-004248`) |
| Infraestructura crítica | `infraestructura` | OSM `amenity=hospital/school/fire_station/...` | Hospitales, bomberos, escuelas, refugios, gasolineras, telecom, agua, electricidad |
| Vías | `vias` | OSM `highway=*`, `bridge=yes` | Carreteras principales y puentes |
| Incidentes | `incidentes` | Reportes ciudadanos (Telegram, web, manual) | Personas atrapadas, heridos, necesidades, urgencia |
| Evidencias | `evidencias` | Fotos, testimonios, confirmaciones | Nivel de confianza por evidencia |

## Flujo de datos: de un reporte a una decisión de rescate

1. **Ingesta geográfica** (una vez, o cuando se actualice el mapa base):
   Overpass → JSON → PostGIS. Esto da el inventario de "qué existe" en la zona.
2. **Ingesta operativa** (continua, durante la emergencia):
   Un reporte ciudadano llega con coordenadas GPS → se inserta en `incidentes`.
3. **Matching automático** (trigger `trg_incidentes_matching`):
   PostGIS busca el edificio más cercano dentro de 150m. Si no hay ninguno,
   el incidente queda sin asignar para revisión manual.
4. **Recálculo de prioridad** (trigger `trg_incidentes_prioridad`):
   Se recalculan personas atrapadas, heridos, tiempo sin ayuda, reportes
   confirmados/pendientes, y bonus por infraestructura crítica/cercanía.
   El resultado se guarda en `buildings.prioridad_score` y `buildings.prioridad`.
5. **Consulta operativa**: cualquier consumidor (bot, panel, API) lee de
   las vistas `v_edificios_prioridad` y `v_incidentes_detalle`, ya con todo
   pre-calculado y ordenado por urgencia.

## Por qué PostGIS y no una solución ad-hoc

- El operador KNN (`<->`) con índice GiST permite buscar "el punto más cercano"
  entre decenas de miles de edificios en milisegundos — probado sobre 96,634
  filas.
- `ST_DWithin` con geografía (metros reales, no grados) evita errores de
  distorsión que aparecen al usar distancia euclidiana en lat/lon plano.
- Los triggers de recálculo automático significan que no hace falta un job
  externo (cron, worker) para mantener la prioridad actualizada — la
  consistencia vive en la base de datos misma.
