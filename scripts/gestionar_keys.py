#!/usr/bin/env python3
"""
Gestión de API keys de RescueGIS.

La key se genera con secrets.token_urlsafe(32) y SOLO se muestra una vez,
en el momento de crearla. En la base queda únicamente su hash SHA-256.

Uso:
    python3 gestionar_keys.py crear "Bomberos de Caracas" --rol emergencia \
        --org "CBDC" --contacto "ops@bomberoscaracas.ve"
    python3 gestionar_keys.py listar
    python3 gestionar_keys.py desactivar 3
    python3 gestionar_keys.py uso 3          # últimas 30 acciones de esa key
"""
import argparse
import hashlib
import secrets

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def crear(args):
    key = "rgis_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO api_keys (key_hash, nombre, organizacion, contacto, rol, rate_limit_min, notas)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (key_hash, args.nombre, args.org, args.contacto, args.rol, args.rate, args.notas))
        kid = cur.fetchone()[0]
    print(f"""
✅ Key creada (id={kid}, rol={args.rol}) para: {args.nombre}

    {key}

⚠  GUÁRDALA AHORA — no se puede recuperar (solo almacenamos su hash).
   Entrégala por un canal seguro (Signal/llamada), nunca por correo abierto.
   Uso:  curl -H "X-Api-Key: {key[:12]}..." https://tu-host/api/v1/incidentes
""")


def listar(_args):
    with conectar() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, nombre, organizacion, rol, activo, rate_limit_min,
                              creado_en::date AS creada, ultimo_uso_en
                       FROM api_keys ORDER BY id""")
        rows = cur.fetchall()
    if not rows:
        print("No hay keys. Crea una con: python3 gestionar_keys.py crear <nombre> --rol <rol>")
        return
    for r in rows:
        estado = "✅" if r["activo"] else "🚫"
        ultimo = r["ultimo_uso_en"].strftime("%Y-%m-%d %H:%M") if r["ultimo_uso_en"] else "nunca"
        print(f"{estado} [{r['id']:>3}] {r['nombre']:<32} rol={r['rol']:<11} "
              f"org={r['organizacion'] or '—':<20} {r['rate_limit_min']:>4} req/min · último uso: {ultimo}")


def desactivar(args):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("UPDATE api_keys SET activo=FALSE WHERE id=%s RETURNING nombre", (args.id,))
        row = cur.fetchone()
    print(f"🚫 Key {args.id} ({row[0]}) desactivada." if row else f"No existe key con id {args.id}.")


def uso(args):
    with conectar() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT l.creado_en, l.metodo, l.ruta, l.status, l.detalle
                       FROM api_log l WHERE l.api_key_id=%s
                       ORDER BY l.creado_en DESC LIMIT 30""", (args.id,))
        rows = cur.fetchall()
    if not rows:
        print("Sin actividad registrada para esa key.")
    for r in rows:
        print(f"{r['creado_en']:%Y-%m-%d %H:%M:%S} {r['metodo']:<6} {r['ruta']:<45} "
              f"{r['status']} {r['detalle'] or ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crear", help="crear una key nueva")
    c.add_argument("nombre")
    c.add_argument("--rol", default="socio", choices=["emergencia", "ingesta", "socio", "admin"])
    c.add_argument("--org", default=None)
    c.add_argument("--contacto", default=None)
    c.add_argument("--rate", type=int, default=120, help="req/min (def. 120)")
    c.add_argument("--notas", default=None)
    c.set_defaults(func=crear)

    l = sub.add_parser("listar", help="listar keys")
    l.set_defaults(func=listar)

    d = sub.add_parser("desactivar", help="desactivar una key")
    d.add_argument("id", type=int)
    d.set_defaults(func=desactivar)

    u = sub.add_parser("uso", help="ver actividad de una key")
    u.add_argument("id", type=int)
    u.set_defaults(func=uso)

    args = ap.parse_args()
    args.func(args)
