#!/usr/bin/env python3
"""
Correlación de reportes → SUCESOS (precisión mejorada del escaneo)
===================================================================

Agrupa incidentes abiertos que probablemente describen el MISMO suceso
(mismo derrumbe, mismas personas atrapadas) reportado por varias personas
o plataformas, y calcula por grupo:

  1. POSICIÓN REFINADA — centroide ponderado por 1/precisión²:
     con 2 reportes de coordenada degradada (±330 m de una fuente federada)
     y 1 GPS bueno (±10 m del bot de Telegram), la posición del suceso queda
     prácticamente en el GPS bueno.

  2. PRECISIÓN REFINADA — la mejor del grupo (la evidencia más precisa
     define qué tan bien sabemos dónde es).

  3. CONFIANZA MULTI-FUENTE (0–95, nunca 100 automático):
        base 25
      + 15 por cada fuente DISTINTA adicional (telegram + sosvenezuela + ong
        confirmándose mutuamente vale más que 10 reportes del mismo canal)
      +  5 por cada reporte adicional (tope +20)
      + 10 si alguna evidencia tiene confianza >= 60

  4. RE-MATCH de edificio con la posición refinada: un suceso puede lograr
     match FIRME (auto_150m) aunque sus reportes individuales fueran
     aproximados o sin match. Esto es lo que más precisión aporta.

Clustering en DOS PASADAS (consciente de la incertidumbre, sin encadenar):

  Pasada A: reportes PRECISOS (precisión <= 60 m) se agrupan con DBSCAN
            (ST_ClusterDBSCAN de PostGIS), eps = 120 m para ATRAPADOS/HERIDOS,
            250 m para daño estructural. Son las "anclas".
  Pasada B: reportes IMPRECISOS (coordenada degradada por privacidad) se
            ADHIEREN al ancla más cercana si está dentro de su radio de
            incertidumbre (+ eps). No se encadenan entre sí a través de
            su incertidumbre — eso fusionaría barrios enteros.
            Los que no alcanzan ningún ancla forman sus propios grupos
            con DBSCAN estándar.

Idempotente: regenera los sucesos de incidentes ABIERTOS en cada corrida
(los resueltos conservan su suceso como histórico). Correr por cron o tras
cada ingesta.

Uso:
    python3 correlacionar_sucesos.py            # correlaciona y escribe
    python3 correlacionar_sucesos.py --dry-run  # muestra clusters sin escribir
"""
import argparse
import math
import sys
import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

PRECISO_UMBRAL_M = 60.0    # <= esto se considera GPS confiable (ancla)
PRECISION_DEFAULT = 30.0   # para reportes sin coord_precision_m
GRUPOS = [
    (["ATRAPADOS", "HERIDOS"], 120),
    (["DANO_ESTRUCTURAL", "NECESIDAD_BASICA", "FALLECIDO", "OTRO"], 250),
]
ORDEN_URG = {"BAJA": 0, "MEDIA": 1, "ALTA": 2, "CRITICA": 3}
ORDEN_TIPO = {"ATRAPADOS": 4, "HERIDOS": 3, "FALLECIDO": 2,
              "DANO_ESTRUCTURAL": 1, "NECESIDAD_BASICA": 1, "OTRO": 0}

SQL_DBSCAN = """
WITH candidatos AS (
    SELECT i.id, i.tipo, i.urgencia::text AS urgencia, i.personas, i.heridos, i.fuente,
           i.lat, i.lon, i.geom,
           COALESCE(i.coord_precision_m, %(prec_def)s) AS prec,
           (SELECT MAX(e.nivel_confianza) FROM evidencias e WHERE e.incidente_id = i.id) AS max_evid,
           i.parroquia_consistente
    FROM incidentes i
    WHERE i.resuelto_en IS NULL
      AND i.estado_verificacion NOT IN ('DESCARTADO','DUPLICADO')
      AND i.lat IS NOT NULL
      AND i.tipo = ANY(%(tipos)s)
      AND (%(precisos)s AND COALESCE(i.coord_precision_m, %(prec_def)s) <= %(umbral)s
           OR NOT %(precisos)s AND COALESCE(i.coord_precision_m, %(prec_def)s) > %(umbral)s)
)
SELECT ST_ClusterDBSCAN(ST_Transform(geom, 3857), eps := %(eps)s, minpoints := 1)
           OVER () AS cluster_id,
       id, tipo, urgencia, personas, heridos, fuente, lat, lon, prec, max_evid,
       parroquia_consistente
FROM candidatos
"""


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def centroide_ponderado(miembros):
    wsum = sum(1.0 / (m["prec"] ** 2) for m in miembros)
    lat = sum(m["lat"] / (m["prec"] ** 2) for m in miembros) / wsum
    lon = sum(m["lon"] / (m["prec"] ** 2) for m in miembros) / wsum
    return lat, lon


def clusterizar(cur, tipos, eps, precisos):
    cur.execute(SQL_DBSCAN, {"tipos": tipos, "eps": eps, "prec_def": PRECISION_DEFAULT,
                             "umbral": PRECISO_UMBRAL_M, "precisos": precisos})
    clusters = {}
    for f in cur.fetchall():
        clusters.setdefault(f["cluster_id"], []).append(dict(f))
    return list(clusters.values())


def correlacionar(conn, dry_run=False):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if not dry_run:
        cur.execute("""
            DELETE FROM sucesos s
            WHERE NOT EXISTS (SELECT 1 FROM incidentes i
                              WHERE i.suceso_id = s.id AND i.resuelto_en IS NOT NULL)""")

    total = multi = firmes_ganados = adheridos = 0

    for tipos, eps in GRUPOS:
        # Pasada A: anclas (reportes precisos)
        anclas = clusterizar(cur, tipos, eps, precisos=True)
        for a in anclas:
            a_lat, a_lon = centroide_ponderado(a)
            a.append  # noqa — anclas es lista de listas; guardamos centroide aparte
        centroides = [centroide_ponderado(a) for a in anclas]

        # Pasada B: imprecisos se adhieren al ancla más cercana dentro de su incertidumbre
        imprecisos = clusterizar(cur, tipos, eps, precisos=False)
        residuales = []
        for grupo in imprecisos:
            for m in grupo:
                mejor_i, mejor_d = None, None
                for i, (clat, clon) in enumerate(centroides):
                    d = haversine_m(m["lat"], m["lon"], clat, clon)
                    if d <= m["prec"] + eps and (mejor_d is None or d < mejor_d):
                        mejor_i, mejor_d = i, d
                if mejor_i is not None:
                    anclas[mejor_i].append(m)
                    adheridos += 1
                else:
                    residuales.append(m)

        # Residuales: DBSCAN ya los agrupó entre sí (mismo cluster_id de la pasada B);
        # re-agrupamos los que quedaron del mismo cluster original
        res_grupos = {}
        for m in residuales:
            res_grupos.setdefault(id(next(g for g in imprecisos if m in g)), []).append(m)

        for miembros in anclas + list(res_grupos.values()):
            if not miembros:
                continue
            lat_ref, lon_ref = centroide_ponderado(miembros)
            prec_ref = min(m["prec"] for m in miembros)
            fuentes = {m["fuente"] for m in miembros}
            urg = max((m["urgencia"] for m in miembros), key=lambda u: ORDEN_URG[u])
            tipo_dom = max((m["tipo"] for m in miembros), key=lambda t: ORDEN_TIPO.get(t, 0))
            confianza = min(25 + 15 * (len(fuentes) - 1)
                            + min(5 * (len(miembros) - 1), 20)
                            + (10 if any((m["max_evid"] or 0) >= 60 for m in miembros) else 0)
                            # issue #3: coordenada contradice la parroquia declarada → -15
                            - (15 if any(m.get("parroquia_consistente") is False for m in miembros) else 0), 95)
            confianza = max(confianza, 5)

            if dry_run:
                if len(miembros) > 1:
                    print(f"  [{tipo_dom}] {len(miembros)} reportes · {len(fuentes)} fuentes · "
                          f"conf={confianza} · ±{prec_ref:.0f} m")
                total += 1
                multi += len(miembros) > 1
                continue

            cur.execute("SELECT * FROM buscar_edificio_aproximado(%s,%s,%s)",
                        (lat_ref, lon_ref, prec_ref))
            mm = cur.fetchone()
            b_id = mm["building_id"] if mm else None
            b_dist = mm["distancia_m"] if mm else None
            b_met = mm["metodo"] if mm else "sin_match"
            if b_met == "auto_150m" and any(x["prec"] > PRECISO_UMBRAL_M for x in miembros):
                firmes_ganados += 1

            cur.execute("""
                INSERT INTO sucesos (tipo_dominante, urgencia_max, personas_max, heridos_max,
                                     num_reportes, num_fuentes, confianza,
                                     lat, lon, coord_precision_m,
                                     building_id, building_match_metodo, building_match_distancia_m)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tipo_dom, urg,
                 max(x["personas"] or 0 for x in miembros),
                 max(x["heridos"] or 0 for x in miembros),
                 len(miembros), len(fuentes), confianza,
                 lat_ref, lon_ref, prec_ref, b_id, b_met, b_dist))
            sid = cur.fetchone()["id"]
            cur.execute("UPDATE incidentes SET suceso_id=%s WHERE id = ANY(%s)",
                        (sid, [x["id"] for x in miembros]))
            total += 1
            multi += len(miembros) > 1

    return total, multi, firmes_ganados, adheridos


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    try:
        n, m, f, adh = correlacionar(conn, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"""
  ── Correlación de sucesos ───────────────────────────
  Sucesos generados:            {n}
  Con múltiples reportes:       {m}
  Imprecisos adheridos a GPS:   {adh}  (reportes degradados unidos a un ancla precisa)
  Matches firmes GANADOS:       {f}  (sucesos con match preciso que ningún
                                      reporte individual habría logrado)
  Tiempo:                       {time.time()-t0:.1f}s
  ─────────────────────────────────────────────────────""")
    sys.exit(0)
