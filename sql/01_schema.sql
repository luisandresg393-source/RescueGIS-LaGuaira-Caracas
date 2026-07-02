-- ============================================================
-- VENEZUELA GIS EMERGENCY — ESQUEMA BASE
-- Terremoto La Guaira / Caracas — 24 junio 2026
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm; -- para búsquedas de texto (nombres de edificios)

-- ------------------------------------------------------------
-- ENUMS
-- ------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE estado_edificio AS ENUM ('SIN_REPORTE', 'REPORTADO', 'VERIFICADO', 'DESCARTADO');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE nivel_prioridad AS ENUM ('BAJA', 'MEDIA', 'ALTA', 'CRITICA');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE municipio_enum AS ENUM ('LA_GUAIRA', 'CARACAS');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ------------------------------------------------------------
-- TABLA PRINCIPAL: EDIFICIOS
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS buildings (
    id              BIGSERIAL PRIMARY KEY,
    osm_id          BIGINT NOT NULL,
    osm_type        VARCHAR(10) NOT NULL DEFAULT 'way',       -- way | relation
    nombre          TEXT,
    tipo            VARCHAR(60),                              -- valor del tag building=*
    tipo_amenity    VARCHAR(60),                              -- amenity=* si existe (hospital, school, etc.)
    municipio       municipio_enum NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    geom            geometry(Point, 4326) NOT NULL,
    pisos           INTEGER,                                  -- building:levels
    material        VARCHAR(60),                              -- building:material
    direccion       TEXT,                                     -- addr:street + addr:housenumber
    tags_extra      JSONB,                                    -- todos los tags OSM originales (respaldo)

    -- Campos operativos de emergencia
    estado          estado_edificio NOT NULL DEFAULT 'SIN_REPORTE',
    prioridad_score INTEGER NOT NULL DEFAULT 0,
    prioridad       nivel_prioridad NOT NULL DEFAULT 'BAJA',
    num_reportes    INTEGER NOT NULL DEFAULT 0,
    personas_atrapadas_estimado INTEGER NOT NULL DEFAULT 0,
    heridos_estimado INTEGER NOT NULL DEFAULT 0,
    ninos_estimado  INTEGER NOT NULL DEFAULT 0,

    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (osm_type, osm_id)
);

CREATE INDEX IF NOT EXISTS idx_buildings_geom ON buildings USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_buildings_municipio ON buildings (municipio);
CREATE INDEX IF NOT EXISTS idx_buildings_estado ON buildings (estado);
CREATE INDEX IF NOT EXISTS idx_buildings_prioridad ON buildings (prioridad);
CREATE INDEX IF NOT EXISTS idx_buildings_prioridad_score ON buildings (prioridad_score DESC);
CREATE INDEX IF NOT EXISTS idx_buildings_nombre_trgm ON buildings USING GIN (nombre gin_trgm_ops);

-- ------------------------------------------------------------
-- CAPAS DE INFRAESTRUCTURA CRÍTICA
-- (hospitales, bomberos, escuelas, gasolineras, refugios, etc.)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS infraestructura (
    id              BIGSERIAL PRIMARY KEY,
    osm_id          BIGINT NOT NULL,
    osm_type        VARCHAR(10) NOT NULL DEFAULT 'node',
    capa            VARCHAR(40) NOT NULL,   -- hospital | bomberos | escuela | gasolinera | refugio | telecom | agua | electricidad
    nombre          TEXT,
    municipio       municipio_enum NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    geom            geometry(Point, 4326) NOT NULL,
    operativo       BOOLEAN,                -- estado operativo reportado (NULL = desconocido)
    tags_extra      JSONB,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (osm_type, osm_id, capa)
);

CREATE INDEX IF NOT EXISTS idx_infra_geom ON infraestructura USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_infra_capa ON infraestructura (capa);
CREATE INDEX IF NOT EXISTS idx_infra_municipio ON infraestructura (municipio);

-- ------------------------------------------------------------
-- CAPAS LINEALES: carreteras, puentes
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vias (
    id              BIGSERIAL PRIMARY KEY,
    osm_id          BIGINT NOT NULL,
    tipo            VARCHAR(30) NOT NULL,    -- carretera | puente
    nombre          TEXT,
    municipio       municipio_enum,
    highway_tag     VARCHAR(40),
    es_puente       BOOLEAN DEFAULT FALSE,
    geom            geometry(LineString, 4326),
    tags_extra      JSONB,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (osm_id, tipo)
);

CREATE INDEX IF NOT EXISTS idx_vias_geom ON vias USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_vias_tipo ON vias (tipo);

-- ------------------------------------------------------------
-- REPORTES CIUDADANOS (vinculados a edificios)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reportes (
    id                  BIGSERIAL PRIMARY KEY,
    building_id         BIGINT REFERENCES buildings(id) ON DELETE SET NULL,
    lat                 DOUBLE PRECISION,           -- por si el reporte no matchea a un edificio exacto
    lon                 DOUBLE PRECISION,
    geom                geometry(Point, 4326),

    fuente              VARCHAR(30) NOT NULL DEFAULT 'telegram', -- telegram | twitter | manual | ong
    reportero_id        TEXT,                        -- id de usuario telegram / handle twitter
    reportero_nombre    TEXT,

    personas_atrapadas  INTEGER DEFAULT 0,
    heridos             INTEGER DEFAULT 0,
    ninos               INTEGER DEFAULT 0,
    fallecidos_reportados INTEGER DEFAULT 0,

    necesidades         TEXT[],                      -- ['agua','medicamentos','rescate', ...]
    descripcion         TEXT,
    foto_url            TEXT,

    verificado          BOOLEAN NOT NULL DEFAULT FALSE,
    verificado_por      TEXT,
    verificado_en       TIMESTAMPTZ,
    estado_verificacion VARCHAR(30) NOT NULL DEFAULT 'PENDIENTE_VERIFICACION',
        -- PENDIENTE_VERIFICACION | VERIFICADO | DESCARTADO | DUPLICADO

    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reportes_building ON reportes (building_id);
CREATE INDEX IF NOT EXISTS idx_reportes_geom ON reportes USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_reportes_estado ON reportes (estado_verificacion);
CREATE INDEX IF NOT EXISTS idx_reportes_creado ON reportes (creado_en DESC);

-- ------------------------------------------------------------
-- TRIGGER: mantener geom sincronizado con lat/lon en buildings
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION sync_geom_from_latlon() RETURNS TRIGGER AS $$
BEGIN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    NEW.actualizado_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_buildings_geom ON buildings;
CREATE TRIGGER trg_buildings_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON buildings
    FOR EACH ROW EXECUTE FUNCTION sync_geom_from_latlon();

DROP TRIGGER IF EXISTS trg_infra_geom ON infraestructura;
CREATE TRIGGER trg_infra_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON infraestructura
    FOR EACH ROW EXECUTE FUNCTION sync_geom_from_latlon();

CREATE OR REPLACE FUNCTION sync_geom_reporte() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reportes_geom ON reportes;
CREATE TRIGGER trg_reportes_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON reportes
    FOR EACH ROW EXECUTE FUNCTION sync_geom_reporte();

-- ------------------------------------------------------------
-- FUNCIÓN: recalcular prioridad de un edificio
-- Prioridad = (Reportes x 30) + (Personas x 2) + (Heridos x 10) + (Niños x 5)
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION recalcular_prioridad(p_building_id BIGINT) RETURNS VOID AS $$
DECLARE
    v_reportes INTEGER;
    v_personas INTEGER;
    v_heridos  INTEGER;
    v_ninos    INTEGER;
    v_score    INTEGER;
    v_nivel    nivel_prioridad;
BEGIN
    SELECT
        COUNT(*),
        COALESCE(SUM(personas_atrapadas), 0),
        COALESCE(SUM(heridos), 0),
        COALESCE(SUM(ninos), 0)
    INTO v_reportes, v_personas, v_heridos, v_ninos
    FROM reportes
    WHERE building_id = p_building_id
      AND estado_verificacion != 'DESCARTADO';

    v_score := (v_reportes * 30) + (v_personas * 2) + (v_heridos * 10) + (v_ninos * 5);

    v_nivel := CASE
        WHEN v_score >= 150 THEN 'CRITICA'
        WHEN v_score >= 80  THEN 'ALTA'
        WHEN v_score >= 30  THEN 'MEDIA'
        ELSE 'BAJA'
    END;

    UPDATE buildings
    SET num_reportes = v_reportes,
        personas_atrapadas_estimado = v_personas,
        heridos_estimado = v_heridos,
        ninos_estimado = v_ninos,
        prioridad_score = v_score,
        prioridad = v_nivel,
        estado = CASE WHEN v_reportes > 0 AND estado = 'SIN_REPORTE' THEN 'REPORTADO' ELSE estado END,
        actualizado_en = now()
    WHERE id = p_building_id;
END;
$$ LANGUAGE plpgsql;

-- Trigger: cada vez que se inserta/actualiza/borra un reporte, recalcular prioridad del edificio
CREATE OR REPLACE FUNCTION trg_recalcular_prioridad() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.building_id IS NOT NULL THEN
            PERFORM recalcular_prioridad(OLD.building_id);
        END IF;
        RETURN OLD;
    ELSE
        IF NEW.building_id IS NOT NULL THEN
            PERFORM recalcular_prioridad(NEW.building_id);
        END IF;
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reportes_prioridad ON reportes;
CREATE TRIGGER trg_reportes_prioridad
    AFTER INSERT OR UPDATE OR DELETE ON reportes
    FOR EACH ROW EXECUTE FUNCTION trg_recalcular_prioridad();

-- ------------------------------------------------------------
-- VISTA: resumen operativo por edificio (para el mapa / bot)
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_buildings_resumen AS
SELECT
    b.id, b.osm_id, b.nombre, b.tipo, b.municipio, b.lat, b.lon,
    b.pisos, b.material, b.estado, b.prioridad, b.prioridad_score,
    b.num_reportes, b.personas_atrapadas_estimado, b.heridos_estimado, b.ninos_estimado,
    (SELECT MAX(creado_en) FROM reportes r WHERE r.building_id = b.id) AS ultimo_reporte_en,
    (SELECT array_agg(DISTINCT unnest) FROM reportes r, unnest(r.necesidades) WHERE r.building_id = b.id) AS necesidades_agregadas
FROM buildings b;

-- ------------------------------------------------------------
-- TABLA DE CONTROL DE IMPORTACIÓN (para trazabilidad del ingest)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_log (
    id BIGSERIAL PRIMARY KEY,
    fuente VARCHAR(50),
    municipio municipio_enum,
    capa VARCHAR(40),
    registros_importados INTEGER,
    ejecutado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    notas TEXT
);
