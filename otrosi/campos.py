"""Coerción y validación de los campos de un tipo de otrosí.

Sin dependencia de Streamlit: el formulario y la carga masiva validan con las
mismas reglas y las mismas etiquetas. Las etiquetas y los tipos ya no viven aquí
sino en el descriptor del tipo (`tipos.py`); lo que se queda es todo lo que
depende del *tipo de dato* y no del otrosí concreto. Desde que la concordancia de
género es un dato del tipo, este módulo no depende de nada del proyecto.
"""

import re
import unicodedata
from datetime import date, datetime

FECHA_MINIMA = date(1970, 1, 1)
FECHA_MAXIMA = date(2100, 12, 31)

# `|` parte una fila de tabla en más celdas de las que _escribir_tabla dimensiona y lo que
# sobra se descarta sin error; `**` corre los índices con que _escribir_runs reparte la
# negrita, y con un número impar los asteriscos se imprimen en el contrato.
PROHIBIDOS = ("|", "**")

# Un valor que abra una línea del cuerpo la convierte en tabla o en viñeta, y con un cuerpo
# que escribe cualquiera ya no hay un solo campo que pueda caer ahí: aplica a todos.
INICIALES_PROHIBIDAS = ("|", "- ")

# `strip()` se lleva el NBSP de los extremos, pero el espacio de ancho cero y la marca de
# orden de bytes no son espacios para Python y sobreviven a NFKC.
_INVISIBLES = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

# Sin \t \n \r, que los absorbe el colapso de espacios; el resto lo rechazaría lxml al
# guardar el .docx, y es mejor decirlo en la carga que a mitad de un lote.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_ARTICULO = re.compile(r"^(La|El|Los|Las) ")


def faltantes(tipo, datos):
    """Claves obligatorias vacías del payload: {'cargo': ''} -> ['cargo']."""
    faltan = []
    for campo in tipo["campos"]:
        if not campo["obligatorio"]:
            continue
        valor = datos.get(campo["clave"])
        if valor is None:
            faltan.append(campo["clave"])
        elif isinstance(valor, str) and not valor.strip():
            faltan.append(campo["clave"])
        elif isinstance(valor, int) and valor <= 0:
            faltan.append(campo["clave"])
    return faltan


def revisar(tipo, datos):
    """Errores de un payload ya armado: {'ciudad': 'Bogotá | D.C.'} -> ['«Ciudad…»: …']."""
    errores = []
    for campo in tipo["campos"]:
        valor = datos.get(campo["clave"])
        if campo["tipo"] == "texto" and isinstance(valor, str):
            errores.extend(_revisar_texto(campo, valor.strip()))
        if campo["no_futura"] and isinstance(valor, date) and valor > date.today():
            errores.append(
                f"«{campo['etiqueta']}»: es una fecha futura; el contrato que se modifica "
                "ya tiene que existir"
            )
    return errores


def _revisar_texto(campo, texto):
    """Los caracteres que corrompen el .docx en silencio, uno por uno."""
    errores = []
    for prohibido in PROHIBIDOS:
        if prohibido in texto:
            errores.append(
                f"«{campo['etiqueta']}»: quita «{prohibido}»; el documento se arma con "
                "tablas y negrita en Markdown, y ese carácter borra en silencio lo que "
                "viene después"
            )
    for inicial in INICIALES_PROHIBIDAS:
        if texto.startswith(inicial):
            errores.append(
                f"«{campo['etiqueta']}»: no puede empezar por «{inicial.strip()}»; "
                "convierte la frase del documento en una tabla o en una viñeta"
            )
    if _CONTROL.search(texto):
        errores.append(
            f"«{campo['etiqueta']}»: tiene un carácter de control invisible; vuelve a "
            "escribir el dato a mano en vez de pegarlo"
        )
    return errores


def avisos(tipo, datos):
    """Sospechas de un payload ya armado: no bloquean, pero casi siempre son un error."""
    sospechas = []
    campos = {campo["clave"]: campo for campo in tipo["campos"]}
    for campo in tipo["campos"]:
        valor = datos.get(campo["clave"])
        anterior = campos.get(campo["posterior_a"] or "")
        if anterior is not None:
            previo = datos.get(anterior["clave"])
            if isinstance(valor, date) and isinstance(previo, date) and valor < previo:
                sospechas.append(
                    f"«{campo['etiqueta']}» es anterior a «{anterior['etiqueta']}»; revisa "
                    "si están intercambiadas o si el día y el mes quedaron al revés"
                )
        if campo["articulo_minuscula"] and _ARTICULO.match(str(valor or "")):
            sospechas.append(
                f"«{campo['etiqueta']}» empieza por «{str(valor).split()[0]}» en mayúscula, "
                "casi seguro por la autocorrección de Excel; se imprime tal cual a mitad "
                "de frase"
            )
    return sospechas


def normalizar(tipo, fila):
    """Fila cruda del Excel al payload plano -> (datos, errores, avisos)."""
    datos, errores, sospechas, fallidas = {}, [], [], set()
    for campo in tipo["campos"]:
        clave = campo["clave"]
        cruda = fila.get(clave)
        if cruda is None or (isinstance(cruda, str) and not cruda.strip()):
            continue  # lo vacío lo reporta faltantes(), con el criterio del formulario
        if campo["tipo"] == "texto" and isinstance(cruda, str) and re.search(r"[\n\r]", cruda):
            sospechas.append(
                f"«{campo['etiqueta']}»: traía un salto de línea (Alt+Enter); se imprime en "
                "un solo renglón"
            )
        try:
            datos[clave] = _convertir(campo, cruda)
        except ValueError as error:
            fallidas.add(clave)
            errores.append(f"«{campo['etiqueta']}»: {error}")

    # lo que ya falló al convertirse no se vuelve a reportar como campo vacío
    etiquetas = {campo["clave"]: campo["etiqueta"] for campo in tipo["campos"]}
    if faltan := [clave for clave in faltantes(tipo, datos) if clave not in fallidas]:
        errores.append(
            "faltan datos en " + ", ".join(f"«{etiquetas[clave]}»" for clave in faltan)
        )
    errores.extend(revisar(tipo, datos))
    sospechas.extend(avisos(tipo, datos))
    return datos, errores, sospechas


def _convertir(campo, cruda):
    """Despacha la celda al conversor de su tipo de campo."""
    if campo["tipo"] in ("cedula", "entero"):
        return _entero(cruda)
    if campo["tipo"] == "fecha":
        return _fecha(cruda)
    if campo["tipo"] == "lista":
        return _opcion(cruda, campo["opciones"], campo["sinonimos"])
    return _texto(cruda)


# En los cuatro conversores el bool se descarta antes que cualquier rama numérica:
# isinstance(True, int) es True, y una celda con VERDADERO no es un dato del otrosí.


def _texto(valor):
    """Normaliza NFKC y colapsa espacios: ' Ana\xa0Ruiz\n' -> 'Ana Ruiz'."""
    if isinstance(valor, bool):
        raise ValueError("dale formato de texto a la celda; VERDADERO/FALSO no es un texto")
    if isinstance(valor, (datetime, date)):
        raise ValueError("la celda tiene formato de fecha; dale formato de texto")
    if isinstance(valor, float):
        if not valor.is_integer():
            raise ValueError(f"llegó como número decimal ({valor}); escríbelo como texto")
        valor = int(valor)
    plano = _INVISIBLES.sub("", unicodedata.normalize("NFKC", str(valor)))
    return re.sub(r"\s+", " ", plano).strip()


def _entero(valor):
    """Cédula desde int, float entero o texto: '1.020.345.678' -> 1020345678."""
    if isinstance(valor, bool):
        raise ValueError("escribe el número de la cédula, no VERDADERO/FALSO")
    if isinstance(valor, int):
        numero = valor
    elif isinstance(valor, float):
        # nunca vía str(): str(1020345678.0) conserva el '.0' y la cédula sale con un dígito más
        if not valor.is_integer():
            raise ValueError(f"tiene decimales ({valor}); la cédula es un número entero")
        numero = int(valor)
    else:
        digitos = re.sub(r"[.,'\s-]", "", str(valor))
        # isdecimal y no isdigit: '²'.isdigit() es True pero int('²') estalla
        if not digitos.isdecimal():
            raise ValueError(
                f"«{valor}» no es un número; escríbela solo con dígitos, sin «CC», sin "
                "letras y sin notación científica"
            )
        numero = int(digitos)
    if numero <= 0:
        raise ValueError("no puede ser cero ni negativa")
    return numero


def _fecha(valor):
    """Solo fechas reales de Excel o texto ISO; lo ambiguo se rechaza, no se adivina."""
    if isinstance(valor, datetime):  # datetime hereda de date: esta rama va primero
        fecha = valor.date()
    elif isinstance(valor, date):
        fecha = valor
    elif isinstance(valor, str):
        texto = valor.strip()
        # la regex va delante de fromisoformat: en Python 3.11 '20260403' también parsea y
        # dejaría pasar un error de tecleo de 8 dígitos
        if not _ISO.match(texto):
            raise ValueError(
                "dale formato de fecha a la celda; como texto solo se acepta AAAA-MM-DD, "
                "porque «03/04/2026» puede ser el 3 de abril o el 4 de marzo"
            )
        try:
            fecha = date.fromisoformat(texto)
        except ValueError:
            raise ValueError(f"«{texto}» no corresponde a una fecha que exista") from None
    else:
        raise ValueError(
            "dale formato de fecha a la celda; un número suelto es un serial de Excel y no "
            "hay forma de saber si es una fecha"
        )
    if not FECHA_MINIMA <= fecha <= FECHA_MAXIMA:
        raise ValueError(
            f"{fecha:%d/%m/%Y} está fuera de {FECHA_MINIMA.year}-{FECHA_MAXIMA.year}"
        )
    return fecha


def _opcion(valor, opciones, sinonimos):
    """Texto de la celda a una de las opciones: ' FEMENINO ' -> 'Femenino'."""
    if isinstance(valor, bool):
        raise ValueError(f"usa la lista desplegable, {_o(opciones)}, no VERDADERO/FALSO")
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)  # Excel devuelve como 2.0 el «2» que se tecleó
    clave = _comparable(valor)
    for opcion in opciones:
        if clave == _comparable(opcion):
            return opcion
    # los sinónimos se normalizan al comparar para que el .json del tipo no tenga que
    # escribirlos ya en minúscula y sin tildes
    for sinonimo, opcion in sinonimos.items():
        if clave == _comparable(sinonimo) and opcion in opciones:
            return opcion
    raise ValueError(f"«{valor}» no vale; usa {_o(opciones)}")


def _o(opciones):
    """['a', 'b'] -> '«a» o «b»', para los mensajes de error."""
    return " o ".join(f"«{opcion}»" for opcion in opciones) or "una de las opciones"


def _comparable(valor):
    """Normaliza para comparar opciones: ' FEMENINO ' -> 'femenino'."""
    plano = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plano).strip().lower()
