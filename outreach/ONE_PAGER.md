# 🇻🇪 RescueGIS — Motor de priorización de rescate · Venezuela 2026

**Aporte técnico voluntario para la activación #2026_LACH_VE_EQ (HOT)**
Repo: https://github.com/luisandresg393-source/RescueGIS-LaGuaira-Caracas (MIT)

---

## ¿Qué es?

La capa de **inteligencia espacial** entre los reportes ciudadanos y los cuerpos
de emergencia: vincula cada reporte GPS a un **edificio específico** del
inventario OSM, **correlaciona reportes duplicados** del mismo suceso, calcula
una **prioridad transparente**, y la entrega por API al mapa del coordinador y
al **teléfono del rescatista** en terreno.

No es otro mapa de reportes — es el módulo que conecta los mapas con el despacho.

---

## 1. Base geográfica (OSM vía Overpass, refresco 2026-07-02)

| Municipio | Edificios | Infra crítica | Vías/puentes | Parroquias |
|---|---|---|---|---|
| La Guaira (Vargas) | 47,173 | 193 | 835 | 11 |
| Caracas | 54,402 | 1,213 | 5,717 | 32 |
| **Total** | **101,568** | **1,406** | **6,552** | **43** |

![Cobertura GIS](map/screenshot_mapa_verificacion.png)

## 2. Pipeline completo (todo construido y probado)

```
 bot Telegram (±10 m) ──┐
 SOS Venezuela (±330 m) ├─► matching GPS→edificio ─► correlación de sucesos ─► cola priorizada
 API socios / manual  ──┘    (KNN, radio adaptativo    (posición refinada por      │
                              según precisión GPS;      1/precisión², confianza    ├─► panel Leaflet coordinador
                              nunca fuerza matches      multi-fuente, validación   ├─► vista móvil /campo (navegar+llamar)
                              inciertos)                por parroquias)            └─► push Telegram a rescatistas
```

**Detalles que importan en emergencia real:**
- Reportes con coordenada degradada por privacidad (p.ej. SOS Venezuela trunca
  a ±330 m) se marcan `match_aproximado` y **exigen confirmación humana** — pero
  al correlacionarse con un GPS preciso del mismo suceso, el grupo logra match
  firme (probado: 3 reportes ±330/±330/±9 m → 1 suceso a 2.2 m del edificio).
- Validación cruzada con los 43 polígonos de parroquias: coordenada que
  contradice la parroquia declarada → confianza penalizada.
- Nada se verifica automáticamente: la verificación siempre lleva firma
  (API key auditada) de un coordinador.
- Fórmula de prioridad **pública y auditable** (personas, heridos, tiempo sin
  ayuda, infra crítica, confirmaciones) — ayuda a decidir, no sustituye criterio.

## 3. Entrega a cuerpos de emergencia

- **API REST** con roles: lectura pública con coordenadas degradadas
  (anti-saqueo); precisión completa + despacho (asignar/verificar/resolver)
  con API key auditada. Exportes **GeoJSON** (directo a QGIS) y CSV.
- **Vista móvil** para el teléfono del rescatista: cola por urgencia,
  navegación turn-by-turn (Google Maps / `geo:` para OsmAnd **offline**),
  llamada directa al reportero.
- **Push Telegram**: sucesos críticos llegan solos al teléfono, filtrados por
  municipio o radio.

## 4. Interoperabilidad (lo que nos gustaría conversar con HOT)

- **Consumimos**: cualquier fuente con API (ya integrado: SOS Venezuela 2026,
  respetando su modelo de privacidad). Formato de ingesta documentado.
- **Ofrecemos**: sucesos correlacionados y priorizados vía API/GeoJSON — 
  ¿útil para cruzar con el dataset de daños validado por humanos de fAIr
  (Caraballeda/La Guaira/Caracas)? Ese cruce daría prioridad × daño confirmado.
- **Despliegue en 1 comando** (`sudo bash instalar.sh` en Debian/Ubuntu limpio:
  BD + datos + API en ~40 min, probado). Cualquier equipo puede auto-hospedar.

## 5. Estado y necesidad

| | |
|---|---|
| Software | ✅ completo, probado E2E, MIT |
| Servidor de producción | ⏳ en curso (VPS esta semana) |
| Lo que buscamos | integración con proyectos activos de la activación, acceso al dataset de daños fAIr, y difusión entre equipos técnicos |

**Contacto:** Luis (autor) — vía issues del repo o el correo de este hilo.
