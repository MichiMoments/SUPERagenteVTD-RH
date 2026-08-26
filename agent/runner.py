"""Polling loop: lee mensajes de Teams, los pasa al agente y envía la respuesta.

Usa teams_core para la lectura y envío de mensajes por Microsoft Graph.
El agente decide qué herramienta invocar según el mensaje del usuario.
"""

import base64
import logging
import os
import time

from teams_core.config import TeamsConfig
from teams_core.auth.provider import MsalTokenProvider
from teams_core.adapters.graph.client import GraphClient
from teams_core.adapters.graph.sender import GraphMessageSender
from teams_core.adapters.graph.reader import GraphMessageReader
from teams_core.domain.models import ConversationRef, ConversationKind, OutboundMessage

from agent.bot import crear_agente

logger = logging.getLogger(__name__)

INTERVALO_POLLING = int(os.environ.get("POLLING_INTERVAL", "10"))


def _extraer_adjuntos(mensaje):
    """Extrae archivos adjuntos del mensaje como {nombre: bytes_base64}."""
    adjuntos = {}
    if not hasattr(mensaje, "attachments") or not mensaje.attachments:
        return adjuntos
    for adj in mensaje.attachments:
        nombre = getattr(adj, "name", None) or "archivo"
        contenido = getattr(adj, "content", None) or getattr(adj, "content_bytes", None)
        if contenido and isinstance(contenido, bytes):
            adjuntos[nombre] = base64.b64encode(contenido).decode()
        elif contenido and isinstance(contenido, str):
            adjuntos[nombre] = contenido
    return adjuntos


def _enriquecer_input(texto, adjuntos):
    """Añade información de adjuntos al texto de entrada del agente."""
    if not adjuntos:
        return texto
    partes = [texto] if texto else []
    for nombre, b64 in adjuntos.items():
        ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
        partes.append(f"[Archivo adjunto: {nombre} ({ext})]")
    return "\n".join(partes)


def _enviar_archivos(sender, conv, resultado, reply_to):
    """Si el resultado contiene archivos codificados, los envía por Teams."""
    archivos_enviados = []
    for clave in ("docx_base64", "xlsx_base64", "zip_base64"):
        if clave not in resultado:
            continue
        nombre = resultado.get("archivo", f"archivo.{clave.split('_')[0]}")
        try:
            respuesta = OutboundMessage(
                body_html=f"<p>Archivo generado: {nombre}</p>",
                reply_to_message_id=reply_to,
            )
            sender.send(conv, respuesta)
            archivos_enviados.append(nombre)
        except Exception as e:
            logger.error("Error enviando archivo %s: %s", nombre, e)
    return archivos_enviados


def main():
    """Arranca el loop de polling contra Teams."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    clave_api = os.environ.get("GEMINI_API_KEY")
    if not clave_api:
        raise RuntimeError("GEMINI_API_KEY no está configurada en .env")

    chat_id = os.environ.get("TEAMS_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TEAMS_CHAT_ID no está configurada en .env")

    cfg = TeamsConfig.from_env()
    tokens = MsalTokenProvider(cfg)
    client = GraphClient(cfg, tokens)
    reader = GraphMessageReader(client)
    sender = GraphMessageSender(client)

    agente = crear_agente(clave_api)

    conv = ConversationRef(kind=ConversationKind.CHAT, chat_id=chat_id)

    historial_chat = {}
    ultimo_visto = None

    logger.info("Agente iniciado. Polling cada %ds en chat %s", INTERVALO_POLLING, chat_id)

    while True:
        try:
            mensajes = reader.history(conv, limit=20)
            nuevos = []
            for msg in mensajes:
                if ultimo_visto and msg.message_id <= ultimo_visto:
                    continue
                if msg.author.is_application:
                    continue
                nuevos.append(msg)

            for msg in nuevos:
                logger.info("Mensaje de %s: %s", msg.author.display_name, msg.text[:100])

                adjuntos = _extraer_adjuntos(msg)
                texto_entrada = _enriquecer_input(msg.text or "", adjuntos)

                chat_key = msg.conversation.chat_id or "default"
                historial = historial_chat.setdefault(chat_key, [])

                try:
                    resultado = agente.invoke({
                        "input": texto_entrada,
                        "chat_history": historial[-20:],
                    })

                    texto_salida = resultado.get("output", "No pude procesar tu solicitud.")

                    respuesta = OutboundMessage(
                        body_html=f"<p>{texto_salida}</p>",
                        reply_to_message_id=msg.message_id,
                    )
                    sender.send(conv, respuesta)

                    from langchain_core.messages import HumanMessage, AIMessage
                    historial.append(HumanMessage(content=texto_entrada))
                    historial.append(AIMessage(content=texto_salida))

                except Exception as e:
                    logger.error("Error procesando mensaje %s: %s", msg.message_id, e)
                    try:
                        sender.send(conv, OutboundMessage(
                            body_html=f"<p>Hubo un error procesando tu solicitud: {e}</p>",
                            reply_to_message_id=msg.message_id,
                        ))
                    except Exception:
                        logger.exception("Error enviando mensaje de error")

                ultimo_visto = msg.message_id

        except Exception as e:
            logger.error("Error en el loop de polling: %s", e)

        time.sleep(INTERVALO_POLLING)


if __name__ == "__main__":
    main()
