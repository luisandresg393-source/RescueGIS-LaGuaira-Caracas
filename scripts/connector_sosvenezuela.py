#!/usr/bin/env python3
"""
Conector SOS Venezuela 2026 → RescueGIS
========================================

Ingiere los reportes públicos de https://sosvenezuela2026.com (GET /api/reports,
API abierta de solo lectura, CORS abierto, sin autenticación) y los convierte en
`incidentes` de RescueGIS, con matching automático a la base de edificios OSM.

Reglas de su API que este conector respeta:
  * Límite ~90 req/min por IP  → hacemos UNA sola petición por corrida.
  * Cachear respuestas          → guardamos el JSON crudo en data/ con timestamp.
  * Citar la fuente             → columna `atribucion` = "SOS Venezuela 2026".
  * Respetar la privacidad      → sus coordenadas vienen truncadas (3 decimales)
                                  y con jitter de 80–250 m (anti-saqueo). NUNCA
                                  intentamos desanonimizarlas; en su lugar
                                  registramos `coord_precision_m` y degradamos
                                  el match a 'match_aproximado' (exige
                                  confirmación humana antes de despachar).

Mapeo de categorías (su enum report_category → nuestro tipo de incidente):
  trapped_people      → ATRAPADOS
  collapsed_building  → DANO_ESTRUCTURAL (colapso)
  damaged_building    → DANO_ESTRUCTURAL
  fire | gas_leak     → OTRO (peligro activo)
  medical_need        → HERIDOS
  flooding            → OTRO
  blocked_road        → (se omite: no es un incidente de edificio; futuro: tabla vias)
  shelter|water_point|aid_point → (se omiten: son recursos, no emergencias)

Verificación (su enum → evidencia nuestra, NUNCA verificación automática):
  community_confirmed → evidencia 'testimonio' con nivel_confianza 60
  official_verified   → evidencia 'testimonio' con nivel_confianza 85
  false_report        → se omite el reporte
  resolved            → se ingiere y se anota en la descripción (para no
                        reabrir búsquedas ya resueltas)

Dedupe: índice único (fuente, id_externo) → correr esto N veces no duplica nada.

Uso:
    python3 connector_sosvenezuela.py                # corrida normal (API en vivo)
    python3 connector_sosvenezuela.py --from-file data/sos_reports_YYYYMMDD.json
    python3 connector_sosvenezuela.py --dry-run      # no escribe en la BD
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

# SOS_API_URL permite apuntar a un mock local para pruebas end-to-end
API_URL = os.environ.get("SOS_API_URL", "https://sosvenezuela2026.com/api/reports")
FUENTE = "sosvenezuela2026"
ATRIBUCION = "SOS Venezuela 2026 (sosvenezuela2026.com) — datos abiertos para fines humanitarios"
USER_AGENT = "RescueGIS-LaGuaira-Caracas/1.0 (conector humanitario; github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas)"

DATA_DIR = Path(os.environ.get("RESCUEGIS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------
# Mapeos
# ------------------------------------------------------------------
CATEGORIA_A_TIPO = {
    "trapped_people": "ATRAPADOS",
    "collapsed_building": "DANO_ESTRUCTURAL",
    "damaged_building": "DANO_ESTRUCTURAL",
    "fire": "OTRO",
    "gas_leak": "OTRO",
    "flooding": "OTRO",
    "medical_need": "HERIDOS",
}
CATEGORIAS_OMITIDAS = {"shelter", "water_point", "aid_point", "blocked_road"}

SEVERIDAD_A_URGENCIA = {
    "rojo": "CRITICA",
    "naranja": "ALTA",
    "amarillo": "MEDIA",
    "verde": "BAJA",
}

VERIFICACION_A_CONFIANZA = {
    "community_confirmed": 60,
    "official_verified": 85,
}


def estimar_precision_m(lat, lng):
    """
    Estima la precisión (metros) de una coordenada publicada por SOS Venezuela.

    Su función create_hazard_report aplica: jitter aleatorio de 80–250 m y
    redondeo a 3 decimales (~±55 m en lat, ~±55 m·cos(lat) en lng).
    → peor caso ≈ 250 + 78 ≈ 330 m con 3 decimales.
    Si detectamos MENOS decimales (reportes viejos o importados con 2), la
    celda de redondeo domina: ~±550 m → precisión ≈ 800 m.
    """
    def decimales(v):
        s = repr(float(v))
        return len(s.split(".")[1]) if "." in s else 0

    d = min(decimales(lat), decimales(lng))
    if d >= 4:
        return 250.0 + 8.0     # solo el jitter domina
    if d == 3:
        return 250.0 + 78.0    # jitter + celda de 3 decimales
    if d == 2:
        return 250.0 + 780.0   # celda de 2 decimales domina
    return 250.0 + 7800.0      # 1 decimal o menos: inutilizable para match


def fetch_reports(max_retries=4, base_wait=15):
    """Una sola petición lógica, con reintentos exponenciales si el API falla."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.get(API_URL, headers=headers, timeout=30)
            if r.status_code == 200:
                data = r.json()
                # cache local con timestamp (buena práctica pedida por la fuente)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                cache = DATA_DIR / f"sos_reports_{stamp}.json"
                cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                print(f"  API OK: {len(data)} reportes (cache: {cache.name})")
                return data
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        wait = base_wait * (attempt + 1)
        print(f"  [intento {attempt+1}/{max_retries}] {last_err} — reintento en {wait}s...")
        time.sleep(wait)
    raise RuntimeError(
        f"API de SOS Venezuela no disponible ({last_err}). "
        f"Puedes reintentarlo más tarde o usar --from-file con un cache previo."
    )


def transformar(reporte):
    """Convierte un reporte de SOS Venezuela en una fila de `incidentes` (o None si se omite)."""
    cat = reporte.get("category")
    if cat in CATEGORIAS_OMITIDAS or cat not in CATEGORIA_A_TIPO:
        return None
    if reporte.get("verification") == "false_report":
        return None
    lat, lng = reporte.get("lat_pub"), reporte.get("lng_pub")
    if lat is None or lng is None:
        return None

    tipo = CATEGORIA_A_TIPO[cat]
    urgencia = SEVERIDAD_A_URGENCIA.get(reporte.get("severity"), "MEDIA")
    # ATRAPADOS siempre es al menos ALTA, aunque venga sin severidad
    if tipo == "ATRAPADOS" and urgencia in ("BAJA", "MEDIA"):
        urgencia = "ALTA"

    personas = reporte.get("people_trapped_count") or 0
    desc_partes = []
    if reporte.get("title"):
        desc_partes.append(reporte["title"].strip())
    if reporte.get("description"):
        desc_partes.append(reporte["description"].strip())
    desc_partes.append(f"[categoría original: {cat}]")
    if reporte.get("people_trapped_unknown"):
        desc_partes.append("[número de atrapados DESCONOCIDO según el reportero]")
    if reporte.get("verification") == "resolved":
        desc_partes.append("[marcado RESUELTO en la fuente]")
    if reporte.get("building_type"):
        desc_partes.append(f"[tipo de edificio: {reporte['building_type']}]")
    if reporte.get("site_class"):
        desc_partes.append(f"[suelo NEHRP clase {reporte['site_class']}, Vs30={reporte.get('site_vs30')} m/s]")

    return {
        "id_externo": reporte["id"],
        "tipo": tipo,
        "descripcion": " · ".join(desc_partes),
        "personas": int(personas),
        "urgencia": urgencia,
        "lat": float(lat),
        "lon": float(lng),
        "coord_precision_m": estimar_precision_m(lat, lng),
        "url_fuente": reporte.get("source_url") or f"https://sosvenezuela2026.com/?report={reporte['id']}",
        "fecha": reporte.get("created_at"),
        "municipio_texto": reporte.get("municipio"),
        "parroquia_texto": reporte.get("parroquia"),
        "verification": reporte.get("verification", "unverified"),
        "image_url": reporte.get("image_url"),
    }


def ingerir(reportes, dry_run=False):
    filas = [t for t in (transformar(r) for r in reportes) if t]
    omitidos = len(reportes) - len(filas)
    print(f"  {len(filas)} incidentes a ingerir ({omitidos} omitidos: recursos/vías/false_report/sin coords)")
    if dry_run:
        from collections import Counter
        print("  DRY-RUN — no se escribe en la BD. Desglose:", Counter(f["tipo"] for f in filas))
        return

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    nuevos = matched = aprox = sin_match = duplicados = evidencias_creadas = 0
    try:
        with conn.cursor() as cur:
            for f in filas:
                # 1) matching con precisión limitada (SQL, indexado KNN)
                cur.execute(
                    "SELECT building_id, distancia_m, metodo FROM buscar_edificio_aproximado(%s, %s, %s)",
                    (f["lat"], f["lon"], f["coord_precision_m"]),
                )
                m = cur.fetchone()
                b_id, b_dist, b_metodo = (m if m else (None, None, "sin_match"))

                # 2) insert con dedupe por (fuente, id_externo)
                cur.execute(
                    """
                    INSERT INTO incidentes
                        (tipo, descripcion, personas, urgencia, fuente, id_externo,
                         url_fuente, atribucion, coord_precision_m, lat, lon,
                         building_id, building_match_metodo, building_match_distancia_m,
                         fecha, reportero_nombre, parroquia_declarada)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            COALESCE(%s::timestamptz, now()), %s, %s)
                    ON CONFLICT (fuente, id_externo) WHERE id_externo IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    (
                        f["tipo"], f["descripcion"], f["personas"], f["urgencia"],
                        FUENTE, f["id_externo"], f["url_fuente"], ATRIBUCION,
                        f["coord_precision_m"], f["lat"], f["lon"],
                        b_id, b_metodo, b_dist,
                        f["fecha"],
                        (f["municipio_texto"] or "") + (" / " + f["parroquia_texto"] if f["parroquia_texto"] else ""),
                        f["parroquia_texto"],
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    duplicados += 1
                    continue
                incidente_id = row[0]
                nuevos += 1
                if b_metodo == "auto_150m":
                    matched += 1
                elif b_metodo == "match_aproximado":
                    aprox += 1
                else:
                    sin_match += 1

                # 3) su verificación entra como EVIDENCIA, nunca como verificación automática
                conf = VERIFICACION_A_CONFIANZA.get(f["verification"])
                if conf:
                    cur.execute(
                        """
                        INSERT INTO evidencias (incidente_id, tipo, url_archivo, usuario, nivel_confianza, notas)
                        VALUES (%s, 'testimonio', %s, %s, %s, %s)
                        """,
                        (
                            incidente_id, f.get("image_url"), FUENTE, conf,
                            f"Estado '{f['verification']}' en SOS Venezuela 2026 — requiere confirmación propia antes de despachar.",
                        ),
                    )
                    evidencias_creadas += 1

            cur.execute(
                "INSERT INTO import_log (fuente, capa, registros_importados, notas) VALUES (%s,%s,%s,%s)",
                (FUENTE, "incidentes", nuevos,
                 f"conector: {matched} matched / {aprox} aproximados / {sin_match} sin_match / {duplicados} duplicados omitidos"),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"""
  ── Resultado de la corrida ──────────────────────────
  Nuevos incidentes:        {nuevos}
    · matched (firme):      {matched}
    · match_aproximado:     {aprox}   ← exigen confirmación humana
    · sin_match:            {sin_match}
  Duplicados omitidos:      {duplicados}
  Evidencias creadas:       {evidencias_creadas}
  ─────────────────────────────────────────────────────""")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-file", help="ingiere desde un JSON cacheado en vez del API en vivo")
    ap.add_argument("--dry-run", action="store_true", help="transforma pero no escribe en la BD")
    args = ap.parse_args()

    if args.from_file:
        print(f"Leyendo cache {args.from_file}...")
        reportes = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        print(f"Consultando {API_URL} ...")
        reportes = fetch_reports()

    ingerir(reportes, dry_run=args.dry_run)
