-- ============================================================
-- MIGRACIÓN 06 — NOTIFICACIONES PUSH A RESCATISTAS (issue #2)
--
-- Suscripciones: qué chat de Telegram recibe avisos de sucesos
-- críticos, con filtro opcional por municipio o radio geográfico.
-- Notificaciones enviadas: para no repetir avisos del mismo suceso
-- (se re-avisa solo si la urgencia sube o los reportes crecen mucho).
-- ============================================================

CREATE TABLE IF NOT EXISTS suscripciones_campo (
    id            BIGSERIAL PRIMARY KEY,
    chat_id       BIGINT NOT NULL UNIQUE,     -- chat de Telegram del rescatista/cuerpo
    nombre        TEXT,                       -- "Unidad R-7 Bomberos Caracas"
    municipio     municipio_enum,             -- NULL = todos
    -- filtro por radio (opcional, prioridad sobre municipio si está definido)
    centro_lat    DOUBLE PRECISION,
    centro_lon    DOUBLE PRECISION,
    radio_m       DOUBLE PRECISION,
    urgencia_min  urgencia_enum NOT NULL DEFAULT 'CRITICA',  -- desde qué urgencia avisar
    activa        BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notificaciones_enviadas (
    id            BIGSERIAL PRIMARY KEY,
    suscripcion_id BIGINT NOT NULL REFERENCES suscripciones_campo(id) ON DELETE CASCADE,
    suceso_id     BIGINT NOT NULL REFERENCES sucesos(id) ON DELETE CASCADE,
    -- estado del suceso cuando se avisó (para decidir si re-avisar)
    urgencia      urgencia_enum,
    num_reportes  INTEGER,
    enviado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (suscripcion_id, suceso_id, urgencia)  -- re-aviso solo si sube la urgencia
);

CREATE INDEX IF NOT EXISTS idx_notif_suceso ON notificaciones_enviadas (suceso_id);

INSERT INTO import_log (fuente, capa, registros_importados, notas)
VALUES ('sistema', 'notificaciones', 0,
        'Migración 06: suscripciones_campo + notificaciones_enviadas (push Telegram a rescatistas)');
