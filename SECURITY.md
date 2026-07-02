# Política de seguridad y manejo de datos sensibles

## Qué NO debe estar nunca en este repositorio

- Nombres, cédulas, teléfonos o direcciones exactas de personas reales
  (desaparecidas, atrapadas, evacuadas, etc.).
- Fotos o videos identificables de víctimas.
- Credenciales de bases de datos, tokens de bots, claves de API.
- Volcados (`dumps`) de bases de datos de producción.
- Coordenadas GPS de incidentes reales sin agregación/anonimización.

Todo lo anterior debe vivir exclusivamente en la base de datos de producción,
protegida y con acceso restringido a coordinadores autorizados — nunca en
control de versiones.

## Si encontraste datos sensibles expuestos accidentalmente

**No abras un issue público.** En su lugar:

1. Contacta directamente a los mantenedores del repositorio (ver `README.md`
   para datos de contacto actualizados).
2. Si el repositorio termina alojado o enlazado desde la infraestructura de
   HOT (Humanitarian OpenStreetMap Team), repórtalo también a
   `services@hotosm.org`.
3. Describe qué se expuso, dónde (commit, archivo, línea) y desde cuándo,
   si lo sabes.

Actuaremos con prioridad máxima: purgar el dato del historial de git,
rotar cualquier credencial afectada, y notificar a quien corresponda.

## Diseño pensado para minimizar riesgo

- El esquema (`sql/`) separa claramente los datos geográficos públicos de OSM
  (tabla `buildings`, `infraestructura`, `vias`) de los datos operativos de
  emergencia (`incidentes`, `evidencias`), que en un despliegue real deben
  vivir en una base de datos separada o con controles de acceso más estrictos
  que este repositorio de referencia.
- Los scripts de ejemplo en `data_samples/` contienen únicamente metadatos
  públicos de edificios (geometría, tipo, número de pisos) tal como existen
  en OpenStreetMap — no hay ni ha habido datos de personas en este repositorio.
- El matching automático de reportes a edificios tiene un radio máximo (150m)
  precisamente para evitar vincular un reporte a un edificio equivocado por
  error de GPS, lo que podría dirigir mal a un equipo de rescate.
