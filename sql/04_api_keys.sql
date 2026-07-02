-- ============================================================
-- MIGRACIÓN 04 — API PROPIA DE RescueGIS
--
-- Tabla de API keys con roles, para que cualquier cuerpo de
-- emergencia / ONG / plataforma aliada consuma o aporte datos.
--
-- Roles:
--   'emergencia'  cuerpos de rescate: lectura PRECISA + exportes GeoJSON/CSV
--                 + pueden verificar/descartar incidentes
--   'ingesta'     bots/plataformas que APORTAN reportes (POST)
--   'socio'       plataformas federadas: lectura precisa, sin verificación
--   'admin'       gestión de keys
--
-- La key NUNCA se guarda en claro: solo su hash SHA-256.
-- ============================================================

CREATE TABLE IF NOT EXISTS api_keys (
    id              BIGSERIAL PRIMARY KEY,
    key_hash        TEXT NOT NULL UNIQUE,          -- sha256 hex de la key
    nombre          TEXT NOT NULL,                 -- ej. "Bomberos de Caracas"
    organizacion    TEXT,
    contacto        TEXT,                          -- email/teléfono del responsable
    rol             TEXT NOT NULL DEFAULT 'socio'
                    CHECK (rol IN ('emergencia','ingesta','socio','admin')),
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_min  INTEGER NOT NULL DEFAULT 120,  -- peticiones/minuto permitidas
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_uso_en   TIMESTAMPTZ,
    notas           TEXT
);

-- Log de uso (auditoría: quién consultó/aportó qué y cuándo)
CREATE TABLE IF NOT EXISTS api_log (
    id           BIGSERIAL PRIMARY KEY,
    api_key_id   BIGINT REFERENCES api_keys(id) ON DELETE SET NULL,
    ip           TEXT,
    metodo       TEXT,
    ruta         TEXT,
    status       INTEGER,
    detalle      TEXT,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_api_log_key ON api_log (api_key_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_api_log_creado ON api_log (creado_en DESC);

-- Campos de despacho operativo en incidentes (para que un cuerpo de
-- emergencia marque qué está atendiendo — evita duplicar esfuerzos)
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS asignado_a TEXT;          -- nombre del cuerpo que lo tomó
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS asignado_en TIMESTAMPTZ;
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS resuelto_en TIMESTAMPTZ;
ALTER TABLE incidentes ADD COLUMN IF NOT EXISTS resultado TEXT;           -- rescatados | sin_hallazgo | falso | trasladado

INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'api_propia', 0,
        'Migración 04: api_keys + api_log + campos de despacho (asignado_a/resuelto_en/resultado)');
