#!/usr/bin/env python3
"""
Watcher del API de SOS Venezuela 2026
======================================

Sondea GET /api/reports cada N minutos (con una petición HEAD-ligera) y, en
cuanto el API vuelva a responder 200, ejecuta el conector automáticamente.
Pensado para dejarlo corriendo en segundo plano o como cron.

Respeta el rate limit de la fuente (~90 req/min): por defecto sondea cada
5 minutos = 0,003 % de su presupuesto de peticiones.

Uso:
    python3 watch_sosvenezuela.py                  # sondea cada 5 min, para nunca
    python3 watch_sosvenezuela.py --interval 120   # cada 2 min
    python3 watch_sosvenezuela.py --max-checks 12  # se rinde tras 12 intentos
    python3 watch_sosvenezuela.py --once           # un solo chequeo (ideal cron):
                                                   #   exit 0 = ingirió, exit 1 = API caída

Cron sugerido (cada 10 min, con lock para no solaparse):
    */10 * * * * cd /ruta/al/repo/scripts && flock -n /tmp/sos.lock \
        python3 watch_sosvenezuela.py --once >> ../data/watcher.log 2>&1
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# SOS_API_URL permite apuntar a un mock local para pruebas end-to-end
API_URL = os.environ.get("SOS_API_URL", "https://sosvenezuela2026.com/api/reports")
USER_AGENT = "RescueGIS-LaGuaira-Caracas/1.0 (watcher humanitario; github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas)"
CONNECTOR = Path(__file__).resolve().parent / "connector_sosvenezuela.py"


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def check_api(timeout=20):
    """Devuelve (ok, detalle). Una sola petición GET liviana."""
    try:
        r = requests.get(API_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                         timeout=timeout, stream=True)
        # No descargamos el cuerpo completo aquí: solo status + primer chunk
        # para validar que de verdad es JSON y no una página de error.
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        primer_chunk = next(r.iter_content(chunk_size=64), b"")
        if not primer_chunk.strip().startswith((b"[", b"{")):
            return False, f"HTTP 200 pero cuerpo no-JSON ({primer_chunk[:30]!r})"
        return True, "HTTP 200 + JSON"
    except Exception as e:
        return False, f"error de red: {e}"


def correr_conector():
    print(f"[{ts()}] 🟢 API disponible — ejecutando el conector...")
    proc = subprocess.run([sys.executable, str(CONNECTOR)], cwd=str(CONNECTOR.parent))
    return proc.returncode


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=int, default=300, help="segundos entre chequeos (def. 300)")
    ap.add_argument("--max-checks", type=int, default=0, help="máximo de intentos (0 = infinito)")
    ap.add_argument("--once", action="store_true", help="un solo chequeo y salir (para cron)")
    args = ap.parse_args()

    intento = 0
    while True:
        intento += 1
        ok, detalle = check_api()
        if ok:
            rc = correr_conector()
            sys.exit(rc)
        print(f"[{ts()}] 🔴 API caída ({detalle}) — intento {intento}"
              + ("" if args.max_checks == 0 else f"/{args.max_checks}"))
        if args.once:
            sys.exit(1)
        if args.max_checks and intento >= args.max_checks:
            print(f"[{ts()}] Me rindo tras {intento} intentos. Vuelve a correrme más tarde.")
            sys.exit(1)
        time.sleep(args.interval)
