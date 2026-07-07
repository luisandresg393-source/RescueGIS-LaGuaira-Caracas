#!/usr/bin/env bash
# ============================================================
# RescueGIS — exponer la API a internet (nginx + TLS) en UN comando
#
# Prerrequisito: haber corrido `sudo bash instalar.sh` (API viva en :8000)
# y tener un dominio/subdominio apuntando (registro A) a la IP del servidor.
#
#     sudo bash publicar.sh api.tudominio.org tu-correo@ejemplo.com
#
# Qué hace:
#   1. Instala nginx + certbot
#   2. Configura el reverse proxy con rate limit de red
#   3. Obtiene certificado TLS de Let's Encrypt (renovación automática)
#   4. Smoke test público
#
# ¿Sin dominio todavía? Puedes usar uno gratis en minutos:
#   - DuckDNS (duckdns.org): rescuegis.duckdns.org → tu IP
#   - O el subdominio que da tu proveedor de VPS
# ============================================================
set -euo pipefail

DOMINIO="${1:-}"
CORREO="${2:-}"
[[ -n "$DOMINIO" && -n "$CORREO" ]] || {
  echo "Uso: sudo bash publicar.sh <dominio> <correo-para-lets-encrypt>"
  echo "Ej.:  sudo bash publicar.sh api.rescuegis.org admin@rescuegis.org"
  exit 1; }
[[ $EUID -eq 0 ]] || { echo "Corre con sudo"; exit 1; }

log() { echo -e "\n\033[1;36m▶ $*\033[0m"; }
ok()  { echo -e "\033[1;32m✔ $*\033[0m"; }

log "Verificando que la API local responde"
curl -sf -m 5 http://127.0.0.1:8000/api/v1/salud >/dev/null \
  || { echo "✘ La API no responde en :8000 — corre antes: sudo bash instalar.sh"; exit 1; }
ok "API local viva"

log "Instalando nginx + certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
ok "Instalados"

log "Configurando nginx para $DOMINIO"
cat > /etc/nginx/sites-available/rescuegis <<EOF
limit_req_zone \$binary_remote_addr zone=rgapi:10m rate=120r/m;

server {
    listen 80;
    server_name $DOMINIO;

    location / {
        limit_req zone=rgapi burst=60 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/rescuegis /etc/nginx/sites-enabled/rescuegis
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ok "nginx configurado"

log "Obteniendo certificado TLS (Let's Encrypt)"
certbot --nginx -d "$DOMINIO" -m "$CORREO" --agree-tos --non-interactive --redirect \
  || { echo "⚠ certbot falló — ¿el DNS de $DOMINIO ya apunta a esta IP? (dig +short $DOMINIO)"; exit 1; }
ok "TLS activo con renovación automática"

log "Smoke test público"
sleep 2
CODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "https://$DOMINIO/api/v1/salud")
[[ "$CODE" == "200" ]] && ok "https://$DOMINIO/api/v1/salud → 200" \
  || echo "⚠ https://$DOMINIO/api/v1/salud → $CODE (revisa: journalctl -u rescuegis-api, nginx -t)"

echo -e "\n\033[1;32m════════════════════════════════════════════════\033[0m"
echo -e "\033[1;32m  RescueGIS PÚBLICO en https://$DOMINIO\033[0m"
echo "  Documentación interactiva: https://$DOMINIO/docs"
echo "  Vista de campo:            https://$DOMINIO/campo?key=<key-emergencia>"
echo "  Siguiente: crear keys (gestionar_keys.py) y el bot (docs/BOT_TELEGRAM.md)"
echo -e "\033[1;32m════════════════════════════════════════════════\033[0m"
