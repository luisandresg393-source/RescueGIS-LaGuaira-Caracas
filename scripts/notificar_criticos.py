#!/usr/bin/env python3
"""
Push a rescatistas (issue #2): avisa por Telegram cuando aparece un
suceso que cumple el umbral de urgencia del suscriptor.
=====================================================================

Flujo:
    correlacionar_sucesos.py genera/actualiza sucesos
        → este worker detecta los que aún no se avisaron a cada suscriptor
        → manda mensaje Telegram con botones: 🧭 Navegar · 📋 Vista de campo
        → registra el envío (no repite; re-avisa SOLO si la urgencia subió)

Suscripciones (tabla suscripciones_campo):
    el rescatista manda /alta al MISMO bot ciudadano y un coordinador lo
    aprueba insertando su chat_id, o se inserta directo:
      INSERT INTO suscripciones_campo (chat_id, nombre, municipio, urgencia_min)
      VALUES (123456789, 'Unidad R-7', 'LA_GUAIRA', 'CRITICA');
    Filtros: por municipio, o por radio (centro_lat/lon + radio_m), o todo.

Uso:
    export TELEGRAM_TOKEN="..."         # mismo bot ciudadano u otro dedicado
    export RESCUEGIS_CAMPO_URL="https://api.tu-host/campo?key=..."  # opcional
    python3 notificar_criticos.py            # una pasada (ideal cron * * * * *)
    python3 notificar_criticos.py --loop 30  # daemon: cada 30 s
    python3 notificar_criticos.py --dry-run  # muestra qué enviaría, no envía
"""
import argparse
import math
import os
import sys
import time

import psycopg2
import psycopg2.extras
import requests

from db_config import DB_CONFIG

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CAMPO_URL = os.environ.get("RESCUEGIS_CAMPO_URL")  # enlace a /campo?key=... (opcional)
ORDEN_URG = {"BAJA": 0, "MEDIA": 1, "ALTA": 2, "CRITICA": 3}
EMOJI = {"CRITICA": "🔴", "ALTA": "🟠", "MEDIA": "🟡", "BAJA": "🟢"}


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def enviar_telegram(chat_id, texto, lat=None, lon=None):
    """Mensaje + botón de navegación. Devuelve True si Telegram aceptó."""
    botones = []
    if lat is not None:
        botones.append([{"text": "🧭 Navegar hasta ahí",
                         "url": f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"}])
    if CAMPO_URL:
        botones.append([{"text": "📋 Ver cola de campo", "url": CAMPO_URL}])
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown",
                            **({"reply_markup": {"inline_keyboard": botones}} if botones else {})},
                      timeout=20)
    ok = r.json().get("ok", False)
    if not ok:
        print(f"  [tg] fallo chat {chat_id}: {r.text[:120]}", file=sys.stderr)
    return ok


def pasada(dry_run=False):
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    enviados = 0
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM suscripciones_campo WHERE activa")
        subs = cur.fetchall()
        if not subs:
            print("  (sin suscriptores — inserta en suscripciones_campo)")
            return 0

        # sucesos abiertos que aún no se notificaron a cada sub (o subieron de urgencia)
        cur.execute("""
            SELECT s.id, s.codigo, s.tipo_dominante, s.urgencia_max::text AS urgencia,
                   s.personas_max, s.num_reportes, s.num_fuentes, s.confianza,
                   s.lat, s.lon, s.coord_precision_m, s.building_match_metodo,
                   b.codigo_corto AS edificio, b.municipio::text AS municipio
            FROM sucesos s
            LEFT JOIN buildings b ON b.id = s.building_id
            WHERE EXISTS (SELECT 1 FROM incidentes i
                          WHERE i.suceso_id = s.id AND i.resuelto_en IS NULL)""")
        sucesos = cur.fetchall()

        for sub in subs:
            for s in sucesos:
                if ORDEN_URG[s["urgencia"]] < ORDEN_URG[sub["urgencia_min"]]:
                    continue
                # filtro geográfico: radio > municipio > todo
                if sub["radio_m"] and sub["centro_lat"] is not None and s["lat"] is not None:
                    if haversine_m(s["lat"], s["lon"], sub["centro_lat"], sub["centro_lon"]) > sub["radio_m"]:
                        continue
                elif sub["municipio"] and s["municipio"] and s["municipio"] != sub["municipio"]:
                    continue
                # ¿ya avisado con esta urgencia (o mayor)?
                cur.execute("""SELECT 1 FROM notificaciones_enviadas
                               WHERE suscripcion_id=%s AND suceso_id=%s
                                 AND urgencia >= %s::urgencia_enum LIMIT 1""",
                            (sub["id"], s["id"], s["urgencia"]))
                if cur.fetchone():
                    continue

                lugar = f"🏢 {s['edificio']}" if s["building_match_metodo"] == "auto_150m" and s["edificio"] \
                    else f"📌 posición ±{int(s['coord_precision_m'] or 0)} m (confirmar en sitio)"
                texto = (f"{EMOJI[s['urgencia']]} *{s['urgencia']}* — "
                         f"{s['tipo_dominante'].replace('_', ' ')}\n"
                         f"`{s['codigo']}` · {s['municipio'] or 'zona'}\n"
                         + (f"👥 hasta *{s['personas_max']}* personas\n" if s["personas_max"] else "")
                         + f"{lugar}\n"
                         f"{s['num_reportes']} reporte(s), {s['num_fuentes']} fuente(s), "
                         f"confianza {s['confianza']}%")

                if dry_run:
                    print(f"  [dry] → {sub['nombre'] or sub['chat_id']}: {s['codigo']} {s['urgencia']}")
                    enviados += 1
                    continue

                if enviar_telegram(sub["chat_id"], texto, s["lat"], s["lon"]):
                    cur.execute("""INSERT INTO notificaciones_enviadas
                                   (suscripcion_id, suceso_id, urgencia, num_reportes)
                                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                (sub["id"], s["id"], s["urgencia"], s["num_reportes"]))
                    enviados += 1
                    time.sleep(0.5)  # rate limit amable con Telegram
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return enviados


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=int, metavar="SEG", help="correr en bucle cada SEG segundos")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not TELEGRAM_TOKEN:
        sys.exit("Falta TELEGRAM_TOKEN (o usa --dry-run).")

    if args.loop:
        print(f"Worker de notificaciones cada {args.loop}s — Ctrl+C para parar")
        while True:
            n = pasada(args.dry_run)
            if n:
                print(f"  {n} notificación(es) enviadas")
            time.sleep(args.loop)
    else:
        n = pasada(args.dry_run)
        print(f"{n} notificación(es) {'simuladas' if args.dry_run else 'enviadas'}")
