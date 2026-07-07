#!/usr/bin/env bash
# ============================================================
# RescueGIS — instalación completa en UN comando
#
# En un servidor Debian 12/13 o Ubuntu 22.04+ limpio:
#
#     sudo bash instalar.sh              # todo: BD + datos OSM + API
#     sudo bash instalar.sh --sin-osm    # sin descargar OSM (usa data/ ya presente)
#     bash instalar.sh --solo-verificar  # smoke test del stack ya instalado
#
# Qué hace (idempotente — se puede re-correr sin romper nada):
#   1. Instala PostgreSQL 17 + PostGIS + Python deps
#   2. Crea BD, aplica migraciones sql/01..07
#   3. Descarga y carga los ~101k edificios + infra + vías + parroquias (~20-30 min)
#   4. Precomputa distancias y crea la primera API key admin
#   5. Instala la API como servicio systemd (puerto 8000)
#   6. Corre el smoke test
#
# Después solo falta: nginx+TLS (docs/DESPLIEGUE_API.md §3) y el token del bot.
# ============================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
SIN_OSM=0
SOLO_VERIFICAR=0
for a in "$@"; do
  case $a in
    --sin-osm) SIN_OSM=1 ;;
    --solo-verificar) SOLO_VERIFICAR=1 ;;
  esac
done

log()  { echo -e "\n\033[1;36m▶ $*\033[0m"; }
ok()   { echo -e "\033[1;32m✔ $*\033[0m"; }
fail() { echo -e "\033[1;31m✘ $*\033[0m"; exit 1; }

# ------------------------------------------------------------
# 0. .env (se genera con contraseña aleatoria si no existe)
# ------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  log "Generando .env con contraseña aleatoria"
  # openssl en vez de tr</dev/urandom|head: este último dispara SIGPIPE con pipefail
  PASS="$(openssl rand -hex 16 2>/dev/null || date +%s%N | sha256sum | head -c 24)"
  cat > "$ENV_FILE" <<EOF
PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=rescuegis
PGUSER=postgres
PGPASSWORD=$PASS
EOF
  ok ".env creado (guárdalo: contiene la contraseña de la BD)"
fi
set -a; source "$ENV_FILE"; set +a

verificar() {
  log "SMOKE TEST del stack"
  psql -qtA -c "SELECT 1" >/dev/null || fail "PostgreSQL no responde"
  local NB NP NS
  NB=$(psql -qtA -c "SELECT count(*) FROM buildings")
  NP=$(psql -qtA -c "SELECT count(*) FROM parroquias" 2>/dev/null || echo 0)
  NS=$(psql -qtA -c "SELECT count(*) FROM sucesos" 2>/dev/null || echo 0)
  echo "  edificios=$NB · parroquias=$NP · sucesos=$NS"
  [[ "$NB" -gt 90000 ]] || fail "faltan edificios (¿corriste sin --sin-osm?)"
  local CODE
  CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/v1/salud || echo 000)
  if [[ "$CODE" == "200" ]]; then ok "API viva en :8000 (salud=200)";
  else echo "  ⚠ API no responde en :8000 (¿servicio systemd activo? journalctl -u rescuegis-api)"; fi
  ok "Smoke test completado"
}

if [[ "$SOLO_VERIFICAR" == 1 ]]; then verificar; exit 0; fi

[[ $EUID -eq 0 ]] || fail "Corre con sudo (instala paquetes y crea el servicio systemd)"

# ------------------------------------------------------------
# 1. Paquetes
# ------------------------------------------------------------
log "Instalando PostgreSQL + PostGIS + Python (puede tardar unos minutos)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql postgresql-17-postgis-3 postgis \
  python3 python3-pip python3-venv curl cron >/dev/null 2>&1 \
  || apt-get install -y -qq postgresql postgresql-16-postgis-3 postgis \
       python3 python3-pip python3-venv curl cron >/dev/null
pip install -q --break-system-packages psycopg2-binary requests fastapi \
  "uvicorn[standard]" shapely 2>/dev/null \
  || pip install -q psycopg2-binary requests fastapi "uvicorn[standard]" shapely
ok "Paquetes instalados"

# ------------------------------------------------------------
# 2. Base de datos
# ------------------------------------------------------------
log "Configurando PostgreSQL"
pg_ctlcluster 17 main start 2>/dev/null || pg_ctlcluster 16 main start 2>/dev/null || systemctl start postgresql || true
sudo -u postgres psql -qc "ALTER USER postgres PASSWORD '$PGPASSWORD';"
sudo -u postgres psql -qc "CREATE DATABASE $PGDATABASE;" 2>/dev/null || true
for f in "$REPO_DIR"/sql/0*.sql; do
  echo "  migración $(basename "$f")"
  psql -q -f "$f" 2>/dev/null || psql -q -f "$f"   # 2º intento muestra el error real
done
psql -qc "CREATE EXTENSION IF NOT EXISTS unaccent;"
ok "BD lista con migraciones 01–07"

# ------------------------------------------------------------
# 3. Datos OSM
# ------------------------------------------------------------
export RESCUEGIS_DATA_DIR="$REPO_DIR/data"
mkdir -p "$RESCUEGIS_DATA_DIR"
cd "$REPO_DIR/scripts"
if [[ "$SIN_OSM" == 0 ]]; then
  log "Descargando OSM (edificios/infra/vías/parroquias — 20-30 min, reintenta solo)"
  for m in la_guaira caracas; do
    [[ -f "$RESCUEGIS_DATA_DIR/buildings_$m.json" ]] || python3 download_overpass.py buildings $m
    [[ -f "$RESCUEGIS_DATA_DIR/infra_$m.json"     ]] || python3 download_overpass.py infra $m
    [[ -f "$RESCUEGIS_DATA_DIR/vias_$m.json"      ]] || python3 download_overpass.py roads $m
  done
else
  log "Saltando descarga OSM (--sin-osm): usando $RESCUEGIS_DATA_DIR"
fi

log "Cargando datos a PostGIS"
for m in la_guaira caracas; do
  python3 load_buildings.py $m
  python3 load_infra.py $m
  python3 load_vias.py $m
  [[ -f "$RESCUEGIS_DATA_DIR/parroquias_$m.json" ]] && python3 load_parroquias.py $m || true
done
python3 precompute_distancias.py
psql -qc "UPDATE buildings SET es_infraestructura_critica=TRUE
          WHERE tipo IN ('hospital','school','fire_station')
             OR tipo_amenity IN ('hospital','clinic','school','university','college',
                                 'fire_station','shelter','social_facility');"
python3 load_parroquias.py validar 2>/dev/null || true
ok "Datos cargados"

# ------------------------------------------------------------
# 4. Primera key admin (solo si no hay ninguna)
# ------------------------------------------------------------
NKEYS=$(psql -qtA -c "SELECT count(*) FROM api_keys")
if [[ "$NKEYS" == "0" ]]; then
  log "Creando primera API key (rol admin) — GUÁRDALA, no se vuelve a mostrar"
  python3 gestionar_keys.py crear "Coordinador principal" --rol admin
fi

# ------------------------------------------------------------
# 5. Servicio systemd de la API
# ------------------------------------------------------------
log "Instalando servicio systemd rescuegis-api"
cat > /etc/systemd/system/rescuegis-api.service <<EOF
[Unit]
Description=RescueGIS API
After=postgresql.service

[Service]
WorkingDirectory=$REPO_DIR/api
EnvironmentFile=$ENV_FILE
ExecStart=$(command -v python3) -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now rescuegis-api 2>/dev/null || {
  echo "  (systemd no disponible — arranca a mano: cd api && uvicorn main:app --port 8000)"; }
sleep 3

# ------------------------------------------------------------
# 6. Cron de mantenimiento (correlación + watcher SOS)
# ------------------------------------------------------------
log "Instalando cron (correlación cada 2 min, watcher SOS cada 10)"
if command -v crontab >/dev/null 2>&1; then
  CRON_TMP=$(mktemp)
  crontab -l 2>/dev/null | grep -v rescuegis > "$CRON_TMP" || true
  cat >> "$CRON_TMP" <<EOF
*/2 * * * * cd $REPO_DIR/scripts && $(command -v python3) correlacionar_sucesos.py >> $REPO_DIR/data/cron.log 2>&1 # rescuegis
*/10 * * * * cd $REPO_DIR/scripts && flock -n /tmp/sos.lock $(command -v python3) watch_sosvenezuela.py --once >> $REPO_DIR/data/cron.log 2>&1 # rescuegis
*/15 * * * * cd $REPO_DIR/scripts && flock -n /tmp/chatmap.lock $(command -v python3) sync_chatmap.py >> $REPO_DIR/data/cron.log 2>&1 # rescuegis
EOF
  crontab "$CRON_TMP" && rm -f "$CRON_TMP"
  ok "Cron instalado"
else
  echo "  ⚠ crontab no disponible — instala cron (apt install cron) y re-corre, o usa systemd timers"
fi

verificar

echo -e "\n\033[1;32m════════════════════════════════════════════════\033[0m"
echo -e "\033[1;32m  RescueGIS instalado.\033[0m"
echo "  API:        http://127.0.0.1:8000/docs"
echo "  Siguiente:  nginx + TLS  → docs/DESPLIEGUE_API.md §3"
echo "              bot Telegram → docs/BOT_TELEGRAM.md"
echo "              keys         → scripts/gestionar_keys.py crear ..."
echo -e "\033[1;32m════════════════════════════════════════════════\033[0m"
