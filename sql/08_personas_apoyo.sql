-- ============================================================
-- MIGRACIÓN 08 — PERSONAS DESAPARECIDAS + SOLICITUDES DE APOYO
--
-- 1. personas_desaparecidas: dominio DISTINTO al de incidentes
--    (reunificación familiar, no rescate). Reglas de privacidad:
--      * menores de edad: el nombre NUNCA sale en salidas públicas
--        (se publica "Menor de X años, zona Y" — regla estándar
--        humanitaria de protección de la niñez)
--      * teléfonos de contacto solo con key rol emergencia
--    Se vincula al edificio/zona por coordenadas como los incidentes.
--
-- 2. Solicitudes de apoyo/recursos en incidentes:
--      * recursos_solicitados TEXT[]: herramientas, maquinaria,
--        personal (ej. 'retroexcavadora','motosierra','médico',
--        'perro_rescate','grua','generador')
--      * el score de prioridad del suceso NO cambia por pedir
--        recursos — pero la vista de despacho los agrega para que
--        logística sepa QUÉ llevar a dónde.
-- ============================================================

DO $$ BEGIN
    CREATE TYPE estado_persona_enum AS ENUM
        ('BUSCADA','INFO_RECIBIDA','ENCONTRADA_VIVA','ENCONTRADA_FALLECIDA','REUNIFICADA');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS personas_desaparecidas (
    id              BIGSERIAL PRIMARY KEY,
    codigo          TEXT UNIQUE,                    -- PER-000001
    fuente          TEXT NOT NULL,                  -- desaparecidosvenezuela | manual | sosvenezuela...
    id_externo      TEXT,
    nombre          TEXT,                           -- puede ser NULL/enmascarado
    edad            INTEGER CHECK (edad BETWEEN 0 AND 120),
    es_menor        BOOLEAN GENERATED ALWAYS AS (edad IS NOT NULL AND edad < 18) STORED,
    genero          TEXT,
    descripcion     TEXT,                           -- señas, ropa, condición
    zona_texto      TEXT,                           -- "La Guaira · Caraballeda · ..."
    estado          estado_persona_enum NOT NULL DEFAULT 'BUSCADA',
    foto_url        TEXT,
    contacto        TEXT,                           -- SOLO visible con key emergencia
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    geom            geometry(Point, 4326),
    building_id     BIGINT REFERENCES buildings(id) ON DELETE SET NULL,
    parroquia_geo   TEXT,
    -- vínculo opcional con un suceso (ej.: vista por última vez en un derrumbe)
    suceso_id       BIGINT REFERENCES sucesos(id) ON DELETE SET NULL,
    reportado_en    TIMESTAMPTZ,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fuente, id_externo)
);
CREATE INDEX IF NOT EXISTS idx_personas_geom ON personas_desaparecidas USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_personas_estado ON personas_desaparecidas (estado);

CREATE SEQUENCE IF NOT EXISTS personas_codigo_seq;
CREATE OR REPLACE FUNCTION set_codigo_persona() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.codigo IS NULL THEN
        NEW.codigo := 'PER-' || LPAD(nextval('personas_codigo_seq')::text, 6, '0');
    END IF;
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
        -- vincular edificio y parroquia automáticamente
        SELECT b.building_id INTO NEW.building_id
        FROM buscar_edificio_aproximado(NEW.lat, NEW.lon, 100) b;
        SELECT p.nombre INTO NEW.parroquia_geo FROM parroquia_de_punto(NEW.lat, NEW.lon) p;
    END IF;
    NEW.actualizado_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_personas_codigo ON personas_desaparecidas;
CREATE TRIGGER trg_personas_codigo
    BEFORE INSERT OR UPDATE ON personas_desaparecidas
    FOR EACH ROW EXECUTE FUNCTION set_codigo_persona();

-- Vista PÚBLICA: menores enmascarados, sin contacto, coordenada degradada a 3 decimales
CREATE OR REPLACE VIEW v_personas_publico AS
SELECT
    codigo,
    CASE WHEN es_menor THEN 'MENOR DE EDAD (' || edad || ' años) — datos protegidos'
         ELSE COALESCE(nombre, 'Sin identificar') END AS nombre_publico,
    CASE WHEN es_menor THEN NULL ELSE edad END AS edad,
    es_menor, genero, zona_texto, estado::text, parroquia_geo,
    CASE WHEN es_menor THEN NULL ELSE descripcion END AS descripcion,
    round(lat::numeric, 3) AS lat_aprox,
    round(lon::numeric, 3) AS lon_aprox,
    reportado_en, fuente
FROM personas_desaparecidas
ORDER BY es_menor DESC, reportado_en DESC;

-- ------------------------------------------------------------
-- 2. SOLICITUDES DE APOYO/RECURSOS en incidentes
-- ------------------------------------------------------------
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS recursos_solicitados TEXT[];

-- Vista de logística: qué recursos se piden, dónde, agregado por parroquia
CREATE OR REPLACE VIEW v_recursos_solicitados AS
SELECT
    COALESCE(i.parroquia_geo, '(sin parroquia)') AS parroquia,
    r.recurso,
    COUNT(*) AS solicitudes,
    MAX(i.urgencia::text) AS urgencia_max,
    array_agg(DISTINCT i.codigo) AS incidentes
FROM incidentes i, unnest(i.recursos_solicitados) AS r(recurso)
WHERE i.resuelto_en IS NULL
GROUP BY 1, 2
ORDER BY solicitudes DESC;

INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'personas_apoyo', 0,
        'Migración 08: personas_desaparecidas (protección de menores) + recursos_solicitados en incidentes');
