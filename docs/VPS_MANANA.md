# Puesta en producción — guía exacta para mañana

> Objetivo: RescueGIS público en internet en ~1 hora, con solo copiar/pegar.
> Dos scripts hacen todo: `instalar.sh` (stack completo) y `publicar.sh` (nginx+TLS).

## Paso 0 — Elegir proveedor (5 min, tarjeta o PayPal)

| Proveedor | Plan | Precio | Nota |
|---|---|---|---|
| **Hetzner** (recomendado) | CX22 (2 vCPU, 4 GB) | ~4,5 €/mes | crear en https://console.hetzner.cloud — imagen **Debian 13**, región da igual |
| DigitalOcean | Basic 2 GB | ~12 $/mes | más caro, muy simple |
| Oracle Cloud Free | ARM 4core/24GB | **gratis** | el registro puede tardar/rechazar tarjetas — no lo dejes para el día D |

Al crear el servidor: elige **Debian 13** (o Ubuntu 24.04), añade tu clave SSH
si sabes usarla — si no, elige contraseña por correo. Anota la **IP pública**.

## Paso 1 — (Opcional pero recomendado) dominio gratis (5 min)

1. Entra a https://www.duckdns.org (login con GitHub)
2. Crea el subdominio `rescuegis` → te da `rescuegis.duckdns.org`
3. Pon la IP del VPS en el campo IP → update

*(Si tienes dominio propio: crea un registro A `api.tudominio` → IP del VPS.)*

## Paso 2 — Conectarse e instalar (2 comandos, ~40 min)

```bash
ssh root@LA_IP_DEL_VPS

# dentro del servidor:
apt-get update && apt-get install -y git
git clone https://github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas.git
cd RescueGIS-LaGuaira-Caracas
sudo bash instalar.sh          # BD + 101k edificios + API + cron. Café ☕
```

⚠️ **Al final imprime la API key admin (`rgis_...`) — CÓPIALA YA** a un lugar
seguro. También guarda el archivo `.env` (contraseña de la BD).

## Paso 3 — Exponer a internet (1 comando, ~3 min)

```bash
sudo bash publicar.sh rescuegis.duckdns.org tu-correo@ejemplo.com
```

Cuando termine: **https://rescuegis.duckdns.org/docs** debe abrir la
documentación interactiva desde cualquier navegador del mundo. 🎉

## Paso 4 — Primeras keys y bot (10 min)

```bash
cd scripts
# una key por cada cuerpo/socio (entregar por Signal/llamada, nunca correo abierto)
python3 gestionar_keys.py crear "Bomberos ..." --rol emergencia --contacto "..."
python3 gestionar_keys.py crear "Bot Telegram" --rol ingesta

# bot ciudadano (token de @BotFather en Telegram: /newbot)
export TELEGRAM_TOKEN="123:ABC..." RESCUEGIS_API_URL="https://rescuegis.duckdns.org" RESCUEGIS_API_KEY="rgis_<key-ingesta>"
nohup python3 ../bot/telegram_bot.py >> ../data/bot.log 2>&1 &
```

## Paso 5 — Verificar el sistema vivo (2 min)

```bash
bash instalar.sh --solo-verificar
curl https://TU_DOMINIO/api/v1/stats
```

Y desde tu teléfono: abre `https://TU_DOMINIO/campo?key=<key-emergencia>`.

## Checklist de seguridad post-despliegue

- [ ] Key admin y `.env` guardados fuera del servidor
- [ ] `apt-get install -y ufw && ufw allow 22,80,443/tcp && ufw enable` (firewall)
- [ ] Backup diario: `crontab -e` → `0 3 * * * pg_dump rescuegis | gzip > /root/backup_$(date +\%u).sql.gz`
- [ ] Revocar el token de GitHub expuesto en el chat y crear uno nuevo
- [ ] Actualizar los issues #4 y #5 con la URL pública (¡y el post de Facebook!)

## Si algo falla

| Síntoma | Comando |
|---|---|
| API no responde | `journalctl -u rescuegis-api -n 50` |
| nginx error | `nginx -t` y `systemctl status nginx` |
| certbot falla | ¿DNS apunta a la IP? `dig +short TU_DOMINIO` |
| BD caída | `systemctl status postgresql` |

O pega el error en la sesión con la IA y se resuelve en vivo.
