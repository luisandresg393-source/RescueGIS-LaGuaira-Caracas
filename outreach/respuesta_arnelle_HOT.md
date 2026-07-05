# Borrador de respuesta a Arnelle (HOT) — 2026-07-05

> **Cómo usarlo:** elige el bloque que corresponda a lo que Arnelle respondió,
> ajusta los `[corchetes]` y envía. Regla de oro: respuesta CORTA, con máximo
> 1 pregunta y 1 acción concreta propuesta. Ella coordina una activación
> regional — facilítale decir "sí" rápido.

---

## Bloque común (inicio, va siempre)

Hola Arnelle,

¡Gracias por responder! Actualización rápida desde mi último correo — el
proyecto avanzó bastante:

- Pipeline completo funcionando: matching GPS→edificio (ahora 101,568
  edificios), **correlación de reportes duplicados** que refina posición
  entre fuentes con distinta precisión, API REST con control de acceso para
  cuerpos de emergencia, bot de Telegram ciudadano, vista móvil para
  rescatistas en terreno y notificaciones push.
- Integración federada ya operativa con una plataforma ciudadana
  (SOS Venezuela 2026), respetando su modelo de privacidad de coordenadas.
- Despliegue reproducible en un comando (`instalar.sh`) — cualquier equipo
  de la activación puede auto-hospedarlo en ~40 minutos.
- One-pager actualizado adjunto; todo el código sigue en:
  https://github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas

## Escenario A — te invitó al Slack/canal del working group

Ya me uní al canal [nombre] / me uniré hoy mismo. Presentaré el proyecto ahí
con el one-pager. ¿Hay algún equipo en particular (terremotovenezuela.app u
otro) al que sugieras escribirle directamente sobre la capa de priorización?

Gracias de nuevo — quedo a disposición.

## Escenario B — pidió más información / una llamada

Encantado. Estoy disponible [pon 2-3 franjas horarias concretas, con zona
horaria]. Si prefieres async, el one-pager adjunto resume todo en una página
y puedo grabar un video de 5 minutos del flujo completo si les sirve.

Una pregunta concreta mientras tanto: ¿el dataset de daños validado por
humanos (Caraballeda/La Guaira/Caracas, fAIr) está disponible para cruzarlo
con la base de edificios? Ese cruce daría "prioridad × daño confirmado" y
creo que es el aporte más inmediato que podemos darle a la activación.

## Escenario C — respuesta tibia / "lo compartiré con el equipo"

¡Perfecto, gracias! Solo dos cosas concretas para facilitar:

1. El one-pager adjunto es reenviable tal cual (1 página).
2. Si algún equipo quiere probarlo, se instala en un comando en cualquier
   servidor Debian/Ubuntu — sin dependencia de mí.

Sin apuro — sé que la activación los tiene a full. Cualquier equipo que
quiera la capa de matching/priorización, estamos listos para integrar.

## Escenario D — pidió algo específico que no está aquí

Respóndeme (a la IA) con lo que pidió exactamente y te preparo la respuesta
+ el material técnico que haga falta (demo, dataset de ejemplo, endpoint
de prueba, lo que sea).

---

## Checklist antes de enviar

- [ ] Adjuntar `outreach/ONE_PAGER.md` exportado a PDF (o pegar el link al repo)
- [ ] Firmar con tu nombre real y el link del repo
- [ ] Si mañana ya hay VPS: añadir "La API estará pública en [URL] esta semana"
- [ ] Máximo 150 palabras del bloque elegido — ella lee decenas de correos/día
