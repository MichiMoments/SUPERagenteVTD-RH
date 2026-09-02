"""Modelo de datos de una citación jurisdiccional.

Dataclass simple, no Pydantic: aunque pydantic llega hoy de rebote vía
langchain-core, no es una dependencia directa de este proyecto, y el resto de
otrosi/ tampoco lo usa.
"""

from dataclasses import dataclass
from datetime import date, datetime

ESTADOS = ("pendiente", "atendida", "vencida")


@dataclass
class Citacion:
    """Una citación jurisdiccional registrada por Gestión Humana."""

    persona_citada: str
    tipo_citacion: str
    fecha_citacion: date
    autoridad: str
    registrado_por: str
    estado: str = "pendiente"
    id: int | None = None
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None

    def __post_init__(self):
        if self.estado not in ESTADOS:
            raise ValueError(
                f"«{self.estado}» no es un estado válido. Los estados son: "
                + ", ".join(ESTADOS)
            )


def desde_fila(fila: tuple) -> Citacion:
    """Convierte una fila de psycopg2 (orden de columnas de schema.sql) en Citacion."""
    (id_, persona_citada, tipo_citacion, fecha_citacion, autoridad,
     estado, registrado_por, creado_en, actualizado_en) = fila
    return Citacion(
        id=id_,
        persona_citada=persona_citada,
        tipo_citacion=tipo_citacion,
        fecha_citacion=fecha_citacion,
        autoridad=autoridad,
        estado=estado,
        registrado_por=registrado_por,
        creado_en=creado_en,
        actualizado_en=actualizado_en,
    )
