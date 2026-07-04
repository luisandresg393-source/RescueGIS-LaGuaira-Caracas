#!/usr/bin/env python3
"""
Carga los polígonos de parroquias (admin_level=7, OSM) a PostGIS y
valida la consistencia parroquia-declarada vs coordenada de los
incidentes existentes (issue #3).

Uso:
    python3 load_parroquias.py la_guaira
    python3 load_parroquias.py caracas
    python3 load_parroquias.py validar     # revalida todos los incidentes
"""
import json
import os
import sys
import unicodedata
from pathlib import Path

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

DATA_DIR = Path(os.environ.get("RESCUEGIS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
MUNICIPIO_MAP = {"la_guaira": "LA_GUAIRA", "caracas": "CARACAS"}


def normalizar(s):
    """'Parroquia Catia La Mar' → 'catia la mar' (sin tildes, sin prefijo)."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for pref in ("parroquia ", "pquia. ", "pquia "):
        if s.startswith(pref):
            s = s[len(pref):]
    return s.strip()


def ways_a_multipolygon(members):
    """Ensambla los ways 'outer' de una relación OSM en un WKT MULTIPOLYGON,
    usando shapely (linemerge + polygonize), que es robusto ante ways
    fragmentados, invertidos o con huecos."""
    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import linemerge, polygonize, unary_union

    lineas = []
    for m in members:
        if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry"):
            pts = [(p["lon"], p["lat"]) for p in m["geometry"]]
            if len(pts) >= 2:
                lineas.append(LineString(pts))
    if not lineas:
        return None

    fusionado = linemerge(unary_union(lineas))
    polys = [p for p in polygonize(fusionado) if p.is_valid and p.area > 0]
    if not polys:
        return None
    return MultiPolygon(polys).wkt


def cargar(municipio_key):
    municipio = MUNICIPIO_MAP[municipio_key]
    path = DATA_DIR / f"parroquias_{municipio_key}.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    conn = psycopg2.connect(**DB_CONFIG)
    n = 0
    with conn, conn.cursor() as cur:
        for el in data.get("elements", []):
            nombre = el.get("tags", {}).get("name")
            if not nombre:
                continue
            wkt = ways_a_multipolygon(el.get("members", []))
            if not wkt:
                print(f"  ⚠ {nombre}: no se pudo ensamblar el polígono, se omite")
                continue
            cur.execute("""
                INSERT INTO parroquias (osm_id, nombre, nombre_norm, municipio, geom, area_km2, radio_equiv_m)
                VALUES (%s, %s, %s, %s,
                        ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromText(%s, 4326)), 3)),
                        ST_Area(ST_MakeValid(ST_GeomFromText(%s, 4326))::geography) / 1e6,
                        sqrt(ST_Area(ST_MakeValid(ST_GeomFromText(%s, 4326))::geography) / pi()))
                ON CONFLICT (osm_id) DO UPDATE SET
                    nombre = EXCLUDED.nombre, nombre_norm = EXCLUDED.nombre_norm,
                    geom = EXCLUDED.geom, area_km2 = EXCLUDED.area_km2,
                    radio_equiv_m = EXCLUDED.radio_equiv_m""",
                (el["id"], nombre, normalizar(nombre), municipio, wkt, wkt, wkt))
            n += 1
        cur.execute("INSERT INTO import_log (fuente, municipio, capa, registros_importados) VALUES ('osm', %s, 'parroquias', %s)",
                    (municipio, n))
    conn.close()
    print(f"✅ {n} parroquias cargadas para {municipio}")


def validar():
    """Cruza parroquia declarada vs coordenada de todos los incidentes."""
    conn = psycopg2.connect(**DB_CONFIG)
    with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # geolocalizar: en qué parroquia cae cada incidente
        cur.execute("""
            UPDATE incidentes i
            SET parroquia_geo = p.nombre
            FROM parroquias p
            WHERE i.lat IS NOT NULL
              AND ST_Contains(p.geom, i.geom)""")
        geo = cur.rowcount

        # extraer parroquia declarada del texto del reportero (fuente federada la puso ahí)
        cur.execute("""
            UPDATE incidentes
            SET parroquia_declarada = TRIM(SPLIT_PART(reportero_nombre, '/', 2))
            WHERE fuente = 'sosvenezuela2026'
              AND reportero_nombre LIKE '%%/%%'
              AND parroquia_declarada IS NULL""")

        # consistencia (matching difuso por nombre normalizado)
        cur.execute("""
            UPDATE incidentes i SET parroquia_consistente = sub.ok
            FROM (
                SELECT i2.id,
                       (pg.nombre_norm IS NOT NULL AND
                        (pg.nombre_norm = pd.norm
                         OR similarity(pg.nombre_norm, pd.norm) > 0.45)) AS ok
                FROM incidentes i2
                LEFT JOIN parroquias pg ON pg.nombre = i2.parroquia_geo
                CROSS JOIN LATERAL (SELECT lower(unaccent(COALESCE(i2.parroquia_declarada,''))) AS norm) pd
                WHERE i2.parroquia_declarada IS NOT NULL AND i2.parroquia_declarada != ''
            ) sub WHERE i.id = sub.id""")
        val = cur.rowcount
    conn.close()
    print(f"✅ {geo} incidentes geolocalizados por parroquia · {val} validados declarada-vs-geo")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in (*MUNICIPIO_MAP, "validar"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "validar":
        validar()
    else:
        cargar(sys.argv[1])
