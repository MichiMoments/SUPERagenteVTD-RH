from typing import Literal

from langgraph.graph import MessagesState
from pydantic import BaseModel


class Intencion(BaseModel):
    texto: str
    categoria: Literal["otrosi", "citaciones", "social", "fuera_de_alcance"]


class Triaje(BaseModel):
    intenciones: list[Intencion]
    fuera_de_alcance: bool


class EstadoAgente(MessagesState):
    intenciones: list[Intencion]
    fuera_de_alcance: bool
