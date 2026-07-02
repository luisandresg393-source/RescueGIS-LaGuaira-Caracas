"""
Configuración de conexión a PostgreSQL/PostGIS, vía variables de entorno.
Nunca hardcodear credenciales reales en el código fuente.

Variables esperadas (con valores por defecto para desarrollo local):
    PGHOST      (default: 127.0.0.1)
    PGPORT      (default: 5432)
    PGDATABASE  (default: rescuegis)
    PGUSER      (default: postgres)
    PGPASSWORD  (sin default — debes definirla, ver .env.example)
"""
import os

DB_CONFIG = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5432")),
    dbname=os.environ.get("PGDATABASE", "rescuegis"),
    user=os.environ.get("PGUSER", "postgres"),
    password=os.environ.get("PGPASSWORD"),
)

if DB_CONFIG["password"] is None:
    raise RuntimeError(
        "Falta la variable de entorno PGPASSWORD. "
        "Copia .env.example a .env, defínela, y expórtala antes de correr los scripts "
        "(por ejemplo: export $(cat .env | xargs))."
    )
