#!/usr/bin/env python3
"""
Carga los edificios descargados de Overpass (JSON) a la tabla `buildings` en PostGIS.

Uso:
    python3 load_buildings.py la_guaira
    python3 load_buildings.py caracas
"""
import os
import sys
import json
import re
import psycopg2
import psycopg2.extras
from pathlib import Path

from db_config import DB_CONFIG

# Por defecto usa las muestras públicas del repo (data_samples/). Para trabajar con
# la descarga completa de Overpass, define RESCUEGIS_DATA_DIR=./data (ver download_overpass.py).
DATA_DIR = Path(os.environ.get("RESCUEGIS_DATA_DIR", Path(__file__).resolve().parent.parent / "data_samples"))

MUNICIPIO_MAP = {
    "la_guaira": "LA_GUAIRA",
    "caracas": "CARACAS",
}


def parse_levels(tags: dict):
    """Extrae número de pisos de building:levels (puede venir como '3', '3.5', '2-4', etc.)"""
    raw = tags.get("building:levels")
    if not raw:
        return None
    m = re.search(r"\d+", raw)
    return int(m.group()) if m else None


def parse_direccion(tags: dict):
    calle = tags.get("addr:street")
    numero = tags.get("addr:housenumber")
    if calle and numero:
        return f"{calle} {numero}"
    return calle or numero


def load(municipio_key: str):
    municipio = MUNICIPIO_MAP[municipio_key]
    path = DATA_DIR / f"buildings_{municipio_key}.json"
    print(f"Leyendo {path}...")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    elements = data.get("elements", [])
    print(f"  {len(elements)} elementos a procesar")

    rows = []
    for el in elements:
        tags = el.get("tags", {})
        if "building" not in tags:
            continue  # ignorar nodos de referencia sin tag building (subelementos de relations)

        # lat/lon: en 'way'/'relation' viene en 'center'; si es 'node' viene directo
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center")
            if not center:
                continue
            lat, lon = center["lat"], center["lon"]

        if lat is None or lon is None:
            continue

        rows.append((
            el["id"],                                  # osm_id
            el["type"],                                 # osm_type
            tags.get("name"),                            # nombre
            tags.get("building"),                        # tipo
            tags.get("amenity"),                         # tipo_amenity
            municipio,                                   # municipio
            lat, lon,                                    # lat, lon
            parse_levels(tags),                          # pisos
            tags.get("building:material"),               # material
            parse_direccion(tags),                        # direccion
            json.dumps(tags, ensure_ascii=False),        # tags_extra
        ))

    print(f"  {len(rows)} edificios válidos (con tag building y coordenadas)")

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO buildings
                    (osm_id, osm_type, nombre, tipo, tipo_amenity, municipio, lat, lon, pisos, material, direccion, tags_extra)
                VALUES %s
                ON CONFLICT (osm_type, osm_id) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    tipo = EXCLUDED.tipo,
                    tipo_amenity = EXCLUDED.tipo_amenity,
                    municipio = EXCLUDED.municipio,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon,
                    pisos = EXCLUDED.pisos,
                    material = EXCLUDED.material,
                    direccion = EXCLUDED.direccion,
                    tags_extra = EXCLUDED.tags_extra,
                    actualizado_en = now()
                """,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                page_size=2000,
            )
            cur.execute(
                "INSERT INTO import_log (fuente, municipio, capa, registros_importados, notas) VALUES (%s,%s,%s,%s,%s)",
                ("overpass", municipio, "buildings", len(rows), f"archivo {path.name}"),
            )
        conn.commit()
        print(f"  ✅ {len(rows)} edificios insertados/actualizados en PostGIS para {municipio}")
    except Exception as e:
        conn.rollback()
        print("  ❌ Error, rollback:", e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MUNICIPIO_MAP:
        print(__doc__)
        sys.exit(1)
    load(sys.argv[1])
