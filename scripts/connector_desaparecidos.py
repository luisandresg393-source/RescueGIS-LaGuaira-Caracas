#!/usr/bin/env python3
"""
Conector desaparecidosvenezuela.com → RescueGIS (personas desaparecidas)
=========================================================================

Ingiere el directorio público de personas desaparecidas de
https://www.desaparecidosvenezuela.com/api/personas (API abierta, la misma
que consume el sync de SOS Venezuela 2026) a la tabla personas_desaparecidas.

Reglas de privacidad (más estrictas que la fuente):
  * Menores de 18: el nombre se ALMACENA (para reunificación con key
    emergencia) pero NUNCA sale en vistas/exportes públicos.
  * El teléfono de contacto de la fuente no se re-publica jamás.
  * Coordenadas públicas degradadas a 3 decimales (v_personas_publico).

Mapeo de estados:
  BUSCADO → BUSCADA · INFO_RECIBIDA → INFO_RECIBIDA · SANO_SALVO → ENCONTRADA_VIVA

Dedupe por (fuente, id_externo). Vinculación automática a edificio (<=100 m)
y parroquia por trigger.

Uso:
    python3 connector_desaparecidos.py            # API en vivo
    python3 connector_desaparecidos.py --dry-run
"""
import argparse
import sys

import psycopg2
import requests

from db_config import DB_CONFIG

API = "https://www.desaparecidosvenezuela.com/api/personas"
FUENTE = "desaparecidosvenezuela"
UA = {"User-Agent": "RescueGIS-conector-desaparecidos/1.0 (humanitario; "
                    "github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas)"}

ESTADO_MAP = {
    "BUSCADO": "BUSCADA",
    "INFO_RECIBIDA": "INFO_RECIBIDA",
    "SANO_SALVO": "ENCONTRADA_VIVA",
    "FALLECIDO": "ENCONTRADA_FALLECIDA",
}


def main(dry_run=False):
    r = requests.get(API, headers=UA, timeout=30)
    r.raise_for_status()
    personas = r.json()
    print(f"  {len(personas)} personas en el directorio de la fuente")

    if dry_run:
        menores = sum(1 for p in personas if p.get("edad") and p["edad"] < 18)
        print(f"  menores de edad (nombre se protegerá): {menores}")
        for p in personas[:8]:
            edad = p.get("edad")
            nom = "«MENOR — protegido»" if (edad and edad < 18) else (p.get("nombre") or "¿?")
            print(f"  · {nom[:30]:30} {edad or '?':>3} años · {p.get('estado'):13} · {(p.get('zona') or '')[:40]}")
        return

    conn = psycopg2.connect(**DB_CONFIG)
    nuevos = actualizados = 0
    try:
        with conn, conn.cursor() as cur:
            for p in personas:
                if p.get("oculto"):
                    continue
                estado = ESTADO_MAP.get(p.get("estado"), "BUSCADA")
                foto = p.get("fotoUrl")
                if foto and not foto.startswith("http"):
                    foto = "https://www.desaparecidosvenezuela.com" + foto
                cur.execute("""
                    INSERT INTO personas_desaparecidas
                        (fuente, id_externo, nombre, edad, descripcion, zona_texto,
                         estado, foto_url, lat, lon, reportado_en)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::estado_persona_enum,%s,%s,%s,%s::timestamptz)
                    ON CONFLICT (fuente, id_externo) DO UPDATE SET
                        estado = EXCLUDED.estado,
                        descripcion = EXCLUDED.descripcion,
                        actualizado_en = now()
                    RETURNING (xmax = 0) AS insertado""",
                    (FUENTE, p["id"], p.get("nombre"), p.get("edad"),
                     p.get("descripcion"), p.get("zona"),
                     estado, foto, p.get("lat"), p.get("lng"), p.get("createdAt")))
                if cur.fetchone()[0]:
                    nuevos += 1
                else:
                    actualizados += 1
            cur.execute("INSERT INTO import_log (fuente, capa, registros_importados, notas) VALUES (%s,'personas',%s,%s)",
                        (FUENTE, nuevos, f"{actualizados} actualizadas"))
    finally:
        conn.close()

    print(f"""
  ── Desaparecidos → RescueGIS ────────────────────────
  Nuevas personas:      {nuevos}
  Actualizadas:         {actualizados}
  ─────────────────────────────────────────────────────""")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(dry_run=args.dry_run)
