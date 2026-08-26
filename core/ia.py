"""Inferencia de campos de un tipo de otrosí con la API de Gemini.

Sin dependencia de Streamlit: recibe la clave de la API y los datos, y devuelve una
lista de campos o lanza ValueError con un mensaje en español. La IA solo propone
metadatos de campo (clave, etiqueta, tipo, opciones); el cuerpo del documento viaja
como contexto de lectura, nunca de vuelta, así que no puede reescribir el texto del
contrato.
"""

import json

import requests

from . import tipos

MODELO_DEFECTO = "gemini-3-flash-preview"

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

# primera llamada de red del proyecto: nunca debe colgar el hilo de Streamlit
_TIMEOUT = 30

# techo alto a propósito: un .json de campos cortado a la mitad no parsea y se
# pierde todo el trabajo, que es el peor fallo posible aquí
_MAXIMO_TOKENS_SALIDA = 8192

_ESQUEMA_CAMPO = {
    "type": "OBJECT",
    "properties": {
        "clave": {
            "type": "STRING",
            "description": "snake_case, minúsculas, sin tildes: ^[a-z][a-z0-9_]*$",
        },
        "etiqueta": {"type": "STRING", "description": "Nombre legible para el formulario y el Excel"},
        "tipo": {"type": "STRING", "enum": list(tipos.TIPOS_CAMPO)},
        "opciones": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Solo si tipo es 'lista': las opciones válidas",
        },
        "obligatorio": {"type": "BOOLEAN"},
    },
    "required": ["clave", "etiqueta", "tipo"],
}

_ESQUEMA_RESPUESTA = {"type": "ARRAY", "items": _ESQUEMA_CAMPO}

_PROMPT = """Eres un asistente que ayuda a declarar los campos de un tipo de otrosí \
(una modificación de contrato laboral) para una plantilla de documentos.

Las siguientes palabras fueron marcadas a mano en el documento original porque \
probablemente deban convertirse en campos que varían de persona a persona:
{variables}

Aquí está el cuerpo del documento, solo para que entiendas el contexto de cada \
palabra e infieras su tipo de dato correcto (NO lo repitas ni lo cites en tu \
respuesta, es exclusivamente contexto de lectura):

---
{cuerpo}
---

Para cada palabra de la lista, propone UN campo con:
- "clave": snake_case, minúsculas, sin tildes, empieza por letra
  (^[a-z][a-z0-9_]*$). Derívala del significado, no copies la palabra literal si
  tiene mayúsculas o tildes (p. ej. «Teletrabajadora» -> "genero", «Dias» ->
  "dias_teletrabajo").
- "etiqueta": una descripción corta y legible en español para un formulario.
- "tipo": exactamente uno de {tipos_validos}.
  - "cedula" para números de identificación.
  - "entero" para cantidades enteras.
  - "fecha" para fechas.
  - "lista" cuando el contexto sugiere un conjunto cerrado de opciones (género,
    ciudad, días de la semana...); en ese caso incluye "opciones" con las que
    tengan sentido según el contexto.
  - "texto" en cualquier otro caso.
- "obligatorio": true salvo que el contexto sugiera claramente que es opcional.

Devuelve un campo por cada palabra de la lista, en el mismo orden, sin añadir
campos adicionales."""


def proponer_campos(variables, cuerpo, clave_api, modelo=MODELO_DEFECTO):
    """(['Teletrabajadora', 'Dias'], cuerpo, clave) -> [{'clave': ..., 'tipo': ...}, ...].

    El cuerpo es solo contexto de lectura para inferir el tipo de cada campo; no
    vuelve en la respuesta, así que la IA no puede alterar el texto del contrato.
    """
    if not variables:
        return []  # nada que inferir: no hace falta una clave para no llamar a nada
    if not clave_api:
        raise ValueError("No hay una clave de la API de Gemini configurada.")

    peticion = {
        "contents": [{"parts": [{"text": _PROMPT.format(
            variables="\n".join(f"- «{variable}»" for variable in variables),
            cuerpo=cuerpo,
            tipos_validos=", ".join(tipos.TIPOS_CAMPO),
        )}]}],
        "generationConfig": {
            "maxOutputTokens": _MAXIMO_TOKENS_SALIDA,
            "responseMimeType": "application/json",
            "responseSchema": _ESQUEMA_RESPUESTA,
        },
    }

    try:
        respuesta = requests.post(
            _URL.format(modelo=modelo), params={"key": clave_api},
            json=peticion, timeout=_TIMEOUT,
        )
    except requests.RequestException as error:
        raise ValueError(f"No se pudo contactar la API de Gemini: {error}") from None

    if respuesta.status_code != 200:
        raise ValueError(
            f"La API de Gemini respondió con error {respuesta.status_code}: "
            f"{respuesta.text[:300]}"
        )

    try:
        candidatos = respuesta.json()["candidates"]
        if not candidatos:
            raise ValueError("La API de Gemini no devolvió ningún candidato.")
        razon = candidatos[0].get("finishReason", "")
        if razon == "MAX_TOKENS":
            raise ValueError(
                "La respuesta de Gemini se cortó por el límite de tokens antes de "
                "terminar el JSON; sube _MAXIMO_TOKENS_SALIDA o reduce el cuerpo enviado."
            )
        if razon not in ("STOP", ""):
            raise ValueError(
                f"Gemini no completó la respuesta (razón: {razon}). "
                "Intenta de nuevo o revisa el contenido del documento."
            )
        campos = json.loads(candidatos[0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"La respuesta de Gemini no tuvo el formato esperado: {error}") from None

    if not isinstance(campos, list):
        raise ValueError("La respuesta de Gemini no es una lista de campos.")
    return [campo for campo in campos if isinstance(campo, dict) and campo.get("clave")]
