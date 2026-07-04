-- ============================================================
-- MIGRACIÓN 07 — PARROQUIAS (issue #3: precisión por polígonos)
--
-- Polígonos administrativos admin_level=7 de OSM (11 La Guaira +
-- 32 Caracas). Usos:
--   1. Validación cruzada: si un reporte federado dice "parroquia X"
--      pero su coordenada cae en otra parroquia → sospechoso, se
--      degrada la confianza del match.
--   2. Acotación: si la coordenada es imprecisa (>300 m) pero la
--      parroquia declarada es pequeña, la incertidumbre efectiva
--      se limita al tamaño de la parroquia.
--   3. Agregación operativa: conteos de sucesos por parroquia para
--      asignar zonas a cuerpos de emergencia.
-- ============================================================

CREATE TABLE IF NOT EXISTS parroquias (
    id          BIGSERIAL PRIMARY KEY,
    osm_id      BIGINT NOT NULL UNIQUE,
    nombre      TEXT NOT NULL,
    nombre_norm TEXT NOT NULL,          -- normalizado para matching de texto
    municipio   municipio_enum NOT NULL,
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    area_km2    DOUBLE PRECISION,
    -- radio equivalente: sqrt(area/pi) — cota de incertidumbre si solo
    -- sabemos la parroquia
    radio_equiv_m DOUBLE PRECISION,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_parroquias_geom ON parroquias USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_parroquias_nombre ON parroquias USING GIN (nombre_norm gin_trgm_ops);

-- Campos de validación en incidentes
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS parroquia_declarada TEXT;      -- lo que dijo la fuente
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS parroquia_geo TEXT;            -- donde cae la coordenada
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS parroquia_consistente BOOLEAN; -- NULL = no evaluable

-- Función: ¿en qué parroquia cae un punto?
CREATE OR REPLACE FUNCTION parroquia_de_punto(p_lat DOUBLE PRECISION, p_lon DOUBLE PRECISION)
RETURNS TABLE(nombre TEXT, municipio municipio_enum, radio_equiv_m DOUBLE PRECISION) AS $$
    SELECT p.nombre, p.municipio, p.radio_equiv_m
    FROM parroquias p
    WHERE ST_Contains(p.geom, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326))
    LIMIT 1;
$$ LANGUAGE sql STABLE;

-- Vista de agregación operativa por parroquia
CREATE OR REPLACE VIEW v_parroquias_resumen AS
SELECT
    p.nombre, p.municipio::text, p.area_km2,
    COUNT(s.id) AS sucesos_abiertos,
    COUNT(s.id) FILTER (WHERE s.urgencia_max = 'CRITICA') AS criticos,
    COALESCE(SUM(s.personas_max), 0) AS personas_max_total
FROM parroquias p
LEFT JOIN sucesos s ON ST_Contains(p.geom, s.geom)
    AND EXISTS (SELECT 1 FROM incidentes i WHERE i.suceso_id = s.id AND i.resuelto_en IS NULL)
GROUP BY p.id ORDER BY criticos DESC, sucesos_abiertos DESC;

INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'parroquias', 0,
        'Migración 07: parroquias admin_level=7 + validación cruzada de coordenadas (issue #3)');
