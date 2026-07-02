# 🇻🇪 Motor de priorización PostGIS — Respuesta al terremoto Venezuela 2026

**Aporte técnico voluntario para la activación #2026_LACH_VE_EQ (HOT)**

---

## ¿Qué es?

Un módulo de **matching automático de reportes ciudadanos a edificios** + **priorización transparente de rescate**, construido sobre una base geográfica de **96,634 edificios reales** de La Guaira y Caracas (OpenStreetMap vía Overpass API).

No es otro mapa de reportes más — es la capa de **inteligencia espacial** que conecta un reporte con coordenadas GPS a un edificio específico del inventario, y calcula qué tan urgente es atenderlo, de forma auditable.

---

## 1. Base geográfica ya cargada

| Municipio | Edificios | Infraestructura crítica | Vías / puentes |
|---|---|---|---|
| La Guaira (Municipio Vargas) | 46,889 | 193 (hospitales, bomberos, escuelas, refugios, gasolineras, telecom, agua, electricidad) | 835 |
| Caracas | 49,745 | 1,180 | 5,704 |
| **Total** | **96,634** | **1,406** | **6,530** |

![Cobertura GIS](map/screenshot_mapa_verificacion.png)

*Cada punto es un edificio real de OSM con centroide, tipo, pisos y material (cuando el dato existe en OSM). Los puntos de color son infraestructura crítica.*

---

## 2. El flujo que resuelve

Un reporte ciudadano por sí solo ("hay gente atrapada en tal calle") no le dice a un coordinador **a qué edificio específico del inventario corresponde**, ni **qué tan urgente es comparado con los otros 500 reportes que llegaron hoy**. Eso es lo que resuelve este módulo:

![Flujo end-to-end](map/screenshot_flujo_rescate.png)

**Paso a paso:**
1. Llega un reporte con coordenadas GPS (Telegram, formulario web, lo que sea).
2. PostGIS busca el edificio más cercano usando un índice espacial GiST + operador KNN — **rápido incluso sobre 96k+ filas** (con toda la base, el precómputo de distancias a infraestructura crítica tomó ~53 segundos).
3. **Radio máximo de 150 metros.** Si no hay ningún edificio conocido cerca, el reporte queda `sin_match` para revisión manual — **nunca se fuerza una asignación incierta**.
4. La prioridad del edificio se recalcula automáticamente (trigger de base de datos, no un cron ni un job externo) cada vez que llega un nuevo reporte o evidencia.
5. Todo reporte entra como `PENDIENTE_VERIFICACIÓN`. Solo sube a `VERIFICADO` cuando un coordinador autorizado lo confirma, o hay evidencia cruzada suficiente (foto + GPS + testimonios independientes).

---

## 3. Fórmula de prioridad (transparente, por componentes — no una caja negra)

```
score = (personas_atrapadas    × 3)
      + (heridos               × 5)
      + (fallecidos_reportados × 2)
      + (horas_sin_ayuda, tope 48h)
      + (incidentes_confirmados × 20)
      + (incidentes_pendientes  × 5)
      + 15  si el edificio ES infraestructura crítica (hospital/escuela/bomberos)
      + 10  si tiene hospital o bomberos a <200m
```

🔴 CRÍTICA ≥120 · 🟠 ALTA ≥60 · 🟡 MEDIA ≥20 · 🟢 BAJA <20

Cada componente queda registrado y es auditable — un coordinador puede ver *por qué* un edificio tiene ese score, no solo el número final. Esto es deliberado: **la fórmula es una ayuda a la decisión, no un reemplazo del criterio humano.**

---

## 4. Stack técnico

- **PostgreSQL 17 + PostGIS 3.5** — esquema con triggers para recálculo automático de prioridad.
- Ingesta desde **Overpass API** (edificios, infraestructura crítica, vías/puentes) con reintentos y control de rate-limit.
- Todo el código es Python + SQL estándar, sin dependencias exóticas — fácil de auditar e integrar.
- **Cero datos personales o sensibles en este repositorio.** Las pruebas mostradas usan datos sintéticos (el ejemplo "30 personas atrapadas" es un caso de prueba, no un incidente real).

---

## 5. Lo que buscamos

**No queremos fragmentar el ecosistema que ya existe** (terremotovenezuela.app, venezuela-ayuda, y el trabajo de HOT con fAIr y el dataset de daños validado por humanos). Este módulo se ofrece como:

- Código de referencia para cualquier equipo que ya tenga un mapa de reportes y necesite la capa de matching + priorización.
- Punto de partida para integrar directamente con un proyecto activo, si algún equipo lo necesita.
- Complemento a los datos de daños de HOT/fAIr: cruzar "qué edificios ya están dañados" (dataset validado) con "qué edificios tienen reportes de personas atrapadas ahora" (este módulo) podría ser una combinación útil.

---

## Contacto

[TU NOMBRE] · [tu email] · [tu GitHub/LinkedIn]
Repositorio de referencia completo (código, SQL, docs, guía de instalación de
10 minutos): [URL-DE-TU-REPO-EN-GITHUB]
