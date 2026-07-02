#!/usr/bin/env python3
"""
Descarga edificios (y capas de infraestructura crítica) desde la API de Overpass
para La Guaira (Municipio Vargas) y Caracas, y guarda los resultados crudos en JSON.

Uso:
    python3 download_overpass.py buildings la_guaira
    python3 download_overpass.py buildings caracas
    python3 download_overpass.py infra la_guaira
    python3 download_overpass.py infra caracas
"""
import sys
import time
import json
import requests
from pathlib import Path

import os

# Por defecto descarga a ./data (ignorado por git, ver .gitignore) para no mezclar
# descargas completas con las muestras públicas en data_samples/.
DATA_DIR = Path(os.environ.get("RESCUEGIS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"  # mirror de respaldo

HEADERS = {"User-Agent": "VenezuelaGIS-Emergency/1.0 (proyecto humanitario terremoto 2026)"}

# area(id:XXXXXXXXXX) = relation_id + 3600000000
AREAS = {
    "la_guaira": 2442703,   # Municipio Vargas (La Guaira) admin_level 6
    "caracas": 11219583,    # Caracas (ciudad) admin_level 8
}

# Capas de infraestructura crítica: tag OSM -> nombre de capa interno
INFRA_QUERIES = {
    "hospital": 'node["amenity"~"hospital|clinic"](area.searchArea);way["amenity"~"hospital|clinic"](area.searchArea);',
    "bomberos": 'node["amenity"="fire_station"](area.searchArea);way["amenity"="fire_station"](area.searchArea);',
    "escuela": 'node["amenity"~"school|university|college"](area.searchArea);way["amenity"~"school|university|college"](area.searchArea);',
    "gasolinera": 'node["amenity"="fuel"](area.searchArea);way["amenity"="fuel"](area.searchArea);',
    "refugio": 'node["amenity"~"shelter|social_facility"](area.searchArea);way["amenity"~"shelter|social_facility"](area.searchArea);node["emergency"="assembly_point"](area.searchArea);',
    "telecom": 'node["man_made"~"tower|mast"]["tower:type"="communication"](area.searchArea);way["man_made"~"tower|mast"]["tower:type"="communication"](area.searchArea);',
    "agua": 'node["man_made"~"water_works|water_tower|reservoir_covered"](area.searchArea);way["man_made"~"water_works|water_tower|reservoir_covered"](area.searchArea);way["landuse"="reservoir"](area.searchArea);',
    "electricidad": 'node["power"~"substation|plant|generator"](area.searchArea);way["power"~"substation|plant|generator"](area.searchArea);',
}


def run_query(query: str, max_retries: int = 5, timeout: int = 180):
    urls = [OVERPASS_URL, OVERPASS_MIRROR]
    for attempt in range(max_retries):
        url = urls[attempt % len(urls)]
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"  [intento {attempt+1}] {url} -> HTTP {r.status_code}, reintentando en {10*(attempt+1)}s...")
        except Exception as e:
            print(f"  [intento {attempt+1}] {url} -> error: {e}, reintentando en {10*(attempt+1)}s...")
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("No se pudo completar la consulta Overpass tras varios reintentos")


def download_buildings(municipio: str):
    rel_id = AREAS[municipio]
    area_id = 3600000000 + rel_id
    query = f"""
    [out:json][timeout:180];
    area(id:{area_id})->.searchArea;
    (
      way["building"](area.searchArea);
      relation["building"](area.searchArea);
    );
    out center tags;
    """
    print(f"Descargando edificios de {municipio} (area {area_id})...")
    data = run_query(query)
    out_path = DATA_DIR / f"buildings_{municipio}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  -> {len(data.get('elements', []))} elementos guardados en {out_path}")
    return out_path


def download_infra(municipio: str):
    rel_id = AREAS[municipio]
    area_id = 3600000000 + rel_id
    results = {}
    for capa, clause in INFRA_QUERIES.items():
        query = f"""
        [out:json][timeout:120];
        area(id:{area_id})->.searchArea;
        (
          {clause}
        );
        out center tags;
        """
        print(f"Descargando capa '{capa}' de {municipio}...")
        try:
            data = run_query(query, max_retries=3, timeout=120)
            n = len(data.get("elements", []))
            print(f"  -> {n} elementos")
            results[capa] = data
        except Exception as e:
            print(f"  !! Falló capa {capa}: {e}")
            results[capa] = {"elements": []}
        time.sleep(3)  # ser amable con el servidor público

    out_path = DATA_DIR / f"infra_{municipio}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print(f"Infraestructura de {municipio} guardada en {out_path}")
    return out_path


def download_roads(municipio: str):
    rel_id = AREAS[municipio]
    area_id = 3600000000 + rel_id
    query = f"""
    [out:json][timeout:180];
    area(id:{area_id})->.searchArea;
    (
      way["highway"~"motorway|trunk|primary|secondary|tertiary"](area.searchArea);
      way["bridge"="yes"](area.searchArea);
    );
    out geom tags;
    """
    print(f"Descargando vías/puentes de {municipio}...")
    data = run_query(query)
    out_path = DATA_DIR / f"vias_{municipio}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  -> {len(data.get('elements', []))} elementos guardados en {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("buildings", "infra", "roads") or sys.argv[2] not in AREAS:
        print(__doc__)
        sys.exit(1)
    kind, municipio = sys.argv[1], sys.argv[2]
    if kind == "buildings":
        download_buildings(municipio)
    elif kind == "infra":
        download_infra(municipio)
    elif kind == "roads":
        download_roads(municipio)
