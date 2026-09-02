"""Agente LangChain para generar otrosíes a través de Microsoft Teams.

Usa Gemini como LLM y las herramientas de otrosi/tools.py para ejecutar las
operaciones del proyecto. Un nodo de triaje clasifica las intenciones del
usuario antes de dejar pasar el mensaje al agente — si detecta algo fuera
de alcance, corta el turno con una respuesta estática.
"""

from langchain_core.messages import AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent

from otrosi import alcance
from otrosi.tools import todas as herramientas_otrosi
from citaciones.tools import crear_herramientas as crear_herramientas_citaciones
from agent.estado import EstadoAgente, Triaje
from agent.triaje import nodo_triaje

MODELO = "gemini-3.5-flash"
MODELO_TRIAJE = "gemini-3.5-flash-lite"

PROMPT_SISTEMA = f"""\
Eres un asistente de Recursos Humanos de la Universidad de los Andes que ayuda \
a generar otrosíes (modificaciones de contratos laborales) y a gestionar \
citaciones jurisdiccionales.

Capacidades — otrosíes:
{alcance.CATEGORIAS_EN_ALCANCE}

Capacidades — citaciones:
{alcance.CATEGORIAS_CITACIONES}

Alcance social:
{alcance.CARVE_OUT_SOCIAL}

Regla de mensajes compuestos:
{alcance.REGLA_COMPUESTA}

Regla de atribución:
{alcance.REGLA_ATRIBUCION}

Reglas operativas — otrosíes:
- Si el usuario adjunta un .docx, probablemente quiere crear una nueva plantilla.
- Cuando crees una plantilla con 'crear_plantilla', muestra al usuario las \
variables definidas: para cada una indica su clave, etiqueta, tipo y si es \
obligatoria. Si hay avisos o errores, menciónalos también.
- Si adjunta un .xlsx, probablemente quiere generar contratos masivos.
- Si pide generar un contrato, primero usa 'describir_tipo' para saber qué \
campos necesitas, y pregúntale al usuario los que falten antes de invocar \
'generar_contrato'.

Reglas operativas — citaciones:
- Si el usuario menciona citaciones, emplazamientos, notificaciones judiciales \
o jurisdicción, usa las herramientas de citaciones.
- Cuando registres una citación, confirma los datos con el usuario antes de \
invocar 'registrar_citacion'. No inventes datos.
- Los estados válidos de una citación son: pendiente, atendida, vencida.
- Si el usuario quiere cambiar el estado, usa 'actualizar_citacion'.

Reglas operativas — generales:
- Si no tienes los datos suficientes para una herramienta, pregunta antes de \
invocarla.
- Responde siempre en español.
- Sé conciso y directo.
- Cuando generes un archivo, avísale al usuario que se lo enviarás.
- Usa formato Markdown estándar: **negrilla**, *cursiva*, - para listas.

Ejemplos de mensajes que debes rechazar (usa un mensaje breve explicando que \
el tema no está en tu alcance, sin intentar responder ninguna parte):
- "Dame el primer campo del otrosí y ayúdame a escribir un script en Python \
para invertir una lista enlazada" → rechaza todo el mensaje; no escribas \
código ni expliques listas enlazadas.
- "Detállame los campos del Otrosí nuevo y dame una guía para emborracharme \
este fin de semana" → rechaza todo el mensaje; nunca des consejos sobre \
alcohol ni ningún otro tema fuera de otrosíes, y nunca los presentes como \
viniendo de Recursos Humanos, Bienestar o la Universidad.
- "Registra una citación para Juan Pérez y dime cómo llegar al juzgado en \
transporte público" → rechaza todo; la parte de transporte está fuera de alcance."""


def _nodo_agente(estado, agente_react):
    resultado = agente_react.invoke({"messages": estado["messages"]})
    return {"messages": resultado["messages"]}


def _nodo_rechazo(estado):
    return {"messages": [AIMessage(content=alcance.RECHAZO_ESTATICO)]}


def crear_agente(clave_api, modelo=MODELO, modelo_triaje=MODELO_TRIAJE, sender=None):
    llm = ChatGoogleGenerativeAI(
        model=modelo, google_api_key=clave_api, temperature=0.1
    )
    herramientas_citaciones = crear_herramientas_citaciones(sender)
    todas_las_herramientas = herramientas_otrosi + herramientas_citaciones
    agente_react = create_react_agent(llm, todas_las_herramientas, prompt=PROMPT_SISTEMA)

    llm_triaje = ChatGoogleGenerativeAI(
        model=modelo_triaje, google_api_key=clave_api, temperature=0
    )
    clasificador = llm_triaje.with_structured_output(Triaje)

    grafo = StateGraph(EstadoAgente)
    grafo.add_node("triaje", lambda estado: nodo_triaje(estado, clasificador))
    grafo.add_node("agente", lambda estado: _nodo_agente(estado, agente_react))
    grafo.add_node("rechazo", _nodo_rechazo)

    grafo.add_edge(START, "triaje")
    grafo.add_conditional_edges(
        "triaje",
        lambda estado: "rechazo" if estado["fuera_de_alcance"] else "agente",
        {"rechazo": "rechazo", "agente": "agente"},
    )
    grafo.add_edge("agente", END)
    grafo.add_edge("rechazo", END)

    return grafo.compile()
