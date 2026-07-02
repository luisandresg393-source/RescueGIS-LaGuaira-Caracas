# Kit de difusión — buscar colaboradores

Mensajes listos para pegar. El repo ya está preparado para recibir gente:
topics de GitHub configurados, 3 issues abiertos (2 `help wanted`, 1 `good
first issue`), README con instalación en 15 min.

**Regla de oro al difundir:** sé específico sobre lo que necesitas (no "ayuda",
sino "un dev de JS para el issue #1") y responde rápido a quien aparezca —
un colaborador que espera 2 días se va.

---

## 1. Comunidades donde publicar (en orden de probabilidad de éxito)

| Dónde | Cómo |
|---|---|
| **HOT — Humanitarian OSM Team** | Slack `slack.hotosm.org` canal #general y #latam · ya hay borrador de contacto en `outreach/mensaje_contacto_HOT.md` |
| **OSM Venezuela / OSM Latam** | Telegram: @OSMve, @OSMLatam — es SU mapa, el proyecto usa sus 101k edificios |
| **Foro OSM (community.openstreetmap.org)** | categoría Latin America, tag `venezuela` |
| **r/venezuela y r/vzla (Reddit)** | post en español, foco: "proyecto open source para el terremoto, se buscan devs" |
| **Equipo SOS Venezuela 2026** | issue en `github.com/Z1Code/sosvenezuela2026` proponiendo federación (ya consumimos su API respetando su privacidad) |
| **Twitter/X + LinkedIn** | hilo corto con el mapa/panel como imagen; hashtags #TerremotoVenezuela #OpenSource |
| **Colectivos de devs venezolanos** | Telegram: Venezuela Dev, Programadores Venezuela; Discord de Devs LATAM |

## 2. Mensaje corto (Telegram/Slack/Discord — pegar tal cual)

> 🇻🇪 **Se buscan devs para RescueGIS** (open source, MIT)
>
> Motor de priorización de rescate para el terremoto: 101k edificios reales
> de OSM en PostGIS, API REST, bot de Telegram ciudadano y vista móvil para
> rescatistas. Funcionando — falta gente para el panel de coordinación
> (Leaflet), notificaciones push y más precisión en el matching.
>
> Issues marcados `good first issue` y `help wanted`, instalación en 15 min:
> https://github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas
>
> Python/PostGIS/JS. Español o inglés. Cada hora cuenta.

## 3. Mensaje largo (foro OSM / Reddit)

> **RescueGIS: motor open-source de priorización de rescate para el terremoto de Venezuela — se buscan colaboradores**
>
> Tras el doblete sísmico del 24 de junio (Mw 7.2/7.5) construimos un módulo
> específico que no vimos en las plataformas existentes: **matching automático
> de reportes ciudadanos GPS → edificio OSM concreto**, con fórmula de
> prioridad transparente y correlación multi-fuente (varios reportes del
> mismo derrumbe se agrupan y refinan la posición entre sí).
>
> **Qué hay funcionando** (todo reproducible con Docker en ~15 min):
> - 101.568 edificios de La Guaira + Caracas desde Overpass, en PostGIS
> - Matching con radio adaptativo según precisión GPS (nunca fuerza
>   asignaciones inciertas — las marca para confirmación humana)
> - Conector federado con SOS Venezuela 2026 (respeta su truncado de privacidad)
> - API REST con roles (público degradado / cuerpos de emergencia precisos)
> - Bot de Telegram: reporte ciudadano en 30 segundos
> - Vista móvil `/campo` para el teléfono del rescatista (navegar/llamar)
>
> **Qué falta y dónde ayudar** — issues abiertos:
> - Panel de coordinación Leaflet (#1, JS vanilla)
> - Push a rescatistas por Telegram en sucesos críticos (#2, Python)
> - Refinar precisión con polígonos de parroquias (#3, PostGIS, good first issue)
>
> No es "otro mapa más": es un módulo pensado para integrarse en lo que ya
> existe (HOT, SOS Venezuela, etc.). Licencia MIT.
> https://github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas

## 4. Hilo Twitter/X (3 tuits)

> 1/ Tras el terremoto Mw 7.5 en Venezuela construimos RescueGIS: software
> libre que convierte reportes ciudadanos ("hay gente atrapada aquí") en una
> cola priorizada por edificio para los rescatistas. 101k edificios reales de
> OpenStreetMap. 🧵
>
> 2/ Ya funciona: bot de Telegram (reporte en 30 seg), API para cuerpos de
> emergencia, correlación de reportes duplicados que REFINA la posición GPS,
> y vista móvil para el teléfono del rescatista con botón "navegar hasta ahí".
>
> 3/ Somos poquísimos y hay issues abiertos para JS (mapa Leaflet), Python
> (notificaciones push) y PostGIS. Si programas y quieres ayudar a Venezuela,
> 15 min para levantar el entorno: [link] #TerremotoVenezuela #OpenSource

## 5. Al responder a un interesado

1. Agradece + pregunta qué stack maneja
2. Apúntalo al issue que mejor le calce y a `docs/INSTALACION.md`
3. Ofrécele 20 min de llamada si se traba — el primer PR es el que cuesta
4. Dale merge rápido aunque sea imperfecto; se corrige después. Momentum > perfección.
