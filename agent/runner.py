"""Polling loop: lee mensajes de Teams, los pasa al agente y envía la respuesta.

Usa teams_core para la lectura y envío de mensajes por Microsoft Graph.
El agente decide qué herramienta invocar según el mensaje del usuario.
"""

import base64
import json
import logging
import os
import time
from html import escape as _html_escape

import markdown as _md

from teams_core.config import TeamsConfig
from teams_core.auth.provider import MsalTokenProvider
from teams_core.adapters.graph.client import GraphClient
from teams_core.adapters.graph.downloader import GraphFileDownloader
from teams_core.adapters.graph.sender import GraphMessageSender
from teams_core.adapters.graph.reader import GraphMessageReader
from teams_core.adapters.blob.storage import BlobStorageUploader
from teams_core.domain.models import ConversationRef, ConversationKind, OutboundMessage

from agent.bot import crear_agente

logger = logging.getLogger(__name__)

INTERVALO_POLLING = int(os.environ.get("POLLING_INTERVAL", "10"))
STAGING_DIR = os.path.join(os.path.dirname(__file__), "..", "output", ".staging")


def _descargar_adjuntos(mensaje, downloader):
    """Descarga adjuntos del mensaje a disco. Devuelve {nombre: ruta_local}."""
    if not mensaje.attachments:
        return {}
    os.makedirs(STAGING_DIR, exist_ok=True)
    archivos = {}
    for att in mensaje.attachments:
        try:
            descargado = downloader.download(att)
            ruta = os.path.join(STAGING_DIR, descargado.name)
            with open(ruta, "wb") as f:
                f.write(descargado.content)
            archivos[descargado.name] = ruta
            logger.info("Adjunto descargado: %s -> %s", att.name, ruta)
        except Exception as e:
            logger.warning("No se pudo descargar %s: %s", att.name, e)
    return archivos


def _enriquecer_input(texto, adjuntos):
    """Añade info de adjuntos al texto de entrada del agente (nombre + ruta, sin contenido)."""
    if not adjuntos:
        return texto
    partes = [texto] if texto else []
    for nombre, ruta in adjuntos.items():
        ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
        partes.append(f"[Archivo adjunto: {nombre} ({ext}), ruta: {ruta}]")
    return "\n".join(partes)


def _md_a_html(texto):
    """Convierte Markdown del agente a HTML apto para Teams."""
    return _md.markdown(texto, extensions=["nl2br"])


def _extraer_texto(content):
    """Extrae texto plano del content de un AIMessage (puede ser str o lista de bloques)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for bloque in content:
            if isinstance(bloque, dict) and bloque.get("type") == "text":
                partes.append(bloque.get("text", ""))
            elif isinstance(bloque, str):
                partes.append(bloque)
        return "\n".join(partes)
    return str(content)


def _extraer_archivos(mensajes):
    """Extrae datos de archivos de los ToolMessages del agente."""
    from langchain_core.messages import ToolMessage

    archivos = []
    for msg in mensajes:
        if not isinstance(msg, ToolMessage):
            continue
        try:
            data = json.loads(msg.content) if isinstance(msg.content, str) else None
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or "archivo" not in data:
            continue

        nombre = data["archivo"]
        if "docx_base64" in data:
            archivos.append((nombre, base64.b64decode(data["docx_base64"])))
        elif "xlsx_base64" in data:
            archivos.append((nombre, base64.b64decode(data["xlsx_base64"])))
        elif "ruta" in data:
            try:
                with open(data["ruta"], "rb") as f:
                    archivos.append((nombre, f.read()))
            except FileNotFoundError:
                logger.warning("Archivo no encontrado: %s", data["ruta"])
    return archivos


def _subir_archivos(archivos, uploader):
    """Sube archivos a Azure Blob Storage. Devuelve lista de BlobRef."""
    refs = []
    for nombre, contenido in archivos:
        try:
            ref = uploader.upload(contenido, nombre)
            logger.info("Archivo subido a blob: %s -> %s", nombre, ref.url)
            refs.append(ref)
        except Exception as e:
            logger.error("Error subiendo %s a blob storage: %s", nombre, e)
    return refs


def _formatear_enlaces(refs):
    """Genera HTML con enlaces de descarga para los archivos subidos."""
    if not refs:
        return ""
    lineas = ['<br/><b>Archivos generados:</b><ul>']
    for ref in refs:
        lineas.append(f'<li><a href="{ref.url}">{ref.name}</a></li>')
    lineas.append("</ul>")
    return "".join(lineas)


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
    downloader = GraphFileDownloader(client)
    blob_uploader = BlobStorageUploader(cfg)

    agente = crear_agente(clave_api)

    conv = ConversationRef(kind=ConversationKind.CHAT, chat_id=chat_id)
    es_chat = conv.kind == ConversationKind.CHAT

    historial_chat = {}
    ids_enviados = set()

    mensajes_iniciales = reader.history(conv, limit=20)
    ultimo_visto = mensajes_iniciales[0].message_id if mensajes_iniciales else None
    logger.info("Agente iniciado. Polling cada %ds en chat %s (último msg: %s)",
                INTERVALO_POLLING, chat_id, ultimo_visto)

    while True:
        try:
            mensajes = reader.history(conv, limit=20)
            nuevos = []
            for msg in mensajes:
                if not ultimo_visto or msg.message_id <= ultimo_visto:
                    continue
                if msg.message_id in ids_enviados:
                    continue
                if msg.author.is_application:
                    continue
                nuevos.append(msg)

            for msg in nuevos:
                logger.info("Mensaje de %s: %s", msg.author.display_name, msg.text[:100])

                adjuntos = _descargar_adjuntos(msg, downloader)
                texto_entrada = _enriquecer_input(msg.text or "", adjuntos)

                chat_key = msg.conversation.chat_id or "default"
                historial = historial_chat.setdefault(chat_key, [])

                try:
                    from langchain_core.messages import HumanMessage, AIMessage

                    mensajes = historial[-20:] + [HumanMessage(content=texto_entrada)]
                    resultado = agente.invoke({"messages": mensajes})

                    msgs_salida = resultado.get("messages", [])
                    texto_salida = _extraer_texto(msgs_salida[-1].content) if msgs_salida else "No pude procesar tu solicitud."

                    archivos = _extraer_archivos(msgs_salida)
                    enlaces_html = ""
                    if archivos:
                        refs = _subir_archivos(archivos, blob_uploader)
                        enlaces_html = _formatear_enlaces(refs)

                    respuesta = OutboundMessage(
                        body_html=f"{_md_a_html(texto_salida)}{enlaces_html}",
                        reply_to_message_id=None if es_chat else msg.message_id,
                    )
                    sent_id = sender.send(conv, respuesta)
                    ids_enviados.add(sent_id)

                    historial.append(HumanMessage(content=texto_entrada))
                    historial.append(AIMessage(content=texto_salida))

                except Exception as e:
                    logger.error("Error procesando mensaje %s: %s", msg.message_id, e)
                    try:
                        err_id = sender.send(conv, OutboundMessage(
                            body_html=f"<p>Hubo un error procesando tu solicitud: {_html_escape(str(e))}</p>",
                            reply_to_message_id=None if es_chat else msg.message_id,
                        ))
                        ids_enviados.add(err_id)
                    except Exception:
                        logger.exception("Error enviando mensaje de error")

                ultimo_visto = msg.message_id

        except Exception as e:
            logger.error("Error en el loop de polling: %s", e)

        time.sleep(INTERVALO_POLLING)


if __name__ == "__main__":
    main()
