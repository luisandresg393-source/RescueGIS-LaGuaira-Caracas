# Bot de Telegram — ingesta ciudadana

El eslabón final de la cadena: cualquier persona con Telegram reporta una
emergencia en ~30 segundos, y el reporte cae ya matcheado y priorizado en la
cola que consumen los cuerpos de emergencia vía la API.

```
Ciudadano (Telegram) ──► bot/telegram_bot.py ──► POST /api/v1/reportes (key ingesta)
                                                        │ matching GPS→edificio (PostGIS)
                                                        ▼
Cuerpos de emergencia ◄── GET /api/v1/incidentes?sin_asignar=true (key emergencia)
```

## Flujo del ciudadano (3 pasos + opcional)

1. **Comparte ubicación** (📎 → Ubicación). Telegram manda el GPS con su
   precisión (`horizontal_accuracy`) — se propaga a `coord_precision_m`.
2. **Tipo** con botones: atrapados / heridos / daño estructural / necesidad / otro.
3. **Personas** (solo si atrapados/heridos) y **descripción** breve.
4. *(Opcional)* **Compartir teléfono** — botón explícito de Telegram; solo lo
   ven cuerpos de emergencia con key, nunca el público.

Recibe un código `INC-XXXXXX` para seguimiento. Reenvíos no duplican
(dedupe por `tg-<chat>-<mensaje>`).

## Puesta en marcha (10 minutos)

### 1. Crear el bot en Telegram

Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → nombre
(ej. «RescueGIS Venezuela») → usuario (ej. `@RescueGISVzlaBot`).
Te da el `TELEGRAM_TOKEN`.

### 2. Crear la key de ingesta

```bash
cd scripts
python3 gestionar_keys.py crear "Bot Telegram" --rol ingesta --rate 300
```

### 3. Correr el bot

Puede correr en la MISMA máquina de la API o en cualquier otra (solo necesita
HTTPS hacia la API):

```bash
export TELEGRAM_TOKEN="123456:ABC..."
export RESCUEGIS_API_URL="https://api.turescuegis.org"
export RESCUEGIS_API_KEY="rgis_..."
python3 bot/telegram_bot.py
```

Como servicio systemd: igual que la API (ver `DESPLIEGUE_API.md`), con
`ExecStart=/usr/bin/python3 /opt/RescueGIS-LaGuaira-Caracas/bot/telegram_bot.py`.

### Probar sin Telegram (CI / desarrollo)

```bash
python3 bot/telegram_bot.py --simular
```

Simula la conversación completa de un ciudadano contra la API real y verifica
que el reporte llega (exit 0 = OK).

## Decisiones de diseño

- **Sin librerías de bot** (python-telegram-bot, aiogram): solo `requests` y
  long polling. Menos dependencias = menos que romper en una emergencia, y
  corre hasta en una Raspberry Pi.
- **El bot no toca la base de datos.** Todo pasa por la API con una key
  `ingesta` — misma auditoría, mismo rate limit, mismo dedupe que cualquier
  otra fuente. Si el bot se ve comprometido, esa key solo puede crear
  reportes (no leer contactos ni verificar nada) y se revoca con un comando.
- **La urgencia inicial la fija el tipo** (atrapados→CRÍTICA, heridos→ALTA...),
  no el reportero — en pánico todo es "crítico"; la fórmula de prioridad y el
  coordinador ajustan después.
- **911/171 siempre visible**: el bot nunca pretende sustituir a los canales
  oficiales de emergencia.
