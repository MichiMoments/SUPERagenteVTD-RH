-- Tabla de citaciones jurisdiccionales.
-- estado usa CHECK en vez de un tipo ENUM de Postgres: agregar un estado
-- nuevo no requiere ALTER TYPE, solo cambiar este CHECK y
-- citaciones/models.py::ESTADOS.

CREATE TABLE IF NOT EXISTS citaciones (
    id                SERIAL PRIMARY KEY,
    persona_citada    TEXT NOT NULL,
    tipo_citacion     TEXT NOT NULL,
    fecha_citacion    DATE NOT NULL,
    autoridad         TEXT NOT NULL,
    estado            TEXT NOT NULL DEFAULT 'pendiente'
                      CHECK (estado IN ('pendiente', 'atendida', 'vencida')),
    registrado_por    TEXT NOT NULL,
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_citaciones_estado_fecha
    ON citaciones (estado, fecha_citacion);

CREATE INDEX IF NOT EXISTS idx_citaciones_tipo
    ON citaciones (tipo_citacion);
