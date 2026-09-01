"""Generador de otrosíes - front-end Streamlit."""

import ast
import hashlib
from datetime import date

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from otrosi import campos
from otrosi import documento
from otrosi import ia
from otrosi import masivo
from otrosi import tipos
from otrosi import transcripcion

from otrosi.campos import FECHA_MAXIMA, FECHA_MINIMA

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_ZIP = "application/zip"

TOPE_MENSAJES = 50  # con 300 filas la lista de errores puede tapar la pantalla entera

TOPE_RADIO = 4  # con más opciones que estas, un desplegable ocupa mucho menos

# Valores con que se previsualiza un campo al que no se le puso ejemplo.
EJEMPLO_POR_TIPO = {"texto": "(ejemplo)", "cedula": "1020345678", "entero": "1"}

ORIGENES = {
    "integrado": "viene con la app",
    "integrado_editado": "integrado, con cambios guardados aquí",
    "personalizado": "creado aquí",
}


def _etiqueta_opcion(campo, opcion):
    """'Femenino' -> 'Femenino — «la Teletrabajadora»' si el campo trae frases derivadas."""
    # se arma desde las frases del propio campo, así el formulario no puede
    # desincronizarse de lo que dirá el documento firmado
    frases = [
        por_opcion[opcion]
        for por_opcion in campo["derivados"].values()
        if opcion in por_opcion
    ]
    return f"{opcion} — «{frases[0]}»" if frases else str(opcion)


def _widget(tipo, campo):
    """Pinta el widget que corresponde al tipo del campo y devuelve su valor."""
    # la clave lleva el id del tipo: dos tipos que compartan un nombre de campo chocarían
    # en DuplicateWidgetID
    clave = f"campo_{tipo['id']}_{campo['clave']}"
    etiqueta, ayuda = campo["etiqueta"], campo["ayuda"] or None

    if campo["tipo"] in ("cedula", "entero"):
        return st.number_input(
            etiqueta, min_value=1, value=None, step=1, format="%d",
            placeholder="Solo números, sin puntos", help=ayuda, key=clave,
        )
    if campo["tipo"] == "fecha":
        return st.date_input(
            etiqueta,
            # la que rellena la app en el Excel viene puesta con la de hoy en el formulario
            value=date.today() if campo["opcional_en_hoja"] else None,
            min_value=FECHA_MINIMA,  # sin esto el calendario solo llega a hoy - 10 años
            max_value=date.today() if campo["no_futura"] else FECHA_MAXIMA,
            format="DD/MM/YYYY", help=ayuda, key=clave,
        )
    if opciones := tipos.opciones(campo):
        widget = st.radio if len(opciones) <= TOPE_RADIO else st.selectbox
        return widget(
            etiqueta, opciones, index=None,
            # la etiqueta lleva la frase que se va a imprimir, si el campo tiene alguna
            format_func=lambda opcion: _etiqueta_opcion(campo, opcion),
            help=ayuda, key=clave,
        )
    if campo["sugerencias"]:
        return st.selectbox(
            etiqueta, campo["sugerencias"], index=None, accept_new_options=True,
            placeholder="Selecciona o escribe", help=ayuda, key=clave,
        )
    return st.text_input(
        etiqueta, placeholder=campo["ejemplo"] or None, help=ayuda, key=clave
    )


def render_formulario(tipo):
    """Los campos del tipo; devuelve el payload plano que consume documento.py."""
    datos, grupo = {}, None
    for campo in tipo["campos"]:
        if campo["grupo"] and campo["grupo"] != grupo:
            grupo = campo["grupo"]
            st.subheader(grupo)
        datos[campo["clave"]] = _widget(tipo, campo)
    return datos


def resumen(tipo, datos):
    """Valores tal como quedarán impresos, para verificarlos antes de descargar."""
    # el mismo contexto que usa el .docx, así el resumen no puede desincronizarse
    impreso = documento.contexto(tipo, datos)
    filas = []
    for campo in tipo["campos"]:
        clave, valor = campo["clave"], datos.get(campo["clave"])
        if campo["tipo"] == "cedula":
            texto = f"No. {impreso.get(clave, '')}"
        elif campo["derivados"] and valor is not None:
            texto = _etiqueta_opcion(campo, valor)
        else:
            texto = impreso.get(clave, "" if valor is None else valor)
        filas.append((campo["etiqueta"], texto))
    return filas


def mostrar_resultado(tipo, resultado):
    st.divider()
    st.success("Documento generado.")

    st.subheader("Verifica antes de descargar")
    st.caption(
        "Refleja los datos del último «Generar otrosí». Si cambias un campo, "
        "vuelve a generarlo."
    )
    for etiqueta, valor in resumen(tipo, resultado["datos"]):
        st.markdown(f"- **{etiqueta}:** {valor}")

    # on_click="ignore" evita el rerun que antes borraba este bloque al descargar
    st.download_button(
        "Descargar .docx",
        data=resultado["docx"],
        file_name=resultado["archivo"],
        mime=MIME_DOCX,
        on_click="ignore",
        type="primary",
    )

    with st.expander("Ver Markdown intermedio (no es el .docx)"):
        st.caption("No incluye el encabezado, el pie ni el logo: esos los arma documento.py.")
        st.code(resultado["markdown"], language="markdown")


def _pestaña_individual(tipo):
    # el formulario evita que cada tecleo dispare un rerun, y deja los valores en
    # pantalla alineados con el documento que se generó
    with st.form(f"otrosi_{tipo['id']}"):
        datos = render_formulario(tipo)
        enviado = st.form_submit_button("Generar otrosí", type="primary")

    if enviado:
        problemas = []
        if faltantes := campos.faltantes(tipo, datos):
            etiquetas = tipos.etiquetas(tipo)
            problemas.append(
                "Faltan campos obligatorios: "
                + ", ".join(etiquetas[clave] for clave in faltantes)
            )
        # los mismos caracteres prohibidos que en la carga masiva: la validación vive en
        # campos.py justamente para que los dos modos no puedan divergir
        problemas.extend(campos.revisar(tipo, datos))
        if problemas:
            # no dejar descargable el documento anterior junto a un error
            st.session_state.pop("resultado", None)
            st.error("\n\n".join(problemas))
        else:
            md = documento.render_markdown(tipo, datos)
            st.session_state["resultado"] = {
                "datos": datos,
                "markdown": md,
                "docx": documento.markdown_a_docx(md, tipo["titulo"]),
                "archivo": documento.nombre_archivo(tipo, datos),
            }

    if resultado := st.session_state.get("resultado"):
        mostrar_resultado(tipo, resultado)


def _vista_previa(tipo, registros):
    """Filas válidas con los filtros del .docx: aquí se ve un «4 de marzo» al revés."""
    return [
        {"Fila": registro["fila"], "Archivo": registro["archivo"],
         **dict(resumen(tipo, registro["datos"]))}
        for registro in registros
    ]


def _recortar(mensajes):
    """Recorta la lista para no tapar la pantalla: 60 mensajes -> los 50 y «y 10 más»."""
    if len(mensajes) <= TOPE_MENSAJES:
        return mensajes
    return mensajes[:TOPE_MENSAJES] + [f"y {len(mensajes) - TOPE_MENSAJES} más"]


def _lista(mensajes):
    """Los mensajes como viñetas de Markdown para un st.error o un st.warning."""
    return "\n".join(f"- {mensaje}" for mensaje in _recortar(mensajes))


def _campo_del_lote(tipo):
    """El campo de fecha que rellena el selector del lote, si el tipo tiene alguno."""
    for campo in tipo["campos"]:
        if campo["opcional_en_hoja"] and campo["tipo"] == "fecha":
            return campo
    return None


def _pestaña_masiva(tipo):
    st.subheader("Paso 1 · Descarga la plantilla")
    st.caption(
        f"Una fila por persona, hasta {masivo.MAXIMO_FILAS}. Las instrucciones y los "
        "valores permitidos van en la segunda hoja."
    )
    st.download_button(
        "Descargar plantilla",
        # la función y no los bytes: no se arma el .xlsx hasta que se hace clic
        data=lambda: masivo.construir_plantilla(tipo),
        file_name=f"plantilla_{tipo['id']}.xlsx",
        mime=MIME_XLSX,
        on_click="ignore",
        key="masivo_plantilla",
    )

    st.subheader("Paso 2 · Sube la plantilla diligenciada")
    archivo = st.file_uploader("Archivo .xlsx", type=["xlsx"], key="masivo_archivo")
    del_lote = _campo_del_lote(tipo)
    fecha_lote = date.today()
    if del_lote:
        fecha_lote = st.date_input(
            del_lote["etiqueta"] + " del lote",
            value=date.today(),
            min_value=FECHA_MINIMA,
            max_value=FECHA_MAXIMA,
            format="DD/MM/YYYY",
            help="Rellena las filas que dejes en blanco en esa columna de la hoja.",
            key="masivo_fecha_firma",
        )
    if archivo is None:
        st.session_state.pop("masivo", None)
        return

    contenido = archivo.getvalue()
    huella = hashlib.sha256(contenido + tipo["id"].encode()).hexdigest()
    guardado = st.session_state.get("masivo")
    if guardado and guardado["huella"] != huella:
        # el análogo masivo de no dejar descargable el documento anterior junto a un error
        st.session_state.pop("masivo", None)

    with st.spinner("Leyendo el archivo…"):
        registros, errores = masivo.leer_libro(tipo, contenido, fecha_lote)
    avisos = [aviso for registro in registros for aviso in registro["avisos"]]
    validos = [registro for registro in registros if not registro["errores"]]

    if errores:
        st.error(_lista(errores), title="Corrige el archivo y vuelve a subirlo")
    if avisos:
        st.warning(_lista(avisos), title="Revisa estos puntos; no impiden generar")
    if validos:
        st.subheader("Paso 3 · Verifica antes de generar")
        st.caption("Los valores ya están como saldrán impresos en el .docx.")
        st.dataframe(_vista_previa(tipo, validos), hide_index=True)

    if st.button(
        f"Generar {len(validos)} otrosíes",
        type="primary",
        disabled=bool(errores) or not validos,
        key="masivo_generar",
    ):
        with st.status(f"Generando {len(validos)} documentos…") as estado:
            barra = st.progress(0.0)
            paquete, fallos, generados = masivo.generar_zip(
                tipo,
                validos,
                lambda hechos, total: barra.progress(hechos / total, f"{hechos} de {total}"),
            )
            estado.update(label=f"{len(generados)} documentos listos.", state="complete")
        st.session_state["masivo"] = {
            "huella": huella,
            "zip": paquete,
            "fallos": fallos,
            "archivos": generados,  # solo los que de verdad entraron al .zip
        }

    guardado = st.session_state.get("masivo")
    if not guardado:
        return
    st.divider()
    st.success(f"{len(guardado['archivos'])} documentos generados.")
    st.download_button(
        "Descargar .zip",
        data=guardado["zip"],
        file_name=f"{tipo['prefijo_archivo']}_{fecha_lote:%Y%m%d}.zip",
        mime=MIME_ZIP,
        on_click="ignore",
        type="primary",
        key="masivo_zip",
    )
    if guardado["fallos"]:
        # una fila que falle al renderizar no mata el lote: el resto se entrega, anotado
        st.warning(_lista(guardado["fallos"]), title="Filas que no se pudieron generar")
    with st.expander(f"Ver los {len(guardado['archivos'])} archivos del .zip"):
        for nombre in guardado["archivos"]:
            st.markdown(f"- `{nombre}`")


# ---------------------------------------------------------------- editor de tipos


COLUMNAS = {
    "Clave": "Lo que se escribe entre {{ }} en el cuerpo. Minúsculas, sin tildes.",
    "Etiqueta": "El rótulo del formulario y el encabezado de la columna del Excel.",
    "Tipo": "Decide el widget, cómo se lee la celda de Excel y cómo se imprime.",
    "Obligatorio": "Si está vacío, no se genera el documento.",
    "Opciones": "Solo para los campos de tipo lista. Sepáralas con «;».",
    "Ejemplo": "Se muestra en las instrucciones del Excel y en la previsualización.",
    "Ayuda": "Texto del iconito de ayuda en el formulario.",
    "Grupo": "Subtítulo bajo el que se agrupa en el formulario. Opcional.",
    "Sugerencias": "Atajos de un desplegable que igual acepta escribir otra cosa. Con «;».",
    "No futura": "Rechaza una fecha posterior a hoy.",
    "Posterior a": "Clave de otra fecha: avisa si esta es anterior.",
    "Artículo minúscula": "Avisa si el texto empieza por «La», «El», «Los» o «Las».",
    "La rellena la app": "Puede ir en blanco: en el Excel la pone el lote; en el formulario, hoy.",
}


def _a_tabla(tipo):
    """Los campos como filas para el st.data_editor."""
    return [
        {
            "Clave": campo["clave"],
            "Etiqueta": campo["etiqueta"],
            "Tipo": campo["tipo"],
            "Obligatorio": campo["obligatorio"],
            "Opciones": "; ".join(campo["opciones"]),
            "Ejemplo": str(campo["ejemplo"]),
            "Ayuda": campo["ayuda"],
            "Grupo": campo["grupo"],
            "Sugerencias": "; ".join(campo["sugerencias"]),
            "No futura": campo["no_futura"],
            "Posterior a": campo["posterior_a"] or "",
            "Artículo minúscula": campo["articulo_minuscula"],
            "La rellena la app": campo["opcional_en_hoja"],
        }
        for campo in tipo["campos"]
    ]


def _de_tabla(filas, anteriores):
    """Las filas del editor de vuelta a campos, conservando lo que la tabla no muestra."""
    # los sinónimos y las frases derivadas no se editan en esta tabla: se conservan por
    # clave, para que editar una etiqueta no borre en silencio lo que ya estaba
    previos = {campo["clave"]: campo for campo in anteriores}
    # y si la clave no aparece pero el número de filas no cambió, se conserva por posición:
    # eso cubre el renombrado de una clave. Con filas añadidas o borradas no se adivina,
    # porque asignarle las frases al campo equivocado es peor que perderlas
    mismo_numero = len(filas) == len(anteriores)
    nuevos = []
    for indice, fila in enumerate(filas):
        clave = str(fila.get("Clave") or "").strip()
        if not clave and not str(fila.get("Etiqueta") or "").strip():
            continue  # fila que el editor añadió y quedó vacía
        heredado = previos.get(clave) or (anteriores[indice] if mismo_numero else {})
        campo = dict(heredado)
        campo.update({
            "clave": clave,
            "etiqueta": str(fila.get("Etiqueta") or "").strip(),
            "tipo": str(fila.get("Tipo") or "texto"),
            "obligatorio": bool(fila.get("Obligatorio")),
            "opciones": _partir(fila.get("Opciones")),
            "ejemplo": str(fila.get("Ejemplo") or "").strip(),
            "ayuda": str(fila.get("Ayuda") or "").strip(),
            "grupo": str(fila.get("Grupo") or "").strip(),
            "sugerencias": _partir(fila.get("Sugerencias")),
            "no_futura": bool(fila.get("No futura")),
            "posterior_a": str(fila.get("Posterior a") or "").strip() or None,
            "articulo_minuscula": bool(fila.get("Artículo minúscula")),
            "opcional_en_hoja": bool(fila.get("La rellena la app")),
        })
        nuevos.append(campo)
    return nuevos


def _partir(texto):
    """'a; b ; ' -> ['a', 'b']."""
    return [parte.strip() for parte in str(texto or "").split(";") if parte.strip()]


def _fila_ejemplo(tipo):
    """Una fila cruda con el ejemplo de cada campo, para previsualizar."""
    fila = {}
    for campo in tipo["campos"]:
        ejemplo = str(campo["ejemplo"]).strip()
        opciones = tipos.opciones(campo)
        # un ejemplo que ya no es una de las opciones (porque se renombraron) no puede
        # tumbar la previsualización: se toma la primera y se sigue
        if opciones and ejemplo not in opciones:
            ejemplo = opciones[0]
        elif not ejemplo:
            ejemplo = (
                date.today().isoformat() if campo["tipo"] == "fecha"
                else EJEMPLO_POR_TIPO.get(campo["tipo"], "(ejemplo)")
            )
        fila[campo["clave"]] = ejemplo
    return fila


def _previsualizar(tipo):
    """Renderiza el tipo con los ejemplos y pinta el .md y el .docx de muestra."""
    datos, errores, _ = campos.normalizar(tipo, _fila_ejemplo(tipo))
    if errores:
        st.warning(
            _lista(errores),
            title="Los ejemplos de los campos no se pudieron convertir; corrígelos para ver "
                  "la muestra completa",
        )
    try:
        md = documento.render_markdown(tipo, datos)
    except (ValueError, KeyError) as error:
        st.error(str(error), title="No se pudo renderizar")
        return
    st.download_button(
        "Descargar el .docx de muestra",
        data=documento.markdown_a_docx(md, tipo["titulo"]),
        file_name=f"muestra_{tipo['id']}.docx",
        mime=MIME_DOCX,
        on_click="ignore",
        type="primary",
        key="tipos_muestra",
    )
    st.caption(
        "Ábrelo: es la única forma de ver de verdad si una tabla quedó partida o si un "
        "título de Markdown se imprimió literal."
    )
    with st.expander("Ver el Markdown intermedio"):
        st.code(md, language="markdown")


def _columna_opcion(opcion):
    """'Femenino' -> 'Si es «Femenino»'.

    El rótulo no es la opción a secas para que no pueda coincidir con «Marcador», que es
    la otra columna: si coincidiera, las dos irían a la misma clave del dict y la frase
    se perdería sin decir nada.
    """
    return f"Si es «{opcion}»"


def _firma_a_opciones(firma):
    """La firma de vuelta a la lista de opciones; [] si no hay firma o está rota."""
    try:
        return list(ast.literal_eval(firma or "[]"))
    except (ValueError, SyntaxError):
        return []


def _a_filas_derivados(derivados, opciones):
    """Las frases como filas para el editor: [{'Marcador': 'identificado', …}]."""
    return [
        {
            "Marcador": marcador,
            **{_columna_opcion(o): str(por_opcion.get(o, "")) for o in opciones},
        }
        for marcador, por_opcion in derivados.items()
    ]


def _de_filas_derivados(filas, opciones):
    """Las filas del editor de vuelta a {marcador: {opción: frase}}."""
    derivados = {}
    for fila in filas:
        marcador = str(fila.get("Marcador") or "").strip()
        if not marcador:
            continue  # fila que el editor añadió y quedó vacía
        derivados[marcador] = {
            opcion: str(fila.get(_columna_opcion(opcion)) or "").strip()
            for opcion in opciones
        }
    return derivados


def _generador_concordancia(campo, opciones, llave_base):
    """Los dos cuadros del sustantivo y el botón que escribe la concordancia de género."""
    clave = campo["clave"]
    izquierda, derecha, boton = st.columns([2, 2, 2])
    with izquierda:
        femenino = st.text_input(
            "Sustantivo en femenino", key=f"tipos_fem_{clave}",
            placeholder="Teletrabajadora", label_visibility="collapsed",
        )
    with derecha:
        masculino = st.text_input(
            "Sustantivo en masculino", key=f"tipos_masc_{clave}",
            placeholder="Teletrabajador", label_visibility="collapsed",
        )
    with boton:
        pulsado = st.button(
            "Generar y reemplazar", key=f"tipos_gen_{clave}",
            help="Escribe las frases con el artículo y las contracciones ya resueltas "
                 "(«de» + «el X» da «del X»). Reemplaza lo que haya en la tabla.",
        )
    if not pulsado:
        return
    try:
        nuevos = tipos.generar_concordancia(femenino, masculino, opciones)
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state[llave_base] = _a_filas_derivados(nuevos, opciones)
    # la base cambia, así que el delta del editor anterior ya no aplica. La clave del
    # widget lleva la firma de las opciones dentro, por eso se borran todas las suyas
    for llave in [k for k in st.session_state.keys() if k.startswith(f"tipos_der_{clave}_")]:
        st.session_state.pop(llave, None)
    st.rerun()


def _frases_derivadas(campos_borrador):
    """Una tabla de frases por cada campo con opciones; devuelve los campos actualizados."""
    elegibles = [campo for campo in campos_borrador if tipos.opciones(campo)]
    if not elegibles:
        return campos_borrador
    # NO va dentro de un st.expander: sin `key` el expander vuelve a su estado inicial en
    # cada rerun, y editar una celda provoca uno, así que se cerraba en cada tecla
    st.subheader("Frases derivadas")
    st.caption(
        "Una opción puede arrastrar frases que se imprimen en el documento. Es lo que "
        "resuelve la concordancia de género: eliges «Femenino» y sale «la Teletrabajadora», "
        "«a la Teletrabajadora»… El **marcador** es lo que escribes entre `{{ }}` en el "
        "cuerpo."
    )
    for campo in elegibles:
        opciones = tipos.opciones(campo)
        st.markdown(f"**{campo['etiqueta']}** · {' / '.join(opciones)}")
        filas = _tabla_derivados(campo, opciones)
        campo["derivados"] = _de_filas_derivados(filas, opciones)
        if len(opciones) == 2:
            _generador_concordancia(campo, opciones, f"tipos_derivados_{campo['clave']}")
        st.divider()
    return campos_borrador


def _tabla_derivados(campo, opciones):
    """El editor de frases de un campo, con su base fija y a prueba de cambios de opción."""
    clave = campo["clave"]
    llave_base, llave_firma = f"tipos_derivados_{clave}", f"tipos_opciones_{clave}"
    # los títulos de las columnas llevan el texto de la opción, así que si las opciones
    # cambian hay que reescribir la base: si no, _de_filas_derivados buscaría columnas que
    # ya no existen y vaciaría todas las frases sin decir nada
    firma = repr(opciones)
    if llave_base not in st.session_state:
        st.session_state[llave_base] = _a_filas_derivados(campo["derivados"], opciones)
        st.session_state[llave_firma] = firma
    elif st.session_state.get(llave_firma) != firma:
        previas = _firma_a_opciones(st.session_state.get(llave_firma))
        st.session_state[llave_base] = _remapear_derivados(
            st.session_state[llave_base], [o for o in previas if o], opciones
        )
        st.session_state[llave_firma] = firma
    # la clave del widget lleva la firma de las opciones: al cambiar las columnas nace un
    # editor nuevo, y el delta del anterior no se puede aplicar sobre una tabla distinta
    return st.data_editor(
        [dict(fila) for fila in st.session_state[llave_base]],  # copia, como arriba
        num_rows="dynamic", hide_index=True, key=f"tipos_der_{clave}_{firma}",
        column_config={
            "Marcador": st.column_config.TextColumn(
                "Marcador", help="Lo que se escribe entre {{ }}, en minúsculas."
            ),
        },
    )


def _remapear_derivados(filas, previas, opciones):
    """Reescribe las filas cuando cambian las opciones, conservando lo que se pueda.

    Por nombre si la opción sigue existiendo; y si no, por posición cuando el número de
    opciones no cambió, que es el caso de renombrar una. Con opciones añadidas o
    quitadas no se adivina: esa columna queda vacía y el revisor la reclama.
    """
    misma_cantidad = len(previas) == len(opciones)
    nuevas = []
    for fila in filas:
        remapeada = {"Marcador": fila.get("Marcador", "")}
        for indice, opcion in enumerate(opciones):
            if _columna_opcion(opcion) in fila:
                valor = fila[_columna_opcion(opcion)]
            elif misma_cantidad:
                valor = fila.get(_columna_opcion(previas[indice]), "")
            else:
                valor = ""
            remapeada[_columna_opcion(opcion)] = valor
        nuevas.append(remapeada)
    return nuevas


def _editor(base):
    """El formulario de edición; devuelve un descriptor nuevo, sin tocar la base."""
    borrador = dict(base)
    st.text_input("Nombre del tipo", key="tipos_nombre")
    columnas = st.columns(2)
    with columnas[0]:
        st.text_input(
            "Título del encabezado", key="tipos_titulo",
            help="Sale centrado arriba en todas las páginas, al lado del logo.",
        )
    with columnas[1]:
        st.text_input(
            "Prefijo del nombre de archivo", key="tipos_prefijo",
            help="El .docx se llamará «prefijo_nombre_fecha.docx». Minúsculas y guion bajo.",
        )
    borrador["nombre"] = st.session_state["tipos_nombre"]
    borrador["titulo"] = st.session_state["tipos_titulo"]
    borrador["prefijo_archivo"] = st.session_state["tipos_prefijo"]

    st.subheader("Campos")
    st.caption(
        "Un campo por fila. La **clave** es lo que escribes entre `{{ }}` en el cuerpo; "
        "la **etiqueta** es lo que ve quien diligencia."
    )
    # se le pasa la tabla de cuando se abrió el tipo, NO la ya editada: con `key`, el editor
    # guarda las ediciones como un delta contra los datos que recibe, y realimentarle el
    # resultado las aplicaría dos veces
    filas = st.data_editor(
        # una copia: el widget no puede mutar la base que le entregamos, o dejaría de ser
        # la referencia fija contra la que se calcula el delta
        [dict(fila) for fila in st.session_state["tipos_campos_base"]],
        num_rows="dynamic",
        hide_index=True,
        key="tipos_campos",
        column_config={
            "Tipo": st.column_config.SelectboxColumn(
                "Tipo", options=list(tipos.TIPOS_CAMPO), help=COLUMNAS["Tipo"], required=True
            ),
            **{
                nombre: (
                    st.column_config.CheckboxColumn(nombre, help=ayuda)
                    if nombre in ("Obligatorio", "No futura", "Artículo minúscula",
                                  "La rellena la app")
                    else st.column_config.TextColumn(nombre, help=ayuda)
                )
                for nombre, ayuda in COLUMNAS.items()
                if nombre != "Tipo"
            },
        },
    )
    borrador["campos"] = [
        tipos.completar_campo(campo) for campo in _de_tabla(filas, base["campos"])
    ]

    # las frases derivadas no aplican a un tipo recién generado por la IA: un campo
    # como «Ciudad: Bogotá/Medellín» no es de género, y ofrecer el generador de
    # concordancia ahí sería una herramienta que no viene al caso. Es transitorio:
    # tipos.cargar recalcula _origen en cuanto el tipo se guarda y se reabre
    if base.get("_origen") != "automatico" and st.button(
        "Añadir un campo de género", key="tipos_add_genero",
        help="Crea el campo con sus dos opciones, sus sinónimos y las cinco frases de "
             "concordancia ya escritas. Luego puedes cambiarle el sustantivo.",
    ):
        # la base nueva parte de los campos YA editados, no de la de apertura: así pulsar
        # este botón no descarta lo que se llevaba escrito en la tabla
        ampliados = [*borrador["campos"], tipos.campo_genero()]
        # hay que mover las dos: la tabla no lleva las frases derivadas, y _de_tabla las
        # recupera del descriptor base. Actualizar solo una las perdería en el rerun
        st.session_state["borrador"] = {**base, "campos": ampliados}
        st.session_state["tipos_campos_base"] = _a_tabla({"campos": ampliados})
        st.session_state.pop("tipos_campos", None)
        st.rerun()

    claves_texto = [c["clave"] for c in borrador["campos"] if c["tipo"] == "texto"]
    claves_fecha = [c["clave"] for c in borrador["campos"] if c["tipo"] == "fecha"]
    columnas = st.columns(2)
    with columnas[0]:
        borrador["campo_nombre"] = st.selectbox(
            "Campo que da el nombre del archivo", [None] + claves_texto,
            index=([None] + claves_texto).index(borrador["campo_nombre"])
            if borrador["campo_nombre"] in claves_texto else 0,
            key="tipos_campo_nombre",
        )
    with columnas[1]:
        borrador["campo_fecha_archivo"] = st.selectbox(
            "Campo que da la fecha del archivo", [None] + claves_fecha,
            index=([None] + claves_fecha).index(borrador["campo_fecha_archivo"])
            if borrador["campo_fecha_archivo"] in claves_fecha else 0,
            key="tipos_campo_fecha",
        )

    if base.get("_origen") != "automatico":
        borrador["campos"] = _frases_derivadas(borrador["campos"])

    st.subheader("Cuerpo del otrosí")
    ayuda, texto = st.columns([1, 3])
    with ayuda:
        st.caption("**Marcadores disponibles**")
        for clave in tipos.marcadores(borrador):
            st.code(f"{{{{{clave}}}}}", language=None)
        st.caption(
            "**Markdown que existe**\n\n"
            "- párrafos separados por una línea en blanco\n"
            "- `**negrita**`\n"
            "- viñetas con `- `\n"
            "- tablas `| a | b |`\n\n"
            "Nada más: un `#` o un `[enlace](x)` se imprimen tal cual. "
            "Ninguna línea de un párrafo puede empezar por `- ` ni por `|`."
        )
    with texto:
        borrador["cuerpo"] = st.text_area(
            "Pega aquí el texto del otrosí", height=520, key="tipos_cuerpo",
            label_visibility="collapsed",
        )
    return borrador


def _acciones(borrador, errores):
    """Los botones de guardar, descartar y borrar/restaurar."""
    integrado = tipos.es_integrado(borrador["id"])
    botones = st.columns(3)
    with botones[0]:
        if st.button("Guardar", type="primary", disabled=bool(errores), key="tipos_guardar"):
            try:
                tipos.guardar(borrador)
            except (OSError, ValueError) as error:
                st.error(str(error), title="No se pudo guardar")
            else:
                st.session_state.pop("borrador", None)
                st.session_state["tipos_mensaje"] = f"Guardado «{borrador['nombre']}»."
                st.rerun()
    with botones[1]:
        if st.button("Descartar cambios", key="tipos_descartar"):
            st.session_state.pop("borrador", None)
            st.rerun()
    with botones[2]:
        rotulo = "Restaurar el original" if integrado else "Borrar el tipo"
        if st.button(rotulo, key="tipos_borrar"):
            tipos.borrar(borrador["id"])
            st.session_state.pop("borrador", None)
            st.session_state["tipos_mensaje"] = (
                f"«{borrador['id']}» volvió al texto original."
                if integrado else f"Se borró «{borrador['id']}»."
            )
            st.rerun()


def _abrir(borrador, avisos_transcripcion=()):
    """Deja el tipo en session_state como base y siembra los widgets del editor."""
    st.session_state["borrador"] = borrador
    # los avisos de una transcripción automática son de ESTE borrador: se fijan aquí,
    # junto con todo lo demás que hay que sembrar al abrir un tipo, para que abrir
    # cualquier otro los borre y para que sobrevivan al rerun de más abajo
    st.session_state["tipos_transcripcion"] = list(avisos_transcripcion)
    st.session_state["tipos_nombre"] = borrador["nombre"]
    st.session_state["tipos_titulo"] = borrador["titulo"]
    st.session_state["tipos_prefijo"] = borrador["prefijo_archivo"]
    st.session_state["tipos_cuerpo"] = borrador["cuerpo"]
    # el delta del editor anterior no puede sobrevivir a abrir otro tipo; y las bases de
    # las frases están indexadas por clave de campo, así que dos tipos con un campo del
    # mismo nombre se pisarían las tablas
    for llave in list(st.session_state.keys()):
        if llave.startswith(("tipos_campos", "tipos_der_", "tipos_derivados_",
                             "tipos_opciones_")):
            st.session_state.pop(llave, None)
    st.session_state["tipos_campos_base"] = _a_tabla(borrador)
    for campo in borrador["campos"]:
        if opciones := tipos.opciones(campo):
            st.session_state[f"tipos_derivados_{campo['clave']}"] = _a_filas_derivados(
                campo["derivados"], opciones
            )
            st.session_state[f"tipos_opciones_{campo['clave']}"] = " ".join(opciones)
    st.rerun()


def _clave_gemini():
    """La clave de .streamlit/secrets.toml, o None si no hay ni un archivo de secretos.

    st.secrets.get(...) no basta: cuando no existe NINGÚN secrets.toml, leer cualquier
    clave lanza StreamlitSecretNotFoundError en vez de devolver None (hereda de
    FileNotFoundError, no de KeyError, así que el .get() de Mapping no lo atrapa solo).
    """
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except StreamlitSecretNotFoundError:
        return None


def _inferir_campos(encontrados, cuerpo):
    """Las palabras marcadas -> ([campo completo, ...], avisos); nunca hace fallar la subida.

    Un fallo de la IA (sin clave configurada, red caída, respuesta cortada...) degrada
    a "sin campos inferidos": la transcripción sigue abriendo el editor igual que hoy.
    """
    if not encontrados:
        return [], []
    try:
        propuestas = ia.proponer_campos(encontrados, cuerpo, _clave_gemini())
    except ValueError as error:
        return [], [f"No se pudieron inferir los campos con IA: {error}"]

    campos_generados = []
    for indice, variable in enumerate(encontrados):
        propuesta = dict(propuestas[indice]) if indice < len(propuestas) else {}
        # tipos.slug_identificador garantiza una clave válida pase lo que proponga la IA;
        # si no propuso nada para esta variable, se deriva de la palabra original
        clave = tipos.slug_identificador(propuesta.get("clave") or variable) or f"campo_{indice + 1}"
        propuesta["clave"] = clave
        propuesta.setdefault("etiqueta", variable)
        campos_generados.append(tipos.completar_campo(propuesta))
    return campos_generados, []


def _catalogo(disponibles):
    """La lista de tipos con sus acciones, cuando no se está editando ninguno."""
    st.caption(
        "Aquí se define qué otrosíes puede generar la app. Un tipo son sus campos y el "
        "texto del documento, con los campos marcados entre llaves."
    )
    if mensaje := st.session_state.pop("tipos_mensaje", None):
        st.success(mensaje)

    arriba = st.columns([1.2, 2])
    with arriba[0]:
        if st.button("Nuevo tipo, vacío", key="tipos_nuevo"):
            _abrir(tipos.nuevo())
        word = st.file_uploader(
            "Nuevo tipo, automático", type=["docx"], key="tipos_docx",
            help="Sube el .docx del otrosí. Se transcribe el cuerpo a Markdown y se "
                 "abre como tipo nuevo; los campos y los {{marcadores}} los defines tú.",
        )

    if word is not None:
        # la huella evita que el archivo, que sigue cargado tras el rerun de _abrir,
        # reabra el mismo borrador en cada rerun posterior (p. ej. al descartar cambios)
        huella = hashlib.sha256(word.getvalue()).hexdigest()
        if st.session_state.get("tipos_docx_visto") != huella:
            st.session_state["tipos_docx_visto"] = huella
            # sin esto la pantalla queda inmóvil varios segundos mientras se consulta la
            # IA, y es fácil pensar que la app se colgó; el patrón es el mismo que ya usa
            # _pestaña_masiva para generar el .zip
            with st.status("Procesando el documento…", expanded=True) as estado:
                try:
                    estado.update(label="Leyendo el .docx…")
                    cuerpo, avisos_docx = transcripcion.leer_docx(word.getvalue())
                except ValueError as error:
                    estado.update(label="No se pudo leer el .docx", state="error")
                    st.error(str(error), title="No se pudo leer el .docx")
                else:
                    if not cuerpo.strip():
                        estado.update(label="El .docx no trae texto", state="error")
                        st.error(
                            "El .docx no trae texto en el cuerpo. El encabezado, el pie "
                            "y las imágenes no se leen: lo que se transcribe es el texto "
                            "del documento."
                        )
                    else:
                        estado.update(label="Buscando palabras marcadas con «…»…")
                        encontrados = transcripcion.marcadores_entre_guillemets(cuerpo)
                        cuerpo = transcripcion.convertir_guillemets_a_marcadores(cuerpo)

                        if encontrados:
                            estado.update(
                                label=f"Consultando la IA para {len(encontrados)} campos…"
                            )
                        campos_generados, avisos_ia = _inferir_campos(encontrados, cuerpo)
                        cuerpo = transcripcion.renombrar_marcadores(cuerpo, encontrados, campos_generados)

                        estado.update(label="Listo.", state="complete")

                        borrador = tipos.nuevo(word.name.rsplit(".", 1)[0] or "Otrosí nuevo")
                        borrador["cuerpo"] = cuerpo
                        if campos_generados:
                            borrador["campos"] = campos_generados
                        # marca transitoria: _frases_derivadas no aplica a campos que la IA
                        # acaba de proponer (un «Ciudad: Bogotá/Medellín» no es de género), y
                        # tipos.cargar la recalcula sola en cuanto el tipo se guarda y se
                        # reabre, así que no hace falta limpiarla a mano en ningún otro sitio
                        borrador["_origen"] = "automatico"
                        _abrir(borrador, [*avisos_docx, *avisos_ia])

    st.divider()
    for identificador, tipo in disponibles.items():
        errores, avisos = tipos.validar(tipo)
        with st.container(border=True):
            st.markdown(
                f"**{tipo['nombre']}** · `{identificador}` · "
                f"{ORIGENES.get(tipo['_origen'], tipo['_origen'])}"
            )
            st.caption(
                f"{len(tipo['campos'])} campos · "
                + (f"⚠ {len(errores)} errores" if errores else "sin errores")
                + (f" · {len(avisos)} avisos" if avisos else "")
            )
            acciones = st.columns(2)
            with acciones[0]:
                if st.button("Editar", key=f"editar_{identificador}"):
                    _abrir(tipos.cargar(identificador))
            with acciones[1]:
                if st.button("Duplicar", key=f"duplicar_{identificador}"):
                    copia = tipos.cargar(identificador)
                    copia["nombre"] = f"{copia['nombre']} (copia)"
                    copia["id"] = tipos.slug_identificador(copia["nombre"])
                    copia["_origen"], copia["_marca"] = "nuevo", None
                    copia.pop("cuerpo_archivo", None)
                    _abrir(copia)


def _pestaña_tipos():
    base = st.session_state.get("borrador")
    if base is None:
        _catalogo(tipos.listar())
        return

    st.caption(
        f"Editando **{base['nombre']}** · identificador `{base['id']}`. "
        "Nada se guarda hasta que pulses «Guardar»."
    )
    # se lee con .get y no con .pop: el editor rerenderiza en cada tecla del cuerpo, y
    # un .pop lo borraría en el primer keystroke en vez de dejarlo mientras se revisa
    if transcritos := st.session_state.get("tipos_transcripcion"):
        st.warning(
            _lista(transcritos),
            title="El .docx se transcribió con cambios; revísalos contra el original",
        )
    borrador = _editor(base)
    borrador["id"] = borrador["id"] or tipos.slug_identificador(borrador["nombre"])

    st.divider()
    errores, avisos = tipos.validar(borrador)
    if errores:
        st.error(_lista(errores), title=f"{len(errores)} cosas que hay que corregir")
    if avisos:
        st.warning(_lista(avisos), title=f"{len(avisos)} avisos; no impiden guardar")
    if not errores and not avisos:
        st.success("Sin errores ni avisos.")

    _acciones(borrador, errores)
    if not errores:
        st.divider()
        st.subheader("Previsualización con los ejemplos de cada campo")
        _previsualizar(borrador)


# ---------------------------------------------------------------------------- app


def _tipo_activo(disponibles):
    """El selector de tipo; al cambiarlo se descarta lo generado con el anterior."""
    elegido = st.selectbox(
        "Tipo de otrosí",
        list(disponibles),
        format_func=lambda identificador: disponibles[identificador]["nombre"],
        key="tipo_activo",
    )
    if st.session_state.get("tipo_previo") not in (None, elegido):
        # no dejar descargable un documento del tipo anterior, igual que al fallar la
        # validación no se deja el .docx anterior junto al error
        st.session_state.pop("resultado", None)
        st.session_state.pop("masivo", None)
    st.session_state["tipo_previo"] = elegido
    return disponibles[elegido]


def main():
    st.set_page_config(page_title="Generador de otrosíes", page_icon="📄")
    st.title("Generador de otrosíes")
    st.caption(
        "Universidad de los Andes, Vicerrectoria de Transformacion Digital"
    )

    disponibles = tipos.listar()
    # el selector va fuera de st.tabs y antes de ellas: las dos pestañas operativas trabajan
    # sobre el mismo tipo, y así se ve arriba a qué otrosí se refiere todo lo de abajo
    tipo = _tipo_activo(disponibles) if disponibles else None

    # st.tabs ejecuta los cuerpos de las tres en cada rerun: la pestaña masiva vuelve a leer
    # el archivo aunque estés en la individual, y es justo lo que se quiere
    individual, masiva, editor = st.tabs(["Un otrosí", "Carga masiva", "Tipos de otrosí"])

    if tipo is None:
        for pestaña in (individual, masiva):
            with pestaña:
                st.error(
                    "No hay ningún tipo de otrosí definido. Créalo en la pestaña "
                    "«Tipos de otrosí»."
                )
        with editor:
            _pestaña_tipos()
        return

    errores = tipos.validar(tipo)[0]
    with individual:
        if errores:
            st.error(
                _lista(errores),
                title=f"El tipo «{tipo['nombre']}» tiene errores: corrígelo en «Tipos de otrosí»",
            )
        else:
            _pestaña_individual(tipo)
    with masiva:
        if errores:
            st.error(f"El tipo «{tipo['nombre']}» tiene errores: corrígelo antes de usarlo.")
        else:
            _pestaña_masiva(tipo)
    with editor:
        _pestaña_tipos()


if __name__ == "__main__":
    main()
