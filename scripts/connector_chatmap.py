#!/usr/bin/env python3
"""
Conector ChatMap (HOT) → RescueGIS
===================================

Ingiere el GeoJSON que produce ChatMap (chatmap.hotosm.org — la herramienta
oficial de HOT para mapear chats de WhatsApp/Telegram/Signal, usada en la
activación #2026_LACH_VE_EQ) y lo convierte en `incidentes` de RescueGIS,
con matching a edificio, clasificación del texto y correlación posterior.

Formato de entrada (verificado con el parser oficial `chatmap-py`):
    FeatureCollection de Points; properties: id, message, username, chat,
    time, file, related. Cada feature = una ubicación compartida en el chat,
    emparejada con el mensaje más cercano del mismo usuario.

Qué hace este conector:
  * CLASIFICA el texto libre del mensaje (español) en tipo de incidente y
    urgencia con palabras clave — transparente y auditable, NO una caja negra.
    Extrae número de personas si el texto lo menciona ("hay 3 atrapados").
  * Coordenadas de chat = ubicación compartida por una persona → precisión
    GPS de teléfono típica (~25 m). Se registra coord_precision_m=25 y el
    matching decide firme/aproximado con la regla estándar.
  * Dedupe por (fuente, id_externo) con id_externo = <chat>#<id-mensaje>.
  * La foto/video del chat (file) queda como evidencia (confianza 55 — es
    del propio reportero, no verificación independiente).
  * TODO entra como PENDIENTE_VERIFICACION — la clasificación automática
    jamás verifica nada, solo ordena la cola.

Uso:
    python3 connector_chatmap.py mapa.geojson              # archivo exportado de ChatMap
    python3 connector_chatmap.py mapa.geojson --chat "SOS Vargas"  # etiqueta de origen
    python3 connector_chatmap.py --url https://.../map.geojson     # descarga directa
    python3 connector_chatmap.py mapa.geojson --dry-run    # clasifica sin escribir

Tras ingerir, correr:  python3 correlacionar_sucesos.py
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import psycopg2
import requests

from db_config import DB_CONFIG

FUENTE = "chatmap"
ATRIBUCION = "Reportes ciudadanos vía ChatMap (HOT — chatmap.hotosm.org), activación 2026 Venezuela EQ"
PRECISION_CHAT_M = 25.0   # GPS de teléfono compartido en chat: precisión típica

# ------------------------------------------------------------------
# Clasificación por palabras clave (español venezolano incluido).
# Orden = prioridad: la primera categoría que matchee gana.
# ------------------------------------------------------------------
CLASIFICADOR = [
    ("ATRAPADOS", "CRITICA", [
        "atrapad", "atrapado", "bajo los escombros", "bajo escombros", "no puede salir",
        "no pueden salir", "gritos", "se escuchan voces", "se escucha gente",
        "gente adentro", "personas adentro", "enterrad", "sepultad", "rescate urgente",
    ]),
    ("HERIDOS", "ALTA", [
        "herid", "sangr", "fractur", "ambulancia", "primeros auxilios",
        "inconsciente", "no responde", "lesionad", "quemad",
    ]),
    ("FALLECIDO", "ALTA", [
        "fallecid", "muert", "cadaver", "cadáver", "sin vida", "cuerpo de",
    ]),
    ("DANO_ESTRUCTURAL", "MEDIA", [
        "colaps", "derrumb", "cayo el edificio", "cayó el edificio", "se cayo", "se cayó",
        "grieta", "agrietad", "pared caida", "pared caída", "techo caido", "techo caído",
        "edificio dañad", "estructura", "inclinad", "a punto de caer",
    ]),
    ("NECESIDAD_BASICA", "MEDIA", [
        "agua", "comida", "alimento", "medicin", "medicament", "insulina",
        "oxigeno", "oxígeno", "panal", "pañal", "formula", "fórmula", "refugio",
        "colchon", "colchón", "frazada", "cobija",
    ]),
]
# Modificadores de urgencia
ESCALADORES = ["urgente", "emergencia", "auxilio", "socorro", "ayuda ya", "por favor rapido",
               "por favor rápido", "se muere", "grave", "critico", "crítico", "niñ", "nin", "bebe", "bebé",
               "embarazada", "anciano", "adulto mayor"]

RE_PERSONAS = re.compile(
    r"(?:hay|son|somos|como|al menos|unas?|unos?)\s+(\d{1,3})\s*(?:person|atrapad|herid|gent|adult|niñ|nin)"
    r"|(\d{1,3})\s+(?:person|atrapad|herid|adult|niñ|nin)", re.IGNORECASE)

NUM_PALABRA = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
               "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
RE_PERSONAS_PALABRA = re.compile(
    r"\b(un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+(?:person|atrapad|herid|niñ|nin)",
    re.IGNORECASE)


def sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


# Negaciones: "no hay heridos", "sin heridos", "ningún herido" no deben
# disparar la categoría. Se eliminan esos fragmentos ANTES de clasificar.
RE_NEGACION = re.compile(
    r"(?:no hay|no se ven|no vimos|sin|ningun|ninguna|no tenemos|nadie)\s+(?:\w+\s){0,2}?"
    r"(herid\w*|atrapad\w*|muert\w*|fallecid\w*|lesionad\w*)", re.IGNORECASE)


def clasificar(texto):
    """(tipo, urgencia, personas, ninos) desde texto libre. Auditable: keywords."""
    if not texto:
        return "OTRO", "MEDIA", 0, 0
    t = RE_NEGACION.sub(" ", sin_tildes(texto))

    tipo, urgencia = "OTRO", "MEDIA"
    for tip, urg, claves in CLASIFICADOR:
        if any(k in t for k in claves):
            tipo, urgencia = tip, urg
            break

    # escalar urgencia un nivel si hay palabras de gravedad/vulnerables
    if any(e in t for e in ESCALADORES):
        urgencia = {"BAJA": "MEDIA", "MEDIA": "ALTA", "ALTA": "CRITICA"}.get(urgencia, urgencia)

    personas = 0
    m = RE_PERSONAS.search(t)
    if m:
        personas = int(next(g for g in m.groups() if g))
    else:
        mp = RE_PERSONAS_PALABRA.search(t)
        if mp:
            personas = NUM_PALABRA[mp.group(1).lower()]
    personas = min(personas, 500)

    ninos = personas if ("niñ" in texto.lower() or "nin" in t or "bebe" in t) and personas else 0
    return tipo, urgencia, personas, min(ninos, personas)


def ingerir(geojson, chat_label=None, dry_run=False, tipo_default=None):
    feats = geojson.get("features", [])
    print(f"  {len(feats)} ubicaciones en el GeoJSON de ChatMap")

    if dry_run:
        from collections import Counter
        c = Counter()
        for f in feats:
            p = f.get("properties", {})
            tipo, urg, pers, nin = clasificar(p.get("message"))
            if tipo == "OTRO" and tipo_default:
                tipo = tipo_default
            c[f"{tipo}/{urg}"] += 1
            print(f"  [{tipo:>16} {urg:>7} pers={pers}] {(p.get('message') or '')[:70]}")
        print("\n  Desglose:", dict(c))
        return

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    nuevos = dup = firmes = aprox = sin_match = evid = 0
    try:
        with conn.cursor() as cur:
            for f in feats:
                p = f.get("properties", {})
                geom = f.get("geometry", {})
                if geom.get("type") != "Point":
                    continue
                lon, lat = geom["coordinates"][:2]
                texto = p.get("message") or ""
                chat = chat_label or p.get("chat") or "chat"
                id_ext = f"{chat}#{p.get('id')}"
                tipo, urgencia, personas, ninos = clasificar(texto)
                if tipo == "OTRO" and tipo_default:
                    # chat temático (p.ej. evaluación de daños): el contexto define el tipo
                    tipo = tipo_default

                cur.execute("SELECT * FROM buscar_edificio_aproximado(%s,%s,%s)",
                            (lat, lon, PRECISION_CHAT_M))
                m = cur.fetchone()
                b_id, b_dist, b_met = (m if m else (None, None, "sin_match"))

                cur.execute("""
                    INSERT INTO incidentes
                        (tipo, descripcion, personas, ninos, urgencia, fuente, id_externo,
                         atribucion, coord_precision_m, lat, lon,
                         building_id, building_match_metodo, building_match_distancia_m,
                         fecha, reportero_nombre)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            COALESCE(%s::timestamptz, now()), %s)
                    ON CONFLICT (fuente, id_externo) WHERE id_externo IS NOT NULL DO NOTHING
                    RETURNING id""",
                    (tipo,
                     texto[:2000] + f" · [clasificado automático desde chat '{chat}' — requiere verificación]",
                     personas, ninos, urgencia, FUENTE, id_ext, ATRIBUCION,
                     PRECISION_CHAT_M, lat, lon, b_id, b_met, b_dist,
                     p.get("time"), p.get("username")))
                row = cur.fetchone()
                if row is None:
                    dup += 1
                    continue
                nuevos += 1
                firmes += b_met == "auto_150m"
                aprox += b_met == "match_aproximado"
                sin_match += b_met == "sin_match"

                if p.get("file"):
                    cur.execute("""
                        INSERT INTO evidencias (incidente_id, tipo, url_archivo, usuario, nivel_confianza, notas)
                        VALUES (%s, 'foto', %s, %s, 55, 'Adjunto del chat (ChatMap) — mismo reportero, no verificación independiente')""",
                        (row[0], p["file"], p.get("username")))
                    evid += 1

            cur.execute("INSERT INTO import_log (fuente, capa, registros_importados, notas) VALUES (%s,'incidentes',%s,%s)",
                        (FUENTE, nuevos, f"chatmap: {firmes} firmes/{aprox} aprox/{sin_match} sin_match/{dup} dup"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"""
  ── ChatMap → RescueGIS ──────────────────────────────
  Nuevos incidentes:   {nuevos}
    · match firme:     {firmes}
    · aproximado:      {aprox}
    · sin match:       {sin_match}
  Duplicados omitidos: {dup}
  Evidencias (fotos):  {evid}
  ─────────────────────────────────────────────────────
  Siguiente paso: python3 correlacionar_sucesos.py""")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo", nargs="?", help="GeoJSON exportado de ChatMap")
    ap.add_argument("--url", help="URL del GeoJSON (mapas 'Live' publicados por ChatMap)")
    ap.add_argument("--chat", help="etiqueta del chat de origen (para el id de dedupe)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tipo-default", choices=["DANO_ESTRUCTURAL","NECESIDAD_BASICA","OTRO"],
                    help="tipo para mensajes sin señales en el texto (chats temáticos, p.ej. evaluación de daños)")
    args = ap.parse_args()

    if args.url:
        r = requests.get(args.url, timeout=30,
                         headers={"User-Agent": "RescueGIS-conector-chatmap/1.0 (humanitario)"})
        r.raise_for_status()
        geojson = r.json()
    elif args.archivo:
        geojson = json.loads(Path(args.archivo).read_text(encoding="utf-8"))
    else:
        ap.error("indica un archivo GeoJSON o --url")

    ingerir(geojson, chat_label=args.chat, dry_run=args.dry_run, tipo_default=args.tipo_default)
