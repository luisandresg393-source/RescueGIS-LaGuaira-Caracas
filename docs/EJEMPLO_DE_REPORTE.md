# Ejemplo de reporte — flujo completo (datos sintéticos)

Este es el mismo caso de prueba mostrado en las capturas del README, con el
SQL exacto para reproducirlo en tu propia base local.

> ⚠️ **Datos sintéticos.** "María G." y el edificio `LG-004248` son parte de
> una prueba, no un incidente real. Nunca se han cargado datos de personas
> reales en este repositorio.

## 1. Llega un reporte ciudadano

```sql
INSERT INTO incidentes (tipo, descripcion, personas, urgencia, fuente, reportero_nombre, lat, lon, necesidades)
VALUES (
    'ATRAPADOS',
    'Mujer reporta 30 personas vivas bajo edificio, escucha golpes desde el sótano',
    30,
    'CRITICA',
    'telegram',
    'María G. (vecina)',
    10.61155,
    -66.37955,
    ARRAY['rescate','agua','medicamentos']
)
RETURNING id, codigo, building_id, building_match_metodo, building_match_distancia_m;
```

**Resultado** (el trigger `trg_incidentes_matching` corre automáticamente):

```
 id |   codigo   | building_id | building_match_metodo | building_match_distancia_m
----+------------+-------------+------------------------+----------------------------
  1 | INC-000001 |        4248 | auto_150m               |                 2.67897245
```

El incidente se vinculó automáticamente al edificio `LG-004248`, a solo 2.68
metros del GPS reportado — dentro del radio máximo de 150m.

## 2. El edificio ya tiene una prioridad calculada

```sql
SELECT codigo_corto, estado_rescate, prioridad, prioridad_score
FROM buildings WHERE id = 4248;
```

```
 codigo_corto |   estado_rescate    | prioridad | prioridad_score
--------------+---------------------+-----------+------------------
 LG-004248    | PERSONAS_ATRAPADAS  | ALTA      |               95
```

Desglose: 30 personas × 3 = 90, + 1 reporte pendiente × 5 = 95 puntos → 🟠 ALTA.

## 3. Llega evidencia que respalda el reporte

```sql
INSERT INTO evidencias (incidente_id, tipo, usuario, nivel_confianza, notas)
VALUES (1, 'foto', 'voluntario_juan', 90,
        'Foto recibida, GPS coincide, 3 personas confirman de forma independiente');
```

## 4. Un coordinador autorizado verifica el incidente

**Importante: esto nunca ocurre automáticamente.**

```sql
UPDATE incidentes
SET estado_verificacion = 'VERIFICADO',
    confirmado = TRUE,
    verificado_por = 'Coordinador Defensa Civil - Puesto La Guaira',
    verificado_en = now()
WHERE id = 1;
```

## 5. La prioridad sube automáticamente

```sql
SELECT codigo_corto, prioridad, prioridad_score FROM buildings WHERE id = 4248;
```

```
 codigo_corto | prioridad | prioridad_score
--------------+-----------+------------------
 LG-004248    | ALTA      |              110
```

El score subió de 95 a 110 porque el reporte pasó de "pendiente" (×5) a
"confirmado" (×20).

## 6. Consultar el panel operativo completo

```sql
SELECT * FROM v_edificios_prioridad WHERE id = 4248;
SELECT * FROM v_incidentes_detalle WHERE id = 1;
```

Estas dos vistas son las que un bot de Telegram o un panel web consumirían
directamente — ya traen todo pre-calculado (icono de prioridad, necesidades
agregadas, número de evidencias, confianza promedio).

## Caso límite importante: reporte sin edificio cercano

```sql
INSERT INTO incidentes (tipo, personas, urgencia, fuente, lat, lon)
VALUES ('ATRAPADOS', 5, 'MEDIA', 'telegram', 10.55, -66.90)
RETURNING codigo, building_id, building_match_metodo;
```

```
   codigo   | building_id | building_match_metodo
------------+-------------+------------------------
 INC-000002 |             | sin_match
```

El sistema **no fuerza una asignación incierta**. Este incidente queda visible
para que un coordinador lo asigne manualmente o pida más información de
ubicación al reportero.
