# Cómo contribuir

Gracias por considerar aportar a este proyecto. Este es un módulo pensado para
**integrarse con esfuerzos existentes** de respuesta al terremoto en Venezuela
(2026), no para competir con ellos. Antes de contribuir, por favor lee esto.

## Principios no negociables

1. **Nunca subas datos personales o sensibles** al repositorio: nombres reales
   de víctimas, teléfonos, direcciones exactas de personas, fotos identificables,
   ubicaciones de rescates en curso sin verificar, etc. Todo lo que hay aquí
   (incluida `data_samples/`) son datos públicos de OpenStreetMap o datos
   sintéticos de prueba.
2. **Ningún reporte se marca como verificado automáticamente.** Cualquier
   cambio al flujo de verificación de incidentes debe mantener el estado
   `PENDIENTE_VERIFICACION` como punto de entrada por defecto.
3. **La fórmula de prioridad es una ayuda a la decisión, no una autoridad.**
   Cambios a `recalcular_prioridad_edificio()` en `sql/02_modelo_rescate.sql`
   deben mantener el desglose por componentes visible y auditable — nada de
   cajas negras ni modelos que no se puedan explicar a un coordinador de campo.

## Cómo proponer un cambio

1. Haz un fork del repositorio.
2. Crea una rama descriptiva (`feature/matching-radio-configurable`,
   `fix/typo-trigger-incidentes`, etc.).
3. Si tocas el esquema SQL, añade una nueva migración numerada
   (`sql/03_tu_cambio.sql`) en vez de editar las existentes — así el historial
   de cambios queda claro para quien despliegue en producción.
4. Abre un Pull Request describiendo:
   - Qué problema resuelve.
   - Cómo lo probaste (idealmente con datos sintéticos, como en `docs/`).
   - Si afecta la fórmula de prioridad, incluye un ejemplo de antes/después.
5. Para vulnerabilidades de seguridad o exposición accidental de datos, **no
   abras un issue público** — sigue el proceso en `SECURITY.md`.

## Áreas donde más ayuda hace falta ahora mismo

- Bot de Telegram (fase 3, ver `README.md`) para alimentar la tabla `incidentes`.
- Panel de coordinación (mapa Leaflet) sobre la vista `v_edificios_prioridad`.
- Integración/cruce con datasets de daños ya validados por HOT (fAIr) para
  Caraballeda, La Guaira y Caracas.
- Pruebas automatizadas del matching GPS→edificio y del recálculo de prioridad.

## Código de conducta

Este proyecto se rige por respeto mutuo y foco en el impacto humanitario.
No se tolera el uso de este código o sus datos para fines distintos a la
respuesta a la emergencia y la ayuda humanitaria.
