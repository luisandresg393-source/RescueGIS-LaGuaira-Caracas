#!/usr/bin/env python3
"""
Generador de salidas ESTÁTICAS para hacer llegar los reportes a las manos
correctas SIN necesidad de servidor propio:

  docs/live/brief_whatsapp.txt   → parte operativo listo para pegar en el
                                   grupo WhatsApp/Telegram de un cuerpo de
                                   emergencia (el canal que SÍ usan hoy)
  docs/live/sucesos.geojson      → capa lista para QGIS/ArcGIS/uMap
  docs/live/refugios.geojson     → refugios/campamentos reportados
  docs/live/cola_incidentes.csv  → lista imprimible / radio
  docs/live/panel.html           → panel visual (vía generar_panel.py)

Publicadas por GitHub Pages quedan en URLs fijas que se comparten como
cualquier enlace. Pensado para correr en GitHub Actions (gratis) tras cada
sincronización — el "servidor" es el propio repositorio.

Uso:  python3 generar_brief.py [directorio_salida]
"""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "live"
OUT.mkdir(parents=True, exist_ok=True)

PAGES = "https://luisandresg393-source.github.io/RescueGIS-LaGuaira-Caracas"


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
ahora = datetime.now(timezone.utc)

# ------------------------------------------------------------------
# Datos
# ------------------------------------------------------------------
sucesos = q(cur, """
    SELECT s.codigo, s.tipo_dominante, s.urgencia_max::text AS urgencia,
           s.personas_max, s.num_reportes, s.num_fuentes, s.confianza,
           s.lat, s.lon, s.coord_precision_m, s.building_match_metodo,
           b.codigo_corto AS edificio, b.nombre AS edificio_nombre,
           (SELECT i.parroquia_geo FROM incidentes i WHERE i.suceso_id=s.id
             AND i.parroquia_geo IS NOT NULL LIMIT 1) AS parroquia
    FROM sucesos s LEFT JOIN buildings b ON b.id=s.building_id
    WHERE s.lat IS NOT NULL
    ORDER BY CASE s.urgencia_max WHEN 'CRITICA' THEN 3 WHEN 'ALTA' THEN 2
             WHEN 'MEDIA' THEN 1 ELSE 0 END DESC, s.num_reportes DESC""")

refugios = q(cur, """
    SELECT nombre, lat, lon, tags_extra->>'hora' AS reportado_en,
           tags_extra->>'mapa' AS mapa
    FROM infraestructura WHERE capa='refugio' AND osm_type='chatmap'
    ORDER BY 4 DESC""")

(stats,) = q(cur, """
    SELECT (SELECT count(*) FROM incidentes)::int AS reportes,
           (SELECT count(*) FROM sucesos)::int AS sucesos,
           (SELECT count(*) FROM sucesos WHERE num_reportes>1)::int AS corroborados,
           (SELECT count(DISTINCT fuente) FROM incidentes)::int AS fuentes,
           (SELECT max(fecha) FROM incidentes) AS ultimo_reporte""")

por_parroquia = q(cur, """
    SELECT COALESCE(parroquia_geo,'(sin parroquia)') AS parroquia, count(*) AS n
    FROM incidentes GROUP BY 1 ORDER BY 2 DESC LIMIT 10""")

personas = q(cur, """
    SELECT codigo, nombre_publico, edad, es_menor, estado, parroquia_geo, zona_texto
    FROM v_personas_publico WHERE estado='BUSCADA' LIMIT 30""")

cruces = q(cur, """
    SELECT p.codigo AS per, p.es_menor, s.codigo AS suc, s.num_reportes,
           round(ST_Distance(p.geom::geography, s.geom::geography)) AS dist_m
    FROM personas_desaparecidas p
    JOIN LATERAL (SELECT * FROM sucesos s2 WHERE s2.geom IS NOT NULL
                  ORDER BY p.geom <-> s2.geom LIMIT 1) s ON true
    WHERE p.estado='BUSCADA'
      AND ST_Distance(p.geom::geography, s.geom::geography) < 600
    ORDER BY 5 LIMIT 10""")

recursos = q(cur, "SELECT * FROM v_recursos_solicitados LIMIT 10")
conn.close()

# ------------------------------------------------------------------
# 1. Brief para WhatsApp (texto plano, formato de negritas de WhatsApp)
# ------------------------------------------------------------------
def gmaps(lat, lon):
    return f"https://maps.google.com/?q={lat:.5f},{lon:.5f}"

lineas = [
    f"🚨 *RESCUEGIS — PARTE OPERATIVO* · {ahora.strftime('%d-%b %H:%M')} UTC",
    "",
    f"Fuente: reportes ciudadanos de la activación HOT (ChatMap) + fuentes federadas",
    f"*{stats['reportes']} reportes* → *{stats['sucesos']} sucesos* únicos "
    f"({stats['corroborados']} corroborados por 2+ reportes)",
    f"Último reporte ingerido: {stats['ultimo_reporte'].strftime('%d-%b %H:%M') if stats['ultimo_reporte'] else '—'}",
    "",
    "*CONCENTRACIÓN POR PARROQUIA:*",
]
for p in por_parroquia[:6]:
    lineas.append(f"  • {p['parroquia'].replace('Parroquia ','')}: {p['n']} reportes")

lineas += ["", "*SUCESOS PRINCIPALES* (más reportados primero):"]
for s in sucesos[:12]:
    icono = {"CRITICA": "🔴", "ALTA": "🟠", "MEDIA": "🟡"}.get(s["urgencia"], "🟢")
    edif = s["edificio_nombre"] or s["edificio"] or "edificio por confirmar"
    parr = (s["parroquia"] or "").replace("Parroquia ", "")
    lineas.append(
        f"{icono} *{s['codigo']}* · {s['tipo_dominante'].replace('_',' ').title()} · "
        f"{s['num_reportes']} rep. · {edif}{' · '+parr if parr else ''}")
    lineas.append(f"   📍 {gmaps(s['lat'], s['lon'])}")

if personas:
    menores = sum(1 for p in personas if p["es_menor"])
    lineas += ["", f"*🔍 PERSONAS DESAPARECIDAS: {len(personas)} búsquedas activas*"
               + (f" ({menores} menores — datos protegidos)" if menores else "")]
    for p in personas[:8]:
        zona = (p["parroquia_geo"] or p["zona_texto"] or "").replace("Parroquia ", "")
        lineas.append(f"  • {p['codigo']}: {p['nombre_publico'][:40]} · {zona[:35]}")
    lineas.append(f"  Directorio completo/aportar datos: desaparecidosvenezuela.com")

if cruces:
    lineas += ["", "*⚠️ DESAPARECIDOS CERCA DE EDIFICIOS DAÑADOS* (cruce georreferenciado):"]
    for c in cruces[:6]:
        quien = "MENOR" if c["es_menor"] else c["per"]
        lineas.append(f"  • {quien} a {int(c['dist_m'])} m del suceso {c['suc']} ({c['num_reportes']} reportes de daño)")
    lineas.append("  _Sugerencia: los equipos que inspeccionen esos sitios lleven las fichas de búsqueda._")

if recursos:
    lineas += ["", "*🛠 RECURSOS/APOYO SOLICITADOS:*"]
    for r in recursos:
        lineas.append(f"  • {r['recurso']}: {r['solicitudes']} solicitud(es) en {r['parroquia'].replace('Parroquia ','')}")

if refugios:
    lineas += ["", f"*🏕 REFUGIOS/CAMPAMENTOS REPORTADOS ({len(refugios)}):*"]
    for r in refugios:
        lineas.append(f"  • {gmaps(r['lat'], r['lon'])} (reportado {(r['reportado_en'] or '')[:10]})")

lineas += [
    "",
    f"🗺 Mapa completo: {PAGES}/live/panel.html",
    f"📥 Capa QGIS (GeoJSON): {PAGES}/live/sucesos.geojson",
    "",
    "⚠️ _Reportes ciudadanos SIN verificación oficial — confirmar en sitio._",
    "_Datos: © OpenStreetMap + ChatMap (HOT). Proyecto open source:_",
    "github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas",
]
(OUT / "brief_whatsapp.txt").write_text("\n".join(lineas), encoding="utf-8")

# ------------------------------------------------------------------
# 2. GeoJSON de sucesos (QGIS/uMap) y de refugios
# ------------------------------------------------------------------
def fc(rows, props):
    return {"type": "FeatureCollection",
            "name": "rescuegis",
            "attribution": "RescueGIS · reportes vía ChatMap (HOT) · edificios © OpenStreetMap",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                          "properties": {k: (float(r[k]) if hasattr(r[k], 'quantize') else r[k])
                                         for k in props if r.get(k) is not None}}
                         for r in rows]}

(OUT / "sucesos.geojson").write_text(json.dumps(fc(sucesos,
    ["codigo", "tipo_dominante", "urgencia", "personas_max", "num_reportes",
     "num_fuentes", "confianza", "coord_precision_m", "edificio", "edificio_nombre",
     "parroquia", "building_match_metodo"]), ensure_ascii=False), encoding="utf-8")

(OUT / "refugios.geojson").write_text(json.dumps(fc(refugios,
    ["nombre", "reportado_en", "mapa"]), ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------------
# 3. CSV imprimible
# ------------------------------------------------------------------
with open(OUT / "cola_incidentes.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["codigo", "urgencia", "tipo", "reportes", "personas", "edificio",
                "parroquia", "lat", "lon", "precision_m", "link_maps"])
    for s in sucesos:
        w.writerow([s["codigo"], s["urgencia"], s["tipo_dominante"], s["num_reportes"],
                    s["personas_max"], s["edificio"] or "",
                    (s["parroquia"] or "").replace("Parroquia ", ""),
                    round(s["lat"], 5), round(s["lon"], 5),
                    int(s["coord_precision_m"] or 0), gmaps(s["lat"], s["lon"])])

print(f"✅ Salidas en {OUT}:")
for p in sorted(OUT.iterdir()):
    print(f"   {p.name} ({p.stat().st_size:,} bytes)")
