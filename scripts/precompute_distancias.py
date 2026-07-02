#!/usr/bin/env python3
"""
Precalcula, para cada edificio, la distancia (en metros) al hospital y a la estación
de bomberos más cercana, usando el operador KNN de PostGIS (<->), que es indexado
y rápido incluso sobre 96k+ filas.

Se hace en un solo UPDATE masivo con LATERAL JOIN (mucho más rápido que llamar la
función PL/pgSQL fila por fila desde Python).
"""
import psycopg2
import time

from db_config import DB_CONFIG

SQL = """
UPDATE buildings b
SET dist_hospital_m = (
        SELECT ST_Distance(i.geom::geography, b.geom::geography)
        FROM infraestructura i
        WHERE i.capa = 'hospital'
        ORDER BY i.geom <-> b.geom
        LIMIT 1
    ),
    dist_bomberos_m = (
        SELECT ST_Distance(i.geom::geography, b.geom::geography)
        FROM infraestructura i
        WHERE i.capa = 'bomberos'
        ORDER BY i.geom <-> b.geom
        LIMIT 1
    );
"""

if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    t0 = time.time()
    with conn.cursor() as cur:
        print("Calculando distancias a hospital/bomberos más cercano para todos los edificios...")
        cur.execute(SQL)
        print(f"  {cur.rowcount} filas actualizadas")
    conn.commit()
    conn.close()
    print(f"Listo en {time.time()-t0:.1f}s")
