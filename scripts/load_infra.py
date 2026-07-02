#!/usr/bin/env python3
"""
Carga la infraestructura crítica descargada de Overpass (JSON por capas) a la tabla
`infraestructura` en PostGIS.

Uso:
    python3 load_infra.py la_guaira
    python3 load_infra.py caracas
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


def load(municipio_key: str):
    municipio = MUNICIPIO_MAP[municipio_key]
    path = DATA_DIR / f"infra_{municipio_key}.json"
    print(f"Leyendo {path}...")
    with open(path, encoding="utf-8") as f:
        capas_data = json.load(f)

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    total_global = 0
    try:
        with conn.cursor() as cur:
            for capa, data in capas_data.items():
                elements = data.get("elements", [])
                rows = []
                for el in elements:
                    tags = el.get("tags", {})
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
                        el["id"], el["type"], capa, tags.get("name"),
                        municipio, lat, lon, json.dumps(tags, ensure_ascii=False),
                    ))

                if not rows:
                    print(f"  capa '{capa}': 0 registros, se omite")
                    continue

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO infraestructura
                        (osm_id, osm_type, capa, nombre, municipio, lat, lon, tags_extra)
                    VALUES %s
                    ON CONFLICT (osm_type, osm_id, capa) DO UPDATE SET
                        nombre = EXCLUDED.nombre,
                        municipio = EXCLUDED.municipio,
                        lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon,
                        tags_extra = EXCLUDED.tags_extra,
                        actualizado_en = now()
                    """,
                    rows,
                    template="(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    page_size=1000,
                )
                print(f"  capa '{capa}': {len(rows)} registros cargados")
                total_global += len(rows)

            cur.execute(
                "INSERT INTO import_log (fuente, municipio, capa, registros_importados, notas) VALUES (%s,%s,%s,%s,%s)",
                ("overpass", municipio, "infraestructura_todas", total_global, f"archivo {path.name}"),
            )
        conn.commit()
        print(f"✅ Total infraestructura cargada para {municipio}: {total_global}")
    except Exception as e:
        conn.rollback()
        print("❌ Error, rollback:", e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MUNICIPIO_MAP:
        print(__doc__)
        sys.exit(1)
    load(sys.argv[1])
