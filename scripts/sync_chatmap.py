#!/usr/bin/env python3
"""
Sincronizador automático de ChatMap (HOT) → RescueGIS
======================================================

Descubre TODOS los mapas públicos de ChatMap cuyo centroide cae en la zona
de la emergencia (Venezuela norte-costera), y los ingiere automáticamente:

  * Mapas de INCIDENTES/daños → tabla `incidentes` (vía connector_chatmap)
  * Mapas de REFUGIOS/recursos (nombre contiene refugio/campamento/acopio/
    albergue) → tabla `infraestructura` capa 'refugio' (son recursos, no
    emergencias — no deben inflar la cola de rescate)

Idempotente: el dedupe de incidentes por (fuente, id_externo) y el upsert de
infraestructura hacen que correr esto en cron sea seguro. Pensado para:

    */15 * * * * cd .../scripts && python3 sync_chatmap.py >> ../data/cron.log 2>&1

Uso:
    python3 sync_chatmap.py            # descubre e ingiere
    python3 sync_chatmap.py --listar   # solo muestra qué mapas ve
"""
import argparse
import hashlib
import json
import re
import sys
import time

import psycopg2
import requests

from db_config import DB_CONFIG
from connector_chatmap import ingerir

API_BASE = "https://chatmap.hotosm.org/api/v1"
UA = {"User-Agent": "RescueGIS-sync-chatmap/1.0 (humanitario, activacion 2026 VE; "
                    "github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas)"}

# Zona de la emergencia: costa norte-central de Venezuela (La Guaira/Caracas y alrededores)
BBOX = {"lat_min": 9.5, "lat_max": 11.6, "lon_min": -68.5, "lon_max": -65.5}

RE_RECURSO = re.compile(r"refugio|campamento|acopio|albergue|shelter|camp", re.IGNORECASE)


def es_zona_ve(centroid):
    if not centroid or len(centroid) < 2:
        return False
    lat, lon = centroid[0], centroid[1]
    return BBOX["lat_min"] <= lat <= BBOX["lat_max"] and BBOX["lon_min"] <= lon <= BBOX["lon_max"]


def listar_mapas():
    r = requests.get(f"{API_BASE}/map", headers=UA, timeout=30)
    r.raise_for_status()
    mapas = r.json()
    seleccion = [m for m in mapas if es_zona_ve(m.get("centroid"))]
    return mapas, seleccion


def ingerir_refugios(geojson, mapa_nombre):
    """Puntos de un mapa de recursos → infraestructura capa 'refugio'."""
    conn = psycopg2.connect(**DB_CONFIG)
    n = 0
    try:
        with conn, conn.cursor() as cur:
            for f in geojson.get("features", []):
                g = f.get("geometry") or {}
                if g.get("type") != "Point":
                    continue
                lon, lat = g["coordinates"][:2]
                p = f.get("properties", {})
                # osm_id sintético estable: hash DETERMINISTA del id del punto
                # (hash() de Python varía entre procesos — usar sha1)
                pid = p.get("id") or f"{lat},{lon}"
                osm_id = -int(hashlib.sha1(f"chatmap:{pid}".encode()).hexdigest()[:12], 16)  # negativo = no-OSM
                municipio = "LA_GUAIRA" if lon < -66.75 and lat > 10.45 else "CARACAS"
                cur.execute("""
                    INSERT INTO infraestructura (osm_id, osm_type, capa, nombre, municipio, lat, lon, operativo, tags_extra)
                    VALUES (%s, 'chatmap', 'refugio', %s, %s, %s, %s, TRUE, %s::jsonb)
                    ON CONFLICT (osm_type, osm_id, capa) DO UPDATE SET
                        nombre = EXCLUDED.nombre, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                        actualizado_en = now()""",
                    (osm_id,
                     (p.get("message") or "").strip()[:200] or f"Refugio reportado vía ChatMap ({mapa_nombre})",
                     municipio, lat, lon,
                     json.dumps({"fuente": "chatmap", "mapa": mapa_nombre,
                                 "foto": p.get("file"), "hora": p.get("time")}, ensure_ascii=False)))
                n += 1
            cur.execute("INSERT INTO import_log (fuente, capa, registros_importados, notas) VALUES ('chatmap','refugio',%s,%s)",
                        (n, f"sync mapa: {mapa_nombre}"))
    finally:
        conn.close()
    return n


def main(solo_listar=False):
    todos, seleccion = listar_mapas()
    print(f"ChatMap: {len(todos)} mapas públicos, {len(seleccion)} en la zona de la emergencia:")
    for m in seleccion:
        tipo = "RECURSO" if RE_RECURSO.search(m.get("name") or "") else "INCIDENTES"
        print(f"  [{tipo:>10}] «{(m.get('name') or 'sin nombre')[:45]}» pts={m.get('count')} "
              f"live={m.get('is_live')} upd={(m.get('updated_at') or '')[:10]}")
    if solo_listar:
        return

    for m in seleccion:
        nombre = m.get("name") or m["id"][:8]
        try:
            r = requests.get(f"{API_BASE}/map/{m['id']}", headers=UA, timeout=60)
            r.raise_for_status()
            geo = r.json()
        except Exception as e:
            print(f"  !! {nombre}: error descargando ({e})")
            continue

        if RE_RECURSO.search(nombre):
            n = ingerir_refugios(geo, nombre)
            print(f"  ✅ «{nombre}»: {n} refugios/recursos → infraestructura")
        else:
            print(f"  ▶ «{nombre}»: ingiriendo como incidentes...")
            # chats de evaluación de daños: el contexto define el tipo por defecto
            ingerir(geo, chat_label=f"chatmap:{m['id'][:8]}", tipo_default="DANO_ESTRUCTURAL")
        time.sleep(2)  # amable con su API


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true")
    args = ap.parse_args()
    main(solo_listar=args.listar)
