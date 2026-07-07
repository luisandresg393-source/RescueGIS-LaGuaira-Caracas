#!/usr/bin/env python3
"""
API propia de RescueGIS — La Guaira / Caracas
==============================================

API REST (FastAPI) para CAPTAR reportes y ENTREGAR información priorizada a
cualquier cuerpo de emergencia, ONG o plataforma aliada.

Diseño (mismos principios que el resto del proyecto):

  * PÚBLICO (sin key): datos degradados por privacidad — coordenadas
    redondeadas a 3 decimales + jitter, sin contactos, sin descripciones
    completas. Igual que hace SOS Venezuela (anti-saqueo).
  * CON API KEY (header `X-Api-Key`):
      - rol 'emergencia': coordenadas PRECISAS, exportes GeoJSON/CSV,
        verificación de incidentes y despacho (asignar/resolver).
      - rol 'ingesta':    puede POSTear reportes nuevos (bots, plataformas).
      - rol 'socio':      lectura precisa, sin acciones de despacho.
  * Nada se verifica automáticamente: la verificación siempre lleva la firma
    (key) de quien la hizo, y queda auditada en api_log.
  * Rate limit por key (columna rate_limit_min) y por IP para lo público.

Arranque:
    export $(cat ../.env | xargs)          # PGPASSWORD etc.
    uvicorn main:app --host 0.0.0.0 --port 8000

Documentación interactiva automática:  http://<host>:8000/docs
Gestión de keys:                       python3 ../scripts/gestionar_keys.py crear ...
"""
import hashlib
import math
import os
import random
import sys
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from db_config import DB_CONFIG  # noqa: E402

VERSION = "1.0.0"
ATRIBUCION = ("RescueGIS La Guaira/Caracas — datos de edificios © OpenStreetMap (ODbL); "
              "incidentes de fuentes ciudadanas y federadas. Uso humanitario.")

app = FastAPI(
    title="RescueGIS API",
    version=VERSION,
    description=__doc__,
    docs_url="/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # lectura pública abierta, como SOS Venezuela
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

POOL = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=8, **DB_CONFIG)


@contextmanager
def db():
    conn = POOL.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        POOL.putconn(conn)


def qall(conn, sql, args=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def qone(conn, sql, args=None):
    rows = qall(conn, sql, args)
    return rows[0] if rows else None


# ------------------------------------------------------------------
# Rate limiting (en memoria; suficiente para un nodo. Para varios
# nodos usar nginx limit_req o Redis)
# ------------------------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)

def rate_limit(clave: str, por_minuto: int):
    ahora = time.time()
    ventana = _hits[clave]
    while ventana and ventana[0] < ahora - 60:
        ventana.popleft()
    if len(ventana) >= por_minuto:
        raise HTTPException(429, f"Rate limit: máx {por_minuto} req/min. Espera un momento.")
    ventana.append(ahora)


# ------------------------------------------------------------------
# Autenticación por API key (hash sha256, nunca en claro)
# ------------------------------------------------------------------
class Cliente:
    def __init__(self, row=None, ip="?"):
        self.id = row["id"] if row else None
        self.nombre = row["nombre"] if row else "público"
        self.rol = row["rol"] if row else "publico"
        self.rate = row["rate_limit_min"] if row else 30
        self.ip = ip

    @property
    def preciso(self):
        return self.rol in ("emergencia", "socio", "admin")


def autenticar(request: Request, x_api_key: Optional[str] = Header(default=None)) -> Cliente:
    ip = request.client.host if request.client else "?"
    if not x_api_key:
        cli = Cliente(ip=ip)
        rate_limit(f"ip:{ip}", cli.rate)
        return cli
    key_hash = hashlib.sha256(x_api_key.strip().encode()).hexdigest()
    with db() as conn:
        row = qone(conn, "SELECT * FROM api_keys WHERE key_hash=%s AND activo", (key_hash,))
        if not row:
            raise HTTPException(401, "API key inválida o desactivada.")
        qall(conn, "UPDATE api_keys SET ultimo_uso_en=now() WHERE id=%s RETURNING id", (row["id"],))
    cli = Cliente(row, ip=ip)
    rate_limit(f"key:{row['id']}", cli.rate)
    return cli


def exigir_rol(cli: Cliente, *roles):
    if cli.rol not in roles and cli.rol != "admin":
        raise HTTPException(403, f"Esta operación requiere una key con rol {roles} (tu rol: {cli.rol}). "
                                 "Solicita acceso al coordinador del proyecto.")


def log_api(cli: Cliente, metodo: str, ruta: str, status: int, detalle: str = None):
    try:
        with db() as conn:
            qall(conn, """INSERT INTO api_log (api_key_id, ip, metodo, ruta, status, detalle)
                          VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                 (cli.id, cli.ip, metodo, ruta, status, detalle))
    except Exception:
        pass  # el log nunca debe tumbar una respuesta


# ------------------------------------------------------------------
# Privacidad: degradación de coordenadas para respuestas públicas
# (jitter 80–250 m + redondeo a 3 decimales — mismo esquema que SOS Venezuela)
# ------------------------------------------------------------------
def degradar(lat, lon):
    if lat is None or lon is None:
        return None, None
    d = 80 + random.random() * 170
    b = random.random() * 2 * math.pi
    dlat = (d * math.cos(b)) / 111320.0
    dlon = (d * math.sin(b)) / (111320.0 * math.cos(math.radians(lat)))
    return round(lat + dlat, 3), round(lon + dlon, 3)


# ==================================================================
# MODELOS
# ==================================================================
class ReporteIn(BaseModel):
    """Reporte ciudadano/institucional entrante."""
    tipo: str = Field("ATRAPADOS", pattern="^(ATRAPADOS|HERIDOS|DANO_ESTRUCTURAL|NECESIDAD_BASICA|FALLECIDO|OTRO)$")
    descripcion: Optional[str] = Field(None, max_length=4000)
    lat: float = Field(..., ge=10.0, le=11.0, description="Latitud WGS84 (zona La Guaira/Caracas)")
    lon: float = Field(..., ge=-67.5, le=-66.0, description="Longitud WGS84")
    coord_precision_m: Optional[float] = Field(None, ge=0, le=10000,
        description="Precisión estimada del GPS en metros; si >60 el match será 'aproximado'")
    personas: int = Field(0, ge=0, le=1000)
    heridos: int = Field(0, ge=0, le=1000)
    ninos: int = Field(0, ge=0, le=1000)
    urgencia: str = Field("MEDIA", pattern="^(BAJA|MEDIA|ALTA|CRITICA)$")
    necesidades: Optional[list[str]] = None
    reportero_nombre: Optional[str] = Field(None, max_length=200)
    telefono_contacto: Optional[str] = Field(None, max_length=50,
        description="SOLO visible para cuerpos de emergencia, nunca en respuestas públicas")
    id_externo: Optional[str] = Field(None, max_length=200,
        description="ID en tu plataforma (dedupe por fuente+id_externo)")
    url_fuente: Optional[str] = Field(None, max_length=500)
    recursos_solicitados: Optional[list[str]] = Field(None,
        description="herramientas/apoyo que se necesita: retroexcavadora, motosierra, grua, generador, medico, perro_rescate, personal...")


class VerificacionIn(BaseModel):
    accion: str = Field(..., pattern="^(VERIFICADO|DESCARTADO|DUPLICADO)$")
    notas: Optional[str] = Field(None, max_length=2000)


class DespachoIn(BaseModel):
    accion: str = Field(..., pattern="^(asignar|resolver)$")
    resultado: Optional[str] = Field(None, pattern="^(rescatados|sin_hallazgo|falso|trasladado)$")
    notas: Optional[str] = Field(None, max_length=2000)


# ==================================================================
# ENDPOINTS PÚBLICOS (sin key — datos degradados)
# ==================================================================
@app.get("/", tags=["público"])
def raiz():
    return {
        "servicio": "RescueGIS API", "version": VERSION,
        "documentacion": "/docs",
        "atribucion": ATRIBUCION,
        "acceso": {
            "publico": "lectura con coordenadas degradadas por privacidad (~±300 m), 30 req/min por IP",
            "con_key": "header X-Api-Key — roles: emergencia (precisión total + despacho), "
                       "ingesta (POST reportes), socio (lectura precisa)",
            "solicitar_key": "contacta al coordinador del proyecto (ver README del repo)",
        },
    }


@app.get("/api/v1/salud", tags=["público"])
def salud():
    with db() as conn:
        s = qone(conn, """SELECT (SELECT count(*) FROM buildings) AS edificios,
                                 (SELECT count(*) FROM incidentes) AS incidentes,
                                 (SELECT count(*) FROM incidentes WHERE estado_verificacion='PENDIENTE_VERIFICACION') AS pendientes,
                                 (SELECT max(creado_en) FROM incidentes) AS ultimo_incidente""")
    return {"ok": True, "hora": datetime.now(timezone.utc).isoformat(), **s}


@app.get("/api/v1/incidentes", tags=["público"])
def listar_incidentes(
    request: Request,
    response: Response,
    cli: Cliente = Depends(autenticar),
    tipo: Optional[str] = Query(None, pattern="^(ATRAPADOS|HERIDOS|DANO_ESTRUCTURAL|NECESIDAD_BASICA|FALLECIDO|OTRO)$"),
    urgencia: Optional[str] = Query(None, pattern="^(BAJA|MEDIA|ALTA|CRITICA)$"),
    estado: Optional[str] = Query(None, pattern="^(PENDIENTE_VERIFICACION|VERIFICADO|DESCARTADO|DUPLICADO)$"),
    municipio: Optional[str] = Query(None, pattern="^(LA_GUAIRA|CARACAS)$"),
    sin_asignar: bool = Query(False, description="solo incidentes que ningún cuerpo ha tomado"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Incidentes con su edificio matcheado. Público: coordenadas degradadas.
    Con key emergencia/socio: coordenadas precisas + contacto del reportero."""
    where, args = ["1=1"], []
    if tipo:      where.append("i.tipo=%s");                args.append(tipo)
    if urgencia:  where.append("i.urgencia=%s");            args.append(urgencia)
    if estado:    where.append("i.estado_verificacion=%s"); args.append(estado)
    if municipio: where.append("b.municipio=%s");           args.append(municipio)
    if sin_asignar: where.append("i.asignado_a IS NULL AND i.resuelto_en IS NULL")
    args += [limit, offset]

    with db() as conn:
        rows = qall(conn, f"""
            SELECT i.codigo, i.tipo, i.urgencia::text, i.estado_verificacion::text,
                   i.personas, i.heridos, i.ninos, i.necesidades, i.descripcion,
                   i.lat, i.lon, i.coord_precision_m, i.fuente, i.url_fuente,
                   i.building_match_metodo, round(i.building_match_distancia_m::numeric,1) AS match_dist_m,
                   b.codigo_corto AS edificio, b.nombre AS edificio_nombre,
                   b.municipio::text, b.prioridad::text, b.prioridad_score,
                   b.lat AS edificio_lat, b.lon AS edificio_lon,
                   i.telefono_contacto, i.reportero_nombre,
                   i.asignado_a, i.asignado_en, i.resuelto_en, i.resultado,
                   i.fecha, i.creado_en
            FROM incidentes i LEFT JOIN buildings b ON b.id=i.building_id
            WHERE {' AND '.join(where)}
            ORDER BY CASE i.urgencia WHEN 'CRITICA' THEN 3 WHEN 'ALTA' THEN 2 WHEN 'MEDIA' THEN 1 ELSE 0 END DESC,
                     i.fecha DESC
            LIMIT %s OFFSET %s""", args)

    for r in rows:
        r["fecha"] = r["fecha"].isoformat() if r["fecha"] else None
        r["creado_en"] = r["creado_en"].isoformat() if r["creado_en"] else None
        r["asignado_en"] = r["asignado_en"].isoformat() if r["asignado_en"] else None
        r["resuelto_en"] = r["resuelto_en"].isoformat() if r["resuelto_en"] else None
        if not cli.preciso:
            r["lat"], r["lon"] = degradar(r["lat"], r["lon"])
            r["edificio_lat"], r["edificio_lon"] = degradar(r["edificio_lat"], r["edificio_lon"])
            r["telefono_contacto"] = None
            r["reportero_nombre"] = None
            if r["descripcion"]:
                r["descripcion"] = r["descripcion"][:140] + ("…" if len(r["descripcion"]) > 140 else "")
    response.headers["Cache-Control"] = "public, max-age=20" if not cli.preciso else "no-store"
    log_api(cli, "GET", "/api/v1/incidentes", 200, f"{len(rows)} filas")
    return {"total": len(rows), "precision": "exacta" if cli.preciso else "degradada_privacidad",
            "atribucion": ATRIBUCION, "incidentes": rows}


@app.get("/api/v1/edificios/prioridad", tags=["público"])
def edificios_prioridad(
    response: Response,
    cli: Cliente = Depends(autenticar),
    prioridad: Optional[str] = Query(None, pattern="^(BAJA|MEDIA|ALTA|CRITICA)$"),
    municipio: Optional[str] = Query(None, pattern="^(LA_GUAIRA|CARACAS)$"),
    limit: int = Query(200, ge=1, le=1000),
):
    """Cola de trabajo: edificios con reportes, ordenados por score de prioridad
    (fórmula transparente — ver /docs del repo)."""
    where, args = ["num_reportes>0"], []
    if prioridad: where.append("prioridad=%s"); args.append(prioridad)
    if municipio: where.append("municipio=%s"); args.append(municipio)
    args.append(limit)
    with db() as conn:
        rows = qall(conn, f"""
            SELECT codigo_corto, nombre, municipio::text, lat, lon,
                   prioridad::text, prioridad_score, estado_rescate::text,
                   num_reportes, personas_atrapadas_estimado, heridos_estimado, ninos_estimado,
                   round(horas_sin_ayuda::numeric,1) AS horas_sin_ayuda,
                   es_infraestructura_critica,
                   round(dist_hospital_m::numeric) AS dist_hospital_m,
                   round(dist_bomberos_m::numeric) AS dist_bomberos_m
            FROM buildings WHERE {' AND '.join(where)}
            ORDER BY prioridad_score DESC LIMIT %s""", args)
    if not cli.preciso:
        for r in rows:
            r["lat"], r["lon"] = degradar(r["lat"], r["lon"])
    response.headers["Cache-Control"] = "public, max-age=30" if not cli.preciso else "no-store"
    return {"total": len(rows), "precision": "exacta" if cli.preciso else "degradada_privacidad",
            "atribucion": ATRIBUCION, "edificios": rows}


@app.get("/api/v1/stats", tags=["público"])
def stats(response: Response, cli: Cliente = Depends(autenticar)):
    """Cifras agregadas (seguras de publicar)."""
    with db() as conn:
        s = qone(conn, """
            SELECT (SELECT count(*) FROM incidentes) AS incidentes_total,
                   (SELECT count(*) FROM incidentes WHERE tipo='ATRAPADOS') AS atrapados,
                   (SELECT coalesce(sum(personas),0) FROM incidentes WHERE tipo='ATRAPADOS') AS personas_atrapadas,
                   (SELECT count(*) FROM incidentes WHERE estado_verificacion='VERIFICADO') AS verificados,
                   (SELECT count(*) FROM incidentes WHERE asignado_a IS NOT NULL AND resuelto_en IS NULL) AS en_atencion,
                   (SELECT count(*) FROM incidentes WHERE resuelto_en IS NOT NULL) AS resueltos,
                   (SELECT count(*) FROM buildings WHERE prioridad='CRITICA' AND num_reportes>0) AS edif_critica,
                   (SELECT count(*) FROM buildings WHERE prioridad='ALTA' AND num_reportes>0) AS edif_alta,
                   (SELECT count(*) FROM buildings WHERE prioridad='MEDIA' AND num_reportes>0) AS edif_media""")
    response.headers["Cache-Control"] = "public, max-age=30"
    return {**s, "atribucion": ATRIBUCION}


# ==================================================================
# INGESTA (key rol 'ingesta' o 'emergencia')
# ==================================================================
@app.post("/api/v1/reportes", status_code=201, tags=["ingesta"])
def crear_reporte(rep: ReporteIn, cli: Cliente = Depends(autenticar)):
    """Capta un reporte nuevo. El matching GPS→edificio y el recálculo de
    prioridad son automáticos (triggers PostGIS). Dedupe por (fuente, id_externo)."""
    exigir_rol(cli, "ingesta", "emergencia")
    fuente = f"api:{cli.nombre}"
    with db() as conn:
        # matching con precisión limitada (misma función que el conector)
        m = qone(conn, "SELECT * FROM buscar_edificio_aproximado(%s,%s,%s)",
                 (rep.lat, rep.lon, rep.coord_precision_m))
        b_id, b_dist, b_met = (m["building_id"], m["distancia_m"], m["metodo"]) if m else (None, None, "sin_match")
        row = qone(conn, """
            INSERT INTO incidentes (tipo, descripcion, personas, heridos, ninos, necesidades,
                                    urgencia, fuente, id_externo, url_fuente, atribucion,
                                    reportero_nombre, telefono_contacto,
                                    lat, lon, coord_precision_m,
                                    building_id, building_match_metodo, building_match_distancia_m,
                                    recursos_solicitados)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (fuente, id_externo) WHERE id_externo IS NOT NULL DO NOTHING
            RETURNING codigo, building_id""",
            (rep.tipo, rep.descripcion, rep.personas, rep.heridos, rep.ninos, rep.necesidades,
             rep.urgencia, fuente, rep.id_externo, rep.url_fuente, f"vía API key: {cli.nombre}",
             rep.reportero_nombre, rep.telefono_contacto,
             rep.lat, rep.lon, rep.coord_precision_m, b_id, b_met, b_dist,
             rep.recursos_solicitados))
        if row is None:
            log_api(cli, "POST", "/api/v1/reportes", 409, f"duplicado {rep.id_externo}")
            raise HTTPException(409, f"Reporte duplicado: ya existe (fuente={fuente}, id_externo={rep.id_externo}).")
        edificio = qone(conn, "SELECT codigo_corto, prioridad::text, prioridad_score FROM buildings WHERE id=%s",
                        (row["building_id"],)) if row["building_id"] else None
    log_api(cli, "POST", "/api/v1/reportes", 201, row["codigo"])
    return {"codigo": row["codigo"], "match": b_met,
            "match_distancia_m": round(b_dist, 1) if b_dist is not None else None,
            "edificio": edificio,
            "nota": ("match aproximado: requiere confirmación humana antes de despachar"
                     if b_met == "match_aproximado" else None)}


# ==================================================================
# OPERACIÓN (key rol 'emergencia')
# ==================================================================
@app.patch("/api/v1/incidentes/{codigo}/verificacion", tags=["emergencia"])
def verificar(codigo: str, v: VerificacionIn, cli: Cliente = Depends(autenticar)):
    """Marca un incidente como VERIFICADO / DESCARTADO / DUPLICADO.
    Queda firmado por la key y auditado — nunca es automático."""
    exigir_rol(cli, "emergencia")
    with db() as conn:
        row = qone(conn, """UPDATE incidentes
                            SET estado_verificacion=%s::estado_verificacion_enum,
                                confirmado=(%s='VERIFICADO'), verificado_por=%s, verificado_en=now()
                            WHERE codigo=%s RETURNING codigo, building_id""",
                   (v.accion, v.accion, f"{cli.nombre} (API)", codigo))
        if not row:
            raise HTTPException(404, f"Incidente {codigo} no existe.")
        if row["building_id"]:
            qall(conn, "SELECT recalcular_prioridad_edificio(%s)", (row["building_id"],))
    log_api(cli, "PATCH", f"/incidentes/{codigo}/verificacion", 200, f"{v.accion} · {v.notas or ''}")
    return {"codigo": codigo, "estado": v.accion, "verificado_por": cli.nombre}


@app.patch("/api/v1/incidentes/{codigo}/despacho", tags=["emergencia"])
def despachar(codigo: str, d: DespachoIn, cli: Cliente = Depends(autenticar)):
    """asignar: tu cuerpo toma el incidente (visible para los demás — evita duplicar esfuerzos).
    resolver: cierra con resultado (rescatados/sin_hallazgo/falso/trasladado)."""
    exigir_rol(cli, "emergencia")
    with db() as conn:
        if d.accion == "asignar":
            row = qone(conn, """UPDATE incidentes SET asignado_a=%s, asignado_en=now()
                                WHERE codigo=%s AND resuelto_en IS NULL RETURNING codigo, asignado_a""",
                       (cli.nombre, codigo))
            if not row:
                raise HTTPException(409, f"{codigo} no existe o ya está resuelto.")
        else:
            if not d.resultado:
                raise HTTPException(422, "Para 'resolver' debes indicar `resultado`.")
            row = qone(conn, """UPDATE incidentes SET resuelto_en=now(), resultado=%s,
                                       asignado_a=COALESCE(asignado_a,%s)
                                WHERE codigo=%s RETURNING codigo, building_id""",
                       (d.resultado, cli.nombre, codigo))
            if not row:
                raise HTTPException(404, f"Incidente {codigo} no existe.")
            if row.get("building_id"):
                qall(conn, "SELECT recalcular_prioridad_edificio(%s)", (row["building_id"],))
    log_api(cli, "PATCH", f"/incidentes/{codigo}/despacho", 200, f"{d.accion} {d.resultado or ''} · {d.notas or ''}")
    return {"codigo": codigo, "accion": d.accion, "por": cli.nombre, "resultado": d.resultado}


# ==================================================================
# EXPORTES para sistemas GIS de los cuerpos (key emergencia/socio)
# ==================================================================
@app.get("/api/v1/export/geojson", tags=["emergencia"])
def export_geojson(cli: Cliente = Depends(autenticar),
                   capa: str = Query("incidentes", pattern="^(incidentes|edificios)$"),
                   urgencia_min: str = Query("MEDIA", pattern="^(BAJA|MEDIA|ALTA|CRITICA)$")):
    """GeoJSON con coordenadas precisas para QGIS/ArcGIS/Google Earth de los
    cuerpos de emergencia. Requiere key emergencia o socio."""
    exigir_rol(cli, "emergencia", "socio")
    orden = {"BAJA": 0, "MEDIA": 1, "ALTA": 2, "CRITICA": 3}
    niveles = [k for k, v in orden.items() if v >= orden[urgencia_min]]
    with db() as conn:
        if capa == "incidentes":
            rows = qall(conn, """
                SELECT i.codigo, i.tipo, i.urgencia::text, i.personas, i.heridos,
                       i.estado_verificacion::text, i.asignado_a, i.lat, i.lon,
                       i.coord_precision_m, i.building_match_metodo,
                       b.codigo_corto AS edificio
                FROM incidentes i LEFT JOIN buildings b ON b.id=i.building_id
                WHERE i.urgencia::text = ANY(%s) AND i.resuelto_en IS NULL AND i.lat IS NOT NULL""", (niveles,))
        else:
            rows = qall(conn, """
                SELECT codigo_corto AS codigo, prioridad::text AS urgencia, prioridad_score,
                       num_reportes, personas_atrapadas_estimado, lat, lon
                FROM buildings WHERE num_reportes>0 AND prioridad::text = ANY(%s)""", (niveles,))
    feats = []
    for r in rows:
        lat, lon = r.pop("lat"), r.pop("lon")
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": r})
    log_api(cli, "GET", "/export/geojson", 200, f"{capa}:{len(feats)}")
    return {"type": "FeatureCollection", "name": f"rescuegis_{capa}",
            "attribution": ATRIBUCION, "features": feats}


@app.get("/api/v1/export/csv", tags=["emergencia"])
def export_csv(cli: Cliente = Depends(autenticar)):
    """CSV de la cola de incidentes abiertos (para radio/impresión en campo)."""
    exigir_rol(cli, "emergencia", "socio")
    with db() as conn:
        rows = qall(conn, """
            SELECT i.codigo, i.urgencia::text, i.tipo, i.personas, i.heridos,
                   b.codigo_corto AS edificio, b.municipio::text,
                   round(i.lat::numeric,6) AS lat, round(i.lon::numeric,6) AS lon,
                   i.building_match_metodo AS match, i.asignado_a, i.estado_verificacion::text
            FROM incidentes i LEFT JOIN buildings b ON b.id=i.building_id
            WHERE i.resuelto_en IS NULL
            ORDER BY CASE i.urgencia WHEN 'CRITICA' THEN 3 WHEN 'ALTA' THEN 2 WHEN 'MEDIA' THEN 1 ELSE 0 END DESC""")
    if not rows:
        return Response("sin_datos\n", media_type="text/csv")
    cab = list(rows[0].keys())
    lineas = [",".join(cab)]
    for r in rows:
        lineas.append(",".join("" if r[c] is None else str(r[c]).replace(",", ";") for c in cab))
    log_api(cli, "GET", "/export/csv", 200, f"{len(rows)} filas")
    return Response("\n".join(lineas) + "\n", media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=rescuegis_incidentes.csv"})


# ==================================================================
# SUCESOS: reportes correlacionados (posición refinada, confianza multi-fuente)
# ==================================================================
@app.get("/api/v1/sucesos", tags=["público"])
def listar_sucesos(response: Response, cli: Cliente = Depends(autenticar),
                   urgencia: Optional[str] = Query(None, pattern="^(BAJA|MEDIA|ALTA|CRITICA)$"),
                   sin_asignar: bool = Query(False),
                   limit: int = Query(100, ge=1, le=500)):
    """Sucesos abiertos: grupos de reportes que describen la misma emergencia,
    con posición refinada (centroide ponderado por precisión GPS), confianza
    multi-fuente y re-match de edificio. LA vista recomendada para despacho —
    un derrumbe con 6 reportes aparece UNA vez, no seis."""
    where, args = ["1=1"], []
    if urgencia:    where.append("urgencia=%s"); args.append(urgencia)
    if sin_asignar: where.append("NOT COALESCE(alguien_asignado,false)")
    args.append(limit)
    with db() as conn:
        rows = qall(conn, f"""SELECT * FROM v_sucesos_abiertos
                              WHERE {' AND '.join(where)} LIMIT %s""", args)
    for r in rows:
        r["actualizado_en"] = r["actualizado_en"].isoformat() if r["actualizado_en"] else None
        if not cli.preciso:
            r["lat"], r["lon"] = degradar(r["lat"], r["lon"])
    response.headers["Cache-Control"] = "public, max-age=20" if not cli.preciso else "no-store"
    log_api(cli, "GET", "/api/v1/sucesos", 200, f"{len(rows)} filas")
    return {"total": len(rows), "precision": "exacta" if cli.preciso else "degradada_privacidad",
            "atribucion": ATRIBUCION, "sucesos": rows}


@app.get("/api/v1/sucesos/{codigo}", tags=["público"])
def detalle_suceso(codigo: str, cli: Cliente = Depends(autenticar)):
    """Un suceso con todos sus reportes individuales (la evidencia cruda)."""
    with db() as conn:
        suc = qone(conn, "SELECT * FROM v_sucesos_abiertos WHERE codigo=%s", (codigo,))
        if not suc:
            suc = qone(conn, """SELECT s.*, b.codigo_corto AS edificio FROM sucesos s
                                LEFT JOIN buildings b ON b.id=s.building_id
                                WHERE s.codigo=%s""", (codigo,))
        if not suc:
            raise HTTPException(404, f"Suceso {codigo} no existe.")
        reportes = qall(conn, """
            SELECT codigo, tipo, urgencia::text, personas, heridos, fuente,
                   lat, lon, coord_precision_m, descripcion, estado_verificacion::text,
                   asignado_a, telefono_contacto, fecha
            FROM incidentes WHERE suceso_id=(SELECT id FROM sucesos WHERE codigo=%s)
            ORDER BY coord_precision_m ASC NULLS LAST""", (codigo,))
    for k in ("actualizado_en", "creado_en"):
        if suc.get(k): suc[k] = suc[k].isoformat()
    suc.pop("id", None); suc.pop("geom", None); suc.pop("building_id", None)
    for r in reportes:
        r["fecha"] = r["fecha"].isoformat() if r["fecha"] else None
        if not cli.preciso:
            r["lat"], r["lon"] = degradar(r["lat"], r["lon"])
            r["telefono_contacto"] = None
            if r["descripcion"]:
                r["descripcion"] = r["descripcion"][:140]
    if not cli.preciso:
        suc["lat"], suc["lon"] = degradar(suc.get("lat"), suc.get("lon"))
    return {"suceso": suc, "reportes": reportes,
            "precision": "exacta" if cli.preciso else "degradada_privacidad"}


# ==================================================================
# VISTA DE CAMPO: HTML móvil para rescatistas en terreno
# ==================================================================
from fastapi.responses import HTMLResponse  # noqa: E402


@app.get("/campo", response_class=HTMLResponse, tags=["emergencia"])
def vista_campo(key: str = Query(..., description="API key (va en la URL para poder "
                                                   "compartirla como enlace en el grupo del cuerpo)")):
    """Vista móvil para el teléfono del rescatista: cola de sucesos por urgencia,
    botones de navegación (Google Maps/OsmAnd usan la coordenada REFINADA),
    llamada directa al reportero y auto-refresco cada 60 s.
    Autenticación por ?key= para que el jefe de cuerpo la comparta como un
    simple enlace en WhatsApp/Telegram del equipo."""
    key_hash = hashlib.sha256(key.strip().encode()).hexdigest()
    with db() as conn:
        krow = qone(conn, "SELECT * FROM api_keys WHERE key_hash=%s AND activo", (key_hash,))
    if not krow or krow["rol"] not in ("emergencia", "admin"):
        raise HTTPException(401, "Key inválida o sin rol de emergencia.")
    cli = Cliente(krow)
    rate_limit(f"key:{krow['id']}", krow["rate_limit_min"])

    with db() as conn:
        sucesos = qall(conn, "SELECT * FROM v_sucesos_abiertos LIMIT 80")
        # teléfono del mejor reporte de cada suceso (el más preciso que tenga contacto)
        tels = {r["suceso_id"]: r["telefono_contacto"] for r in qall(conn, """
            SELECT DISTINCT ON (suceso_id) suceso_id, telefono_contacto
            FROM incidentes WHERE suceso_id IS NOT NULL AND telefono_contacto IS NOT NULL
            ORDER BY suceso_id, coord_precision_m ASC NULLS LAST""")}
        ids = {r["codigo"]: r["id"] for r in qall(conn, "SELECT id, codigo FROM sucesos")}

    UCOL = {"CRITICA": "#e11d48", "ALTA": "#f97316", "MEDIA": "#eab308", "BAJA": "#22c55e"}
    tarjetas = []
    for s in sucesos:
        c = UCOL.get(s["urgencia"], "#64748b")
        tel = tels.get(ids.get(s["codigo"]))
        conf_txt = f"{s['confianza']}%" + (f" · {s['num_fuentes']} fuentes" if s["num_fuentes"] > 1 else "")
        asign = (f'<div class="asig">🚒 {s["asignado_a"]}</div>' if s.get("asignado_a")
                 else '<div class="asig libre">SIN ASIGNAR</div>')
        botones = (
            f'<a class="btn nav" href="https://www.google.com/maps/dir/?api=1&destination={s["lat"]},{s["lon"]}">🧭 Navegar</a>'
            f'<a class="btn nav2" href="geo:{s["lat"]},{s["lon"]}?q={s["lat"]},{s["lon"]}({s["codigo"]})">📍 App GPS</a>'
            + (f'<a class="btn tel" href="tel:{tel}">📞 Reportero</a>' if tel else "")
        )
        pers = f'<span class="pers">👥 {s["personas_max"]}</span>' if s["personas_max"] else ""
        edif = f' · 🏢 {s["edificio"]}' if s.get("edificio") else ""
        match_warn = (' <span class="aprox">±' + str(int(s["coord_precision_m"] or 0)) + ' m — confirmar en sitio</span>'
                      if s.get("building_match_metodo") != "auto_150m" else "")
        tarjetas.append(f"""
<div class="card" style="border-left:5px solid {c}">
  <div class="fila"><b style="color:{c}">{s['urgencia']}</b> {pers}
    <span class="cod">{s['codigo']}</span></div>
  <div class="tipo">{s['tipo_dominante'].replace('_',' ')}{edif}{match_warn}</div>
  <div class="meta">{s['num_reportes']} reporte(s) · confianza {conf_txt} · {s['municipio'] or ''}</div>
  {asign}
  <div class="botones">{botones}</div>
</div>""")

    html = f"""<!DOCTYPE html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>RescueGIS Campo</title>
<style>
 * {{ box-sizing:border-box; margin:0; padding:0; -webkit-tap-highlight-color:transparent; }}
 body {{ background:#0b1220; color:#e2e8f0; font-family:-apple-system,'Segoe UI',Roboto,sans-serif; padding:10px; }}
 h1 {{ font-size:1.05rem; padding:4px 2px 10px; }} h1 small {{ color:#64748b; font-weight:400; }}
 .card {{ background:#131c30; border-radius:12px; padding:12px 14px; margin-bottom:10px; }}
 .fila {{ display:flex; gap:10px; align-items:center; font-size:1.05rem; }}
 .cod {{ margin-left:auto; color:#64748b; font-family:monospace; font-size:.8rem; }}
 .tipo {{ font-size:1.05rem; font-weight:600; margin:4px 0 2px; }}
 .meta {{ color:#94a3b8; font-size:.8rem; }}
 .pers {{ background:#e11d4822; color:#fda4af; padding:1px 8px; border-radius:99px; font-size:.85rem; }}
 .aprox {{ color:#fdba74; font-size:.75rem; font-weight:400; }}
 .asig {{ font-size:.8rem; margin-top:4px; color:#7dd3fc; }}
 .asig.libre {{ color:#4ade80; font-weight:700; }}
 .botones {{ display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }}
 .btn {{ flex:1; min-width:100px; text-align:center; padding:12px 8px; border-radius:10px;
        text-decoration:none; font-weight:700; font-size:.9rem; }}
 .nav {{ background:#2563eb; color:#fff; }} .nav2 {{ background:#1e293b; color:#cbd5e1; }}
 .tel {{ background:#16a34a; color:#fff; }}
 .foot {{ color:#475569; font-size:.72rem; text-align:center; padding:14px 0 30px; line-height:1.5; }}
</style></head><body>
<h1>🚨 RescueGIS — Cola de campo <small>· {cli.nombre} · auto-actualiza cada 60 s</small></h1>
{''.join(tarjetas) if tarjetas else '<div class="card">Sin sucesos abiertos ahora mismo.</div>'}
<div class="foot">Coordenadas refinadas por correlación multi-reporte.<br>
«confirmar en sitio» = posición aproximada, verifica visualmente al llegar.<br>
Datos: © OpenStreetMap + fuentes ciudadanas · uso humanitario</div>
</body></html>"""
    log_api(cli, "GET", "/campo", 200, f"{len(sucesos)} sucesos")
    return HTMLResponse(html)


# ==================================================================
# PERSONAS DESAPARECIDAS (migración 08)
# ==================================================================
@app.get("/api/v1/personas", tags=["público"])
def listar_personas(response: Response, cli: Cliente = Depends(autenticar),
                    estado: Optional[str] = Query(None, pattern="^(BUSCADA|INFO_RECIBIDA|ENCONTRADA_VIVA|ENCONTRADA_FALLECIDA|REUNIFICADA)$"),
                    limit: int = Query(100, ge=1, le=500)):
    """Directorio de personas desaparecidas. Público: menores SIEMPRE enmascarados,
    sin contactos, coordenadas ~aprox. Con key emergencia: datos completos para
    reunificación (auditado)."""
    with db() as conn:
        if cli.rol == "emergencia" or cli.rol == "admin":
            rows = qall(conn, """SELECT codigo, nombre, edad, es_menor, genero, descripcion,
                                        zona_texto, estado::text, parroquia_geo, foto_url, contacto,
                                        lat, lon, reportado_en, fuente
                                 FROM personas_desaparecidas
                                 WHERE (%s::text IS NULL OR estado::text=%s) 
                                 ORDER BY es_menor DESC, reportado_en DESC LIMIT %s""",
                        (estado, estado, limit))
        else:
            rows = qall(conn, """SELECT * FROM v_personas_publico
                                 WHERE (%s::text IS NULL OR estado=%s) LIMIT %s""",
                        (estado, estado, limit))
    for r in rows:
        if r.get("reportado_en"): r["reportado_en"] = r["reportado_en"].isoformat()
    response.headers["Cache-Control"] = "public, max-age=60" if not cli.preciso else "no-store"
    log_api(cli, "GET", "/api/v1/personas", 200, f"{len(rows)}")
    return {"total": len(rows), "menores_protegidos": True,
            "atribucion": "Directorio base: desaparecidosvenezuela.com + aportes propios",
            "personas": rows}


@app.get("/api/v1/logistica/recursos", tags=["público"])
def recursos_solicitados(response: Response, cli: Cliente = Depends(autenticar)):
    """Agregado de recursos/apoyo solicitados (herramientas, maquinaria, personal)
    por parroquia — para logística: QUÉ llevar A DÓNDE."""
    with db() as conn:
        rows = qall(conn, "SELECT * FROM v_recursos_solicitados")
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"total": len(rows), "recursos": rows}
