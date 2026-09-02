"""Herramientas LangChain que envuelven las funciones puras de citaciones/.

Mismo patrón que otrosi/tools.py: cada herramienta tiene un docstring en
español que Gemini usa para decidir cuál invocar, y ninguna deja subir una
excepción al agente.

``crear_herramientas(sender)`` devuelve las herramientas con notificación
al canal de Teams cuando se registra una citación.
"""

import logging
import os
from datetime import date
from html import escape as _html_escape

from langchain_core.tools import tool

from citaciones import crud
from citaciones.models import ESTADOS, Citacion

logger = logging.getLogger(__name__)


@tool
def registrar_citacion(persona_citada: str, tipo_citacion: str, fecha_citacion: str,
                        autoridad: str, registrado_por: str) -> dict:
    """Registra una nueva citación jurisdiccional en la base de datos.

    Úsala cuando el usuario quiera dejar constancia de una citación,
    emplazamiento o notificación judicial recibida para una persona.
    Pregunta los datos que falten antes de llamar la herramienta; no
    inventes valores.

    Args:
        persona_citada: Nombre completo de la persona o entidad citada.
        tipo_citacion: Tipo de citación (ej: 'Laboral', 'Civil', 'Embargo').
        fecha_citacion: Fecha de la citación, formato 'YYYY-MM-DD'.
        autoridad: Autoridad o jurisdicción que emite la citación (ej: 'Juzgado 3 Laboral de Bogotá').
        registrado_por: Nombre de quien registra la citación.
    """
    try:
        fecha = date.fromisoformat(fecha_citacion)
    except ValueError:
        return {"error": f"Fecha inválida: '{fecha_citacion}'. Usa YYYY-MM-DD."}

    try:
        citacion = Citacion(
            persona_citada=persona_citada,
            tipo_citacion=tipo_citacion,
            fecha_citacion=fecha,
            autoridad=autoridad,
            registrado_por=registrado_por,
        )
    except ValueError as e:
        return {"error": str(e)}

    guardada = crud.crear_citacion(citacion)
    return {
        "id": guardada.id,
        "mensaje": f"Citación #{guardada.id} registrada para {guardada.persona_citada}.",
    }


@tool
def consultar_citaciones(estado: str = "", tipo_citacion: str = "",
                          desde: str = "", hasta: str = "") -> str:
    """Consulta citaciones existentes, con filtros opcionales.

    Úsala cuando el usuario quiera ver el listado de citaciones registradas,
    filtrando por estado, tipo o un rango de fechas. Deja vacío cualquier
    filtro que el usuario no haya pedido.

    Args:
        estado: 'pendiente', 'atendida' o 'vencida'. Vacío para no filtrar.
        tipo_citacion: Tipo exacto de citación. Vacío para no filtrar.
        desde: Fecha mínima de la citación, 'YYYY-MM-DD'. Vacío para no filtrar.
        hasta: Fecha máxima de la citación, 'YYYY-MM-DD'. Vacío para no filtrar.
    """
    if estado and estado not in ESTADOS:
        return f"Error: «{estado}» no es un estado válido. Los estados son: {', '.join(ESTADOS)}."

    try:
        fecha_desde = date.fromisoformat(desde) if desde else None
        fecha_hasta = date.fromisoformat(hasta) if hasta else None
    except ValueError:
        return "Error: las fechas deben ir en formato YYYY-MM-DD."

    resultados = crud.buscar_citaciones(
        estado=estado or None,
        tipo_citacion=tipo_citacion or None,
        desde=fecha_desde,
        hasta=fecha_hasta,
    )
    if not resultados:
        return "No se encontraron citaciones con esos filtros."

    lineas = [
        f"- #{c.id} — *{c.persona_citada}* ({c.tipo_citacion}), {c.fecha_citacion.isoformat()}, "
        f"{c.autoridad} — estado: **{c.estado}**"
        for c in resultados
    ]
    return "\n".join(lineas)


@tool
def obtener_citacion(id_citacion: int) -> dict:
    """Obtiene el detalle completo de una citación por su id.

    Úsala cuando el usuario pida ver los datos completos de una citación
    específica, mencionando su número.

    Args:
        id_citacion: Identificador numérico de la citación.
    """
    citacion = crud.obtener_citacion(id_citacion)
    if citacion is None:
        return {"error": f"No existe ninguna citación con id {id_citacion}."}

    return {
        "id": citacion.id,
        "persona_citada": citacion.persona_citada,
        "tipo_citacion": citacion.tipo_citacion,
        "fecha_citacion": citacion.fecha_citacion.isoformat(),
        "autoridad": citacion.autoridad,
        "estado": citacion.estado,
        "registrado_por": citacion.registrado_por,
        "creado_en": citacion.creado_en.isoformat() if citacion.creado_en else None,
    }


@tool
def actualizar_citacion(id_citacion: int, nuevo_estado: str) -> dict:
    """Actualiza el estado de una citación existente.

    Úsala cuando el usuario indique que una citación ya fue atendida,
    venció, o cambió de estado.

    Args:
        id_citacion: Identificador numérico de la citación.
        nuevo_estado: 'pendiente', 'atendida' o 'vencida'.
    """
    if nuevo_estado not in ESTADOS:
        return {
            "error": f"«{nuevo_estado}» no es un estado válido. "
                     f"Los estados son: {', '.join(ESTADOS)}."
        }

    actualizada = crud.actualizar_estado(id_citacion, nuevo_estado)
    if actualizada is None:
        return {"error": f"No existe ninguna citación con id {id_citacion}."}

    return {
        "id": actualizada.id,
        "estado": actualizada.estado,
        "mensaje": f"Citación #{actualizada.id} actualizada a estado «{actualizada.estado}».",
    }


todas = [registrar_citacion, consultar_citaciones, obtener_citacion, actualizar_citacion]


def _construir_html_citacion(citacion):
    """HTML compartido entre notificación inicial y actualización del mensaje."""
    return (
        f"<b>Citación #{citacion.id}</b><br/>"
        f"<b>Persona citada:</b> {_html_escape(citacion.persona_citada)}<br/>"
        f"<b>Tipo:</b> {_html_escape(citacion.tipo_citacion)}<br/>"
        f"<b>Fecha:</b> {_html_escape(str(citacion.fecha_citacion))}<br/>"
        f"<b>Autoridad:</b> {_html_escape(citacion.autoridad)}<br/>"
        f"<b>Registrada por:</b> {_html_escape(citacion.registrado_por)}<br/>"
        f"<b>Estado:</b> {_html_escape(citacion.estado)}"
    )


def _notificar_canal(sender, citacion_guardada):
    from teams_core.domain.models import ConversationRef, ConversationKind, OutboundMessage

    team_id = os.environ.get("TEAMS_CHANNEL_TEAM_ID")
    channel_id = os.environ.get("TEAMS_CHANNEL_ID")
    if not team_id or not channel_id:
        logger.info("Notificación al canal omitida: faltan TEAMS_CHANNEL_TEAM_ID o TEAMS_CHANNEL_ID")
        return
    try:
        conv = ConversationRef(
            kind=ConversationKind.CHANNEL, team_id=team_id, channel_id=channel_id,
        )
        html = _construir_html_citacion(citacion_guardada)
        msg_id = sender.send(conv, OutboundMessage(body_html=html))
        crud.guardar_message_id(citacion_guardada.id, msg_id)
        logger.info("Notificación de citación #%s enviada al canal (msg_id=%s)",
                     citacion_guardada.id, msg_id)
    except Exception as e:
        logger.warning("No se pudo enviar notificación al canal: %s", e)


def _actualizar_mensaje_canal(sender, citacion):
    team_id = os.environ.get("TEAMS_CHANNEL_TEAM_ID")
    channel_id = os.environ.get("TEAMS_CHANNEL_ID")
    if not team_id or not channel_id or not citacion.message_id:
        return
    try:
        path = f"/teams/{team_id}/channels/{channel_id}/messages/{citacion.message_id}"
        html = _construir_html_citacion(citacion)
        sender._client.request("PATCH", path, json={
            "body": {"contentType": "html", "content": html},
        })
        logger.info("Mensaje del canal actualizado para citación #%s", citacion.id)
    except Exception as e:
        logger.warning("No se pudo actualizar mensaje del canal: %s", e)


def crear_herramientas(sender=None):
    """Devuelve las herramientas de citaciones, con notificación al canal si hay sender."""
    if sender is None:
        return list(todas)

    @tool
    def registrar_citacion(persona_citada: str, tipo_citacion: str, fecha_citacion: str,
                            autoridad: str, registrado_por: str) -> dict:
        """Registra una nueva citación jurisdiccional en la base de datos.

        Úsala cuando el usuario quiera dejar constancia de una citación,
        emplazamiento o notificación judicial recibida para una persona.
        Pregunta los datos que falten antes de llamar la herramienta; no
        inventes valores.

        Args:
            persona_citada: Nombre completo de la persona o entidad citada.
            tipo_citacion: Tipo de citación (ej: 'Laboral', 'Civil', 'Embargo').
            fecha_citacion: Fecha de la citación, formato 'YYYY-MM-DD'.
            autoridad: Autoridad o jurisdicción que emite la citación (ej: 'Juzgado 3 Laboral de Bogotá').
            registrado_por: Nombre de quien registra la citación.
        """
        try:
            fecha = date.fromisoformat(fecha_citacion)
        except ValueError:
            return {"error": f"Fecha inválida: '{fecha_citacion}'. Usa YYYY-MM-DD."}

        try:
            citacion = Citacion(
                persona_citada=persona_citada,
                tipo_citacion=tipo_citacion,
                fecha_citacion=fecha,
                autoridad=autoridad,
                registrado_por=registrado_por,
            )
        except ValueError as e:
            return {"error": str(e)}

        guardada = crud.crear_citacion(citacion)
        _notificar_canal(sender, guardada)
        return {
            "id": guardada.id,
            "mensaje": f"Citación #{guardada.id} registrada para {guardada.persona_citada}.",
        }

    @tool
    def actualizar_citacion(id_citacion: int, nuevo_estado: str) -> dict:
        """Actualiza el estado de una citación existente.

        Úsala cuando el usuario indique que una citación ya fue atendida,
        venció, o cambió de estado.

        Args:
            id_citacion: Identificador numérico de la citación.
            nuevo_estado: 'pendiente', 'atendida' o 'vencida'.
        """
        if nuevo_estado not in ESTADOS:
            return {
                "error": f"«{nuevo_estado}» no es un estado válido. "
                         f"Los estados son: {', '.join(ESTADOS)}."
            }

        actualizada = crud.actualizar_estado(id_citacion, nuevo_estado)
        if actualizada is None:
            return {"error": f"No existe ninguna citación con id {id_citacion}."}

        _actualizar_mensaje_canal(sender, actualizada)
        return {
            "id": actualizada.id,
            "estado": actualizada.estado,
            "mensaje": f"Citación #{actualizada.id} actualizada a estado «{actualizada.estado}».",
        }

    return [registrar_citacion, consultar_citaciones, obtener_citacion, actualizar_citacion]
