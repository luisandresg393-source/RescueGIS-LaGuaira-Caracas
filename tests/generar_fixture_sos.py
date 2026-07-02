#!/usr/bin/env python3
"""
Genera un fixture de PRUEBA para el conector SOS Venezuela → RescueGIS.

⚠️  DATOS SIMULADOS — NO SON REPORTES REALES.
Se usa cuando el API en vivo (sosvenezuela2026.com/api/reports) no está
disponible, para probar el pipeline completo (transformación, matching con
precisión limitada, dedupe, evidencias) sin inventar emergencias reales.

Cómo se construye (fiel al comportamiento real de su plataforma):
  1. Muestrea edificios REALES de nuestra base OSM (La Guaira + Caracas).
  2. Aplica EXACTAMENTE la misma degradación de privacidad que su función
     create_hazard_report (schema.sql de Z1Code/sosvenezuela2026):
        d = 80 + random()*170   (jitter de 80–250 m, rumbo aleatorio)
        round(coord, 3)          (truncado a 3 decimales)
  3. Distribuye categorías/severidades/verificaciones con proporciones
     plausibles para la fase actual de la emergencia.

Uso:
    python3 generar_fixture_sos.py [n_reportes] > ../data/fixture_sos_reports.json
"""
import json
import math
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import psycopg2
from db_config import DB_CONFIG

random.seed(20260702)  # reproducible

N = int(sys.argv[1]) if len(sys.argv) > 1 else 400

CATEGORIAS = [
    ("damaged_building", 0.52), ("collapsed_building", 0.17), ("trapped_people", 0.06),
    ("gas_leak", 0.04), ("fire", 0.03), ("medical_need", 0.05), ("blocked_road", 0.06),
    ("shelter", 0.04), ("aid_point", 0.03),
]
SEVERIDADES = [("rojo", 0.18), ("naranja", 0.30), ("amarillo", 0.38), ("verde", 0.14)]
VERIFICACIONES = [("unverified", 0.55), ("community_confirmed", 0.30),
                  ("official_verified", 0.08), ("resolved", 0.05), ("false_report", 0.02)]


def elegir(dist):
    r, acc = random.random(), 0.0
    for k, p in dist:
        acc += p
        if r <= acc:
            return k
    return dist[-1][0]


def jitter_sos(lat, lng):
    """Réplica exacta de la degradación de privacidad de create_hazard_report."""
    d = 80 + random.random() * 170
    b = random.random() * 2 * math.pi
    dlat = (d * math.cos(b)) / 111320.0
    dlng = (d * math.sin(b)) / (111320.0 * math.cos(math.radians(lat)))
    return round(lat + dlat, 3), round(lng + dlng, 3)


conn = psycopg2.connect(**DB_CONFIG)
with conn.cursor() as cur:
    cur.execute("""
        SELECT lat, lon, municipio::text, COALESCE(nombre,''), COALESCE(pisos,0)
        FROM buildings TABLESAMPLE SYSTEM (2)
        LIMIT %s
    """, (N,))
    edificios = cur.fetchall()
conn.close()

t0 = datetime(2026, 6, 24, 10, 21, tzinfo=timezone.utc)  # hora del sismo principal
reportes = []
for lat, lon, muni, nombre, pisos in edificios[:N]:
    cat = elegir(CATEGORIAS)
    lat_pub, lng_pub = jitter_sos(lat, lon)
    trapped = None
    trapped_unknown = False
    if cat == "trapped_people":
        trapped = random.choice([1, 1, 2, 2, 3, 4, 5, 8, None])
        trapped_unknown = trapped is None
    reportes.append({
        "id": str(uuid.uuid4()),
        "category": cat,
        "severity": elegir(SEVERIDADES) if cat not in ("shelter", "aid_point") else None,
        "resource_status": "open" if cat in ("shelter", "aid_point") else None,
        "verification": elegir(VERIFICACIONES),
        "title": f"[SIMULADO] {cat.replace('_', ' ')} — {nombre or muni.title()}",
        "description": "FIXTURE DE PRUEBA generado desde un edificio OSM real con la "
                       "misma degradación de privacidad de SOS Venezuela. NO es un reporte real.",
        "lat_pub": lat_pub,
        "lng_pub": lng_pub,
        "municipio": "La Guaira" if muni == "LA_GUAIRA" else "Libertador",
        "parroquia": None,
        "building_type": None,
        "people_trapped_count": trapped,
        "people_trapped_unknown": trapped_unknown,
        "source_url": None,
        "image_url": None,
        "site_vs30": random.choice([None, 220, 278, 340, 415, 620]),
        "site_class": random.choice([None, "C", "D", "D", "E"]),
        "created_at": (t0 + timedelta(minutes=random.randint(5, 60 * 24 * 7))).isoformat(),
    })

print(json.dumps(reportes, ensure_ascii=False, indent=1))
print(f"\n[fixture] {len(reportes)} reportes simulados generados", file=sys.stderr)
