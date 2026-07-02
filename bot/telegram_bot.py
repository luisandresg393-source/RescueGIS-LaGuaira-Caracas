#!/usr/bin/env python3
"""
Bot de Telegram de RescueGIS — ingesta ciudadana de reportes
=============================================================

Un ciudadano comparte su UBICACIÓN (GPS del teléfono) y responde 3 preguntas
con botones. El bot POSTea a la API propia de RescueGIS
(`POST /api/v1/reportes`, key rol 'ingesta'), donde el matching GPS→edificio
y el recálculo de prioridad son automáticos.

Diseño:
  * Sin dependencias pesadas: usa la API HTTP de Telegram directo (long polling
    con `requests`). Corre en cualquier máquina con internet — NO necesita
    acceso a la base de datos, solo a la API de RescueGIS.
  * Flujo mínimo (una emergencia no es momento para formularios largos):
      ubicación → tipo (botones) → ¿cuántas personas? → descripción libre
      → confirmar → enviado (recibe el código INC-XXXXXX para seguimiento).
  * Privacidad: el teléfono del reportero solo se adjunta si el usuario
    pulsa explícitamente el botón «Compartir mi teléfono» — y solo lo verán
    cuerpos de emergencia con key, nunca el público.
  * Dedupe: id_externo = tg-<chat_id>-<message_id> — reenvíos no duplican.
  * La precisión del GPS (horizontal_accuracy de Telegram) se propaga a
    coord_precision_m: si es mala, el match será 'aproximado' y pedirá
    confirmación humana, igual que cualquier otra fuente.

Configuración (variables de entorno):
    TELEGRAM_TOKEN        token de @BotFather
    RESCUEGIS_API_URL     ej. https://api.turescuegis.org  (o http://127.0.0.1:8000)
    RESCUEGIS_API_KEY     key con rol 'ingesta' (scripts/gestionar_keys.py)

Uso:
    python3 telegram_bot.py               # producción (long polling)
    python3 telegram_bot.py --simular     # prueba offline: conversación simulada
                                          # contra la API real, sin Telegram
"""
import argparse
import json
import os
import sys
import time

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
API_URL = os.environ.get("RESCUEGIS_API_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("RESCUEGIS_API_KEY")

TG = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

# ------------------------------------------------------------------
# Estado de conversación por chat (en memoria; el flujo dura minutos)
# ------------------------------------------------------------------
ESTADOS: dict[int, dict] = {}

TIPOS = [
    ("🆘 Personas atrapadas", "ATRAPADOS"),
    ("🩸 Heridos", "HERIDOS"),
    ("🏚 Edificio dañado/colapsado", "DANO_ESTRUCTURAL"),
    ("🥫 Necesidad básica (agua/comida/medicinas)", "NECESIDAD_BASICA"),
    ("⚠️ Otro peligro", "OTRO"),
]
URGENCIA_POR_TIPO = {"ATRAPADOS": "CRITICA", "HERIDOS": "ALTA",
                     "DANO_ESTRUCTURAL": "MEDIA", "NECESIDAD_BASICA": "MEDIA", "OTRO": "MEDIA"}

MSG_BIENVENIDA = (
    "🇻🇪 *RescueGIS — Reporte de emergencia*\n\n"
    "Este bot envía tu reporte directamente a la cola de coordinación de rescate "
    "(La Guaira / Caracas).\n\n"
    "*Para reportar:* pulsa el clip 📎 → *Ubicación* → «Enviar mi ubicación actual».\n"
    "_Usa la ubicación del LUGAR de la emergencia, no la tuya si ya no estás ahí "
    "(puedes mover el pin en el mapa)._\n\n"
    "⚠️ Si tu vida corre peligro AHORA, llama también al 911 / 171.\n"
    "🔒 Tu teléfono NUNCA se publica; solo lo ven los cuerpos de emergencia si "
    "eliges compartirlo al final."
)


# ------------------------------------------------------------------
# Telegram helpers (API HTTP cruda)
# ------------------------------------------------------------------
def tg_call(metodo, **params):
    r = requests.post(f"{TG}/{metodo}", json=params, timeout=35)
    data = r.json()
    if not data.get("ok"):
        print(f"[tg] {metodo} → {data}", file=sys.stderr)
    return data.get("result")


def enviar(chat_id, texto, botones=None, pedir_telefono=False):
    """botones: lista de (texto, callback_data) → inline keyboard."""
    params = dict(chat_id=chat_id, text=texto, parse_mode="Markdown")
    if botones:
        params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d}] for t, d in botones]}
    elif pedir_telefono:
        params["reply_markup"] = {
            "keyboard": [[{"text": "📱 Compartir mi teléfono", "request_contact": True}],
                         [{"text": "Omitir (enviar sin teléfono)"}]],
            "one_time_keyboard": True, "resize_keyboard": True}
    else:
        params["reply_markup"] = {"remove_keyboard": True}
    return tg_call("sendMessage", **params)


# ------------------------------------------------------------------
# Envío a la API de RescueGIS
# ------------------------------------------------------------------
def postear_reporte(st, chat_id):
    payload = {
        "tipo": st["tipo"],
        "descripcion": st.get("descripcion") or None,
        "lat": st["lat"], "lon": st["lon"],
        "coord_precision_m": st.get("precision_m"),
        "personas": st.get("personas", 0),
        "urgencia": URGENCIA_POR_TIPO[st["tipo"]],
        "reportero_nombre": st.get("nombre"),
        "telefono_contacto": st.get("telefono"),
        "id_externo": f"tg-{chat_id}-{st['msg_id']}",
        "url_fuente": None,
    }
    r = requests.post(f"{API_URL}/api/v1/reportes", json=payload,
                      headers={"X-Api-Key": API_KEY}, timeout=30)
    if r.status_code == 201:
        return True, r.json()
    if r.status_code == 409:
        return False, {"error": "Este reporte ya fue enviado antes (no se duplicó)."}
    return False, {"error": f"Error {r.status_code}: {r.text[:200]}"}


# ------------------------------------------------------------------
# Máquina de estados del flujo
# ------------------------------------------------------------------
def manejar_mensaje(chat_id, msg):
    st = ESTADOS.setdefault(chat_id, {"paso": "inicio"})

    # Ubicación recibida → arranca (o reinicia) un reporte
    if "location" in msg:
        loc = msg["location"]
        ESTADOS[chat_id] = st = {
            "paso": "tipo", "msg_id": msg.get("message_id", int(time.time())),
            "lat": loc["latitude"], "lon": loc["longitude"],
            "precision_m": loc.get("horizontal_accuracy"),
            "nombre": (msg.get("from") or {}).get("first_name"),
        }
        prec = f" (precisión ±{int(st['precision_m'])} m)" if st.get("precision_m") else ""
        enviar(chat_id, f"📍 Ubicación recibida{prec}.\n\n*¿Qué está pasando ahí?*",
               botones=[(t, f"tipo:{v}") for t, v in TIPOS])
        return

    if "contact" in msg and st.get("paso") == "telefono":
        st["telefono"] = msg["contact"].get("phone_number")
        return finalizar(chat_id, st)

    texto = (msg.get("text") or "").strip()

    if texto.startswith("/start") or texto.startswith("/ayuda"):
        enviar(chat_id, MSG_BIENVENIDA)
        return

    if st["paso"] == "personas":
        if texto.isdigit() and 0 <= int(texto) <= 1000:
            st["personas"] = int(texto)
            st["paso"] = "descripcion"
            enviar(chat_id, "✏️ *Describe brevemente la situación* (piso, referencias, "
                            "qué se ve/escucha). O escribe `listo` para omitir.")
        else:
            enviar(chat_id, "Escribe solo el *número* de personas (ej: `3`). Si no sabes, escribe `0`.")
        return

    if st["paso"] == "descripcion":
        if texto.lower() != "listo":
            st["descripcion"] = texto[:2000]
        st["paso"] = "telefono"
        enviar(chat_id, "📱 *¿Compartir tu teléfono con los rescatistas?* (opcional, "
                        "solo lo ven cuerpos de emergencia — nunca se publica)",
               pedir_telefono=True)
        return

    if st["paso"] == "telefono" and texto.lower().startswith("omitir"):
        return finalizar(chat_id, st)

    # fallback
    enviar(chat_id, "Para reportar una emergencia, comparte la *ubicación* 📎→Ubicación. "
                    "Escribe /ayuda para instrucciones.")


def manejar_callback(chat_id, data):
    st = ESTADOS.get(chat_id)
    if not st:
        enviar(chat_id, "La sesión expiró. Comparte la ubicación de nuevo para empezar.")
        return
    if data.startswith("tipo:") and st["paso"] == "tipo":
        st["tipo"] = data.split(":", 1)[1]
        if st["tipo"] in ("ATRAPADOS", "HERIDOS"):
            st["paso"] = "personas"
            enviar(chat_id, "👥 *¿Cuántas personas?* (escribe el número; `0` si no sabes)")
        else:
            st["personas"] = 0
            st["paso"] = "descripcion"
            enviar(chat_id, "✏️ *Describe brevemente la situación.* O escribe `listo` para omitir.")


def finalizar(chat_id, st):
    ok, resp = postear_reporte(st, chat_id)
    if ok:
        cod = resp["codigo"]
        extra = ""
        if resp.get("match") == "auto_150m" and resp.get("edificio"):
            extra = f"\n🏢 Vinculado al edificio *{resp['edificio']['codigo_corto']}*."
        elif resp.get("match") == "match_aproximado":
            extra = "\n📌 Ubicación aproximada — un coordinador la confirmará."
        enviar(chat_id,
               f"✅ *Reporte enviado.* Código: `{cod}`{extra}\n\n"
               "Ya está en la cola de coordinación de rescate. Guarda el código.\n"
               "Puedes enviar otra ubicación para reportar otro sitio.\n\n"
               "⚠️ Recuerda: si hay peligro inmediato llama también al 911 / 171.")
    else:
        enviar(chat_id, f"❌ No se pudo enviar: {resp['error']}\n"
                        "Intenta de nuevo en unos minutos, o llama al 911 / 171.")
    ESTADOS.pop(chat_id, None)


# ------------------------------------------------------------------
# Long polling
# ------------------------------------------------------------------
def correr():
    if not TELEGRAM_TOKEN or not API_KEY:
        sys.exit("Faltan TELEGRAM_TOKEN y/o RESCUEGIS_API_KEY en el entorno. Ver docs/BOT_TELEGRAM.md")
    print(f"Bot corriendo. API: {API_URL}")
    offset = 0
    while True:
        try:
            updates = tg_call("getUpdates", offset=offset, timeout=30) or []
            for u in updates:
                offset = u["update_id"] + 1
                if "message" in u:
                    manejar_mensaje(u["message"]["chat"]["id"], u["message"])
                elif "callback_query" in u:
                    cq = u["callback_query"]
                    tg_call("answerCallbackQuery", callback_query_id=cq["id"])
                    manejar_callback(cq["message"]["chat"]["id"], cq["data"])
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[bot] error: {e} — reintento en 5s", file=sys.stderr)
            time.sleep(5)


# ------------------------------------------------------------------
# Modo simulación: conversación completa SIN Telegram, contra la API real
# ------------------------------------------------------------------
def simular():
    global enviar, tg_call
    salidas = []

    def enviar_fake(chat_id, texto, botones=None, pedir_telefono=False):
        salidas.append(texto.split("\n")[0])
        print(f"  BOT → {texto.splitlines()[0][:90]}")
        if botones:
            print(f"        [botones: {', '.join(d for _, d in botones)}]")

    enviar = enviar_fake  # noqa: F841

    chat = 99999
    print("SIMULACIÓN — ciudadano reporta personas atrapadas:")
    print("  USR → /start")
    manejar_mensaje(chat, {"text": "/start"})
    print("  USR → [comparte ubicación GPS ±12 m]")
    # coordenada de un edificio OSM real de La Guaira (LG-000020) → match firme esperado
    manejar_mensaje(chat, {"message_id": int(time.time()), "from": {"first_name": "María (SIMULACIÓN)"},
                           "location": {"latitude": 10.597727, "longitude": -67.0024747,
                                        "horizontal_accuracy": 12}})
    print("  USR → [pulsa botón ATRAPADOS]")
    manejar_callback(chat, "tipo:ATRAPADOS")
    print("  USR → 4")
    manejar_mensaje(chat, {"text": "4"})
    print("  USR → [PRUEBA BOT] Se escuchan voces en el segundo piso, portón verde")
    manejar_mensaje(chat, {"text": "[PRUEBA BOT] Se escuchan voces en el segundo piso, portón verde"})
    print("  USR → Omitir (enviar sin teléfono)")
    manejar_mensaje(chat, {"text": "Omitir (enviar sin teléfono)"})
    ok = any("Reporte enviado" in s for s in salidas)
    print(f"\n{'✅ SIMULACIÓN OK — reporte llegó a la API' if ok else '❌ FALLO — revisar arriba'}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simular", action="store_true", help="conversación de prueba sin Telegram")
    args = ap.parse_args()
    sys.exit(simular()) if args.simular else correr()
