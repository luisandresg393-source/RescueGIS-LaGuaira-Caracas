# Despliegue de la API propia de RescueGIS

Guía para poner `api/main.py` en internet y empezar a **captar reportes** y
**entregar información priorizada** a cuerpos de emergencia.

## Qué expone

| Endpoint | Acceso | Para qué |
|---|---|---|
| `GET /api/v1/salud` | público | monitoreo / uptime |
| `GET /api/v1/stats` | público | cifras agregadas (seguras de publicar) |
| `GET /api/v1/incidentes` | público (degradado) / key (preciso) | cola de incidentes con edificio matcheado |
| `GET /api/v1/edificios/prioridad` | público (degradado) / key (preciso) | cola de trabajo por score |
| `POST /api/v1/reportes` | key `ingesta`/`emergencia` | captar reportes (bots, plataformas, operadores) |
| `PATCH /api/v1/incidentes/{cod}/verificacion` | key `emergencia` | verificar/descartar — firmado y auditado |
| `PATCH /api/v1/incidentes/{cod}/despacho` | key `emergencia` | asignar («lo tomamos nosotros») / resolver con resultado |
| `GET /api/v1/export/geojson` | key `emergencia`/`socio` | capa para QGIS/ArcGIS/Google Earth |
| `GET /api/v1/export/csv` | key `emergencia`/`socio` | lista imprimible / radio |
| `GET /docs` | público | documentación interactiva (Swagger, auto-generada) |

**Modelo de privacidad** (mismo criterio que SOS Venezuela):
- Sin key → coordenadas con jitter 80–250 m + 3 decimales, sin teléfonos ni nombres de reporteros, descripciones truncadas.
- Con key `emergencia`/`socio` → precisión completa. Toda petición queda en `api_log`.
- Las keys nunca se guardan en claro (solo hash SHA-256) y se entregan una sola vez.

## Requisitos

- El servidor con la base PostGIS ya cargada (fases 1–2 del README).
- `pip install fastapi "uvicorn[standard]"` (ya en `requirements.txt`).

## Paso 1 — Migración y primera key

```bash
psql -h $PGHOST -U $PGUSER -d $PGDATABASE -f sql/04_api_keys.sql
cd scripts
python3 gestionar_keys.py crear "Bomberos de Caracas" --rol emergencia \
    --org "CBDC" --contacto "ops@..." --rate 240
python3 gestionar_keys.py listar
```

Roles: `emergencia` (todo), `ingesta` (solo POST), `socio` (lectura precisa), `admin`.

## Paso 2 — Arrancar la API

```bash
cd api
export $(cat ../.env | xargs)
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
```

Como servicio systemd (`/etc/systemd/system/rescuegis-api.service`):

```ini
[Unit]
Description=RescueGIS API
After=postgresql.service

[Service]
User=rescuegis
WorkingDirectory=/opt/RescueGIS-LaGuaira-Caracas/api
EnvironmentFile=/opt/RescueGIS-LaGuaira-Caracas/.env
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Paso 3 — Exponer a internet (nginx + TLS)

```nginx
server {
    server_name api.turescuegis.org;
    # rate limit adicional a nivel de red (la API ya limita por key/IP)
    limit_req_zone $binary_remote_addr zone=api:10m rate=60r/m;

    location / {
        limit_req zone=api burst=30 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

TLS gratis: `certbot --nginx -d api.turescuegis.org`.

### ¿Dónde hospedarlo?

| Opción | Costo | Nota |
|---|---|---|
| VPS (Hetzner/Contabo/DigitalOcean) | 4–6 €/mes | control total; 2 GB RAM sobran para esta carga |
| Oracle Cloud Free Tier | gratis | 4 ARM cores / 24 GB RAM gratis — suficiente para todo el stack |
| Fly.io / Railway | gratis–5 $ | despliegue rápido, Postgres gestionado aparte |
| Neon/Supabase (solo la BD) + VPS para la API | gratis + VPS | separa datos de cómputo; Neon es lo que usa SOS Venezuela |

Para una emergencia activa: VPS europeo o americano con backups diarios
(`pg_dump` + cron), y un segundo nodo de solo-lectura si el tráfico crece.

## Paso 4 — Onboarding de un cuerpo de emergencia (5 minutos)

1. Crear su key: `python3 gestionar_keys.py crear "PC Vargas" --rol emergencia --contacto ...`
2. Entregarla por canal seguro (Signal/llamada — nunca correo abierto).
3. Ellos prueban: `curl -H "X-Api-Key: <key>" https://api.../api/v1/incidentes?tipo=ATRAPADOS&sin_asignar=true`
4. Su GIS: cargar `https://api.../api/v1/export/geojson` directo en QGIS (capa remota) — se actualiza sola.
5. Flujo operativo mínimo:
   - `sin_asignar=true` → ven qué no está tomado por nadie
   - `PATCH .../despacho {"accion":"asignar"}` → lo marcan como suyo (los demás cuerpos lo ven)
   - `PATCH .../verificacion` → confirman o descartan en sitio
   - `PATCH .../despacho {"accion":"resolver","resultado":"rescatados"}` → cierre con trazabilidad

## Auditoría

Todo queda en `api_log`: quién (key), desde dónde (IP), qué hizo y cuándo.

```bash
python3 scripts/gestionar_keys.py uso <id_key>     # últimas 30 acciones
```

## Qué NO hace esta API (a propósito)

- No verifica nada automáticamente: la verificación siempre lleva la firma de una key.
- No expone contactos de reporteros al público.
- No desanonimiza coordenadas de fuentes federadas (respeta `coord_precision_m`).
- No jerarquiza vidas: el score es transparente y ajustable, y la decisión final es humana.
