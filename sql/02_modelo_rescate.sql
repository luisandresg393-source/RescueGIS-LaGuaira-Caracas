-- ============================================================
-- MODELO DE RESCATE — sobre la base GIS de 96,634 edificios
-- La Guaira + Caracas — Terremoto 24 junio 2026
-- ============================================================

-- ------------------------------------------------------------
-- 0. LIMPIEZA: retiramos el modelo anterior de "reportes" (0 registros,
--    nunca se usó) para reemplazarlo por incidentes + evidencias.
-- ------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_reportes_prioridad ON reportes;
DROP TRIGGER IF EXISTS trg_reportes_geom ON reportes;
DROP FUNCTION IF EXISTS trg_recalcular_prioridad() CASCADE;
DROP FUNCTION IF EXISTS sync_geom_reporte() CASCADE;
DROP FUNCTION IF EXISTS recalcular_prioridad(BIGINT) CASCADE;
DROP VIEW IF EXISTS v_buildings_resumen;
DROP TABLE IF EXISTS reportes CASCADE;

-- ------------------------------------------------------------
-- 1. ESTADO DE RESCATE EN EDIFICIOS
-- ------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE estado_rescate_enum AS ENUM (
        'SIN_REPORTE',
        'SOS',
        'PERSONAS_ATRAPADAS',
        'DANO_ESTRUCTURAL',
        'EVACUADO',
        'VERIFICADO'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE buildings ADD COLUMN IF NOT EXISTS estado_rescate estado_rescate_enum NOT NULL DEFAULT 'SIN_REPORTE';
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS es_infraestructura_critica BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS dist_hospital_m DOUBLE PRECISION;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS dist_bomberos_m DOUBLE PRECISION;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS horas_sin_ayuda DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS codigo_corto TEXT; -- ej. LG-004982 / CC-012345

CREATE INDEX IF NOT EXISTS idx_buildings_estado_rescate ON buildings (estado_rescate);

-- Código corto legible por humanos: prefijo por municipio + id
UPDATE buildings SET codigo_corto =
    CASE municipio WHEN 'LA_GUAIRA' THEN 'LG-' ELSE 'CC-' END || LPAD(id::text, 6, '0')
WHERE codigo_corto IS NULL;

CREATE OR REPLACE FUNCTION set_codigo_corto() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.codigo_corto IS NULL THEN
        NEW.codigo_corto := CASE NEW.municipio WHEN 'LA_GUAIRA' THEN 'LG-' ELSE 'CC-' END || LPAD(NEW.id::text, 6, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_buildings_codigo ON buildings;
CREATE TRIGGER trg_buildings_codigo
    BEFORE INSERT ON buildings
    FOR EACH ROW EXECUTE FUNCTION set_codigo_corto();

-- Marca de infraestructura crítica: hospital, escuela, bomberos, refugio (si están en `buildings` por tag amenity)
UPDATE buildings SET es_infraestructura_critica = TRUE
WHERE tipo IN ('hospital','school','fire_station')
   OR tipo_amenity IN ('hospital','clinic','school','university','college','fire_station','shelter','social_facility');

-- ------------------------------------------------------------
-- 2. TABLA DE INCIDENTES (reportes ciudadanos / Telegram / ONG)
-- ------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE urgencia_enum AS ENUM ('BAJA','MEDIA','ALTA','CRITICA');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE estado_verificacion_enum AS ENUM ('PENDIENTE_VERIFICACION','VERIFICADO','DESCARTADO','DUPLICADO');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS incidentes (
    id              BIGSERIAL PRIMARY KEY,
    codigo          TEXT UNIQUE,                      -- INC-000001 (asignado automáticamente)

    tipo            TEXT NOT NULL DEFAULT 'ATRAPADOS', -- ATRAPADOS | HERIDOS | DANO_ESTRUCTURAL | NECESIDAD_BASICA | FALLECIDO | OTRO
    descripcion     TEXT,

    personas        INTEGER NOT NULL DEFAULT 0,
    heridos         INTEGER NOT NULL DEFAULT 0,
    ninos           INTEGER NOT NULL DEFAULT 0,
    fallecidos      INTEGER NOT NULL DEFAULT 0,
    necesidades     TEXT[],                           -- ['agua','medicamentos','rescate',...]

    urgencia        urgencia_enum NOT NULL DEFAULT 'MEDIA',   -- clasificación inicial (reportero / IA / operador)
    fuente          TEXT NOT NULL DEFAULT 'telegram',  -- telegram | twitter | manual | ong | llamada
    reportero_id    TEXT,
    reportero_nombre TEXT,
    telefono_contacto TEXT,

    confirmado      BOOLEAN NOT NULL DEFAULT FALSE,
    estado_verificacion estado_verificacion_enum NOT NULL DEFAULT 'PENDIENTE_VERIFICACION',
    verificado_por  TEXT,
    verificado_en   TIMESTAMPTZ,

    -- Ubicación del reporte (puede no coincidir exacto con el centroide del edificio)
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    geom            geometry(Point, 4326),

    -- Vínculo con el edificio (asignado automáticamente por cercanía, o manualmente)
    building_id     BIGINT REFERENCES buildings(id) ON DELETE SET NULL,
    building_match_metodo TEXT,          -- 'auto_150m' | 'manual' | 'sin_match'
    building_match_distancia_m DOUBLE PRECISION,

    fecha           TIMESTAMPTZ NOT NULL DEFAULT now(),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incidentes_geom ON incidentes USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_incidentes_building ON incidentes (building_id);
CREATE INDEX IF NOT EXISTS idx_incidentes_estado ON incidentes (estado_verificacion);
CREATE INDEX IF NOT EXISTS idx_incidentes_urgencia ON incidentes (urgencia);
CREATE INDEX IF NOT EXISTS idx_incidentes_fecha ON incidentes (fecha DESC);

-- Código legible INC-000001
CREATE SEQUENCE IF NOT EXISTS incidentes_codigo_seq;
CREATE OR REPLACE FUNCTION set_codigo_incidente() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.codigo IS NULL THEN
        NEW.codigo := 'INC-' || LPAD(nextval('incidentes_codigo_seq')::text, 6, '0');
    END IF;
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    END IF;
    NEW.actualizado_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_incidentes_codigo ON incidentes;
CREATE TRIGGER trg_incidentes_codigo
    BEFORE INSERT ON incidentes
    FOR EACH ROW EXECUTE FUNCTION set_codigo_incidente();

CREATE OR REPLACE FUNCTION sync_geom_incidente_update() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    END IF;
    NEW.actualizado_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_incidentes_geom_update ON incidentes;
CREATE TRIGGER trg_incidentes_geom_update
    BEFORE UPDATE OF lat, lon ON incidentes
    FOR EACH ROW EXECUTE FUNCTION sync_geom_incidente_update();

-- ------------------------------------------------------------
-- 3. MATCHING AUTOMÁTICO: GPS del incidente -> edificio más cercano
--    Radio máximo: 150 metros. Si no hay edificio cerca, queda SIN match
--    (nunca se fuerza una asignación incierta).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION buscar_edificio_cercano(p_lat DOUBLE PRECISION, p_lon DOUBLE PRECISION, p_radio_m INTEGER DEFAULT 150)
RETURNS TABLE(building_id BIGINT, distancia_m DOUBLE PRECISION) AS $$
    SELECT b.id,
           ST_Distance(b.geom::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography) AS distancia_m
    FROM buildings b
    WHERE ST_DWithin(b.geom::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography, p_radio_m)
    ORDER BY b.geom <-> ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)
    LIMIT 1;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION trg_asignar_edificio_incidente() RETURNS TRIGGER AS $$
DECLARE
    v_match RECORD;
BEGIN
    IF NEW.building_id IS NULL AND NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        SELECT * INTO v_match FROM buscar_edificio_cercano(NEW.lat, NEW.lon, 150);
        IF v_match.building_id IS NOT NULL THEN
            NEW.building_id := v_match.building_id;
            NEW.building_match_metodo := 'auto_150m';
            NEW.building_match_distancia_m := v_match.distancia_m;
        ELSE
            NEW.building_match_metodo := 'sin_match';
        END IF;
    ELSIF NEW.building_id IS NOT NULL AND NEW.building_match_metodo IS NULL THEN
        NEW.building_match_metodo := 'manual';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_incidentes_matching ON incidentes;
CREATE TRIGGER trg_incidentes_matching
    BEFORE INSERT ON incidentes
    FOR EACH ROW EXECUTE FUNCTION trg_asignar_edificio_incidente();

-- ------------------------------------------------------------
-- 4. TABLA DE EVIDENCIAS (fotos, videos, confirmaciones cruzadas)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidencias (
    id              BIGSERIAL PRIMARY KEY,
    incidente_id    BIGINT NOT NULL REFERENCES incidentes(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL DEFAULT 'foto',   -- foto | video | audio | testimonio | gps_confirmado
    url_archivo     TEXT,
    hora            TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario         TEXT,
    nivel_confianza INTEGER NOT NULL DEFAULT 50 CHECK (nivel_confianza BETWEEN 0 AND 100),
    notas           TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evidencias_incidente ON evidencias (incidente_id);

-- ------------------------------------------------------------
-- 5. PRECÓMPUTO: distancia a hospital y bomberos más cercano
--    (KNN indexado con <->, se recalcula solo cuando se necesite —
--     ver script Python de precomputo para lotes de 96k edificios)
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION actualizar_distancias_infra_criticas(p_building_id BIGINT) RETURNS VOID AS $$
DECLARE
    v_geom geometry;
    v_dist_hosp DOUBLE PRECISION;
    v_dist_bomb DOUBLE PRECISION;
BEGIN
    SELECT geom INTO v_geom FROM buildings WHERE id = p_building_id;

    SELECT ST_Distance(i.geom::geography, v_geom::geography) INTO v_dist_hosp
    FROM infraestructura i WHERE i.capa = 'hospital'
    ORDER BY i.geom <-> v_geom LIMIT 1;

    SELECT ST_Distance(i.geom::geography, v_geom::geography) INTO v_dist_bomb
    FROM infraestructura i WHERE i.capa = 'bomberos'
    ORDER BY i.geom <-> v_geom LIMIT 1;

    UPDATE buildings SET dist_hospital_m = v_dist_hosp, dist_bomberos_m = v_dist_bomb
    WHERE id = p_building_id;
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------
-- 6. FÓRMULA DE PRIORIDAD (transparente, por componentes, ajustable)
--
--    score =
--        (personas_atrapadas   x 3)
--      + (heridos              x 5)
--      + (fallecidos_reportados x 2)   -- para triage de recursos, no jerarquiza vidas
--      + LEAST(horas_sin_ayuda, 48) x 1     -- urgencia crece con el tiempo, tope 48h
--      + (reportes_confirmados x 20)
--      + (reportes_pendientes  x 5)
--      + bonus_infraestructura_critica (15 si el edificio ES infra crítica)
--      + bonus_cercania (10 si tiene hospital/bomberos a <200m -> más viable enviar equipo rápido)
--
--    Niveles:
--      🔴 CRITICA >= 120
--      🟠 ALTA    >= 60
--      🟡 MEDIA   >= 20
--      🟢 BAJA    <  20
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION recalcular_prioridad_edificio(p_building_id BIGINT) RETURNS VOID AS $$
DECLARE
    v_personas INTEGER;
    v_heridos INTEGER;
    v_fallecidos INTEGER;
    v_ninos INTEGER;
    v_confirmados INTEGER;
    v_pendientes INTEGER;
    v_primer_reporte TIMESTAMPTZ;
    v_horas_sin_ayuda DOUBLE PRECISION;
    v_bonus_infra INTEGER := 0;
    v_bonus_cercania INTEGER := 0;
    v_score INTEGER;
    v_nivel nivel_prioridad;
    v_estado_rescate estado_rescate_enum;
    v_es_critica BOOLEAN;
    v_dist_hosp DOUBLE PRECISION;
    v_dist_bomb DOUBLE PRECISION;
    v_max_urgencia urgencia_enum;
BEGIN
    SELECT
        COALESCE(SUM(personas), 0),
        COALESCE(SUM(heridos), 0),
        COALESCE(SUM(fallecidos), 0),
        COALESCE(SUM(ninos), 0),
        COUNT(*) FILTER (WHERE estado_verificacion = 'VERIFICADO'),
        COUNT(*) FILTER (WHERE estado_verificacion = 'PENDIENTE_VERIFICACION'),
        MIN(fecha),
        MAX(urgencia)
    INTO v_personas, v_heridos, v_fallecidos, v_ninos, v_confirmados, v_pendientes, v_primer_reporte, v_max_urgencia
    FROM incidentes
    WHERE building_id = p_building_id
      AND estado_verificacion != 'DESCARTADO';

    IF v_primer_reporte IS NULL THEN
        v_horas_sin_ayuda := 0;
    ELSE
        v_horas_sin_ayuda := LEAST(EXTRACT(EPOCH FROM (now() - v_primer_reporte)) / 3600.0, 48);
    END IF;

    SELECT es_infraestructura_critica, dist_hospital_m, dist_bomberos_m
    INTO v_es_critica, v_dist_hosp, v_dist_bomb
    FROM buildings WHERE id = p_building_id;

    IF v_es_critica THEN
        v_bonus_infra := 15;
    END IF;

    IF (v_dist_hosp IS NOT NULL AND v_dist_hosp < 200) OR (v_dist_bomb IS NOT NULL AND v_dist_bomb < 200) THEN
        v_bonus_cercania := 10;
    END IF;

    v_score := ROUND(
        (v_personas * 3)
      + (v_heridos * 5)
      + (v_fallecidos * 2)
      + (v_horas_sin_ayuda * 1)
      + (v_confirmados * 20)
      + (v_pendientes * 5)
      + v_bonus_infra
      + v_bonus_cercania
    );

    v_nivel := CASE
        WHEN v_score >= 120 THEN 'CRITICA'
        WHEN v_score >= 60  THEN 'ALTA'
        WHEN v_score >= 20  THEN 'MEDIA'
        ELSE 'BAJA'
    END;

    -- Estado de rescate: no sobreescribimos EVACUADO/VERIFICADO puestos manualmente por un
    -- coordinador, salvo que llegue un nuevo reporte de personas atrapadas.
    SELECT estado_rescate INTO v_estado_rescate FROM buildings WHERE id = p_building_id;
    IF v_estado_rescate NOT IN ('EVACUADO', 'VERIFICADO') THEN
        IF v_personas > 0 OR v_max_urgencia = 'CRITICA' THEN
            v_estado_rescate := 'PERSONAS_ATRAPADAS';
        ELSIF v_confirmados > 0 THEN
            v_estado_rescate := 'DANO_ESTRUCTURAL';
        ELSIF v_pendientes > 0 THEN
            v_estado_rescate := 'SOS';
        ELSE
            v_estado_rescate := 'SIN_REPORTE';
        END IF;
    END IF;

    UPDATE buildings SET
        prioridad_score = v_score,
        prioridad = v_nivel,
        num_reportes = v_confirmados + v_pendientes,
        personas_atrapadas_estimado = v_personas,
        heridos_estimado = v_heridos,
        ninos_estimado = v_ninos,
        horas_sin_ayuda = v_horas_sin_ayuda,
        estado_rescate = v_estado_rescate,
        actualizado_en = now()
    WHERE id = p_building_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_recalcular_prioridad_incidente() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.building_id IS NOT NULL THEN PERFORM recalcular_prioridad_edificio(OLD.building_id); END IF;
        RETURN OLD;
    ELSE
        IF NEW.building_id IS NOT NULL THEN PERFORM recalcular_prioridad_edificio(NEW.building_id); END IF;
        IF TG_OP = 'UPDATE' AND OLD.building_id IS DISTINCT FROM NEW.building_id AND OLD.building_id IS NOT NULL THEN
            PERFORM recalcular_prioridad_edificio(OLD.building_id);
        END IF;
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_incidentes_prioridad ON incidentes;
CREATE TRIGGER trg_incidentes_prioridad
    AFTER INSERT OR UPDATE OR DELETE ON incidentes
    FOR EACH ROW EXECUTE FUNCTION trg_recalcular_prioridad_incidente();

-- Evidencias también disparan recálculo (afectan confianza/estado del incidente relacionado)
CREATE OR REPLACE FUNCTION trg_evidencia_recalcula() RETURNS TRIGGER AS $$
DECLARE
    v_building_id BIGINT;
BEGIN
    SELECT building_id INTO v_building_id FROM incidentes WHERE id = COALESCE(NEW.incidente_id, OLD.incidente_id);
    IF v_building_id IS NOT NULL THEN
        PERFORM recalcular_prioridad_edificio(v_building_id);
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_evidencias_recalculo ON evidencias;
CREATE TRIGGER trg_evidencias_recalculo
    AFTER INSERT OR UPDATE OR DELETE ON evidencias
    FOR EACH ROW EXECUTE FUNCTION trg_evidencia_recalcula();

-- ------------------------------------------------------------
-- 7. VISTA OPERATIVA: panel de coordinación (mapa + bot)
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_edificios_prioridad AS
SELECT
    b.id, b.codigo_corto, b.osm_id, b.nombre, b.tipo, b.municipio, b.lat, b.lon,
    b.pisos, b.material, b.estado_rescate, b.prioridad, b.prioridad_score,
    b.num_reportes, b.personas_atrapadas_estimado, b.heridos_estimado, b.ninos_estimado,
    b.horas_sin_ayuda, b.es_infraestructura_critica, b.dist_hospital_m, b.dist_bomberos_m,
    CASE b.prioridad
        WHEN 'CRITICA' THEN '🔴'
        WHEN 'ALTA' THEN '🟠'
        WHEN 'MEDIA' THEN '🟡'
        ELSE '🟢'
    END AS icono_prioridad,
    (SELECT MAX(fecha) FROM incidentes i WHERE i.building_id = b.id) AS ultimo_reporte_en,
    (SELECT COUNT(*) FROM incidentes i WHERE i.building_id = b.id AND i.estado_verificacion != 'DESCARTADO') AS total_incidentes,
    (SELECT array_agg(DISTINCT n) FROM incidentes i, unnest(i.necesidades) n WHERE i.building_id = b.id) AS necesidades_agregadas
FROM buildings b
WHERE b.estado_rescate != 'SIN_REPORTE' OR b.num_reportes > 0
ORDER BY b.prioridad_score DESC;

-- Vista de incidentes recientes con toda la evidencia asociada (para el bot Telegram / panel)
CREATE OR REPLACE VIEW v_incidentes_detalle AS
SELECT
    inc.id, inc.codigo, inc.tipo, inc.descripcion, inc.personas, inc.heridos, inc.ninos, inc.fallecidos,
    inc.necesidades, inc.urgencia, inc.fuente, inc.reportero_nombre, inc.confirmado, inc.estado_verificacion,
    inc.lat, inc.lon, inc.fecha,
    inc.building_id, b.codigo_corto AS building_codigo, b.nombre AS building_nombre, b.municipio,
    inc.building_match_metodo, inc.building_match_distancia_m,
    (SELECT COUNT(*) FROM evidencias e WHERE e.incidente_id = inc.id) AS num_evidencias,
    (SELECT ROUND(AVG(nivel_confianza)) FROM evidencias e WHERE e.incidente_id = inc.id) AS confianza_promedio
FROM incidentes inc
LEFT JOIN buildings b ON b.id = inc.building_id
ORDER BY inc.fecha DESC;

-- ------------------------------------------------------------
-- 8. Registrar migración en el log
-- ------------------------------------------------------------
INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'modelo_rescate', 0, 'Migración 02: incidentes + evidencias + estado_rescate + fórmula de prioridad');
