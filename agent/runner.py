"""Polling loop: lee mensajes de Teams, los pasa al agente y envía la respuesta.

Usa teams_core para la lectura y envío de mensajes por Microsoft Graph.
El agente decide qué herramienta invocar según el mensaje del usuario.
Escucha todos los chats en los que participa el usuario autenticado.
"""

import base64
import json
import logging
import os
import re
import time
from html import escape as _html_escape
from html import unescape as _html_unescape

import markdown as _md

from teams_core.config import TeamsConfig
from teams_core.auth.provider import MsalTokenProvider
from teams_core.adapters.graph.client import GraphClient
from teams_core.adapters.graph.downloader import GraphFileDownloader
from teams_core.adapters.graph.sender import GraphMessageSender
from teams_core.adapters.graph.mail_sender import GraphEmailSender
from teams_core.adapters.graph.mail_reader import GraphEmailReader
from teams_core.adapters.graph.reader import GraphMessageReader
from teams_core.adapters.blob.storage import BlobStorageUploader
from teams_core.domain.models import ConversationRef, ConversationKind, OutboundMessage

from agent.bot import crear_agente
from agent.util_mensajes import extraer_texto as _extraer_texto

logger = logging.getLogger(__name__)

INTERVALO_POLLING = int(os.environ.get("POLLING_INTERVAL", "10"))
INTERVALO_REFRESCO_CHATS = int(os.environ.get("CHAT_REFRESH_INTERVAL", "10"))
INTERVALO_POLLING_EMAIL = int(os.environ.get("EMAIL_POLLING_INTERVAL", "60"))
CARPETA_EMAIL = os.environ.get("EMAIL_FOLDER_ID", "") or None
LIMITE_EMAIL = int(os.environ.get("EMAIL_LIMIT", "10"))
STAGING_DIR = os.path.join(os.path.dirname(__file__), "..", "output", ".staging")
MAX_IDS_ENVIADOS = 5000
MAX_HISTORIAL_POR_CHAT = 50
MAX_EMAILS_PROCESADOS = 5000


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


def _obtener_chats(client):
    """Consulta /me/chats y devuelve {chat_id: ConversationRef} para todos los chats."""
    chats = {}
    for item in client.paged("/me/chats", params={"$select": "id,chatType"}):
        chat_id = item.get("id")
        if not chat_id:
            continue
        chats[chat_id] = ConversationRef(kind=ConversationKind.CHAT, chat_id=chat_id)
    return chats


def _inicializar_chat(reader, conv):
    """Lee el último mensaje de un chat recién descubierto para fijar el watermark."""
    try:
        mensajes = reader.history(conv, limit=1)
        return mensajes[0].message_id if mensajes else None
    except Exception as e:
        logger.warning("No se pudo inicializar chat %s: %s", conv.chat_id, e)
        return None


def _podar_estado(ids_enviados, historial_chat, emails_procesados=None):
    """Previene crecimiento ilimitado de ids_enviados, historial_chat y emails_procesados."""
    if len(ids_enviados) > MAX_IDS_ENVIADOS:
        logger.info("Podando ids_enviados: %d -> descartando la mitad más antigua", len(ids_enviados))
        claves = list(ids_enviados.keys())
        for k in claves[: len(claves) // 2]:
            del ids_enviados[k]

    for chat_key, historial in historial_chat.items():
        if len(historial) > MAX_HISTORIAL_POR_CHAT:
            historial_chat[chat_key] = historial[-MAX_HISTORIAL_POR_CHAT:]

    if emails_procesados is not None and len(emails_procesados) > MAX_EMAILS_PROCESADOS:
        logger.info("Podando emails_procesados: %d -> vaciando", len(emails_procesados))
        emails_procesados.clear()


def _html_a_texto(html):
    """Extrae texto plano de HTML de correo, sin dependencias externas."""
    texto = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = _html_unescape(texto)
    return re.sub(r" {2,}", " ", texto).strip()


def _procesar_correos(email_reader, agente, emails_procesados, folder_id, limit, bot_email=""):
    """Lee correos nuevos, los clasifica con el agente y registra citaciones detectadas."""
    from langchain_core.messages import HumanMessage, ToolMessage

    correos = email_reader.list_messages(folder_id=folder_id, limit=limit)
    for correo in correos:
        if correo.message_id in emails_procesados:
            continue

        if bot_email and correo.from_address.address.strip().lower() == bot_email:
            logger.debug("Correo propio omitido: %s", correo.subject)
            emails_procesados.add(correo.message_id)
            continue

        remitente = correo.from_address.name or correo.from_address.address
        cuerpo_texto = (
            _html_a_texto(correo.body_html) if correo.body_html else correo.body_preview
        )
        texto_entrada = (
            f"[Correo recibido]\n"
            f"De: {remitente} <{correo.from_address.address}>\n"
            f"Asunto: {correo.subject}\n"
            f"Fecha: {correo.received_at}\n\n"
            f"{cuerpo_texto}"
        )

        try:
            resultado = agente.invoke({"messages": [HumanMessage(content=texto_entrada)]})
            msgs_salida = resultado.get("messages", [])

            registro_citacion = any(
                isinstance(m, ToolMessage) and m.name == "registrar_citacion"
                for m in msgs_salida
            )
            if registro_citacion:
                logger.info(
                    "Citación registrada desde correo de %s (asunto: %s)",
                    remitente, correo.subject,
                )
            else:
                logger.info(
                    "Correo de %s ignorado (no es una citación): %s",
                    remitente, correo.subject,
                )
        except Exception as e:
            logger.error(
                "Error procesando correo %s de %s: %s",
                correo.message_id, remitente, e,
            )

        emails_procesados.add(correo.message_id)


def main():
    """Arranca el loop de polling contra Teams — escucha todos los chats."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    clave_api = os.environ.get("GEMINI_API_KEY")
    if not clave_api:
        raise RuntimeError("GEMINI_API_KEY no está configurada en .env")

    cfg = TeamsConfig.from_env()
    tokens = MsalTokenProvider(cfg)
    client = GraphClient(cfg, tokens)
    reader = GraphMessageReader(client)
    sender = GraphMessageSender(client)
    email_sender = GraphEmailSender(client)
    email_reader = GraphEmailReader(client)
    downloader = GraphFileDownloader(client)
    blob_uploader = BlobStorageUploader(cfg)

    try:
        _me = client.request("GET", "/me", params={"$select": "id,mail,userPrincipalName"})
        bot_email = (_me.get("mail") or _me.get("userPrincipalName") or "").strip().lower()
        id_bot = _me.get("id", "")
        if bot_email:
            logger.info("Identidad del bot resuelta: %s (id: %s)", bot_email, id_bot)
        else:
            logger.warning("No se pudo resolver email del bot — filtro de auto-correo desactivado")
        if not id_bot:
            logger.warning("No se pudo resolver id del bot — filtro de auto-mensaje por id desactivado")
    except Exception as e:
        logger.warning("Error al llamar /me: %s — filtros de auto-mensaje desactivados", e)
        bot_email = ""
        id_bot = ""

    agente = crear_agente(clave_api, sender=sender, email_sender=email_sender)

    chats_activos = _obtener_chats(client)
    ultimo_visto = {}
    historial_chat = {}
    ids_enviados = {}
    emails_procesados = set()

    for chat_id, conv in chats_activos.items():
        ultimo_visto[chat_id] = _inicializar_chat(reader, conv)

    try:
        for correo in email_reader.list_messages(folder_id=CARPETA_EMAIL, limit=LIMITE_EMAIL):
            emails_procesados.add(correo.message_id)
    except Exception as e:
        logger.warning("No se pudo inicializar el watermark de correos: %s", e)

    logger.info(
        "Agente iniciado. Polling cada %ds en %d chats, correos cada %ds.",
        INTERVALO_POLLING, len(chats_activos), INTERVALO_POLLING_EMAIL,
    )

    ultima_actualizacion_chats = time.monotonic()
    ultima_lectura_email = time.monotonic()

    while True:
        ahora = time.monotonic()
        if ahora - ultima_actualizacion_chats >= INTERVALO_REFRESCO_CHATS:
            try:
                nuevos_chats = _obtener_chats(client)
                for chat_id, conv in nuevos_chats.items():
                    if chat_id not in chats_activos:
                        ultimo_visto[chat_id] = None
                        chats_activos[chat_id] = conv
                        logger.info("Nuevo chat descubierto: %s", chat_id[:12])
                logger.info("Chats actualizados: %d activos", len(chats_activos))
                ultima_actualizacion_chats = ahora
            except Exception as e:
                logger.error("Error actualizando lista de chats: %s", e)

        for chat_id, conv in list(chats_activos.items()):
            try:
                mensajes = reader.history(conv, limit=20)
                marca = ultimo_visto.get(chat_id)
                nuevos = []
                for msg in mensajes:
                    if marca and msg.message_id <= marca:
                        continue
                    if msg.message_id in ids_enviados:
                        continue
                    if msg.author.is_application:
                        continue
                    if id_bot and msg.author.id == id_bot:
                        continue
                    if not msg.author.display_name:
                        continue
                    if not msg.text or not msg.text.strip():
                        continue
                    nuevos.append(msg)

                if nuevos:
                    ultimo_visto[chat_id] = nuevos[0].message_id

                for msg in nuevos:
                    logger.info(
                        "[%s] Mensaje de %s: %s",
                        chat_id[:12], msg.author.display_name,
                        (msg.text or "")[:100],
                    )

                    adjuntos = _descargar_adjuntos(msg, downloader)
                    texto_entrada = _enriquecer_input(msg.text or "", adjuntos)

                    chat_key = msg.conversation.chat_id or chat_id
                    historial = historial_chat.setdefault(chat_key, [])

                    msg_conv = msg.conversation

                    try:
                        from langchain_core.messages import HumanMessage, AIMessage

                        mensajes_agente = historial[-20:] + [HumanMessage(content=texto_entrada)]
                        resultado = agente.invoke({"messages": mensajes_agente})

                        msgs_salida = resultado.get("messages", [])
                        texto_salida = (
                            _extraer_texto(msgs_salida[-1].content)
                            if msgs_salida
                            else "No pude procesar tu solicitud."
                        )

                        archivos = _extraer_archivos(msgs_salida)
                        enlaces_html = ""
                        if archivos:
                            refs = _subir_archivos(archivos, blob_uploader)
                            enlaces_html = _formatear_enlaces(refs)

                        respuesta = OutboundMessage(
                            body_html=f"{_md_a_html(texto_salida)}{enlaces_html}",
                            reply_to_message_id=None,
                        )
                        sent_id = sender.send(msg_conv, respuesta)
                        ids_enviados[sent_id] = None
                        ultimo_visto[chat_id] = sent_id

                        historial.append(HumanMessage(content=texto_entrada))
                        historial.append(AIMessage(content=texto_salida))

                    except Exception as e:
                        logger.error(
                            "Error procesando mensaje %s en chat %s: %s",
                            msg.message_id, chat_id[:12], e,
                        )
                        try:
                            err_id = sender.send(
                                msg_conv,
                                OutboundMessage(
                                    body_html=f"<p>Hubo un error procesando tu solicitud: {_html_escape(str(e))}</p>",
                                    reply_to_message_id=None,
                                ),
                            )
                            ids_enviados[err_id] = None
                            ultimo_visto[chat_id] = err_id
                        except Exception:
                            logger.exception("Error enviando mensaje de error")

            except Exception as e:
                logger.error("Error polling chat %s: %s", chat_id[:12], e)
                continue

        if ahora - ultima_lectura_email >= INTERVALO_POLLING_EMAIL:
            try:
                _procesar_correos(
                    email_reader, agente, emails_procesados, CARPETA_EMAIL, LIMITE_EMAIL,
                    bot_email=bot_email,
                )
            except Exception as e:
                logger.error("Error en polling de correos: %s", e)
            ultima_lectura_email = ahora

        _podar_estado(ids_enviados, historial_chat, emails_procesados)

        time.sleep(INTERVALO_POLLING)


if __name__ == "__main__":
    main()
