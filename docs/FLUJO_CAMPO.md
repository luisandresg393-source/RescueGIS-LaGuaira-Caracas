# Flujo de datos hasta el teléfono del rescatista

```
 reportes crudos                 correlación                     terreno
─────────────────          ─────────────────────          ──────────────────
 bot Telegram ±10m ─┐
 SOS Venezuela ±330m ├──►  correlacionar_sucesos.py  ──►  GET /api/v1/sucesos
 llamadas/manual    ─┘      · clusters DBSCAN 2 pasadas    (una fila por suceso
                            · posición refinada             real, no por reporte)
                              (centroide ponderado                │
                               por 1/precisión²)                  ▼
                            · confianza multi-fuente        /campo?key=...
                            · re-match de edificio          vista móvil:
                                                            🧭 Navegar (Maps/OsmAnd)
                                                            📞 Llamar al reportero
                                                            auto-refresh 60 s
```

## Por qué "sucesos" y no reportes sueltos

Un derrumbe real genera N reportes: el vecino por Telegram (GPS ±10 m), dos
personas por SOS Venezuela (coordenada degradada ±330 m), una llamada. Sin
correlación, el coordinador ve 4 filas — y podría despachar 2 equipos al
mismo sitio mientras otro derrumbe espera.

`correlacionar_sucesos.py` los agrupa y calcula:

| Campo | Cómo | Efecto |
|---|---|---|
| posición refinada | centroide ponderado por 1/precisión² | el GPS de ±10 m "manda"; los imprecisos aportan confirmación, no ruido |
| precisión refinada | la mejor del grupo | el rescatista sabe qué esperar al llegar |
| confianza 0–95 | +15 por fuente distinta, +5 por reporte, +10 por evidencia | lo corroborado por 2+ vías sube en la cola |
| re-match edificio | con la posición refinada | grupos logran match FIRME que ningún reporte solo tenía |

Prueba real del pipeline (test incluido): 3 reportes del mismo derrumbe
(2 degradados ±330 m + 1 GPS ±9 m) → 1 suceso en la posición del GPS bueno,
match firme `auto_150m` a 2,2 m del edificio, confianza 60 (2 fuentes).

**Clustering en 2 pasadas** (importante): los reportes precisos forman
"anclas" con DBSCAN; los imprecisos se adhieren al ancla más cercana dentro
de su radio de incertidumbre, pero NO se encadenan entre sí — si no, dos
coordenadas de ±8 km fusionarían media ciudad en un "suceso".

## La vista de campo (`/campo`)

`https://tu-api/campo?key=<key-emergencia>` — pensada para el teléfono del
rescatista, sin instalar nada:

- Cola de sucesos abiertos por urgencia, tarjetas grandes tocables con guantes
- **🧭 Navegar**: abre Google Maps con turn-by-turn hasta la coordenada refinada
- **📍 App GPS**: URI `geo:` — abre OsmAnd/Organic Maps (funcionan OFFLINE,
  crítico si la red de datos está caída; basta descargar el mapa de Venezuela antes)
- **📞 Reportero**: llamada directa al teléfono del que reportó (si lo compartió)
- "SIN ASIGNAR" en verde → nadie lo ha tomado; el nombre del cuerpo si ya está tomado
- `±N m — confirmar en sitio` cuando el match no es firme
- Auto-refresh cada 60 s (meta-refresh: funciona en cualquier navegador, hasta viejos)

La key va en la URL a propósito: el jefe de cuerpo la comparte como un
enlace normal en el grupo WhatsApp/Telegram de su equipo. Riesgo controlado:
la key es revocable en segundos (`gestionar_keys.py desactivar N`) y todo
uso queda en `api_log`.

## Operación recomendada

```bash
# tras cada ingesta (o cron cada 2-5 min):
python3 scripts/correlacionar_sucesos.py

# los rescatistas solo necesitan el enlace /campo?key=...
# el coordinador de sala usa GET /api/v1/sucesos (o el futuro panel Leaflet, issue #1)
```

## Siguiente eslabón (issue #2, se busca colaborador)

Push activo: worker que detecta sucesos CRITICA nuevos y manda mensaje de
Telegram al rescatista suscrito, con el botón de navegación directo. Con eso
el flujo reporte→teléfono baja de ~60 s (refresh) a segundos.
