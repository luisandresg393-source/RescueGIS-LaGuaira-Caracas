-- ============================================================
-- MIGRACIÓN 05 — SUCESOS (correlación de reportes)
--
-- Problema: el mismo derrumbe genera N reportes (Telegram, SOS
-- Venezuela, llamadas) con coordenadas distintas y precisión
-- variable. Tratarlos por separado dispersa la atención; unirlos
-- a mano no escala.
--
-- Solución: agrupar incidentes abiertos cercanos (DBSCAN espacial)
-- en un SUCESO con:
--   * posición REFINADA: centroide ponderado por 1/precisión²
--     (un GPS de ±8 m pesa ~1400x más que uno de ±300 m)
--   * precisión refinada: la mejor del grupo
--   * confianza agregada: más fuentes independientes = más confianza
--     (nunca 100 automático — eso sigue siendo humano)
--   * re-match de edificio con la posición refinada: un suceso puede
--     lograr match FIRME aunque todos sus reportes fueran aproximados
-- ============================================================

CREATE TABLE IF NOT EXISTS sucesos (
    id                BIGSERIAL PRIMARY KEY,
    codigo            TEXT UNIQUE,                -- SUC-000001
    tipo_dominante    TEXT,                       -- el tipo más grave del grupo
    urgencia_max      urgencia_enum NOT NULL DEFAULT 'MEDIA',
    personas_max      INTEGER NOT NULL DEFAULT 0, -- máx reportado (no suma: son el mismo suceso)
    heridos_max       INTEGER NOT NULL DEFAULT 0,
    num_reportes      INTEGER NOT NULL DEFAULT 0,
    num_fuentes       INTEGER NOT NULL DEFAULT 0, -- fuentes DISTINTAS (clave de confianza)
    confianza         INTEGER NOT NULL DEFAULT 0 CHECK (confianza BETWEEN 0 AND 100),

    -- posición refinada
    lat               DOUBLE PRECISION,
    lon               DOUBLE PRECISION,
    geom              geometry(Point, 4326),
    coord_precision_m DOUBLE PRECISION,

    -- edificio re-matcheado con la posición refinada
    building_id       BIGINT REFERENCES buildings(id) ON DELETE SET NULL,
    building_match_metodo TEXT,
    building_match_distancia_m DOUBLE PRECISION,

    creado_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sucesos_geom ON sucesos USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_sucesos_urgencia ON sucesos (urgencia_max);

CREATE SEQUENCE IF NOT EXISTS sucesos_codigo_seq;
CREATE OR REPLACE FUNCTION set_codigo_suceso() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.codigo IS NULL THEN
        NEW.codigo := 'SUC-' || LPAD(nextval('sucesos_codigo_seq')::text, 6, '0');
    END IF;
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    END IF;
    NEW.actualizado_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sucesos_codigo ON sucesos;
CREATE TRIGGER trg_sucesos_codigo
    BEFORE INSERT OR UPDATE ON sucesos
    FOR EACH ROW EXECUTE FUNCTION set_codigo_suceso();

ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS suceso_id BIGINT REFERENCES sucesos(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_incidentes_suceso ON incidentes (suceso_id);

-- Vista operativa: sucesos abiertos con su edificio, para la API y la vista de campo
CREATE OR REPLACE VIEW v_sucesos_abiertos AS
SELECT
    s.id, s.codigo, s.tipo_dominante, s.urgencia_max::text AS urgencia,
    s.personas_max, s.heridos_max, s.num_reportes, s.num_fuentes, s.confianza,
    s.lat, s.lon, s.coord_precision_m,
    s.building_match_metodo, s.building_match_distancia_m,
    b.codigo_corto AS edificio, b.nombre AS edificio_nombre,
    b.municipio::text AS municipio, b.prioridad::text AS prioridad_edificio,
    (SELECT string_agg(DISTINCT i.fuente, ' + ') FROM incidentes i WHERE i.suceso_id = s.id) AS fuentes,
    (SELECT bool_or(i.asignado_a IS NOT NULL) FROM incidentes i WHERE i.suceso_id = s.id) AS alguien_asignado,
    (SELECT string_agg(DISTINCT i.asignado_a, ', ') FROM incidentes i WHERE i.suceso_id = s.id AND i.asignado_a IS NOT NULL) AS asignado_a,
    s.actualizado_en
FROM sucesos s
LEFT JOIN buildings b ON b.id = s.building_id
WHERE EXISTS (SELECT 1 FROM incidentes i WHERE i.suceso_id = s.id AND i.resuelto_en IS NULL)
ORDER BY CASE s.urgencia_max WHEN 'CRITICA' THEN 3 WHEN 'ALTA' THEN 2 WHEN 'MEDIA' THEN 1 ELSE 0 END DESC,
         s.confianza DESC;

INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'sucesos', 0,
        'Migración 05: sucesos (correlación DBSCAN de reportes, posición refinada por precisión, confianza multi-fuente)');
