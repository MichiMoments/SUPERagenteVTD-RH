"""Agente LangChain para generar otrosíes a través de Microsoft Teams.

Usa Gemini como LLM y las herramientas de agent/tools.py para ejecutar las
tres operaciones del proyecto: generación individual, masiva y creación de
plantillas.
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_tool_calling_agent, AgentExecutor

from agent.tools import todas

MODELO = "gemini-2.0-flash"

PROMPT_SISTEMA = """\
Eres un asistente de Recursos Humanos de la Universidad de los Andes que ayuda \
a generar otrosíes (modificaciones de contratos laborales).

Capacidades:
1. Listar los tipos de otrosí disponibles.
2. Describir los campos que necesita un tipo específico.
3. Generar un contrato individual con los datos de un trabajador.
4. Generar contratos masivos a partir de un archivo Excel (.xlsx).
5. Crear una nueva plantilla de otrosí a partir de un documento Word (.docx).
6. Generar la plantilla Excel vacía para llenado masivo.

Reglas:
- Si el usuario adjunta un .docx, probablemente quiere crear una nueva plantilla.
- Si adjunta un .xlsx, probablemente quiere generar contratos masivos.
- Si pide generar un contrato, primero usa 'describir_tipo' para saber qué \
campos necesitas, y pregúntale al usuario los que falten antes de invocar \
'generar_contrato'.
- Si no tienes los datos suficientes para una herramienta, pregunta antes de \
invocarla.
- Responde siempre en español.
- Sé conciso y directo.
- Cuando generes un archivo, avísale al usuario que se lo enviarás."""


def crear_agente(clave_api, modelo=MODELO):
    """Construye un AgentExecutor listo para .invoke().

    Args:
        clave_api: Clave de la API de Google (Gemini).
        modelo: Nombre del modelo de Gemini a usar.

    Returns:
        AgentExecutor con las herramientas y el prompt configurados.
    """
    llm = ChatGoogleGenerativeAI(
        model=modelo,
        google_api_key=clave_api,
        temperature=0.1,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", PROMPT_SISTEMA),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agente = create_tool_calling_agent(llm, todas, prompt)

    return AgentExecutor(
        agent=agente,
        tools=todas,
        verbose=False,
        handle_parsing_errors=True,
        max_iterations=10,
    )
