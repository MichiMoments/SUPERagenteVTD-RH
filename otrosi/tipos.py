"""Tipos de otrosí: descriptores en disco, valores por defecto y validación.

Sin dependencia de Streamlit. Es el único módulo que sabe que hay archivos: los
tipos integrados vienen del repositorio y los que crea Gestión Humana se guardan
en `plantillas/personalizadas/`, que pisan al integrado del mismo identificador.
"""

import json
import re
from pathlib import Path

from . import documento
from . import RAIZ

DIRECTORIO = Path(__file__).resolve().parent / "plantillas"
PERSONALIZADAS = DIRECTORIO / "personalizadas"

TIPOS_CAMPO = ("texto", "cedula", "entero", "fecha", "lista")

NOMBRE_TIPO_CAMPO = {
    "texto": "Texto",
    "cedula": "Cédula",
    "entero": "Número entero",
    "fecha": "Fecha",
    "lista": "Lista",
}

# La concordancia de género es un campo `lista` con frases derivadas, no un tipo aparte:
# así el sustantivo del rol es un dato y «el Contratista / la Contratista» se puede
# expresar igual que «el Teletrabajador / la Teletrabajadora».
OPCIONES_GENERO = ("Femenino", "Masculino")

# Pegar en Excel borra la validación de la celda, así que al leer se aceptan también las
# grafías que se teclean a mano, no solo los dos textos del desplegable.
SINONIMOS_GENERO = {
    "f": "Femenino", "fem": "Femenino", "femenina": "Femenino", "mujer": "Femenino",
    "m": "Masculino", "masc": "Masculino", "masculina": "Masculino", "hombre": "Masculino",
}

# Frases que concuerdan con la persona pero no dependen del sustantivo del rol.
CONCORDANCIA_SUELTA = {
    "identificado": ("identificada", "identificado"),
    "de_la_misma": ("de la misma", "del mismo"),
}

# Todo menos clave, etiqueta y tipo tiene valor por defecto: un .json escrito a mano
# no necesita las catorce claves, y el editor solo expone lo que de verdad se cambia.
DEFECTOS_CAMPO = {
    "obligatorio": True,
    "ejemplo": "",
    "ayuda": "",
    "grupo": "",  # subtítulo bajo el que se agrupa el campo en el formulario
    "opciones": [],
    "derivados": {},  # marcador -> {opción: frase}; ver generar_concordancia
    "sinonimos": {},
    "sugerencias": [],
    "no_futura": False,
    "posterior_a": None,
    "articulo_minuscula": False,
    "opcional_en_hoja": False,
    "ancho": 0,  # 0 = derivar del largo de la etiqueta
}

DEFECTOS_TIPO = {
    "titulo": documento.TITULO,
    "prefijo_archivo": "otrosi",
    "campo_nombre": None,
    "campo_fecha_archivo": None,
    "campos": [],
    "cuerpo": "",
}

ANCHO_MINIMO, ANCHO_MAXIMO = 12, 40

_ID = re.compile(r"^[a-z0-9_]{1,60}$")
_CLAVE = re.compile(r"^[a-z][a-z0-9_]*$")

# La regex de marcadores y la de separar el filtro son las de documento.py a propósito:
# si el validador usara las suyas podrían divergir del renderizador y aprobar un cuerpo
# que luego no renderiza.
_MARCADOR = documento.MARCADOR

# Las listas numeradas no se avisan: el conversor las imprime literales, que es justo
# lo que quiere quien escribe «1. Computador:», y avisar sería llorar sin lobo.
_ENLACE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_CURSIVA = re.compile(r"(?<!\*)\*(?!\*)")

_NO_SOPORTADO = (
    ("#", "un título de Markdown («# …»)"),
    (">", "una cita de Markdown («> …»)"),
    ("```", "un bloque de código"),
)


def _llave_valida(identificador):
    """True si el identificador es seguro para armar una ruta: 'a_b' -> True."""
    return bool(_ID.match(str(identificador or "")))


def slug_identificador(nombre):
    """'Reducción de jornada' -> 'reduccion_jornada'."""
    return documento._slug(str(nombre or ""))[:60].strip("_")


def _archivo(identificador, personalizada):
    """Ruta del .json de un tipo; el personalizado pisa al integrado."""
    carpeta = PERSONALIZADAS if personalizada else DIRECTORIO
    return carpeta / f"{identificador}.json"


def _completar(bruto, identificador, origen, marca):
    """Rellena los valores por defecto de un descriptor recién leído."""
    tipo = {**DEFECTOS_TIPO, **bruto}
    tipo["id"] = identificador
    tipo["nombre"] = bruto.get("nombre") or identificador
    tipo["campos"] = [completar_campo(campo) for campo in bruto.get("campos", [])]
    tipo["_origen"] = origen
    tipo["_marca"] = marca  # mtime al leer: detecta que otra persona guardó mientras tanto
    return tipo


def completar_campo(bruto):
    """Rellena los valores por defecto de un campo; las listas se copian, no se comparten."""
    campo = {clave: _copia(valor) for clave, valor in DEFECTOS_CAMPO.items()}
    campo.update(bruto)
    campo["clave"] = str(bruto.get("clave", "")).strip()
    campo["etiqueta"] = str(bruto.get("etiqueta", "")).strip() or campo["clave"]
    campo["tipo"] = str(bruto.get("tipo", "texto")).strip()
    campo["derivados"] = {
        str(marcador).strip(): dict(por_opcion)
        for marcador, por_opcion in (campo.get("derivados") or {}).items()
    }
    # Compatibilidad: los .json guardados cuando el género era un tipo de campo traen
    # «genero» y ninguna opción. Se convierten al leer, con los mismos cinco marcadores
    # que inyectaba el código, para que un tipo creado antes siga renderizando igual.
    if campo["tipo"] == "genero":
        campo["tipo"] = "lista"
        campo["opciones"] = campo["opciones"] or list(OPCIONES_GENERO)
        campo["sinonimos"] = campo["sinonimos"] or dict(SINONIMOS_GENERO)
        campo["derivados"] = campo["derivados"] or generar_concordancia(
            "Teletrabajadora", "Teletrabajador", campo["opciones"]
        )
    if not campo["ancho"]:
        campo["ancho"] = max(ANCHO_MINIMO, min(ANCHO_MAXIMO, len(campo["etiqueta"]) + 4))
    return campo


def _copia(valor):
    """Copia superficial de los valores mutables de DEFECTOS_CAMPO."""
    if isinstance(valor, list):
        return list(valor)
    if isinstance(valor, dict):
        return dict(valor)
    return valor


def opciones(campo):
    """Textos válidos de un campo de elección: [] si no es de lista."""
    return list(campo.get("opciones") or [])


def por_clave(tipo):
    """Los campos indexados: {'nombre': {...}}."""
    return {campo["clave"]: campo for campo in tipo["campos"]}


def etiquetas(tipo):
    """clave -> etiqueta, en el orden de los campos (que es el de las columnas del Excel)."""
    return {campo["clave"]: campo["etiqueta"] for campo in tipo["campos"]}


def marcadores(tipo):
    """Claves que se pueden escribir en el cuerpo: los campos y sus frases derivadas."""
    validos = []
    for campo in tipo["campos"]:
        validos.append(campo["clave"])
        validos.extend(campo["derivados"])
    return validos


def generar_concordancia(femenino, masculino, opciones_campo):
    """('Contratista', 'Contratista', ['Femenino', 'Masculino']) -> los cinco derivados.

    Las contracciones las escribe el código y no una persona, así que en el momento de
    crearlas no pueden salir mal: «de» + «el Contratista» da «del Contratista», no
    «de el Contratista». A partir de aquí son datos y quien edite es responsable.
    """
    if len(opciones_campo) != 2:
        raise ValueError(
            "La concordancia de género necesita un campo de exactamente dos opciones, "
            f"y este tiene {len(opciones_campo)}."
        )
    fem_opcion, masc_opcion = opciones_campo
    fem, masc = str(femenino).strip(), str(masculino).strip()
    if not fem or not masc:
        raise ValueError("Escribe las dos formas del sustantivo, la femenina y la masculina.")
    base = slug_identificador(masc) or "persona"
    derivados = {
        base: {fem_opcion: f"la {fem}", masc_opcion: f"el {masc}"},
        f"al_{base}": {fem_opcion: f"a la {fem}", masc_opcion: f"al {masc}"},
        f"del_{base}": {fem_opcion: f"de la {fem}", masc_opcion: f"del {masc}"},
    }
    for marcador, (en_femenino, en_masculino) in CONCORDANCIA_SUELTA.items():
        derivados[marcador] = {fem_opcion: en_femenino, masc_opcion: en_masculino}
    return derivados


def campo_genero(etiqueta="Género en el documento", femenino="Teletrabajadora",
                 masculino="Teletrabajador"):
    """Un campo de lista con las dos opciones de género y su concordancia ya generada."""
    return completar_campo({
        "clave": "genero",
        "etiqueta": etiqueta,
        "tipo": "lista",
        "opciones": list(OPCIONES_GENERO),
        "sinonimos": dict(SINONIMOS_GENERO),
        "derivados": generar_concordancia(femenino, masculino, list(OPCIONES_GENERO)),
        "ejemplo": OPCIONES_GENERO[0],
        "ayuda": "Define la concordancia de género en todo el documento.",
    })


def nuevo(nombre="Otrosí nuevo"):
    """Descriptor vacío para empezar a editar en la web.

    Con un campo en blanco, no con la lista vacía: sin ninguna fila, st.data_editor recibe
    una tabla de cero columnas y su botón de agregar fila no tiene sobre qué operar.
    """
    return _completar({"nombre": nombre, "campos": [{}]},
                      slug_identificador(nombre) or "otrosi_nuevo", "nuevo", None)


def listar():
    """id -> descriptor de todos los tipos; los personalizados pisan a los integrados."""
    encontrados = {}
    for personalizada in (False, True):
        carpeta = PERSONALIZADAS if personalizada else DIRECTORIO
        if not carpeta.is_dir():
            continue
        for archivo in sorted(carpeta.glob("*.json")):
            if not _llave_valida(archivo.stem):
                continue
            try:
                encontrados[archivo.stem] = cargar(archivo.stem)
            except (OSError, ValueError):
                continue  # un .json roto en disco no puede tumbar la app entera
    return encontrados


def cargar(identificador):
    """Lee un tipo del disco, resolviendo el cuerpo y los valores por defecto."""
    if not _llave_valida(identificador):
        raise ValueError(f"«{identificador}» no es un identificador de tipo válido")
    personalizado = _archivo(identificador, True)
    integrado = _archivo(identificador, False)
    archivo = personalizado if personalizado.is_file() else integrado
    if not archivo.is_file():
        raise ValueError(f"No existe el tipo «{identificador}»")
    bruto = json.loads(archivo.read_text(encoding="utf-8"))
    if not isinstance(bruto, dict):
        raise ValueError(f"El archivo del tipo «{identificador}» no es un objeto JSON")

    if not bruto.get("cuerpo") and bruto.get("cuerpo_archivo"):
        # el cuerpo del tipo integrado vive en un .md hermano, para que el texto legal
        # se versione en git línea a línea en vez de como una cadena con \n dentro
        hermano = archivo.parent / str(bruto["cuerpo_archivo"])
        if hermano.parent != archivo.parent or not hermano.is_file():
            raise ValueError(f"No encontré el cuerpo «{bruto['cuerpo_archivo']}»")
        bruto["cuerpo"] = hermano.read_text(encoding="utf-8")

    if archivo == personalizado:
        origen = "integrado_editado" if integrado.is_file() else "personalizado"
    else:
        origen = "integrado"
    return _completar(bruto, identificador, origen, archivo.stat().st_mtime)


def es_integrado(identificador):
    """True si el tipo viene del repositorio y por tanto se puede restaurar."""
    return _archivo(identificador, False).is_file()


def guardar(tipo):
    """Escribe personalizadas/<id>.json con el cuerpo en línea; devuelve el descriptor."""
    identificador = tipo.get("id")
    if not _llave_valida(identificador):
        raise ValueError(
            f"«{identificador}» no sirve como identificador: usa solo minúsculas sin "
            "tildes, números y guion bajo"
        )
    destino = _archivo(identificador, True)
    if destino.is_file() and tipo.get("_marca") not in (None, destino.stat().st_mtime):
        raise ValueError(
            "Alguien más guardó este tipo mientras lo editabas. Vuelve a abrirlo y aplica "
            "tus cambios sobre la versión nueva, o exporta el tuyo antes de sobrescribir."
        )
    PERSONALIZADAS.mkdir(parents=True, exist_ok=True)
    destino.write_text(_serializar(tipo), encoding="utf-8")
    return cargar(identificador)


def borrar(identificador):
    """Borra el override; si el tipo es integrado, vuelve al texto del repositorio."""
    if not _llave_valida(identificador):
        raise ValueError(f"«{identificador}» no es un identificador válido")
    archivo = _archivo(identificador, True)
    if archivo.is_file():
        archivo.unlink()
    return es_integrado(identificador)


def exportar(tipo):
    """Bytes de un .json portátil: el cuerpo va en línea, sin apuntar a otro archivo."""
    return _serializar(tipo).encode("utf-8")


def importar(contenido):
    """Bytes de un .json a descriptor -> (tipo, errores)."""
    try:
        bruto = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, [f"El archivo no es un .json legible: {error}"]
    if not isinstance(bruto, dict):
        return None, ["El archivo no contiene un objeto JSON con el tipo de otrosí."]
    bruto.pop("cuerpo_archivo", None)  # un import no puede apuntar a archivos del servidor
    identificador = bruto.get("id") or slug_identificador(bruto.get("nombre"))
    if not _llave_valida(identificador):
        return None, [
            f"El identificador «{identificador}» no es válido: solo minúsculas sin tildes, "
            "números y guion bajo."
        ]
    tipo = _completar(bruto, identificador, "importado", None)
    return tipo, validar(tipo)[0]


def _serializar(tipo):
    """Descriptor a texto JSON: sin las claves internas y con el cuerpo en línea."""
    limpio = {
        clave: valor
        for clave, valor in tipo.items()
        if not clave.startswith("_") and clave != "cuerpo_archivo"
    }
    limpio["campos"] = [
        {clave: valor for clave, valor in campo.items() if not clave.startswith("_")}
        for campo in tipo["campos"]
    ]
    return json.dumps(limpio, ensure_ascii=False, indent=2) + "\n"


def validar(tipo):
    """Revisa un descriptor completo -> (errores, avisos)."""
    errores, avisos = [], []
    if not _llave_valida(tipo.get("id")):
        errores.append(
            f"El identificador «{tipo.get('id')}» no sirve: solo minúsculas sin tildes, "
            "números y guion bajo, y como máximo 60 caracteres."
        )
    if not str(tipo.get("nombre") or "").strip():
        errores.append("El tipo necesita un nombre para poder elegirlo en la lista.")
    if not str(tipo.get("titulo") or "").strip():
        errores.append("Falta el título que va en el encabezado de todas las páginas.")
    if not _llave_valida(tipo.get("prefijo_archivo")):
        errores.append(
            "El prefijo del nombre de archivo solo puede llevar minúsculas sin tildes, "
            "números y guion bajo."
        )

    errores.extend(_revisar_campos(tipo))
    campos = por_clave(tipo)
    for clave_meta, tipos_validos, rotulo in (
        ("campo_nombre", ("texto",), "el nombre del archivo"),
        ("campo_fecha_archivo", ("fecha",), "la fecha del nombre del archivo"),
    ):
        referido = tipo.get(clave_meta)
        if referido is None:
            continue
        if referido not in campos:
            errores.append(f"«{clave_meta}» apunta a «{referido}», que no es un campo.")
        elif campos[referido]["tipo"] not in tipos_validos:
            errores.append(
                f"El campo que da {rotulo} («{referido}») tiene que ser de tipo "
                + " o ".join(tipos_validos)
            )

    cuerpo = str(tipo.get("cuerpo") or "")
    if not cuerpo.strip():
        errores.append("El cuerpo está vacío: pega el texto del otrosí.")
        return errores, avisos

    mas_errores, mas_avisos = revisar_cuerpo(cuerpo, marcadores(tipo))
    errores.extend(mas_errores)
    avisos.extend(mas_avisos)

    usados = {clave for clave, _ in _usos(cuerpo)}
    for campo in tipo["campos"]:
        # un campo con frases derivadas cuenta como usado si el cuerpo usa cualquiera de
        # ellas: lo normal es que la clave a secas («Femenino») no se imprima nunca
        propios = {campo["clave"], *campo["derivados"]}
        if not propios & usados:
            avisos.append(
                f"«{campo['etiqueta']}» no aparece en el cuerpo: se va a pedir en el "
                f"formulario y en el Excel, pero no se imprime en ninguna parte. Escribe "
                f"{{{{{campo['clave']}}}}} donde deba salir, o quita el campo."
            )
            continue
        for marcador in campo["derivados"]:
            if marcador not in usados:
                avisos.append(
                    f"La frase «{marcador}» de «{campo['etiqueta']}» no se usa en el cuerpo. "
                    f"Escribe {{{{{marcador}}}}} donde deba salir, o bórrala de la tabla."
                )
    return errores, avisos


def _revisar_campos(tipo):
    """Errores de la lista de campos: claves, tipos, opciones y referencias cruzadas."""
    errores = []
    campos = tipo.get("campos") or []
    if not campos:
        return ["El tipo no tiene ningún campo: declara al menos el que cambia de persona a persona."]

    # el contexto del render es plano: una clave de campo y una frase derivada compiten por
    # el mismo nombre de marcador, y quien pisara a quien dependería del orden
    ocupados = {}
    for campo in campos:
        for nombre in (campo.get("clave", ""), *(campo.get("derivados") or {})):
            ocupados.setdefault(nombre, []).append(campo.get("etiqueta") or campo.get("clave"))

    vistas, etiquetas_vistas = set(), set()
    fechas = {campo["clave"] for campo in campos if campo.get("tipo") == "fecha"}
    for numero, campo in enumerate(campos, start=1):
        clave, etiqueta = campo.get("clave", ""), campo.get("etiqueta", "")
        rotulo = f"«{etiqueta or clave or f'campo {numero}'}»"
        if not clave:
            errores.append(f"El campo {numero} no tiene clave.")
        elif not _CLAVE.match(clave):
            errores.append(
                f"La clave «{clave}» no vale: empieza por minúscula y usa solo minúsculas "
                "sin tildes, números y guion bajo (es lo que se escribe entre {{ }})."
            )
        elif clave in vistas:
            errores.append(f"La clave «{clave}» está repetida: cada campo necesita la suya.")
        vistas.add(clave)

        if not etiqueta:
            errores.append(f"El campo «{clave}» no tiene etiqueta.")
        elif etiqueta in etiquetas_vistas:
            errores.append(
                f"La etiqueta {rotulo} está repetida. Es el encabezado de la columna del "
                "Excel: con dos iguales no hay forma de saber cuál vale."
            )
        etiquetas_vistas.add(etiqueta)

        if campo.get("tipo") not in TIPOS_CAMPO:
            errores.append(
                f"{rotulo} tiene el tipo «{campo.get('tipo')}», que no existe. "
                f"Los tipos son: {', '.join(TIPOS_CAMPO)}."
            )
        if campo.get("tipo") == "lista" and len(opciones(campo)) < 2:
            errores.append(
                f"{rotulo} es de tipo lista y necesita al menos dos opciones: un desplegable "
                "con un solo valor no es una elección."
            )
        if repetidas := {o for o in opciones(campo) if opciones(campo).count(o) > 1}:
            # dos opciones iguales dejarían una frase derivada sin forma de distinguirlas
            errores.append(
                f"{rotulo} tiene opciones repetidas: "
                + ", ".join(f"«{opcion}»" for opcion in sorted(repetidas))
                + ". Deja una sola de cada una."
            )
        errores.extend(_revisar_derivados(campo, rotulo, ocupados))
        if campo.get("posterior_a"):
            if campo["posterior_a"] not in fechas:
                errores.append(
                    f"{rotulo} dice que va después de «{campo['posterior_a']}», que no es un "
                    "campo de fecha."
                )
            elif campo["posterior_a"] == clave:
                errores.append(f"{rotulo} no puede ir después de sí mismo.")
        for opcion in opciones(campo):
            for prohibido in ("|", "**"):
                if prohibido in str(opcion):
                    errores.append(
                        f"La opción «{opcion}» de {rotulo} lleva «{prohibido}», que rompe "
                        "las tablas o la negrita del documento."
                    )

    return errores


def _revisar_derivados(campo, rotulo, ocupados):
    """Errores de las frases derivadas de un campo: nombres, colisiones y cobertura."""
    errores = []
    derivados = campo.get("derivados") or {}
    if not derivados:
        return errores
    disponibles = opciones(campo)
    if not disponibles:
        return [
            f"{rotulo} tiene frases derivadas pero no tiene opciones. Una frase derivada "
            "depende de la opción elegida, así que el campo tiene que ser de tipo lista."
        ]
    for marcador, por_opcion in derivados.items():
        if not _CLAVE.match(str(marcador)):
            errores.append(
                f"La frase «{marcador}» de {rotulo} no sirve como marcador: empieza por "
                "minúscula y usa solo minúsculas sin tildes, números y guion bajo."
            )
        if len(ocupados.get(marcador, [])) > 1:
            errores.append(
                f"El nombre «{marcador}» está usado dos veces (en {', '.join(f'«{d}»' for d in ocupados[marcador])}). "
                "Un marcador solo puede venir de un sitio: cámbiale el nombre a uno de los dos."
            )
        if not isinstance(por_opcion, dict):
            errores.append(f"La frase «{marcador}» de {rotulo} no trae un valor por opción.")
            continue
        if faltan := [opcion for opcion in disponibles if not str(por_opcion.get(opcion) or "").strip()]:
            errores.append(
                f"La frase «{marcador}» de {rotulo} no dice qué imprimir cuando la opción es "
                + ", ".join(f"«{opcion}»" for opcion in faltan)
                + ". Si alguien elige eso, el documento no se genera."
            )
        for opcion, frase in por_opcion.items():
            for prohibido in ("|", "**"):
                if prohibido in str(frase):
                    errores.append(
                        f"La frase «{marcador}» de {rotulo} lleva «{prohibido}» en «{opcion}», "
                        "que rompe las tablas o la negrita del documento."
                    )
    return errores


def _usos(cuerpo):
    """[(clave, filtro)] de todos los marcadores bien formados del cuerpo."""
    return [
        documento.separar_marcador(coincidencia.group(1))
        for coincidencia in _MARCADOR.finditer(cuerpo)
    ]


def revisar_cuerpo(cuerpo, validos):
    """Revisa el Markdown de un cuerpo contra el conversor -> (errores, avisos)."""
    errores, avisos = [], []
    permitidos = set(validos)

    for clave, filtro in _usos(cuerpo):
        if not clave:
            errores.append("Hay un «{{ }}» vacío: escribe dentro el nombre del campo.")
        elif clave not in permitidos:
            errores.append(
                f"«{{{{{clave}}}}}» no es un campo de este tipo, así que el documento saldría "
                f"con un hueco. Campos disponibles: {', '.join(sorted(permitidos)) or 'ninguno'}."
            )
        if filtro and filtro not in documento.FILTROS:
            errores.append(
                f"El filtro «{filtro}» de «{{{{{clave}}}}}» no existe. Los que hay: "
                + ", ".join(sorted(documento.FILTROS))
            )

    # los marcadores se neutralizan antes de revisar la estructura porque es el orden real:
    # documento.py sustituye primero y parsea el Markdown después
    neutro = _MARCADOR.sub("VALOR", cuerpo)
    if "{{" in neutro or "}}" in neutro:
        errores.append(
            "Hay un «{{» o un «}}» sin pareja. Un marcador se escribe {{campo}}, con las dos "
            "llaves a cada lado y en la misma línea."
        )
    if "{%" in neutro or "{#" in neutro:
        errores.append(
            "Este editor no usa Jinja: «{%…%}» y «{#…#}» se imprimirían tal cual en el "
            "contrato. Solo existen los marcadores {{campo}}."
        )

    errores.extend(_revisar_estructura(neutro))
    avisos.extend(_avisar_estructura(neutro, cuerpo))
    return errores, avisos


def _revisar_estructura(neutro):
    """Lo que el conversor a .docx corrompería en silencio: tablas, viñetas y negrita."""
    errores = []
    lineas = neutro.splitlines()
    tabla, primera_fila, bloque = [], None, []

    def cerrar_tabla():
        nonlocal tabla, primera_fila
        tabla, primera_fila = [], None

    def cerrar_bloque():
        nonlocal bloque
        if bloque:
            texto = " ".join(linea for _, linea in bloque)
            if texto.count("**") % 2:
                errores.append(
                    f"Línea {bloque[0][0]}: hay un número impar de «**» en el párrafo. La "
                    "negrita se abre y se cierra con dos asteriscos; si falta uno, se imprimen "
                    "los asteriscos y la negrita queda al revés desde ahí."
                )
        bloque = []

    anterior_parrafo = False
    for numero, cruda in enumerate(lineas, start=1):
        linea = cruda.strip()
        if not linea:
            cerrar_bloque()
            cerrar_tabla()
            anterior_parrafo = False
            continue
        if linea == documento._SIN_BORDES:
            cerrar_bloque()
            cerrar_tabla()
            anterior_parrafo = False
            continue
        if linea.startswith("|"):
            if anterior_parrafo:
                errores.append(
                    f"Línea {numero}: empieza por «|» justo debajo de un párrafo, así que "
                    "corta el párrafo y convierte esto en una tabla. Deja una línea en blanco "
                    "antes de la tabla, o no empieces la línea por «|»."
                )
            cerrar_bloque()
            anterior_parrafo = False
            if documento._SEPARADOR_TABLA.match(linea):
                continue
            celdas = documento._celdas(linea)
            if primera_fila is None:
                primera_fila = len(celdas)
            elif len(celdas) != primera_fila:
                errores.append(
                    f"Línea {numero}: la fila tiene {len(celdas)} celdas y la primera de la "
                    f"tabla tiene {primera_fila}. La tabla se dimensiona con la primera fila y "
                    "las celdas de más se pierden sin avisar."
                )
            tabla.append(celdas)
            continue
        if linea.startswith("- "):
            if anterior_parrafo:
                errores.append(
                    f"Línea {numero}: empieza por «- » justo debajo de un párrafo, así que "
                    "corta el párrafo y convierte esto en una viñeta. Deja una línea en blanco "
                    "antes, o no empieces la línea por «- »."
                )
            cerrar_bloque()
            cerrar_tabla()
            anterior_parrafo = False
            if linea[2:].count("**") % 2:
                errores.append(
                    f"Línea {numero}: número impar de «**» en la viñeta."
                )
            continue
        if "|" in linea:
            errores.append(
                f"Línea {numero}: lleva un «|» en medio de un párrafo. El conversor usa «|» "
                "solo para las tablas, y aquí se imprimiría o rompería la estructura. Quítalo."
            )
        cerrar_tabla()
        bloque.append((numero, linea))
        anterior_parrafo = True

    cerrar_bloque()
    return errores


def _avisar_estructura(neutro, cuerpo):
    """Markdown que el conversor no entiende y por tanto imprime literal."""
    avisos = []
    lineas_neutras = neutro.splitlines()
    lineas_crudas = cuerpo.splitlines()
    sin_bordes_suelto = []

    for numero, cruda in enumerate(lineas_neutras, start=1):
        linea = cruda.strip()
        for prefijo, descripcion in _NO_SOPORTADO:
            if linea.startswith(prefijo):
                avisos.append(
                    f"Línea {numero}: parece {descripcion}. El conversor no lo entiende y lo "
                    f"imprime tal cual, con el «{prefijo}» incluido."
                )
        if _ENLACE.search(linea):
            avisos.append(
                f"Línea {numero}: un enlace «[texto](dirección)» se imprime literal, con los "
                "corchetes y los paréntesis. Escribe la dirección sola."
            )
        if len(_CURSIVA.findall(linea)) >= 2:
            avisos.append(
                f"Línea {numero}: los asteriscos sueltos no hacen cursiva; solo «**» hace "
                "negrita. Se imprimirían los asteriscos."
            )
        if linea == documento._SIN_BORDES:
            sin_bordes_suelto.append(numero)

    for numero in sin_bordes_suelto:
        siguientes = [l.strip() for l in lineas_neutras[numero:] if l.strip()]
        if not siguientes or not siguientes[0].startswith("|"):
            avisos.append(
                f"Línea {numero}: «{documento._SIN_BORDES}» solo hace efecto si la siguiente "
                "cosa es una tabla. Aquí no hace nada."
            )

    for numero, cruda in enumerate(lineas_crudas, start=1):
        interior = _MARCADOR.match(cruda.strip())
        if interior and numero > 1 and lineas_crudas[numero - 2].strip():
            continue  # a mitad de párrafo el valor no abre línea y no puede romper la estructura
        if interior:
            clave = documento.separar_marcador(interior.group(1))[0]
            avisos.append(
                f"Línea {numero}: la línea empieza con «{{{{{clave}}}}}». Si ese valor empezara "
                "por «|» o por «- » rompería la estructura del documento; la app ya lo rechaza "
                "al cargarlo, pero es el sitio más delicado de la plantilla."
            )
    return avisos
