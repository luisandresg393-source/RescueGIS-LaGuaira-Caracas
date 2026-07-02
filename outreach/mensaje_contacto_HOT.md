# Mensaje de contacto — Humanitarian OpenStreetMap Team (HOT)

**Para:** Arnelle Isaac (Coordinación, Open Mapping Hub LAC) — arnelle.isaac@hotosm.org
**CC opcional:** services@hotosm.org
**Asunto:** Aporte técnico voluntario — motor de priorización PostGIS para respuesta al terremoto en Venezuela (#2026_LACH_VE_EQ)

---

Hola Arnelle,

Mi nombre es [TU NOMBRE], soy desarrollador/voluntario técnico. Escribo en relación con la activación **2026 Venezuela Earthquake Response** y el proyecto #2026_LACH_VE_EQ.

Durante los últimos días construí, de forma independiente, una base de datos PostGIS con **96,634 edificios** de La Guaira y Caracas (a partir de OpenStreetMap vía Overpass API), junto con un **motor de priorización de rescate**:

- Vinculación automática de reportes ciudadanos (GPS) al edificio más cercano, vía KNN espacial indexado, con radio máximo de 150m — sin forzar coincidencias inciertas.
- Un modelo de incidentes/evidencias con estados de verificación explícitos (`pendiente_verificación → verificado`), pensado para que ningún reporte se trate como confirmado sin evidencia cruzada o validación humana.
- Una fórmula de prioridad transparente y auditable (personas atrapadas, heridos, tiempo sin atención, cercanía a infraestructura crítica, reportes confirmados vs. pendientes), pensada como **ayuda a la decisión para coordinadores, no como sustituto de su criterio**.
- Todo probado extremo a extremo con datos sintéticos (nunca datos reales de víctimas).

Vi que ya existen esfuerzos ciudadanos activos y coordinados con HOT (terremotovenezuela.app, entre otros), y que HOT ya lidera el trabajo de referencia con fAIr, el dataset de daños validado por humanos para Caraballeda/La Guaira/Caracas, y el Tasking Manager. **No quiero crear una plataforma paralela ni fragmentar el esfuerzo.** Mi intención es ofrecer esta pieza específica (el motor de matching + priorización sobre la capa de edificios) como posible aporte técnico a alguno de los proyectos ya activos, si es útil, o como referencia/código abierto para quien lo necesite.

Preguntas concretas:
1. ¿Hay algún equipo o proyecto dentro de la activación (terremotovenezuela.app, venezuela-ayuda, u otro) que esté buscando específicamente lógica de priorización o matching de reportes a edificios, donde este trabajo pudiera aportar?
2. ¿Existe un canal (Slack/Matrix del working group LAC) donde debería presentar esto para que el equipo técnico lo evalúe?
3. ¿Los datasets de daños validados por humanos (Caraballeda, La Guaira, Caracas) están disponibles en un formato que pueda cruzar con mi base de edificios, para complementar en vez de duplicar?

Adjunto un one-pager técnico de una página con capturas del flujo completo (matching de un reporte de prueba a su edificio correspondiente, y el desglose transparente del cálculo de prioridad), por si es útil para una revisión rápida. El código completo (SQL, scripts de ingesta, documentación, guía de instalación de 10 minutos) está publicado en: [URL-DE-TU-REPO-EN-GITHUB]

Quedo atento y disponible para una llamada breve si es útil. Gracias por el trabajo que están haciendo.

Saludos,
[TU NOMBRE]
[tu email / GitHub / LinkedIn]

---

## Notas para Luis (no forma parte del mensaje)

- Reemplaza `[TU NOMBRE]` y los datos de contacto antes de enviar.
- Este mensaje **no adjunta datos**, solo describe capacidades — correcto para un primer contacto.
- Si HOT responde señalando un proyecto específico, ahí sí tiene sentido preparar código/README para ese repo puntual (con su propio `CONTRIBUTING.md`, si lo tiene).
- Canal alternativo/paralelo mientras esperas respuesta de HOT: unirte al Slack/Matrix público (https://slack.hotosm.org/) y presentarte en el canal del working group Latinoamérica y el Caribe — suele ser más rápido que el correo.
- ✅ El one-pager técnico ya está listo: `venezuela_gis/ONE_PAGER.md` (con las capturas embebidas en `venezuela_gis/map/`). Sirve tanto para adjuntar al correo como para pegar en el Slack/Matrix de HOT.
