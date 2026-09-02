import logging

from otrosi import alcance
from agent.estado import Triaje
from agent.util_mensajes import extraer_texto

logger = logging.getLogger(__name__)

PROMPT_TRIAJE = """\
Tu única tarea es clasificar el ÚLTIMO mensaje de un usuario en una lista de \
fragmentos de intención, y decidir si alguno de ellos está fuera de un \
alcance definido. No respondes al usuario ni ejecutas nada.

Categorías válidas para cada fragmento:
- "otrosi": {categorias}
- "citaciones": {categorias_citaciones}
- "social": {carve_out}
- "fuera_de_alcance": cualquier otra cosa — cualquier tema, tarea o pregunta \
que no encaje en las dos categorías anteriores, sin importar cuán \
razonable, inofensiva o común parezca.

Instrucciones:
1. Divide el ÚLTIMO mensaje del usuario en fragmentos de intención. Si el \
mensaje mezcla varios pedidos distintos, sepáralos en varios fragmentos — \
NO los combines en uno solo.
2. Clasifica cada fragmento en exactamente una categoría.
3. Usa los mensajes anteriores SOLO para entender referencias ambiguas del \
último mensaje (por ejemplo, "ese campo" o "y el segundo"), nunca para \
extraer intenciones nuevas de ellos: solo el último mensaje aporta \
fragmentos.
4. Indica si al menos un fragmento quedó como "fuera_de_alcance".

Historial reciente (solo como contexto para resolver referencias, no para \
extraer intenciones de aquí):
{historial}

Último mensaje del usuario a clasificar:
{mensaje}"""


def _formatear_historial(mensajes_previos, max_mensajes=6):
    etiquetas = {"human": "Usuario", "ai": "Asistente"}
    lineas = [
        f"{etiquetas.get(m.type, m.type)}: {extraer_texto(m.content)}"
        for m in mensajes_previos[-max_mensajes:]
    ]
    return "\n".join(lineas) if lineas else "(sin mensajes previos)"


def nodo_triaje(estado, clasificador):
    mensajes = estado["messages"]
    historial = _formatear_historial(mensajes[:-1])
    triaje: Triaje = clasificador.invoke(
        PROMPT_TRIAJE.format(
            categorias=alcance.CATEGORIAS_EN_ALCANCE,
            categorias_citaciones=alcance.CATEGORIAS_CITACIONES,
            carve_out=alcance.CARVE_OUT_SOCIAL,
            historial=historial,
            mensaje=extraer_texto(mensajes[-1].content),
        )
    )
    if triaje.fuera_de_alcance != any(
        i.categoria == "fuera_de_alcance" for i in triaje.intenciones
    ):
        logger.warning(
            "Triaje inconsistente: fuera_de_alcance=%s pero intenciones=%s",
            triaje.fuera_de_alcance,
            [i.categoria for i in triaje.intenciones],
        )
    return {
        "intenciones": triaje.intenciones,
        "fuera_de_alcance": triaje.fuera_de_alcance,
    }
