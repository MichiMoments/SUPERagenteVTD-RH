"""Herramientas LangChain que envuelven las funciones puras de core/.

Cada herramienta tiene un docstring en español que Gemini usa para decidir
cuál invocar. Los bytes (docx, xlsx, zip) se devuelven codificados en base64
dentro de un dict, y el runner se encarga de decodificarlos y enviarlos como
archivo por Teams.
"""

import base64
import os
from datetime import date

from langchain_core.tools import tool

from core import campos, documento, ia, masivo, tipos, transcripcion


@tool
def listar_tipos() -> str:
    """Lista los tipos de otrosí disponibles con su nombre y campos.

    Úsala cuando el usuario quiera saber qué tipos de contrato puede generar.
    No necesita parámetros.
    """
    disponibles = tipos.listar()
    if not disponibles:
        return "No hay tipos de otrosí configurados."
    lineas = []
    for identificador, tipo in disponibles.items():
        n_campos = len(tipo.get("campos", []))
        lineas.append(f"- **{tipo['nombre']}** (id: `{identificador}`) — {n_campos} campos")
    return "\n".join(lineas)


@tool
def describir_tipo(tipo_id: str) -> str:
    """Describe los campos y detalles de un tipo de otrosí específico.

    Úsala cuando el usuario pregunte qué datos necesita un tipo, o antes de
    generar un contrato para saber qué campos pedir.

    Args:
        tipo_id: Identificador del tipo (ej: 'teletrabajo_hibrido').
    """
    try:
        tipo = tipos.cargar(tipo_id)
    except ValueError as e:
        return f"Error: {e}"

    lineas = [f"**{tipo['nombre']}**", f"Título del documento: {tipo['titulo']}", "", "Campos:"]
    for campo in tipo.get("campos", []):
        obligatorio = "obligatorio" if campo.get("obligatorio", True) else "opcional"
        detalle = f"  - `{campo['clave']}` ({campo['etiqueta']}) — tipo: {campo['tipo']}, {obligatorio}"
        if campo.get("opciones"):
            detalle += f", opciones: {campo['opciones']}"
        lineas.append(detalle)
    return "\n".join(lineas)


@tool
def generar_contrato(tipo_id: str, datos: dict) -> dict:
    """Genera un contrato individual (.docx) con los datos de un trabajador.

    Úsala cuando el usuario quiera generar un solo otrosí con datos específicos.
    Primero usa 'describir_tipo' para saber qué campos necesitas y pregúntale
    al usuario los que falten.

    Args:
        tipo_id: Identificador del tipo de otrosí.
        datos: Diccionario con los valores de cada campo. Las claves deben
               coincidir con las del tipo. Las fechas van como 'YYYY-MM-DD'.
    """
    try:
        tipo = tipos.cargar(tipo_id)
    except ValueError as e:
        return {"error": str(e)}

    datos = _preparar_datos(tipo, datos)

    faltan = campos.faltantes(tipo, datos)
    errores = campos.revisar(tipo, datos)
    if faltan or errores:
        return {"error": "Datos inválidos", "faltantes": faltan, "errores": errores}

    try:
        md = documento.render_markdown(tipo, datos)
        docx_bytes = documento.markdown_a_docx(md, tipo["titulo"])
        nombre = documento.nombre_archivo(tipo, datos)
    except ValueError as e:
        return {"error": str(e)}

    return {
        "archivo": nombre,
        "docx_base64": base64.b64encode(docx_bytes).decode(),
        "mensaje": f"Contrato generado: {nombre}",
    }


@tool
def generar_masivo(tipo_id: str, xlsx_base64: str, fecha_defecto: str = "") -> dict:
    """Genera contratos masivos (.zip de .docx) a partir de un archivo Excel.

    Úsala cuando el usuario adjunte un archivo .xlsx con datos de múltiples
    trabajadores.

    Args:
        tipo_id: Identificador del tipo de otrosí.
        xlsx_base64: Contenido del archivo Excel codificado en base64.
        fecha_defecto: Fecha por defecto para campos de fecha opcionales vacíos
                       (formato YYYY-MM-DD). Si no se da, usa la fecha de hoy.
    """
    try:
        tipo = tipos.cargar(tipo_id)
    except ValueError as e:
        return {"error": str(e)}

    xlsx_bytes = base64.b64decode(xlsx_base64)

    try:
        fecha = date.fromisoformat(fecha_defecto) if fecha_defecto else date.today()
    except ValueError:
        return {"error": f"Fecha inválida: '{fecha_defecto}'. Usa el formato YYYY-MM-DD."}

    registros, errores_libro = masivo.leer_libro(tipo, xlsx_bytes, fecha)
    if errores_libro:
        return {"error": "El archivo tiene errores estructurales", "errores": errores_libro}

    validos = [r for r in registros if not r["errores"]]
    if not validos:
        todos_errores = []
        for r in registros:
            if r["errores"]:
                todos_errores.append(f"Fila {r['fila']}: {'; '.join(r['errores'])}")
        return {"error": "Ninguna fila es válida", "errores": todos_errores}

    zip_bytes, fallos, generados = masivo.generar_zip(tipo, validos)

    resultado = {
        "zip_base64": base64.b64encode(zip_bytes).decode(),
        "generados": generados,
        "mensaje": f"Se generaron {len(generados)} contratos.",
    }
    if fallos:
        resultado["fallos"] = fallos

    filas_malas = [r for r in registros if r["errores"]]
    if filas_malas:
        resultado["filas_con_errores"] = [
            f"Fila {r['fila']}: {'; '.join(r['errores'])}" for r in filas_malas
        ]

    return resultado


@tool
def crear_plantilla(docx_base64: str) -> dict:
    """Crea una nueva plantilla de otrosí a partir de un documento Word (.docx).

    Úsala cuando el usuario adjunte un .docx y quiera convertirlo en una
    plantilla reutilizable. Las palabras entre «guillemets» en el documento
    se tratan como campos candidatos.

    Args:
        docx_base64: Contenido del archivo .docx codificado en base64.
    """
    docx_bytes = base64.b64decode(docx_base64)

    cuerpo, avisos_docx = transcripcion.leer_docx(docx_bytes)
    encontrados = transcripcion.marcadores_entre_guillemets(cuerpo)
    cuerpo = transcripcion.convertir_guillemets_a_marcadores(cuerpo)

    clave_api = os.environ.get("GEMINI_API_KEY", "")
    avisos_ia = []
    campos_generados = []

    if encontrados and clave_api:
        try:
            campos_generados = ia.proponer_campos(encontrados, cuerpo, clave_api)
        except ValueError as e:
            avisos_ia.append(f"La IA no pudo inferir los campos: {e}")
    elif encontrados and not clave_api:
        avisos_ia.append(
            "No hay clave de Gemini configurada; los campos se deben declarar a mano."
        )

    if campos_generados:
        cuerpo = transcripcion.renombrar_marcadores(cuerpo, encontrados, campos_generados)

    borrador = tipos.nuevo("Otrosí nuevo")
    borrador["cuerpo"] = cuerpo
    if campos_generados:
        borrador["campos"] = [tipos.completar_campo(c) for c in campos_generados]

    errores, avisos_val = tipos.validar(borrador)

    resultado = {
        "tipo": {
            "id": borrador["id"],
            "nombre": borrador["nombre"],
            "campos": borrador["campos"],
        },
        "cuerpo_preview": cuerpo[:500],
        "avisos": [*avisos_docx, *avisos_ia, *avisos_val],
        "mensaje": f"Plantilla creada con {len(borrador['campos'])} campos.",
    }
    if errores:
        resultado["errores"] = errores
    else:
        tipos.guardar(borrador)
        resultado["mensaje"] += " Guardada exitosamente."

    return resultado


@tool
def plantilla_excel(tipo_id: str) -> dict:
    """Genera la plantilla Excel (.xlsx) vacía para llenado masivo.

    Úsala cuando el usuario quiera la plantilla para llenar datos de múltiples
    trabajadores y luego generar los contratos en lote.

    Args:
        tipo_id: Identificador del tipo de otrosí.
    """
    try:
        tipo = tipos.cargar(tipo_id)
    except ValueError as e:
        return {"error": str(e)}

    xlsx_bytes = masivo.construir_plantilla(tipo)
    nombre = f"plantilla_{tipo_id}.xlsx"

    return {
        "archivo": nombre,
        "xlsx_base64": base64.b64encode(xlsx_bytes).decode(),
        "mensaje": f"Plantilla Excel generada: {nombre}",
    }


def _preparar_datos(tipo, datos):
    """Convierte strings ISO a date para campos de tipo fecha."""
    for campo in tipo.get("campos", []):
        clave = campo["clave"]
        if campo["tipo"] == "fecha" and clave in datos and isinstance(datos[clave], str):
            try:
                datos[clave] = date.fromisoformat(datos[clave])
            except ValueError:
                pass
        if campo["tipo"] == "entero" and clave in datos and isinstance(datos[clave], str):
            try:
                datos[clave] = int(datos[clave])
            except ValueError:
                pass
        if campo["tipo"] == "cedula" and clave in datos and isinstance(datos[clave], str):
            try:
                datos[clave] = int(datos[clave])
            except ValueError:
                pass
    return datos


todas = [listar_tipos, describir_tipo, generar_contrato, generar_masivo, crear_plantilla, plantilla_excel]
