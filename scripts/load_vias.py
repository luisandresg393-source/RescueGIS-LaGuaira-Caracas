#!/usr/bin/env python3
"""
Carga vías (carreteras) y puentes descargados de Overpass a la tabla `vias` en PostGIS.

Uso:
    python3 load_vias.py la_guaira
    python3 load_vias.py caracas
"""
import os
import sys
import json
import psycopg2
import psycopg2.extras
from pathlib import Path

from db_config import DB_CONFIG

DATA_DIR = Path(os.environ.get("RESCUEGIS_DATA_DIR", Path(__file__).resolve().parent.parent / "data_samples"))

MUNICIPIO_MAP = {"la_guaira": "LA_GUAIRA", "caracas": "CARACAS"}


def linestring_wkt(geometry_points):
    coords = ", ".join(f"{p['lon']} {p['lat']}" for p in geometry_points)
    return f"LINESTRING({coords})"


def load(municipio_key: str):
    municipio = MUNICIPIO_MAP[municipio_key]
    path = DATA_DIR / f"vias_{municipio_key}.json"
    print(f"Leyendo {path}...")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    elements = data.get("elements", [])
    rows_carretera = []
    rows_puente = []

    for el in elements:
        if el["type"] != "way":
            continue
        geom_pts = el.get("geometry")
        if not geom_pts or len(geom_pts) < 2:
            continue
        tags = el.get("tags", {})
        wkt = linestring_wkt(geom_pts)
        es_puente = tags.get("bridge") == "yes"
        nombre = tags.get("name")
        highway_tag = tags.get("highway")

        if highway_tag:
            rows_carretera.append((el["id"], "carretera", nombre, municipio, highway_tag, es_puente, wkt, json.dumps(tags, ensure_ascii=False)))
        if es_puente:
            rows_puente.append((el["id"], "puente", nombre, municipio, highway_tag, True, wkt, json.dumps(tags, ensure_ascii=False)))

    print(f"  {len(rows_carretera)} tramos de carretera, {len(rows_puente)} puentes")

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            for rows in (rows_carretera, rows_puente):
                if not rows:
                    continue
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO vias (osm_id, tipo, nombre, municipio, highway_tag, es_puente, geom, tags_extra)
                    VALUES %s
                    ON CONFLICT (osm_id, tipo) DO UPDATE SET
                        nombre = EXCLUDED.nombre,
                        municipio = EXCLUDED.municipio,
                        highway_tag = EXCLUDED.highway_tag,
                        es_puente = EXCLUDED.es_puente,
                        geom = EXCLUDED.geom,
                        tags_extra = EXCLUDED.tags_extra
                    """,
                    rows,
                    template="(%s,%s,%s,%s,%s,%s,ST_SetSRID(ST_GeomFromText(%s),4326),%s::jsonb)",
                    page_size=1000,
                )
            cur.execute(
                "INSERT INTO import_log (fuente, municipio, capa, registros_importados, notas) VALUES (%s,%s,%s,%s,%s)",
                ("overpass", municipio, "vias", len(rows_carretera) + len(rows_puente), f"archivo {path.name}"),
            )
        conn.commit()
        print(f"✅ Vías cargadas para {municipio}")
    except Exception as e:
        conn.rollback()
        print("❌ Error:", e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MUNICIPIO_MAP:
        print(__doc__)
        sys.exit(1)
    load(sys.argv[1])
