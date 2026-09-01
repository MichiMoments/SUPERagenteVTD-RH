"""Transcribe el cuerpo de un .docx de Word al Markdown que entiende documento.py.

Sin dependencia de Streamlit. Traduce lo que el dialecto sabe expresar —párrafos,
negrita, viñetas, tablas, listas numeradas y sangría— y lo que no, lo deja en texto
plano y lo reporta como aviso; nunca lo borra en silencio. No infiere campos ni
inserta {{marcadores}}: eso lo hace después una persona en el editor. La regla que
manda sobre la fidelidad es que `tipos.revisar_cuerpo` no puede sacar un solo error
del resultado, porque el conversor a .docx falla en silencio ante lo que no entiende.
"""

import io
import re
import unicodedata
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml.etree import XMLSyntaxError

from . import campos
from . import documento

_ERROR_ARCHIVO = (
    "No se pudo abrir el archivo como documento de Word. Ábrelo en Word y usa «Guardar "
    "como → Documento de Word (.docx)»; un .doc, un .rtf o un .pdf renombrados no sirven. "
    "Detalle técnico: {}"
)

_LADOS_TABLA = ("top", "left", "bottom", "right", "insideH", "insideV")

# guion, viñeta rellena, cuadrada, hueca y punto medio; se deja fuera el guion largo
# y el corto porque aparecen en rangos de fechas («2024–2026») y no son viñetas
_VINETA_MANUAL = re.compile(r"^[-•▪◦‣·]\s+")

# solo espacios de ancho no estándar: NFKC completo convertiría «7º» en «7o» (el
# ordinal masculino tiene descomposición de compatibilidad a "o"), cambiando en
# silencio el texto legal. NBSP y compañía sí hay que normalizarlos: el colapso de
# espacios de más abajo no los toca porque para \s de Python no son espacio
_ESPACIOS_ANCHOS = re.compile(
    "[   -   　]"
)

_LETRAS = "abcdefghijklmnopqrstuvwxyz"

_ROMANOS = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)

_MENSAJES_AVISO = {
    "cursiva": (
        "Se quitó la cursiva de {n} fragmentos: este editor no tiene cursiva; el texto "
        "se conservó tal cual.{ej}"
    ),
    "control": "Se borraron caracteres de control invisibles en {n} fragmentos.",
    "asterisco": (
        "Se quitaron asteriscos sueltos en {n} fragmentos: en este editor solo «**» "
        "significa negrita.{ej}"
    ),
    "barra": (
        "Se cambió «|» por «/» en {n} fragmentos: el «|» solo delimita celdas de tabla "
        "y en cualquier otro sitio corrompe el documento en silencio.{ej}"
    ),
    "marcador_existente": (
        "El .docx ya traía marcadores «{{{{…}}}}» en {n} fragmentos y se conservaron "
        "tal cual; el revisor los marcará como error hasta que declares esos campos.{ej}"
    ),
    "vineta_manual": (
        "Se convirtieron {n} párrafos que empezaban con un guion o una viñeta escrita a "
        "mano en viñetas de verdad.{ej}"
    ),
    "titulo": (
        "Se convirtieron {n} títulos de Word en párrafos en negrita: este editor no "
        "tiene niveles de título."
    ),
    "hipervinculo": (
        "Se conservó el texto de {n} hipervínculos con la dirección al lado: este "
        "editor no tiene enlaces.{ej}"
    ),
    "enlace_manual": (
        "Se dejaron tal cual {n} fragmentos con forma de enlace «[texto](dirección)» "
        "escritos a mano.{ej}"
    ),
    "sin_soportar": (
        "Se dejaron tal cual {n} párrafos que empezaban con «#», «>» o un bloque de "
        "código: no hay forma de escribirlos sin reescribir el texto.{ej}"
    ),
    "tabla_anidada": (
        "Se aplanó una tabla anidada dentro de una celda en {n} casos: este editor no "
        "admite tablas dentro de tablas."
    ),
    "celdas_combinadas": (
        "Se deshicieron celdas combinadas en {n} tablas: el texto quedó en la celda de "
        "origen y las demás quedaron vacías."
    ),
    "celda_parrafos": (
        "Se unieron en una sola línea las celdas que traían más de un párrafo, en {n} "
        "casos."
    ),
    "imagen": (
        "Se ignoraron las imágenes del documento: no se leen ni el encabezado, ni el "
        "pie, ni las figuras del cuerpo."
    ),
    "eliminado": (
        "El documento traía cambios de control con texto eliminado sin aceptar; ese "
        "texto no se incluyó."
    ),
    "bordes_parciales": (
        "Una tabla con bordes solo en algunos lados se transcribió con todos sus "
        "bordes: este editor no distingue lados sueltos."
    ),
    "lista_sin_definicion": (
        "Una lista numerada no traía su definición de formato y se transcribió como "
        "viñetas."
    ),
    "fallo_parrafo": (
        "No se pudo transcribir {n} párrafos: probablemente traigan una estructura de "
        "Word poco común. Revísalos en el original.{ej}"
    ),
    "fallo_tabla": (
        "No se pudo transcribir {n} tablas: probablemente traigan una estructura de "
        "Word poco común. Revísalas en el original.{ej}"
    ),
}


def leer_docx(contenido):
    """Bytes de un .docx -> (cuerpo en Markdown, avisos)."""
    doc = _abrir(contenido)
    definiciones = _definiciones_numeracion(doc)
    bloques, notas = _recorrer(doc, definiciones)
    cuerpo = "\n\n".join(bloques) + "\n" if bloques else ""
    return cuerpo, _avisos(notas)


def _abrir(contenido):
    """Bytes a Document, traduciendo cualquier fallo de apertura a un ValueError."""
    try:
        return Document(io.BytesIO(contenido))
    except (zipfile.BadZipFile, PackageNotFoundError, XMLSyntaxError, KeyError,
            ValueError) as error:
        raise ValueError(_ERROR_ARCHIVO.format(error)) from None


# --- recorrido del documento --------------------------------------------------------


def _contenido(elemento):
    """Los w:p y w:tbl directos de un elemento -> [('p'|'tbl', elemento_xml)].

    Entra en w:sdt (controles de contenido) y w:ins (inserciones ya aceptadas), y
    descarta w:del: no usa Document.iter_inner_content() porque su xpath es "./w:p |
    ./w:tbl", que deja fuera justo esos tres casos —comunes en plantillas de RR. HH.—
    en vez de fallar con un error visible.
    """
    bloques = []
    for hijo in elemento.iterchildren():
        etiqueta = hijo.tag
        if etiqueta == qn("w:p"):
            bloques.append(("p", hijo))
        elif etiqueta == qn("w:tbl"):
            bloques.append(("tbl", hijo))
        elif etiqueta == qn("w:sdt"):
            contenedor = hijo.find(qn("w:sdtContent"))
            if contenedor is not None:
                bloques.extend(_contenido(contenedor))
        elif etiqueta == qn("w:ins"):
            bloques.extend(_contenido(hijo))
    return bloques


def _recorrer(doc, definiciones):
    """Los bloques del documento ya en el dialecto -> (['línea o tabla', ...], notas)."""
    notas = {}
    contadores = {}
    bloques = []
    vinetas_pendientes = []

    def cerrar_vinetas():
        if vinetas_pendientes:
            bloques.append("\n".join(vinetas_pendientes))
            vinetas_pendientes.clear()

    for tipo, elemento in _contenido(doc.element.body):
        if tipo == "p":
            try:
                linea = _parrafo(Paragraph(elemento, doc), definiciones, contadores, notas)
            except Exception as error:  # una estructura de Word rara no puede tumbar el resto
                _anotar(notas, "fallo_parrafo", str(error)[:80])
                continue
            if not linea:
                continue
            if linea.startswith("- "):
                vinetas_pendientes.append(linea)
            else:
                cerrar_vinetas()
                bloques.append(linea)
        else:
            cerrar_vinetas()
            try:
                lineas_tabla = _tabla(Table(elemento, doc), notas)
            except Exception as error:
                _anotar(notas, "fallo_tabla", str(error)[:80])
                continue
            if lineas_tabla:
                bloques.append("\n".join(lineas_tabla))
    cerrar_vinetas()

    if doc.element.body.xpath(".//w:drawing | .//w:pict"):
        _anotar(notas, "imagen", "")
    # tanto un párrafo entero eliminado (w:del a nivel de bloque, descartado en
    # _contenido) como un run eliminado dentro de un párrafo (descartado en
    # _runs_del_parrafo) llegan aquí: un solo aviso agregado por documento basta
    if doc.element.body.xpath(".//w:del"):
        _anotar(notas, "eliminado", "")
    return bloques, notas


# --- párrafos -------------------------------------------------------------------------


def _parrafo(p, definiciones, contadores, notas):
    """Un w:p a su línea de Markdown; '' si el párrafo queda vacío tras limpiarlo."""
    texto = _emitir(_fundir(_trozos(p, notas))).strip()
    if not texto:
        return ""

    nivel = _nivel_lista(p)
    if nivel is not None:
        prefijo = _prefijo_lista(nivel, definiciones, contadores, notas)
        if _es_titulo(p):
            texto = _en_negrita(texto, notas)
        return f"{texto}" if prefijo == "- " and texto.startswith("- ") else f"{prefijo}{texto}"

    coincidencia = _VINETA_MANUAL.match(texto)
    if coincidencia:
        resto = texto[coincidencia.end():].strip()
        if resto:
            _anotar(notas, "vineta_manual", resto[:60])
            return f"- {resto}"

    if _es_titulo(p):
        return _en_negrita(texto, notas)

    if texto.startswith(("#", ">", "```")):
        _anotar(notas, "sin_soportar", texto[:60])
        return texto

    if _ENLACE_MANUAL.search(texto):
        _anotar(notas, "enlace_manual", texto[:60])

    return " " * _sangria(p) + texto


_ENLACE_MANUAL = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _en_negrita(texto, notas):
    """'CLÁUSULA' -> '**CLÁUSULA**'; no duplica si el párrafo ya quedó todo en negrita."""
    _anotar(notas, "titulo", "")
    if texto.startswith("**") and texto.endswith("**") and texto.count("**") == 2:
        return texto
    return f"**{texto}**"


def _sangria(p):
    """La sangría izquierda del párrafo -> espacios iniciales, a 0,25" por cada 4."""
    indent = p.paragraph_format.left_indent
    if indent is None:
        return 0
    return max(0, round(indent.inches / 0.0625))


def _es_titulo(p):
    """True si el párrafo es un «Título N» de Word, por outlineLvl y no por el nombre."""
    pPr = p._p.pPr
    if pPr is not None and pPr.xpath("./w:outlineLvl"):
        return True
    return _tiene_outline_estilo(p.style)


def _tiene_outline_estilo(estilo):
    """Sube por base_style buscando un outlineLvl; si no hay ninguno, mira el nombre."""
    vistos = set()
    nombre_base = None
    while estilo is not None and id(estilo) not in vistos:
        vistos.add(id(estilo))
        if nombre_base is None:
            nombre_base = estilo.name
        try:
            if estilo.element.xpath("./w:pPr/w:outlineLvl"):
                return True
        except AttributeError:
            break
        estilo = _subir_estilo(estilo)
    if not nombre_base:
        return False
    # el nombre interno es el de Word en inglés para los estilos integrados, pero un
    # Word o un LibreOffice localizados pueden guardar «Título 1»: se comparan los dos
    plano = unicodedata.normalize("NFKD", nombre_base).encode("ascii", "ignore").decode()
    return bool(re.match(r"^(heading|titulo)\s*[1-9]$", plano.strip().lower()))


def _subir_estilo(estilo):
    """El base_style de un estilo, o None si no aplica o el estilo no lo expone."""
    try:
        return estilo.base_style
    except AttributeError:
        return None


# --- negrita, cursiva e hipervínculos ---------------------------------------------------


def _runs_del_parrafo(p, elemento):
    """Genera (w:r, dirección) recorriendo el párrafo; entra en cambios e hipervínculos."""
    for hijo in elemento.iterchildren():
        etiqueta = hijo.tag
        if etiqueta == qn("w:r"):
            yield hijo, None
        elif etiqueta == qn("w:hyperlink"):
            direccion = _direccion_hipervinculo(p, hijo)
            for run_el, _ in _runs_del_parrafo(p, hijo):
                yield run_el, direccion
        elif etiqueta == qn("w:ins"):
            yield from _runs_del_parrafo(p, hijo)
        elif etiqueta in (qn("w:smartTag"), qn("w:sdt")):
            contenedor = hijo.find(qn("w:sdtContent")) if etiqueta == qn("w:sdt") else hijo
            if contenedor is not None:
                yield from _runs_del_parrafo(p, contenedor)
        # w:del se descarta aquí; se cuenta una sola vez por párrafo en _trozos


def _direccion_hipervinculo(p, hyperlink_el):
    """La URL de un w:hyperlink resuelta por relación; '' si es un ancla interna."""
    r_id = hyperlink_el.get(qn("r:id"))
    if not r_id:
        return ""
    try:
        return p.part.rels[r_id].target_ref
    except KeyError:
        return ""


def _trozos(p, notas):
    """Los (texto, negrita) de un párrafo, ya saneados, con sus enlaces resueltos."""
    piezas = []
    grupo_texto, grupo_direccion, direccion_actual = "", None, None

    def cerrar_grupo():
        nonlocal grupo_texto, grupo_direccion
        if grupo_direccion and grupo_direccion not in grupo_texto:
            piezas.append((f" ({grupo_direccion})", False))
            _anotar(notas, "hipervinculo", grupo_texto.strip()[:60])
        grupo_texto, grupo_direccion = "", None

    for run_el, direccion in _runs_del_parrafo(p, p._p):
        if direccion != direccion_actual:
            cerrar_grupo()
            direccion_actual = direccion
        grupo_direccion = direccion
        if run_el.xpath("./w:drawing | ./w:pict"):
            continue
        run = Run(run_el, p)
        crudo = run.text
        if not crudo:
            continue
        if _es_cursiva(run, p) and crudo.strip():
            _anotar(notas, "cursiva", crudo.strip()[:60])
        texto = _limpiar(crudo, notas)
        grupo_texto += crudo
        if texto:
            piezas.append((texto, _negrita(run, p)))
    cerrar_grupo()
    return piezas


def _negrita(run, p):
    """La negrita de verdad de un run: el tri-estado resuelto contra la cadena de estilos."""
    if run.bold is not None:
        return run.bold
    heredada = _negrita_estilo(run.style) if run.style is not None else None
    if heredada is not None:
        return heredada
    return bool(_negrita_estilo(p.style))


def _es_cursiva(run, p):
    """Igual que _negrita, pero para decidir si hay que avisar de la cursiva perdida."""
    if run.italic is not None:
        return run.italic
    heredada = _cursiva_estilo(run.style) if run.style is not None else None
    if heredada is not None:
        return heredada
    return bool(_cursiva_estilo(p.style))


def _negrita_estilo(estilo):
    """Sube por base_style hasta encontrar un w:b explícito."""
    vistos = set()
    while estilo is not None and id(estilo) not in vistos:
        vistos.add(id(estilo))
        if estilo.font.bold is not None:
            return estilo.font.bold
        estilo = _subir_estilo(estilo)
    return None


def _cursiva_estilo(estilo):
    """Igual que _negrita_estilo, para la cursiva."""
    vistos = set()
    while estilo is not None and id(estilo) not in vistos:
        vistos.add(id(estilo))
        if estilo.font.italic is not None:
            return estilo.font.italic
        estilo = _subir_estilo(estilo)
    return None


def _fundir(piezas):
    """[('a', True), (' b', True)] -> [('a b', True)]; saca los espacios de la negrita.

    Word parte una negrita en varios runs por cualquier motivo (revisión ortográfica,
    idioma...), y suele incluir el espacio siguiente dentro del run resaltado: sin este
    paso, "**texto **" imprimiría un espacio en negrita, visible como un hueco más ancho.
    """
    piezas = [[t, b] for t, b in piezas if t]
    piezas = _fundir_adyacentes(piezas)
    piezas = _migrar_espacios(piezas)
    piezas = _fundir_adyacentes(piezas)
    return [(t, b) for t, b in piezas if t]


def _fundir_adyacentes(piezas):
    """Junta fragmentos consecutivos con el mismo estado de negrita."""
    fundidos = []
    for texto, negrita in piezas:
        if fundidos and fundidos[-1][1] == negrita:
            fundidos[-1][0] += texto
        else:
            fundidos.append([texto, negrita])
    return fundidos


def _migrar_espacios(piezas):
    """Saca los espacios sueltos de los extremos de un fragmento en negrita."""
    resultado = []
    for texto, negrita in piezas:
        if not negrita:
            resultado.append([texto, False])
            continue
        nucleo = texto.strip(" ")
        if not nucleo:
            resultado.append([texto, False])
            continue
        prefijo = texto[:len(texto) - len(texto.lstrip(" "))]
        sufijo = texto[len(prefijo) + len(nucleo):]
        if prefijo:
            resultado.append([prefijo, False])
        resultado.append([nucleo, True])
        if sufijo:
            resultado.append([sufijo, False])
    return resultado


def _emitir(piezas):
    """[('a', True), (' y b', False)] -> '**a** y b'."""
    return "".join(f"**{t}**" if b else t for t, b in piezas)


# --- listas numeradas -------------------------------------------------------------------


def _nivel_lista(p):
    """(numId, ilvl) del párrafo -> propio, o el de su estilo; None si no es una lista."""
    numPr = p._p.pPr.numPr if p._p.pPr is not None else None
    if numPr is None and p.style is not None:
        numPr = _numPr_estilo(p.style)
    if numPr is None or numPr.numId is None:
        return None
    ilvl = numPr.ilvl.val if numPr.ilvl is not None else 0
    return str(numPr.numId.val), ilvl


def _numPr_estilo(estilo):
    """Sube por base_style hasta encontrar un w:numPr propio del estilo.

    Es obligatorio y no un adorno: la propia plantilla integrada usa el estilo "List
    Bullet" para las viñetas, que lleva su numId en el estilo y no en el párrafo.
    """
    vistos = set()
    while estilo is not None and id(estilo) not in vistos:
        vistos.add(id(estilo))
        try:
            encontrados = estilo.element.xpath("./w:pPr/w:numPr")
        except AttributeError:
            return None
        if encontrados:
            return encontrados[0]
        estilo = _subir_estilo(estilo)
    return None


def _definiciones_numeracion(doc):
    """(numId, nivel) de una lista -> su formato, su texto y desde dónde cuenta."""
    try:
        raiz = doc.part.numbering_part.element
    except (NotImplementedError, KeyError, AttributeError):
        return {}  # un Word sin ninguna lista no trae numbering.xml

    abstractos = {}
    for abstracto in raiz.findall(qn("w:abstractNum")):
        aid = abstracto.get(qn("w:abstractNumId"))
        niveles = {}
        for lvl in abstracto.findall(qn("w:lvl")):
            ilvl = int(lvl.get(qn("w:ilvl")))
            fmt = lvl.find(qn("w:numFmt"))
            texto = lvl.find(qn("w:lvlText"))
            inicio = lvl.find(qn("w:start"))
            niveles[ilvl] = {
                "formato": fmt.get(qn("w:val")) if fmt is not None else "decimal",
                "texto": texto.get(qn("w:val")) if texto is not None else None,
                "inicio": int(inicio.get(qn("w:val"))) if inicio is not None else 1,
            }
        abstractos[aid] = niveles

    definiciones = {}
    for num in raiz.findall(qn("w:num")):
        num_id = num.get(qn("w:numId"))
        referencia = num.find(qn("w:abstractNumId"))
        if referencia is None:
            continue
        niveles = dict(abstractos.get(referencia.get(qn("w:val")), {}))
        # las sobrescrituras de inicio son por numId, no por abstractNum
        for override in num.findall(qn("w:lvlOverride")):
            ilvl = int(override.get(qn("w:ilvl")))
            inicio = override.find(qn("w:startOverride"))
            if ilvl in niveles and inicio is not None:
                niveles[ilvl] = {**niveles[ilvl], "inicio": int(inicio.get(qn("w:val")))}
        for ilvl, definicion in niveles.items():
            definiciones[(num_id, ilvl)] = definicion
    return definiciones


def _prefijo_lista(nivel, definiciones, contadores, notas):
    """El (numId, ilvl) de un párrafo -> '- ' o el texto numerado ya resuelto, con espacio."""
    num_id, ilvl = nivel
    definicion = definiciones.get((num_id, ilvl))
    if definicion is None:
        _anotar(notas, "lista_sin_definicion", "")
        return "- "  # sin información de formato, una viñeta es la opción más segura
    if definicion["formato"] == "bullet":
        return "- "

    contador = contadores.setdefault(num_id, {})
    contador[ilvl] = contador.get(ilvl, definicion["inicio"] - 1) + 1
    # retomar un nivel reinicia los niveles más profundos que él
    for mas_profundo in [n for n in contador if n > ilvl]:
        del contador[mas_profundo]

    valores = {}
    for nivel_actual in range(ilvl + 1):
        definicion_nivel = definiciones.get((num_id, nivel_actual), definicion)
        n = contador.get(nivel_actual, definicion_nivel["inicio"])
        valores[nivel_actual + 1] = _numero(definicion_nivel["formato"], n)

    texto = definicion["texto"] or f"%{ilvl + 1}."
    resultado = re.sub(r"%(\d)", lambda m: valores.get(int(m.group(1)), ""), texto)
    return f"{resultado} "


def _numero(formato, n):
    """('lowerLetter', 3) -> 'c'; lo que no se sepa formatear cae en decimal."""
    if formato == "decimalZero":
        return f"{n:02d}"
    if formato == "lowerLetter":
        return _letra(n)
    if formato == "upperLetter":
        return _letra(n).upper()
    if formato == "lowerRoman":
        return _romano(n)
    if formato == "upperRoman":
        return _romano(n).upper()
    return str(n)


def _letra(n):
    """3 -> 'c'; 27 -> 'aa', igual que las columnas de Excel (base 26 sin cero)."""
    letras = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = _LETRAS[resto] + letras
    return letras or "a"


def _romano(n):
    """4 -> 'iv'."""
    resultado = ""
    for valor, letra in _ROMANOS:
        while n >= valor:
            resultado += letra
            n -= valor
    return resultado or "i"


# --- tablas ---------------------------------------------------------------------------


def _tabla(tabla, notas):
    """Un w:tbl a sus líneas '| a | b |', con la directiva de sin bordes si toca."""
    filas = _filas(tabla, notas)
    if not filas:
        return []
    lineas = [_fila(fila) for fila in filas]
    if _sin_bordes(tabla, notas):
        return [documento._SIN_BORDES, *lineas]
    return lineas


def _fila(celdas):
    """['a', ''] -> '| a | |'."""
    partes = [f" {celda} " if celda else " " for celda in celdas]
    return "|" + "|".join(partes) + "|"


def _filas(tabla, notas):
    """Las celdas fila a fila, deshaciendo las combinadas y cuadrando el número de celdas."""
    crudas = []
    hubo_combinada = False
    for fila_xml in tabla._tbl.findall(qn("w:tr")):
        fila = []
        trPr = fila_xml.find(qn("w:trPr"))
        if trPr is not None:
            antes = trPr.find(qn("w:gridBefore"))
            if antes is not None:
                fila.extend([""] * int(antes.get(qn("w:val")) or 0))
        for celda_xml in fila_xml.findall(qn("w:tc")):
            span, continuacion = _combinacion_celda(celda_xml)
            hubo_combinada = hubo_combinada or span > 1 or continuacion
            texto = "" if continuacion else _texto_celda(celda_xml, tabla, notas)
            fila.append(texto)
            fila.extend([""] * (span - 1))
        if trPr is not None:
            despues = trPr.find(qn("w:gridAfter"))
            if despues is not None:
                fila.extend([""] * int(despues.get(qn("w:val")) or 0))
        crudas.append(fila)

    if not crudas:
        return []
    ancho = max(len(fila) for fila in crudas)
    if any(len(fila) != ancho for fila in crudas):
        hubo_combinada = True
    if hubo_combinada:
        _anotar(notas, "celdas_combinadas", "")
    return [fila + [""] * (ancho - len(fila)) for fila in crudas]


def _combinacion_celda(celda_xml):
    """(gridSpan, es_continuación_vertical) de un w:tc; (1, False) si no está combinada."""
    tcPr = celda_xml.find(qn("w:tcPr"))
    if tcPr is None:
        return 1, False
    span_el = tcPr.find(qn("w:gridSpan"))
    span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
    vmerge_el = tcPr.find(qn("w:vMerge"))
    # w:vMerge sin @w:val, o con "continue", es la continuación; "restart" es el origen
    continuacion = vmerge_el is not None and vmerge_el.get(qn("w:val")) in (None, "continue")
    return span, continuacion


def _texto_celda(celda_xml, contenedor, notas):
    """Todo el contenido de una celda en un solo renglón, tablas anidadas incluidas."""
    partes = []
    parrafos = 0
    for tipo, elemento in _contenido(celda_xml):
        if tipo == "p":
            parrafos += 1
            texto = _emitir(_fundir(_trozos(Paragraph(elemento, contenedor), notas))).strip()
            if texto:
                partes.append(texto)
        else:
            _anotar(notas, "tabla_anidada", "")
            partes.append(_aplanar_tabla(Table(elemento, contenedor), notas))
    if parrafos > 1:
        _anotar(notas, "celda_parrafos", "")
    return " ".join(parte for parte in partes if parte)


def _aplanar_tabla(tabla, notas):
    """Una tabla anidada dentro de una celda -> 'a / b; c / d'."""
    filas = _filas(tabla, notas)
    return "; ".join(" / ".join(celda for celda in fila if celda) for fila in filas)


def _sin_bordes(tabla, notas):
    """True si ni la tabla ni su estilo pintan un solo borde.

    Sin w:tblStyle, la tabla hereda "Normal Table", que por definición no pinta
    bordes: eso no es una suposición, así que se distingue del caso realmente
    ambiguo (hay un estilo declarado pero no se le pudieron leer los bordes), donde
    sí se asume que tiene bordes —una tabla "sin bordes" por error es peor que una
    con una línea de más.
    """
    bordes = tabla._tbl.xpath("./w:tblPr/w:tblBorders")
    if not bordes:
        if not tabla._tbl.xpath("./w:tblPr/w:tblStyle"):
            return True
        estilo = getattr(tabla, "style", None)
        bordes = _bordes_del_estilo(estilo) if estilo is not None else []
        if not bordes:
            return False
    pintados = [_lado_pintado(bordes[0], lado) for lado in _LADOS_TABLA]
    if any(pintados) and not all(pintados):
        _anotar(notas, "bordes_parciales", "")
    return not any(pintados)


def _lado_pintado(tblBorders, lado):
    """True si el lado tiene un color/grosor real y no 'none'/'nil'."""
    elemento = tblBorders.find(qn(f"w:{lado}"))
    return elemento is not None and elemento.get(qn("w:val")) not in (None, "none", "nil")


def _bordes_del_estilo(estilo):
    """Sube por base_style buscando un w:tblBorders."""
    vistos = set()
    while estilo is not None and id(estilo) not in vistos:
        vistos.add(id(estilo))
        try:
            encontrados = estilo.element.xpath("./w:tblPr/w:tblBorders")
        except AttributeError:
            return []
        if encontrados:
            return encontrados
        estilo = _subir_estilo(estilo)
    return []


# --- saneamiento del texto --------------------------------------------------------------


def _limpiar(texto, notas):
    """Deja el texto imprimible en una sola línea: ' a\xa0|\tb ' -> ' a / b '.

    No usa NFKC completo a propósito: normaliza «7º» (ordinal masculino, con
    descomposición de compatibilidad) a «7o», cambiando en silencio texto legal.
    Solo se homogenizan los espacios de ancho no estándar, antes que nada, para que
    el colapso final se los lleve; asteriscos y «|» van antes del propio colapso,
    porque borrar un carácter puede dejar dos espacios pegados.
    """
    plano = _ESPACIOS_ANCHOS.sub(" ", texto)
    plano = campos._INVISIBLES.sub("", plano)
    if campos._CONTROL.search(plano):
        _anotar(notas, "control", "")
        plano = campos._CONTROL.sub("", plano)
    if "*" in plano:
        _anotar(notas, "asterisco", plano.strip()[:60])
        plano = plano.replace("*", "")
    if "{{" in plano or "}}" in plano or "{%" in plano or "{#" in plano:
        _anotar(notas, "marcador_existente", plano.strip()[:60])
    if "|" in plano:
        _anotar(notas, "barra", plano.strip()[:60])
        plano = plano.replace("|", "/")
    return re.sub(r"\s+", " ", plano)


# --- avisos -----------------------------------------------------------------------------


def _anotar(notas, clase, muestra):
    """Apunta un cambio para el aviso agrupado; solo guarda la primera muestra y cuenta."""
    entrada = notas.setdefault(clase, {"cuenta": 0, "muestra": ""})
    entrada["cuenta"] += 1
    if not entrada["muestra"] and muestra:
        entrada["muestra"] = muestra


def _avisos(notas):
    """Las anotaciones acumuladas -> una frase por clase, con el conteo y un ejemplo."""
    frases = []
    for clase, datos in notas.items():
        plantilla = _MENSAJES_AVISO.get(clase)
        if not plantilla:
            continue
        ejemplo = f" El primero: «{datos['muestra']}»." if datos["muestra"] else ""
        frases.append(plantilla.format(n=datos["cuenta"], ej=ejemplo))
    return frases


# --- candidatos a campo marcados a mano ------------------------------------------------

_GUILLEMET = re.compile(r"«([^»]+)»")


def marcadores_entre_guillemets(cuerpo):
    """'... «Teletrabajadora» ... «Dias» ... «Teletrabajadora»' -> ['Teletrabajadora', 'Dias'].

    Sin duplicados, en el orden de primera aparición: son candidatos a campo que
    alguien marcó a mano en el .docx original, no texto para imprimir dos veces.
    """
    vistos = []
    for palabra in _GUILLEMET.findall(cuerpo):
        if palabra not in vistos:
            vistos.append(palabra)
    return vistos


def convertir_guillemets_a_marcadores(cuerpo):
    """'«Teletrabajadora»' -> '{{Teletrabajadora}}'; no toca el contenido, solo la puntuación.

    No corrige mayúsculas ni tildes: una clave declarada tiene que ser minúscula y
    sin tildes, así que esto deja marcadores que el revisor marcará como
    desconocidos hasta que alguien declare el campo y renombre el marcador a juego.
    """
    return cuerpo.replace("«", "{{").replace("»", "}}")


def renombrar_marcadores(cuerpo, variables, campos_generados):
    """'{{Teletrabajadora}}' -> '{{teletrabajadora}}'; solo cambia el marcador, no el texto."""
    for variable, campo in zip(variables, campos_generados):
        cuerpo = cuerpo.replace("{{" + variable + "}}", "{{" + campo["clave"] + "}}")
    return cuerpo
