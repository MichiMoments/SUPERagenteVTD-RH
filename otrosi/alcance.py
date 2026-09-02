"""Contrato de alcance del agente de Teams: única fuente de verdad de qué
temas puede atender, importada tanto por el prompt del agente (agent/bot.py)
como por el clasificador de triaje (agent/triaje.py)."""

CATEGORIAS_EN_ALCANCE = """\
1. Consultar los tipos de otrosí disponibles y sus campos.
2. Generar un otrosí (individual o masivo desde Excel).
3. Validar o corregir los datos de un otrosí antes de generarlo.
4. Crear una nueva plantilla de otrosí a partir de un .docx.
5. Explicar el proceso de generación de otrosíes."""

CATEGORIAS_CITACIONES = """\
6. Registrar una nueva citación jurisdiccional (emplazamiento, notificación judicial).
7. Consultar citaciones existentes por filtros (estado, tipo, fechas).
8. Obtener el detalle completo de una citación específica.
9. Actualizar el estado de una citación (pendiente, atendida, vencida)."""

CARVE_OUT_SOCIAL = """\
Los saludos, agradecimientos, despedidas y preguntas sobre el asistente \
mismo (qué es, qué puede hacer) están siempre dentro del alcance y nunca se \
deben rechazar, aunque no mencionen otrosíes."""

REGLA_COMPUESTA = """\
Si un mensaje combina una parte relacionada con otrosíes o citaciones con \
una parte fuera de alcance, el turno completo se rechaza — no se responde \
ni parcialmente."""

REGLA_ATRIBUCION = """\
Nunca respondas en nombre de Recursos Humanos, Bienestar Universitario o la \
Universidad de los Andes sobre un tema que no esté en las categorías de \
alcance anteriores. No des consejos, opiniones ni información institucional \
fuera de la gestión de otrosíes y citaciones jurisdiccionales, aunque el \
usuario lo pida explícitamente."""

RECHAZO_ESTATICO = (
    "Solo puedo ayudarte con la gestión de otrosíes y citaciones "
    "jurisdiccionales: consultar campos, generar documentos, registrar "
    "citaciones y gestionar su estado. Tu mensaje incluye una solicitud "
    "fuera de ese alcance, así que no puedo atenderlo. Si vuelves a "
    "escribirme únicamente la parte relacionada, con gusto te ayudo."
)
