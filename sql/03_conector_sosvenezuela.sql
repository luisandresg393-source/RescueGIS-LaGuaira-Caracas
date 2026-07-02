-- ============================================================
-- MIGRACIÓN 03 — CONECTOR SOS VENEZUELA 2026 → RescueGIS
--
-- Soporte para ingesta de fuentes externas federadas:
--   * dedupe por (fuente, id_externo)
--   * precisión de coordenadas (sus lat_pub/lng_pub vienen truncadas
--     y con jitter de 80–250 m por privacidad anti-saqueo)
--   * matching aproximado cuando la precisión no permite asignación
--     confiable a un edificio (regla: nunca forzar asignaciones inciertas)
-- ============================================================

-- ------------------------------------------------------------
-- 1. Campos nuevos en incidentes
-- ------------------------------------------------------------
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS id_externo TEXT;
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS url_fuente TEXT;
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS coord_precision_m DOUBLE PRECISION;
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS atribucion TEXT;

-- Dedupe: un incidente externo solo entra una vez por fuente.
CREATE UNIQUE INDEX IF NOT EXISTS ux_incidentes_fuente_ext
    ON incidentes (fuente, id_externo)
    WHERE id_externo IS NOT NULL;

-- ------------------------------------------------------------
-- 2. Matching con precisión limitada
--    building_match_metodo pasa a admitir:
--      'auto_150m'         GPS preciso, edificio a <150 m
--      'match_aproximado'  coordenada truncada/jittered: el edificio más
--                          cercano es plausible pero EXIGE confirmación humana
--      'sin_match'         nada cerca dentro del radio permitido
--      'manual'            asignado por un coordinador
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION buscar_edificio_aproximado(
    p_lat DOUBLE PRECISION,
    p_lon DOUBLE PRECISION,
    p_precision_m DOUBLE PRECISION,
    p_radio_base_m INTEGER DEFAULT 150
)
RETURNS TABLE(building_id BIGINT, distancia_m DOUBLE PRECISION, metodo TEXT) AS $$
DECLARE
    v_radio DOUBLE PRECISION;
    v_metodo TEXT;
BEGIN
    -- Si la precisión es buena (<= 60 m) usamos el radio estándar y el match es firme.
    -- Si es mala, ampliamos el radio hasta precisión + radio base (tope 500 m),
    -- pero el resultado se etiqueta como aproximado.
    IF p_precision_m IS NULL OR p_precision_m <= 60 THEN
        v_radio := p_radio_base_m;
        v_metodo := 'auto_150m';
    ELSE
        v_radio := LEAST(p_precision_m + p_radio_base_m, 500);
        v_metodo := 'match_aproximado';
    END IF;

    RETURN QUERY
    SELECT b.id,
           ST_Distance(b.geom::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography),
           v_metodo
    FROM buildings b
    WHERE ST_DWithin(b.geom::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography, v_radio)
    ORDER BY b.geom <-> ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- El trigger de matching existente respeta building_id ya asignado por el
-- conector (que usa buscar_edificio_aproximado), así que no hay conflicto:
-- el conector inserta con building_id + building_match_metodo ya resueltos.

-- ------------------------------------------------------------
-- 3. Vista para el panel: incidentes federados con su estado de match
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_incidentes_federados AS
SELECT
    inc.id, inc.codigo, inc.fuente, inc.id_externo, inc.url_fuente, inc.atribucion,
    inc.tipo, inc.descripcion, inc.personas, inc.urgencia,
    inc.estado_verificacion, inc.lat, inc.lon, inc.coord_precision_m,
    inc.building_id, b.codigo_corto AS building_codigo, b.nombre AS building_nombre,
    b.municipio, b.prioridad, b.prioridad_score,
    inc.building_match_metodo, inc.building_match_distancia_m,
    (SELECT COUNT(*) FROM evidencias e WHERE e.incidente_id = inc.id) AS num_evidencias,
    (SELECT MAX(nivel_confianza) FROM evidencias e WHERE e.incidente_id = inc.id) AS confianza_max,
    inc.fecha, inc.creado_en
FROM incidentes inc
LEFT JOIN buildings b ON b.id = inc.building_id
WHERE inc.id_externo IS NOT NULL
ORDER BY inc.fecha DESC;

INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'conector_federacion', 0,
        'Migración 03: id_externo + coord_precision_m + matching aproximado para fuentes federadas (SOS Venezuela 2026)');
