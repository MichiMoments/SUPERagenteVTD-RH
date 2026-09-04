# GenOtrosi

Streamlit tool that generates **otrosíes** — amendments to a labor contract — for
Universidad de los Andes. It is a small template engine, not one hard-coded
document: HR staff define **tipos de otrosí** (an otrosí type = its fields plus the
document text with the per-person values marked), and each type can then be filled
one person at a time in a form, or in bulk from an Excel sheet. The codebase, UI
text, variable names, and comments are entirely in **Spanish** — this is intentional
(the domain, the template, and the end users are Spanish-speaking) and should not be
translated when editing.

## Why this exists

Contract amendments are currently a manual, ad-hoc drafting process. The
operational pain point driving this project is volume and cadence: amendments get
processed in batches roughly every 15 days, and manual drafting was the bottleneck.
The bulk mode exists precisely for that cadence; the single-record form is kept for
one-off corrections and re-issues.

The **type editor** exists because a second kind of otrosí used to mean a developer
touching five separate places in the code. Now it means filling in a form.

## Architecture

Six core modules plus two independent UI layers, and the rule that holds the whole
thing together: **UI layers live only at the top.** Acyclic, no inverted dependencies:

```
otrosi_app_frontend.py  Streamlit UI (three tabs) -> otrosi/*
agent/runner.py   Teams polling loop             -> agent/bot, otrosi/tools, citaciones/tools, teams_core
agent/bot.py      LangGraph ReAct agent          -> otrosi/tools, citaciones/tools

otrosi/           (formerly core/)
  tools.py        @tool wrappers                 -> otrosi/{campos,documento,ia,masivo,tipos,transcripcion}
  alcance.py      Scope contract (categories, rules, rejection text)
  masivo.py       Excel <-> payloads + .zip      -> tipos, campos, documento
  tipos.py        type descriptors on disk       -> documento
  documento.py    marcadores -> Markdown -> docx -> (nothing)
  campos.py       coercion + validation          -> (nothing)
  transcripcion.py .docx de Word -> Markdown     -> documento, campos
  ia.py           infiere campos con Gemini      -> tipos
  plantillas/     LogoUninades.png, built-in .md/.json, personalizadas/

citaciones/       PostgreSQL-backed citations tracker
  models.py       Citacion dataclass (10 fields incl. message_id) + ESTADOS -> (nothing)
  db.py           Lazy connection pool           -> psycopg2 (DATABASE_URL)
  crud.py         5 SQL functions (4 CRUD + guardar_message_id) -> db, models
  tools.py        4 @tool wrappers + factory + channel notify/update + email notify -> crud, models, teams_core (optional)
  schema.sql      DDL: CREATE TABLE (incl. message_id) + indexes
```

Both `otrosi_app_frontend.py` (Streamlit) and `agent/` (Teams) are **independent
consumers** of `otrosi/`. Neither imports the other. The agent adds `teams_core`
(Microsoft Graph middleware) and `langchain`/`langgraph` (LLM orchestration) as
dependencies. `otrosi/tools.py` used to live at `agent/tools.py`; it moved so that
the same package holds both the core logic and its `@tool` wrappers.

`campos.py` stopped importing `documento` when gender agreement became type data —
it now depends only on the standard library.

- [otrosi_app_frontend.py](otrosi_app_frontend.py) — Streamlit only (formerly
  `otrosi.py`). A type selector **above** `st.tabs`, then three tabs.
  `render_formulario(tipo)` dispatches on each field's type and returns a
  **flat dict** — that dict *is* the contract between the UI and the document
  generator. `resumen` renders the values for on-screen verification; `_vista_previa`
  does the same for a bulk batch.
- [otrosi/tipos.py](otrosi/tipos.py) — **no Streamlit.** The only module that knows files exist.
  Loads/saves/validates descriptors. `validar` is the substantial part.
- [otrosi/campos.py](otrosi/campos.py) — **no Streamlit.** Everything that depends on the *data
  type* rather than on the otrosí: the five coercers, the forbidden characters, the
  date policy, and the validation both modes share — `faltantes`, `revisar` (hard
  errors), `avisos` (suspicions), `normalizar` (one raw spreadsheet row → payload).
- [otrosi/masivo.py](otrosi/masivo.py) — **no Streamlit.** Builds the `.xlsx` template from the
  type, reads an uploaded workbook into records, packs the `.docx` files into a
  `.zip`. Its `progreso` callback is what lets Streamlit move a progress bar without
  `masivo.py` importing Streamlit.
- [otrosi/documento.py](otrosi/documento.py) — **no Streamlit and no template library.**
  Substitutes the type's `cuerpo` markers into Markdown, then converts that Markdown
  to a `.docx` via `python-docx`.
- [otrosi/transcripcion.py](otrosi/transcripcion.py) — **no Streamlit.** The other direction:
  reads an uploaded `.docx` and transcribes its body to the Markdown dialect above,
  for the "Nuevo tipo, automático" button. Infers no fields and inserts no
  `{{marcadores}}` — a person still declares those by hand in the editor.
- [otrosi/ia.py](otrosi/ia.py) — **no Streamlit.** Sends the `«guillemet»` words and the body to
  the Gemini API and gets back proposed field metadata (`clave`, `etiqueta`,
  `tipo`, `opciones`). The body travels one-way, as read-only context — the API
  never sends it back, so it cannot rewrite contract text. The project's first
  outbound network call and its first secret.

The payload is flat (not `{generales, detalle}`) because a bulk-mode spreadsheet row
maps onto it one-to-one, with no mapping layer in between. Build those dicts with
explicit per-field coercion rather than handing a dataframe row straight over — see
the `cedula` note under Known limitations for why.

**Why not pandas**, even though Streamlit already pulls it in: `read_excel` is the
direct route to the floating-point `cedula` bug (Excel stores every number as a
double, and pandas hands it over as one), it coerces missing values to `NaN`, and it
does not even save the dependency — it needs `openpyxl` as its xlsx engine anyway.
`masivo.py` reads cells through openpyxl and coerces each field explicitly. Note
`st.data_editor` in the type editor is fed a list of dicts and **returns a list of
dicts**, so pandas stays out of that too.

When adding a **field type**, keep the split: widget in `otrosi_app_frontend.py`, coercion in
`campos.py`, Excel format in `masivo.py`, print format in `documento.FORMATOS`,
validation in `tipos.validar`. Adding a *field* to an existing type needs no code —
that is the whole point.

## The type descriptor

A type is a **plain JSON-serializable dict**, same criterion as the payload. Loaded
by `tipos.cargar`, which fills in every default so a hand-written `.json` needs only
`clave`, `etiqueta` and `tipo` per field.

```
otrosi/plantillas/
  LogoUninades.png
  teletrabajo_hibrido.json        the built-in type: metadata + the 14 fields
  otrosi_teletrabajo_hibrido.md   its cuerpo
  personalizadas/                the app writes here; overrides by id
```

(This directory used to be `plantillas/` at the repo root; it now lives inside the
`otrosi/` package, alongside the code that reads it.)

`cuerpo_archivo` points at a sibling `.md` so the **built-in legal text is versioned
in git line by line** instead of being a 9 KB string with `\n` inside a JSON. Types
created in the web carry the body **inline** under `cuerpo`, so the file is
self-contained and exporting it is portable. `tipos.cargar` resolves either form;
`tipos.exportar` always inlines. A `cuerpo_archivo` is rejected on import — it would
let an uploaded `.json` read an arbitrary server file.

`otrosi/plantillas/personalizadas/<id>.json` **overrides** the built-in of the same id;
deleting it restores the repo text. That is what makes "everything is editable"
recoverable — see Known limitations. Those files are deliberately **not**
gitignored: committing one makes a web-created type survive a redeploy.

`_origen` and `_marca` (the mtime at load, used to detect a lost write) are internal;
`_serializar` strips every `_`-prefixed key.

### The five field types

The field type decides **three things at once**: the form widget, how the Excel cell
is coerced, and **how the value prints**. That is why the template language needs no
formatting filters.

| `tipo` | Payload | Prints as | Widget | Excel column |
|---|---|---|---|---|
| `texto` | `str` | verbatim | `text_input`, or `selectbox` if it has `sugerencias` | text |
| `cedula` | `int` | `1.020.345.678` | `number_input` | `#,##0` |
| `entero` | `int` | plain digits | `number_input` | `#,##0` |
| `fecha` | `date` | `3 de agosto de 2026` | `date_input` | `DD/MM/YYYY` |
| `lista` | `str` (the chosen option) | verbatim, **plus its `derivados`** | `radio` ≤4 options, else `selectbox` | dropdown |

There is deliberately **no `genero` type** — see «Frases derivadas» below. That was a
special case that froze the telework vocabulary into `documento.py`; it is now data.

Per-field flags carry what used to be hard-coded rules, with nothing lost:
`no_futura` (error), `posterior_a` (aviso), `articulo_minuscula` (aviso),
`opcional_en_hoja` (the batch date fills it; in the form it defaults to today),
`grupo` (the form's `st.subheader` groupings), `sugerencias`, `sinonimos`, `ancho`,
`derivados`.

`INICIALES_PROHIBIDAS` applies to **every** text field: with a body anyone can write,
there is no longer a single field that can open a rendered line.

## The template language

```
{{clave}}      {{clave:mayuscula}}      {{clave:mayusculas}}      {{clave:minusculas}}
```

`documento.MARCADOR` + `documento.separar_marcador` + `documento.FILTROS` are the
whole language, and `tipos.py` imports all three rather than defining its own — if
the validator's regex diverged from the renderer's it would approve a body that then
fails to render.

- **The separator is `:`, not `|`.** The `|` delimits table cells in this Markdown
  dialect and the signature block has a marker *inside* a cell
  (`| LA UNIVERSIDAD, | {{teletrabajador:mayusculas}}, |`). `|` is still tolerated on
  read for anyone coming from Jinja.
- **A key that is not a declared field or one of its `derivados` raises at render
  time.** That is what replaced `StrictUndefined`, and it is why `documento.contexto`
  only puts keys that are actually *in* `datos` into the context: a missing key must
  fail loudly instead of emitting a blank into a legal document. Same for a derived
  phrase that does not cover the chosen option — `tipos.validar` rejects that at save.
- `{%` and `{#` are an explicit error ("this editor does not use Jinja").

**Jinja2 is gone from `requirements.txt`.** Because a person's text never reaches an
expression evaluator, the whole class of template injection (`{{ ''.__class__ }}` →
code execution) does not exist: substitution is a `re.sub` against a dict of declared
keys. Safe by construction, not by sandbox.

### Frases derivadas — how gender agreement works

A `lista` field can carry `derivados`: `{marcador: {opción: frase}}`. Choosing an
option puts **both** the option under the field's key and every derived phrase under
its own marker name. `documento.contexto` does that for any list field; the renderer
knows nothing about gender.

```json
"derivados": {
  "teletrabajador":  {"Femenino": "la Teletrabajadora",   "Masculino": "el Teletrabajador"},
  "al_teletrabajador": {"Femenino": "a la Teletrabajadora", "Masculino": "al Teletrabajador"}
}
```

**Why whole phrases and not just the noun:** Spanish contracts `a`+`el` → `al` and
`de`+`el` → `del`, so `de {{el Teletrabajador}}` would render «de el». And the
motivating counter-example is «el Contratista / la Contratista», where **the article
is what changes and the noun does not** — impossible to express while the phrases were
frozen in Python.

`tipos.generar_concordancia(femenino, masculino, opciones)` writes the five usual
phrases with the contractions already resolved, named from the masculine noun's slug
(`contratista`, `al_contratista`, `del_contratista`, plus `identificado` and
`de_la_misma` from `CONCORDANCIA_SUELTA`, which do not depend on the noun). The editor
exposes it as one button. **After that they are plain data**: the checker verifies
structure — that the marker exists, covers every option, and carries no `|`/`**` — and
**not grammar**. Nothing stops someone leaving «de el Contratista» in the table.

Two consequences worth knowing:

- **A type can have several gendered roles** (`el Contratista` and `la Interventora`
  in one document), because the marker names come from the type, not from the code.
  The old "only one `genero` field" rule is gone.
- **A list field's own key is a valid marker** and prints the chosen option
  («Femenino»). Useless in a contract but harmless, and forbidding it would
  reintroduce the special case this design removed.

`tipos.OPCIONES_GENERO` and `SINONIMOS_GENERO` are the preset content for
`campo_genero()`; they live in `tipos.py` because they are descriptor data.

## The Markdown subset (`documento.markdown_a_docx`)

Converts the *rendered* Markdown **block by block** (blank line = block separator, so
a paragraph can span several source lines):

- paragraphs — consecutive non-blank lines, joined with a space
- `- ` bullets
- `**bold**`
- `| a | b |` tables (a `|---|---|` separator row, if present, is discarded)
- `<!-- tabla-sin-bordes -->` — makes the **next** table borderless
- leading spaces on a block's first line → left indent, 0.25" per 4 spaces

**Nothing else is supported** — no headings, links, or other Markdown; anything else
passes through as a literal paragraph.

The page header (logo + title) and the 4-line footer are built in Python by
`_construir_encabezado` / `_construir_pie`, **not** in the Markdown. Two reasons:
Markdown cannot express page furniture at all, and the footer contains a literal `|`
("Universidad de los Andes **|** Vigilada Mineducación") that the table parser would
misread. Only the **title** comes from the type.
`different_first_page_header_footer` is left `False`, which is what makes both repeat
on every page.

`otrosi/plantillas/LogoUninades.png` is embedded into the header via `add_picture` at 1.2"
wide. python-docx reads PNGs natively — **Pillow is not a dependency**, do not add it
to `requirements.txt`. (It shows up in the venv anyway, because Streamlit pulls it
in; that is not a reason to rely on it here.)

## `tipos.validar` — the checker

The highest-value piece, because the converter above fails **silently**. Every rule
maps to a concrete defect. Structural checks run on the body **with markers replaced
by a neutral token**, because that is the real order: substitute, then parse Markdown.
Getting that backwards would flag the signature block's in-cell marker as a table
with three cells.

Errors: unknown marker · malformed marker · unknown filter · `{%`/`{#` · `|` in a
non-table line · a table row whose cell count differs from the block's first row
(`_escribir_tabla` sizes from `len(filas[0])` and the `zip()` **drops the rest with no
error**) · a paragraph continuation line starting with `- ` or `|` · an odd number of
`**` in a block (`_escribir_runs` splits on parity) · empty body · no fields ·
invalid/duplicate key · duplicate label (`_mapa_columnas` would reject the Excel) · a
`lista` with fewer than two options or with repeated ones · an option containing
`|`/`**` · an `id` outside `^[a-z0-9_]{1,60}$` (it becomes a path) ·
`campo_nombre`/`campo_fecha_archivo` pointing at a missing or wrong-typed field.

On `derivados` specifically: a marker name that is not a valid identifier · a name used
twice, whether by another field's key or by another field's phrase (**the render context
is flat**, so one would silently shadow the other) · a phrase that does not cover every
option of its field (whoever picks it gets no marker and the document fails) · phrases
on a field with no options · a `|`/`**` inside a phrase.

Avisos: a declared field whose key and phrases never appear in the body · a declared
phrase that is not used · a marker that opens a line · unsupported Markdown (`#`, `>`,
```` ``` ````, links, single-`*` italics) · a `<!-- tabla-sin-bordes -->` with no table
after it.

**Ordered lists are deliberately not flagged.** `1. ` prints literally, which is
exactly what the built-in template wants for `1. Computador:`, and warning about it
would be crying wolf. The built-in type validates with **zero errors and zero
avisos** — keep it that way, it is what makes the checker trustworthy.

## Transcribir un .docx (`transcripcion.py`)

**"Nuevo tipo, automático"**, junto a "Nuevo tipo, vacío" en el catálogo de tipos,
recibe un `.docx` con un otrosí ya redactado y transcribe su cuerpo al Markdown de
arriba, para no teclearlo a mano. Solo el cuerpo: el encabezado, el pie y cualquier
imagen se ignoran por completo, porque son papelería que ya pone `documento.py`.
Esta es la primera etapa de "generación automática de plantillas" — no infiere
campos ni inserta `{{marcadores}}`; eso lo sigue haciendo una persona en el editor.

La regla que gobierna el diseño: `tipos.revisar_cuerpo` no puede sacar un solo
**error** del resultado, porque `documento.markdown_a_docx` falla en silencio ante
lo que no entiende. Todo lo que el dialecto no puede expresar se sanea de forma
**visible** en vez de borrarse en silencio, y se reporta como un aviso agrupado por
clase de cambio (no uno por ocurrencia, para no desbordar el tope de 50 mensajes de
`otrosi._lista` en un contrato largo):

- **La cursiva se descarta.** El dialecto no la tiene; el texto sobrevive, el
  énfasis no. Ampliarlo tocaría `documento.py` y `tipos.py` —el par de mayor riesgo
  de regresión del proyecto— y fue una decisión explícita no hacerlo.
- **Cualquier `*` suelto se borra** y **cualquier `|` se cambia por `/`**: son
  justo los dos caracteres que `documento.py` corrompe en silencio (ver "A `|` or
  `**` in a field value" en Known limitations), así que no hay forma segura de
  conservarlos tal cual.
- **Las listas numeradas se resuelven de verdad** leyendo `numbering.xml` a mano
  con xpath —python-docx no expone esa parte—, y se imprimen como texto literal
  `1. `, `2. `, `a. `…, exactamente la convención que ya usa la plantilla integrada
  para `1. Computador:`. Una viñeta de Word se convierte en `- `.
- **Un título de Word se convierte en un párrafo en `**negrita**`**, detectado por
  `outlineLvl` (subiendo por `base_style`) y no por el nombre del estilo, que en un
  Word en español puede venir como «Título 1» en vez de «Heading 1».
- **Celdas combinadas se deshacen** (el texto queda en la celda de origen, las
  demás vacías) y **una tabla anidada en una celda se aplana** a `a / b; c / d`: el
  dialecto tiene una sola profundidad de tabla y una celda es un solo párrafo.
- Los controles de contenido (`w:sdt`) y los cambios ya aceptados (`w:ins`) se
  recorren igual que el resto del cuerpo; un cambio de eliminación sin aceptar
  (`w:del`) se descarta y se avisa. Un `.doc`, un `.rtf` o un `.pdf` renombrados a
  `.docx` no abren: se pide volver a guardarlo desde Word.
- Un `{{marcador}}` que el `.docx` ya trajera se conserva tal cual —es la señal de
  que alguien adelantó a mano el trabajo de declarar campos— y el revisor lo
  marcará como error hasta que ese campo exista en la tabla de campos.

La regresión que importa comprobar aquí es la misma que ya exige el renderizador:
generar el `.docx` de `otrosi_teletrabajo_hibrido.md` con `documento.markdown_a_docx`
y volver a transcribirlo produce `documento._bloques` idéntico y
`tipos.validar` con cero errores y cero avisos.

### Candidatos a campo marcados con «comillas angulares»

Alguien puede marcar a mano, en el `.docx` original, las palabras que
probablemente deban volverse campos: `«Teletrabajadora»`, `«Dias»`. Dos
funciones de `transcripcion.py`, sin Streamlit, ayudan con eso y se llaman en
ese orden desde el manejador de "Nuevo tipo, automático" en `otrosi_app_frontend.py`:

- `marcadores_entre_guillemets(cuerpo)` devuelve esas palabras, sin duplicados
  y en el orden de su primera aparición. `otrosi_app_frontend.py` las imprime con
  `print(",".join(...))` **a la consola del proceso de Streamlit** —no a la
  interfaz—, como ayuda para quien esté mirando la terminal al declarar los
  campos del tipo.
- `convertir_guillemets_a_marcadores(cuerpo)` cambia la puntuación, `«` → `{{`
  y `»` → `}}`, así que `«Teletrabajadora»` queda como `{{Teletrabajadora}}` en
  el cuerpo que se guarda en el borrador. Se llama **después** de
  `marcadores_entre_guillemets`, que necesita los guillemets todavía intactos
  para encontrarlos.

**No corrige mayúsculas ni tildes.** Una clave declarada tiene que ser
minúscula y sin tildes (`^[a-z][a-z0-9_]*$` en `tipos.py`), así que
`{{Teletrabajadora}}` o `{{Días}}` quedan tal cual el `.docx` los traía, y el
revisor los marcará como marcador desconocido hasta que alguien declare el
campo con la clave exacta y renombre el marcador en el cuerpo para que
coincida. Es una decisión explícita: normalizar el texto por su cuenta sería
tocar en silencio la redacción de un contrato.

### Inferir los campos con Gemini (`ia.py`)

Con las palabras de `marcadores_entre_guillemets` ya en mano, `otrosi_app_frontend.py` le pide
a Gemini (`ia.proponer_campos`) que proponga, para cada una, un campo completo:
`clave`, `etiqueta`, `tipo` (uno de `tipos.TIPOS_CAMPO`) y, si aplica, `opciones`.
El `.json` final se arma con **código determinista**, no con la IA:

- **El cuerpo viaja de solo lectura.** La API recibe el cuerpo como contexto para
  inferir el tipo de cada palabra (¿es una cédula? ¿una fecha? ¿un conjunto
  cerrado de opciones?), pero **nunca lo devuelve**: la respuesta esperada es
  solo la lista de campos. La IA no puede reescribir ni una palabra del texto
  legal, porque no tiene ningún canal para hacerlo.
- **`tipos.slug_identificador` es quien garantiza una clave válida**, no la IA.
  Cualquier clave que proponga (con mayúscula, con tilde, vacía, ausente) pasa
  por `slug_identificador` antes de usarse; si no propuso nada para una palabra,
  se deriva de la palabra original. Una clave siempre válida, sin excepciones,
  es lo que hace que el paso siguiente sea seguro.
- **`otrosi._renombrar_marcadores` reescribe el cuerpo**, cambiando cada
  `{{Teletrabajadora}}` por `{{teletrabajadora}}` (o lo que haya resuelto el
  paso anterior) — un `str.replace` exacto sobre el marcador completo, que no
  toca una sola letra del texto alrededor. Es lo que hace que el cuerpo y los
  campos declarados terminen usando exactamente la misma clave.
- **Cualquier fallo de la IA degrada, no rompe.** Sin clave configurada, con la
  red caída, con una respuesta cortada por el límite de tokens o con JSON mal
  formado, `_inferir_campos` devuelve `([], [aviso])`: el tipo se abre igual que
  antes de este cambio, con el cuerpo transcrito y un campo en blanco, más un
  aviso explicando qué falló. La inferencia es una mejora, no un requisito.
- **Modelo:** `gemini-3-flash-preview` — es *preview*, el identificador puede
  cambiar o el modelo retirarse con poco aviso.
- **Tokens de salida:** techo alto (`_MAXIMO_TOKENS_SALIDA = 8192`) a propósito.
  Un `.json` de campos cortado a la mitad no parsea y se pierde todo el trabajo,
  que es peor que gastar de más.
- **La clave vive en `.streamlit/secrets.toml`** (`st.secrets["GEMINI_API_KEY"]`),
  gitignored. Es el primer secreto del proyecto; antes de que existiera el
  archivo, la entrada ya estaba en `.gitignore`. `st.secrets.get(...)` **no basta
  por sí solo**: si no existe ningún `secrets.toml` en el sistema, lanza
  `StreamlitSecretNotFoundError` (hereda de `FileNotFoundError`, no de
  `KeyError`, así que el `.get()` de `Mapping` no lo atrapa). `otrosi._clave_gemini`
  la envuelve en su propio `try/except` para devolver `None` en ese caso, en vez
  de tumbar el botón entero en cualquier instalación sin la clave configurada.

### Progreso visible durante la subida

La llamada a Gemini es lo único lento del flujo (unos segundos), y sin ninguna
señal en pantalla es fácil pensar que la app se colgó. El manejador de "Nuevo
tipo, automático" en `otrosi_app_frontend.py` envuelve todo el procesamiento en
`st.status("Procesando el documento…", expanded=True)`, actualizando el label
en cada etapa ("Leyendo el .docx…", "Buscando palabras marcadas…",
"Consultando la IA para N campos…", "Listo.") — el mismo patrón que ya usa
`_pestaña_masiva` para el `.zip` (`st.status` + `estado.update(...)`), solo que
por etapas con nombre en vez de una fracción numérica, porque aquí no hay un
lote de N elementos que contar sino una sola llamada de red. Un fallo (el
`.docx` no abre, el cuerpo queda vacío) deja el `status` en `state="error"` en
vez de un `st.error` suelto, para que el contenedor no parezca congelado a
medias.

### Sin frases derivadas en un tipo recién generado

`_frases_derivadas` se activa para **cualquier** campo `lista`, sin distinguir
si es de género — un campo como «Ciudad: Bogotá/Medellín» que proponga la IA
recibiría igual la tabla de frases y, con 2 opciones, el generador «Sustantivo
femenino/masculino», que no viene al caso ahí. Como Gemini nunca rellena
`derivados` (arriba), ocultar la sección no pierde ningún dato.

Al abrir el borrador recién generado, `otrosi_app_frontend.py` fija
`borrador["_origen"] = "automatico"` — una marca **transitoria**, no un campo
nuevo del descriptor: `_serializar` ya descarta cualquier clave con `_` al
guardar, y `tipos.cargar` **siempre** recalcula `_origen` según dónde está el
archivo en disco, nunca lo lee del `.json`. Así que la marca desaparece sola en
cuanto el tipo se guarda y se reabre — no hace falta tocar `tipos.py` para
nada. Mientras `_origen == "automatico"`, `_editor` no llama a
`_frases_derivadas` y tampoco muestra el botón «Añadir un campo de género»
(`otrosi_app_frontend.py`, guardados ambos detrás de `base.get("_origen") != "automatico"`);
en cuanto se guarda, el tipo pasa a ser `"personalizado"` como cualquier otro y
la sección vuelve a aparecer normal.

## Carga masiva (`masivo.py`)

The `.xlsx` has two sheets: **`Otrosíes`** (row 1 = the type's labels in field order,
frozen; 300 formatted rows) and **`Instrucciones`** (how to fill it in, plus a
per-field table generated from the type so it cannot drift). `wb.active = 0` so the
book opens on the data sheet. There is deliberately **no example row** — the example
lives in `Instrucciones`, so nobody accidentally generates an otrosí for an invented
person.

Non-obvious things that are load-bearing:

- **`DataValidation.formula1` points at cells in `Instrucciones`**, not at an inline
  `'"Femenino,Masculino"'` list. An inline list's separator depends on Excel's UI
  language, and when it mismatches the dropdown just silently does not appear. Those
  same cells double as the visible "valores permitidos" table.
- **The dropdown columns are computed, not hard-coded.** `_campos_lista` assigns each
  choice field a consecutive column from `H`, and the range is sized to the option
  count — a type with four choice fields gets `H`, `I`, `J`, `K`.
- **`showDropDown` is left unset.** The OOXML flag is inverted: `"1"` *hides* the
  arrow. Verified in the emitted XML — openpyxl writes `showDropDown="0"`.
- **Number formats are set cell by cell**, rows 2..301. That *creates* the cells, so
  `max_row` is 301 on a blank template. `_fila_vacia` is what makes that harmless: a
  fully empty row is skipped silently, a partially filled one is a hard error. That
  distinction is what stops a person from vanishing from a batch unnoticed.
- **`read_only=True` is not used** — `ReadOnlyWorksheet` has no `ws.cell()` and its
  `max_row` can be `None`. At 300 rows the memory saving is pointless.
- **Text dates are rejected on purpose.** `03/04/2026` parses fine as both 3 April
  and 4 March, so a strict `strptime` does not raise — it emits the wrong date into a
  signed contract. Only real Excel dates and ISO `YYYY-MM-DD` are accepted, with the
  regex ahead of `date.fromisoformat` because in 3.11 `"20260403"` also parses. A bare
  number in a *date-formatted* cell is fine (Excel already decided what day it is); a
  bare number in a General cell is rejected as an ambiguous serial.
- **Filenames are de-duplicated in `masivo`, not in `documento`.** `_nombres_unicos`
  suffixes `_2`, `_3` and reports each rename as an aviso. An empty slug is detected
  by comparing against `nombre_archivo` with the name field blanked, rather than by
  pattern-matching the filename, so it does not depend on that format.
- Row errors block the whole batch (the generate button is disabled); avisos never do.
  `MAXIMO_FILAS = 300` rests on a **measured** 42 ms per document — ~13 s for a full
  batch.

## The type editor tab

- The draft lives in `st.session_state["borrador"]` as the **baseline as opened**, and
  `_editor` returns a *new* dict each rerun without touching it. That matters:
  `st.data_editor` with a `key` stores edits as a delta against the data it receives,
  so feeding it the already-edited table would apply them twice. It is handed
  `st.session_state["tipos_campos_base"]`, set once by `_abrir`, and always as a
  **copy** so the widget cannot mutate the reference the delta is measured against.
- Each list field gets its own phrases table, based on
  `st.session_state["tipos_derivados_<clave>"]`. `_abrir` purges every `tipos_der*` key
  before seeding: those are indexed by field key, so two types sharing a field name
  would otherwise inherit each other's phrases.
- **The phrases table must not live inside an `st.expander`.** Without a `key` an
  expander is not stateful (`is_stateful = on_change != "ignore"` in
  `elements/layouts.py`) and `current_expanded` resets to the `expanded` argument on
  every rerun — and editing one cell *is* a rerun, so it closed on every keystroke. The
  label also takes part in the element id, and a dynamic label makes it worse.
- **The phrases widget key carries `repr(opciones)`**, because the option text is in the
  column headers (`Si es «X»`). Change an option and the columns are named differently:
  a stale delta would be applied to columns that no longer exist and every phrase would
  blank out silently. A new signature means a new widget, and `_remapear_derivados`
  rewrites the baseline (by name, else by position when the option count is unchanged).
- `_fila_ejemplo` falls back to the first option when a field's `ejemplo` is no longer
  one of them — renaming an option must not break the preview.
- **Anything that changes the field list has to move both** `st.session_state["borrador"]`
  and `tipos_campos_base` (see the «Añadir un campo de género» button). The table carries
  no `derivados`, and `_de_tabla` recovers them from the baseline descriptor — updating
  only one of the two drops the phrases on the next rerun. That was a real bug.
- `_de_tabla` preserves per-field keys the table does not show (`sinonimos`,
  `derivados`) by matching on `clave`; and if the key is not found but the row count did
  not change, by position, which covers renaming a key. With rows added or deleted it
  does **not** guess: attaching phrases to the wrong field is worse than losing them.
- `tipos.nuevo()` starts with **one blank field**, not an empty list: with no rows,
  `st.data_editor` gets a zero-column table and its add-row button has nothing to act on.
- Widget keys in the form are namespaced `campo_{tipo_id}_{clave}` — two types sharing
  a field name would otherwise collide in `DuplicateWidgetID`.
- Changing the type selector pops `resultado` and `masivo` from session state, for the
  same reason a failed validation pops `resultado`: never leave a downloadable
  document from the previous type next to the new one.
- `guardar` compares the target's mtime against `_marca` and refuses a **lost write**.
  With no login and everything editable, two people saving the same type is real.

## Known limitations

- **The app writes to disk for the first time**, in `otrosi/plantillas/personalizadas/`.
  I do not know where this app is hosted, so **I do not know whether that disk
  survives** a restart or a redeploy. If it does not, created types vanish with no
  warning **and there is no mitigation**: the «Importar/Exportar .json» buttons
  that used to be the backup were removed from the UI on purpose, so a web-created
  type now has no way to leave the server at all. `tipos.exportar`/`tipos.importar`
  still exist as functions in `tipos.py` — only the buttons are gone — so restoring
  a UI path or a CLI escape hatch is a matter of wiring, not of rebuilding logic.
  **Assumption:** the Streamlit process can write to the project directory; if it
  cannot, `guardar` fails and `PERSONALIZADAS` has to become configurable.
- **No login, everything editable, no audit trail.** Anyone with the URL can rewrite
  the legal text that gets signed, and nothing records who or when beyond the file
  mtime. «Restaurar el original» recovers a built-in type; a web-created type has
  no way back at all now that exporting its `.json` is no longer offered in the UI.
  This was an explicit decision.
- **Page furniture is not configurable.** The logo and the four footer lines stay in
  `documento.py`; a type can only change the header title.
- **Anyone pasting a contract from Word will hit the Markdown subset** — headings,
  numbered lists and italics print literally. The checker and the preview make that
  *visible*, which is all that is possible without widening the converter.
- **Grammar in `derivados` is nobody's job.** `generar_concordancia` gets the
  contractions right when it writes them, but the phrases are then editable data and
  `tipos.validar` only checks structure. Someone can leave «de el Contratista» in the
  table and the checker will approve it. That is the direct cost of taking the phrases
  out of the code, and it is stated on screen and in the guide.
- **The logo prints soft.** `LogoUninades.png` is 141×66 px; at 1.2" that is ~118 dpi
  against ~300 dpi for crisp print. Fix by dropping a higher-resolution file at the
  same path — no code change needed.
- **Page breaks do not match the PDF.** Calibri metrics and spacing differ from the
  original typesetting, so page count may vary.
- **The `missolicitudes` URL is plain text**, not a hyperlink — python-docx has no
  hyperlink API.
- **Column widths (2.6"/3.9") and the 6.5 pt footer are estimates**, not measured from
  the PDF.
- **Original typos are transcribed verbatim** (e.g. "definidos por misma", missing
  "la", in Parágrafo 3 of CLÁUSULA SEGUNDA). This is official legal wording; don't
  silently "fix" it — raise it instead.
- **No field length is validated anywhere**, and neither is a body's. Not in the form
  (no `max_chars` — it truncates a paste silently, which is the same class of silent
  data loss as the `|` bug), not in the bulk load. A runaway paste into a text field
  reaches the `.docx`, and an absurdly long name produces an absurdly long filename
  (`nombre_archivo` does not truncate: 300 chars in, 333 out). The suggested lengths
  used to be documented in `documentos/campos_y_restricciones.md`, which was
  deleted in the `core/` → `otrosi/` reorganization along with the rest of
  `documentos/` (now gitignored) — that write-up has no replacement yet, and
  the lengths remain enforced by nobody. This was a deliberate call, not an
  oversight.
- **The cédula's digit count is now checked**: `campos.revisar` rejects values with
  fewer than 8 or more than 10 digits. This doubles as a second line of defence against
  the float bug below, since `cedula(1020345678.0)` yields exactly 11 digits.
- **Pasting into the Excel template destroys the dropdowns.** Excel pastes the source
  cell's validation over the target's. That is why `campos.GENERO_SINONIMOS` and a
  field's `sinonimos` are generous when reading even though the template offers only
  the listed options.
- **No per-row download in bulk mode** — it is the whole `.zip` or nothing.
- **`transcripcion.py` infers no fields and inserts no markers.** The body comes
  back with every per-person value as fixed text; declaring fields and marking
  `{{marcadores}}` is still manual work in the editor, unless `«guillemets»` were
  marked by hand and Gemini proposes something for them (see below). Nested
  tables are flattened, merged cells are undone, a cell with several paragraphs
  becomes one line, a paragraph starting with `#`, `>` or ` ``` ` is left alone
  (there is no way to escape it without rewriting the text), indentation is
  rounded to the nearest quarter inch, and comments/footnotes/text boxes are not
  read at all — they live outside `w:body`'s paragraphs and tables.
- **`ia.py`'s proposals are a suggestion, not a decision.** Nobody guarantees
  Gemini picked the right `tipo` or the right `opciones` for a word — review
  them in the editor before saving. `gemini-3-flash-preview` is preview and can
  change or be retired with little notice; any failure (missing key, network,
  truncated response, malformed JSON) degrades to "no fields inferred" rather
  than blocking the upload. Gender agreement (`derivados`) is never inferred —
  that is still the editor's «Añadir un campo de género» button. And the body
  text of an uploaded contract now leaves the server, sent to Google's API as
  read-only context; that was categorically impossible before this feature —
  the project was fully offline until `ia.py`.
- **The dropdown arrow, the localized number formats, and whether Excel-saved cells
  come back as `datetime`/`int` can only be confirmed by opening the file in real
  Excel.** They fail *silently* (a file that opens fine but has no dropdown), not with
  an exception, and the entire date policy plus the cédula fix rest on the last one.

The next three are properties of `documento.py`, which exists precisely to be called
*without* the UI. All callers guard them via `campos.py` and `tipos.validar`; **any
new non-UI caller has to do the same.**

- **A `|` or `**` in a field value silently corrupts the document.** Text fields get
  substituted into `| a | b |` table rows, so `direccion = "Calle 1 | Apto 2"` renders
  a three-cell row; `_escribir_tabla` sizes the table from `len(filas[0])` (two
  columns) and the `zip()` discards the rest — `Apto 2` vanishes with no error. A `**`
  shifts the parity in `_escribir_runs` and inverts the bold. A newline is worse: it
  ends the table block and splits the table in two. `campos.revisar` rejects the first
  two; the newline is collapsed to a space with an aviso.
- **`cedula` mis-renders a float.** `cedula(1020345678.0)` returns
  `"10.203.456.780"` — `str(float)` keeps the `.0` and the `re.sub(r"\D", …)` swallows
  the dot, adding a digit. `cedula("1.02E+09")` is worse: `"10.209"`, six digits
  instead of ten. `st.number_input` hands over an `int` and `campos._entero` never
  routes a float through `str()`, so both modes are safe today.
- **`nombre_archivo` can collide.** `_slug` strips accents and lowercases, so
  "María García" and "Maria Garcia" produce the same filename, and a name with no
  ASCII-able characters produces an empty part that is dropped
  (`otrosi_teletrabajo_20260806.docx`). Harmless for one browser download at a time;
  `masivo._nombres_unicos` de-duplicates for the `.zip`.

## The Teams agent (`agent/`)

A second interface, independent of the Streamlit app, that exposes the same `otrosi/`
capabilities through a Microsoft Teams chat. The agent runs as a **polling loop** that
reads messages from all Teams chats the bot user participates in via Microsoft Graph,
invokes a LangChain/LangGraph ReAct agent to decide intent, and sends the response back.

### Architecture

```
run_agent.py          Entry point: loads .env, calls agent.runner.main()
agent/
  runner.py           Polling loop: Teams ↔ agent. Downloads attachments,
                      enriches input, invokes the agent, sends replies.
                      Also uploads generated files to Azure Blob Storage
                      (teams_core.adapters.blob.storage.BlobStorageUploader)
                      and links them in the reply instead of attaching bytes.
                      Passes `sender` to `crear_agente` for channel notifications.
  bot.py              Agent constructor: crear_agente(clave_api, sender=None)
                      Merges otrosi + citaciones tools, builds StateGraph
                      with triage → agent/rejection routing.
  triaje.py           Intent classifier (structured output → Triaje).
                      Four categories: "otrosi", "citaciones", "social",
                      "fuera_de_alcance".
  estado.py           Pydantic state: Intencion, Triaje, EstadoAgente.
  util_mensajes.py    Extract text from AIMessage content.
  __init__.py         (empty)
otrosi/
  tools.py            Six @tool wrappers around otrosi/ functions (moved here
                      from agent/tools.py in the core/ -> otrosi/ reorg)
  alcance.py          Single source of truth for agent scope: CATEGORIAS_EN_ALCANCE
                      (otrosí, 5 items), CATEGORIAS_CITACIONES (4 items),
                      CARVE_OUT_SOCIAL, REGLA_COMPUESTA, REGLA_ATRIBUCION,
                      RECHAZO_ESTATICO.
citaciones/
  tools.py            Four @tool wrappers + `crear_herramientas(sender, email_sender)` factory.
                      When sender is provided, `registrar_citacion` sends an
                      HTML notification to a Teams channel on success.
                      When email_sender is provided, also sends an email to
                      the addresses in CITACIONES_EMAIL_DESTINATARIOS.
```

The **design principle** is the same as the Streamlit app: the agent layer is a thin
adapter. All business logic lives in `otrosi/` and `citaciones/`. The LLM **never
receives file content** — it only decides intent from the text message. File handling
(download, generation, saving, blob upload) is deterministic in the runner.

### Middleware: `teams_core`

The agent uses the `teams_core` package for all Microsoft Graph interactions.
**It is installed directly from a GitHub repository:**

```
pip install git+https://github.com/MichiMoments/MiddlewareGraph-Azure.git@main
```

This dependency is already declared in `requirements.txt` as:
```
teams_core @ git+https://github.com/MichiMoments/MiddlewareGraph-Azure.git@main
```

The repository was renamed from `MiddlewareTeams` to `MiddlewareGraph-Azure` to
reflect its broader scope (now covers email in addition to Teams chat).

The middleware provides:

| Component | Purpose |
|---|---|
| `TeamsConfig.from_env()` | Reads all `TEAMS_*` env vars into a config object |
| `MsalTokenProvider(cfg)` | MSAL OAuth token management (delegated user flow) |
| `GraphClient(cfg, tokens)` | Low-level HTTP client for Microsoft Graph |
| `GraphMessageReader(client)` | Reads message history from a Teams chat |
| `GraphMessageSender(client)` | Sends messages to a Teams chat |
| `GraphFileDownloader(client)` | Downloads file attachments from Teams messages |
| `BlobStorageUploader(cfg)` | Uploads generated files to Azure Blob Storage, returns a `BlobRef` (`.url`, `.name`) |
| `GraphEmailReader(client)` | Reads emails from the signed-in user's mailbox (`/me/messages`): `list_messages`, `get_message`, `list_folders` |
| `GraphEmailSender(client)` | Sends emails via `/me/sendMail`: `send(OutboundEmail)`, `reply(message_id, body_html)` |
| `ConversationRef`, `ConversationKind`, `OutboundMessage` | Teams domain models |
| `EmailAddress`, `OutboundEmail`, `InboundEmail`, `MailFolder`, `EmailFileAttachment` | Email domain models |
| `FileAttachment`, `DownloadedFile` | Attachment metadata and downloaded content |

Initialization in `runner.py`:
```
TeamsConfig.from_env() → MsalTokenProvider(cfg) → GraphClient(cfg, tokens)
                                                       ├→ GraphMessageReader
                                                       ├→ GraphMessageSender
                                                       ├→ GraphEmailSender
                                                       ├→ GraphFileDownloader
                                                       └→ BlobStorageUploader(cfg)
```

`BlobStorageUploader` reads `cfg.storage_account_connection_string`
(`STORAGE_ACCOUNT_CONNECTION_STRING` in `.env`) and opens an Azure
`ContainerClient.from_container_url` with it — a plain read of `TeamsConfig`, not a
separate client. `runner._subir_archivos` uploads each generated file and
`_formatear_enlaces` turns the returned `BlobRef`s into an HTML `<ul>` of download
links appended to the Teams reply, instead of attaching file bytes directly.

**Important:** `MsalTokenProvider` uses **delegated auth** (user flow, not app-only),
so `InboundMessage.author.is_application` is always `False` for the bot's own messages.
The runner filters those out by tracking `ids_enviados` (sent message IDs).

**Email capabilities.** The middleware includes `GraphEmailReader` and
`GraphEmailSender` for reading and sending emails through the same `GraphClient`
(no separate config needed). Auth scopes include `Mail.ReadWrite` and `Mail.Send`
(delegated, same consent flow as Teams). The email adapters share the same
initialization pattern as the Teams ones — pass `GraphClient` to the constructor.
`GraphEmailSender` is now **wired into the project**: `runner.py` instantiates it
alongside `GraphMessageSender` and passes it through `crear_agente` →
`crear_herramientas` so that `registrar_citacion` sends an email notification to the
addresses in `CITACIONES_EMAIL_DESTINATARIOS` on every new citation registration.
`GraphEmailReader` is not used yet (available for a future feature).
`GraphEmailReader.list_messages` reads `/me/messages` (optionally filtering by folder),
`GraphEmailSender.send` posts to `/me/sendMail`, and `.reply` replies to an existing
message. `OutboundEmail` supports HTML body, to/cc/bcc recipients, importance, and
file attachments (base64-encoded, max 3 MB inline). Test fakes (`FakeEmailSender`,
`FakeEmailReader`) are available in `teams_core.adapters.fakes`.

### `token_cache.enc`

An **encrypted MSAL token cache** at the project root, managed by `MsalTokenProvider`.
It persists Microsoft Graph OAuth tokens (access + refresh) between runs so the agent
does not need to re-authenticate on every start. Encrypted at rest with the Fernet key
in `TEAMS_TOKEN_CACHE_KEY`.

- **Must exist at root** (or wherever `TEAMS_TOKEN_CACHE_PATH` points).
- **Gitignored** — contains session tokens, must never be committed.
- Created automatically on first authentication; if deleted, the user must
  re-authenticate.

### Environment variables

All configuration goes in a `.env` file at the project root (loaded by `python-dotenv`
in `run_agent.py`). There is no `.env.example` — create it manually:

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key (model: `gemini-3.5-flash`) |
| `TEAMS_TENANT_ID` | Yes | Azure AD tenant ID |
| `TEAMS_CLIENT_ID` | Yes | Azure AD app registration client ID |
| `TEAMS_CLIENT_SECRET` | Yes | Azure AD client secret |
| `TEAMS_REDIRECT_URI` | Yes | OAuth redirect URI (e.g. `http://localhost:8400/callback`) |
| `TEAMS_TOKEN_CACHE_PATH` | Yes | Path to encrypted token cache (e.g. `./token_cache.enc`) |
| `TEAMS_TOKEN_CACHE_KEY` | Yes | Fernet key for token cache encryption |
| `TEAMS_TOKEN_LOCK_URL` | Yes | Redis URL for token-refresh locking |
| `TEAMS_NOTIFICATION_URL` | Yes | Webhook URL for Teams change notifications |
| `TEAMS_LIFECYCLE_URL` | Yes | Webhook URL for Teams lifecycle notifications |
| `TEAMS_CLIENT_STATE` | Yes | Shared secret for validating Teams webhook callbacks |
| `STORAGE_ACCOUNT_CONNECTION_STRING` | Yes | Azure Blob container URL/SAS read by `BlobStorageUploader` |
| `DATABASE_URL` | Yes (citaciones) | PostgreSQL connection string (e.g. `postgresql://citaciones:citaciones@localhost:5432/citaciones`) |
| `TEAMS_CHANNEL_TEAM_ID` | No | Team ID for citation channel notifications (omit to skip notifications) |
| `TEAMS_CHANNEL_ID` | No | Channel ID for citation channel notifications (omit to skip notifications) |
| `CITACIONES_EMAIL_DESTINATARIOS` | No | Comma-separated email addresses to notify on new citations (e.g. `d.perezc23@uniandes.edu.co`). Empty or missing → no email sent |
| `POLLING_INTERVAL` | No | Seconds between polling cycles (default: `10`) |
| `CHAT_REFRESH_INTERVAL` | No | Seconds between chat-list refreshes (default: `10`) |

### The agent (LLM)

- **Model:** `gemini-3.5-flash` via `langchain-google-genai` (`ChatGoogleGenerativeAI`,
  temperature 0.1).
- **Triage model:** `gemini-3.5-flash-lite` (temperature 0) — classifies user intent via
  structured output into four categories: `"otrosi"`, `"citaciones"`, `"social"`,
  `"fuera_de_alcance"`. If any fragment is out of scope, the entire turn is short-circuited
  to a static rejection message (`alcance.RECHAZO_ESTATICO`).
- **Framework:** `langgraph.prebuilt.create_react_agent` — a ReAct loop that decides
  which tool to call based on the user's message.
- **Graph:** `StateGraph(EstadoAgente)` with three nodes: `triaje` → `agente` / `rechazo`.
  Routing is binary: `fuera_de_alcance` → rejection, else → agent with all 10 tools.
- **System prompt** (`agent/bot.py`): Spanish-language HR assistant persona for
  Universidad de los Andes. Key rules: `.docx` attachment → template creation;
  `.xlsx` attachment → bulk generation; confirm citation data before saving;
  ask for missing data before calling tools.

### Tools — otrosíes (`otrosi/tools.py`)

Six `@tool`-decorated functions, all delegating to `otrosi/`:

| Tool | Input | Output |
|---|---|---|
| `listar_tipos()` | — | Markdown list of available types |
| `describir_tipo(tipo_id)` | type slug | Field details for one type |
| `generar_contrato(tipo_id, datos)` | type + field values | `{archivo, docx_base64}` |
| `generar_masivo(tipo_id, xlsx_ruta)` | type + **file path** | Saves `.zip` to `output/`, returns `{ruta}` |
| `crear_plantilla(docx_ruta)` | **file path** | Saves template, returns `{variables}` with defined fields |
| `plantilla_excel(tipo_id)` | type slug | `{xlsx_base64}` |

### Tools — citaciones (`citaciones/tools.py`)

Four `@tool`-decorated functions, all delegating to `citaciones/crud.py`. Wired into the
agent via `crear_herramientas(sender)` — a factory that returns tool instances; when
`sender` is provided, `registrar_citacion` sends a channel notification on success.

| Tool | Input | Output |
|---|---|---|
| `registrar_citacion(persona_citada, tipo_citacion, fecha_citacion, autoridad, registrado_por)` | 5 strings (fecha as `YYYY-MM-DD`) | `{id, mensaje}` + channel notification |
| `consultar_citaciones(estado, tipo_citacion, desde, hasta)` | optional filters | Formatted Markdown list |
| `obtener_citacion(id_citacion)` | citation ID | Full detail dict |
| `actualizar_citacion(id_citacion, nuevo_estado)` | ID + new state | `{id, estado, mensaje}` |

`generar_masivo` and `crear_plantilla` accept **local file paths** (not base64) because
the runner downloads attachments to `output/.staging/` before invoking the agent. The
agent sees `[Archivo adjunto: datos.xlsx (xlsx), ruta: output/.staging/datos.xlsx]` in
the enriched input and passes the path to the tool.

### Attachment flow

```
User sends .xlsx in Teams
  → runner polls, finds new message with FileAttachment metadata
  → GraphFileDownloader.download(att) fetches bytes via Graph Shares API
  → bytes saved to output/.staging/<filename>
  → _enriquecer_input adds "[Archivo adjunto: name (ext), ruta: path]" to text
  → agent sees enriched text, decides intent, calls tool with the file path
  → tool reads file from disk, processes it, saves output to output/
  → agent replies with result message
  → runner sends reply to Teams chat
```

The same flow applies to `.docx` attachments for template creation. The `output/`
directory is gitignored.

### Polling loop details (`agent/runner.py`)

- On startup, discovers all chats via `GraphClient.paged("/me/chats")` and initializes
  a per-chat watermark (`ultimo_visto`) by reading the latest message in each chat.
- Every `CHAT_REFRESH_INTERVAL` seconds (default 10), re-fetches the chat list to
  discover new conversations (newly discovered chats are lazy-initialized).
- Every `POLLING_INTERVAL` seconds (default 10), polls each known chat with
  `reader.history(conv, limit=20)` and processes only messages newer than that
  chat's watermark.
- Tracks `ids_enviados` — skips messages sent by the bot itself.
- Skips messages where `author.is_application` is `True`.
- Maintains per-chat conversation history (last 20 messages) for agent context.
- Per-chat error isolation — one failing chat does not stop others.
- Memory pruning: `ids_enviados` is cleared when it exceeds 5000 entries; per-chat
  history is trimmed to 50 messages.
- Replies are sent to `msg.conversation` (the chat the message came from), not a
  global conversation reference.

## Citaciones (`citaciones/`)

A second capability integrated into the same Teams agent: a PostgreSQL-backed tracker
for jurisdiction citations, registered/queried/updated through the same chat, with a
Teams **channel** notification on new registrations. The original design plan lives in
[citaciones_plan.md](citaciones_plan.md).

**Status:** Phase 3 complete (agent integration). Phase 4 (end-to-end testing) pending.

### Package layout

```
citaciones/
  __init__.py       (empty)
  models.py         Dataclass `Citacion` with 10 fields (incl. message_id) +
                    `desde_fila()` row converter. ESTADOS = ('pendiente',
                    'atendida', 'vencida') — CHECK constraint, not ENUM.
                    Validates estado in __post_init__.
  db.py             Lazy SimpleConnectionPool(1, 5), reads DATABASE_URL from env.
                    get_conn() / put_conn() for try/finally connection management.
  crud.py           5 parameterized SQL functions (%s, no concatenation):
                    crear_citacion, buscar_citaciones, obtener_citacion,
                    actualizar_estado, guardar_message_id.
  tools.py          4 LangChain @tool wrappers + `crear_herramientas(sender, email_sender)`
                    factory + `_notificar_canal` + `_notificar_email` +
                    `_actualizar_mensaje_canal` helpers for Teams channel and email
                    notifications. `message_id` is persisted via
                    `crud.guardar_message_id` so the channel message can be
                    PATCHed when a citation's estado changes. Email recipients
                    come from `CITACIONES_EMAIL_DESTINATARIOS` env var.
  schema.sql        CREATE TABLE IF NOT EXISTS citaciones (incl. message_id TEXT
                    nullable) + 2 indexes (estado+fecha, tipo). Includes a
                    commented-out ALTER TABLE migration for existing databases.
  test_consulta_db.py    Standalone diagnostic: prints all tables and rows from PostgreSQL.
                    Run with: python -m citaciones.test_consulta_db
```

### Agent integration (phase 3)

The agent now handles both otrosíes and citaciones through a single LangGraph graph:

- **Triage** (`agent/triaje.py`): four intent categories — `"otrosi"`, `"citaciones"`,
  `"social"`, `"fuera_de_alcance"`. Both `"otrosi"` and `"citaciones"` route to the
  agent node; only `"fuera_de_alcance"` triggers rejection.
- **Scope contract** (`otrosi/alcance.py`): `CATEGORIAS_EN_ALCANCE` (items 1-5, otrosí)
  and `CATEGORIAS_CITACIONES` (items 6-9). `REGLA_COMPUESTA`, `REGLA_ATRIBUCION` and
  `RECHAZO_ESTATICO` cover both domains.
- **State** (`agent/estado.py`): `Intencion.categoria` is a `Literal` with all four
  categories.
- **Bot** (`agent/bot.py`): `crear_agente(clave_api, sender=None, email_sender=None)`
  merges `herramientas_otrosi` (6 tools) + `herramientas_citaciones` (4 tools) = 10
  tools. `PROMPT_SISTEMA` has separate operational rules for each domain.
- **Runner** (`agent/runner.py`): passes `sender=sender, email_sender=email_sender`
  to `crear_agente` so that
  `registrar_citacion` can send channel and email notifications.
- **Factory pattern** (`citaciones/tools.py`): `crear_herramientas(sender, email_sender)`
  returns tool instances. When `sender` and/or `email_sender` are provided,
  `registrar_citacion` and `actualizar_citacion` are redefined inside closures:
  `registrar_citacion` calls `_notificar_canal` (if sender) and `_notificar_email`
  (if email_sender) after a successful insert; `actualizar_citacion` calls
  `_actualizar_mensaje_canal` (if sender) to PATCH the existing channel message with
  the new estado. `consultar_citaciones` and `obtener_citacion` need neither and stay
  at module level.
- **Channel notification** (`_notificar_canal`): reads `TEAMS_CHANNEL_TEAM_ID` and
  `TEAMS_CHANNEL_ID` from env at call time. If either is missing, logs and returns
  silently (graceful degradation). Builds an HTML summary of the new citation
  (HTML-escaped via `_construir_html_citacion`) and sends via
  `sender.send(ConversationRef(CHANNEL, ...), OutboundMessage(...))`. The returned
  `msg_id` is persisted via `crud.guardar_message_id` so the message can be updated
  later. Failure is caught and logged — never breaks the tool response.
- **Channel message update** (`_actualizar_mensaje_canal`): when a citation's estado
  changes, PATCHes the original Teams channel message (identified by `message_id` on
  the `Citacion` dataclass) with the updated HTML card via
  `sender._client.request("PATCH", ...)`. If `message_id` is missing or the env vars
  are not set, returns silently.
- **Email notification** (`_notificar_email`): reads `CITACIONES_EMAIL_DESTINATARIOS`
  from env at call time (comma-separated addresses). If empty or missing, logs and
  returns silently. Builds an `OutboundEmail` with the same HTML from
  `_construir_html_citacion` and a descriptive subject line
  (`"Nueva citación #N — Persona"`), then sends via `email_sender.send(email)`.
  Only fires on registration, not on estado updates. Failure is caught and logged.

### Data flow

```
Save:
  User (Teams) → triage ("citaciones") → agent → registrar_citacion tool
    → crud.crear_citacion → INSERT INTO citaciones RETURNING *
    → _notificar_canal → sender.send to Teams channel (optional)
    → crud.guardar_message_id (persists the channel msg_id)
    → _notificar_email → email_sender.send to CITACIONES_EMAIL_DESTINATARIOS (optional)
    → {id, mensaje} → agent response → Teams reply

Retrieve:
  User (Teams) → triage ("citaciones") → agent → consultar_citaciones / obtener_citacion
    → crud.buscar_citaciones / obtener_citacion → SELECT ... → formatted results

Update:
  User (Teams) → triage ("citaciones") → agent → actualizar_citacion
    → crud.actualizar_estado → UPDATE ... SET estado RETURNING *
    → _actualizar_mensaje_canal → PATCH existing Teams channel message (optional)
    → confirmation
```

### PostgreSQL setup (Docker)

Create and start the container:

```powershell
docker run -d --name citaciones-test -e POSTGRES_USER=citaciones -e POSTGRES_PASSWORD=citaciones -e POSTGRES_DB=citaciones -p 5432:5432 postgres:16
```

Apply the schema (PowerShell — `<` redirect is not supported, use pipe):

```powershell
Get-Content citaciones\schema.sql | docker exec -i citaciones-test psql -U citaciones -d citaciones
```

Add to `.env`:

```
DATABASE_URL=postgresql://citaciones:citaciones@localhost:5432/citaciones
```

Verify with the diagnostic script:

```powershell
venv\Scripts\python -m citaciones.test_consulta_db
```

CRUD smoke test from the project root:

```powershell
venv\Scripts\python -c "
from dotenv import load_dotenv; load_dotenv()
from datetime import date
from citaciones.models import Citacion
from citaciones import crud

c = crud.crear_citacion(Citacion(
    persona_citada='Juan Perez',
    tipo_citacion='Laboral',
    fecha_citacion=date(2026, 9, 15),
    autoridad='Juzgado 3 Laboral de Bogota',
    registrado_por='David Perez',
))
print(f'Creada: #{c.id}')
print(crud.buscar_citaciones())
print(crud.obtener_citacion(c.id))
crud.actualizar_estado(c.id, 'atendida')
print(crud.obtener_citacion(c.id))
"
```

### Dependencies

`psycopg2-binary>=2.9` is declared in `requirements.txt`. Install with:

```powershell
venv\Scripts\pip install psycopg2-binary
```

`datos_prueba/` (gitignored) holds sample `.docx`/`.xlsx` files used for manual testing
of bulk generation and template transcription — not a fixtures directory read by any
test suite, just working files.

## Running it

### Streamlit app (web UI)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run otrosi_app_frontend.py
```

Generated `.docx` files are meant to land in a local `documentos/` folder
(gitignored, doesn't exist by default — the app only offers a browser download, it
doesn't write to disk itself, other than saved types).

### Teams agent

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run_agent.py
```

Requires a `.env` file with all variables listed above. On first run,
`MsalTokenProvider` will prompt for OAuth authentication (browser-based).
After that, `token_cache.enc` persists the tokens.

The agent saves generated files to `output/` (gitignored):
- `output/.staging/` — downloaded attachments (temporary)
- `output/masivo_<tipo_id>.zip` — generated bulk contracts

### Both interfaces share `otrosi/`

`tipos.py`, `campos.py` and `masivo.py` are usable without Streamlit, which is what
keeps the layering honest. To check that it stays true:

```python
import sys
from datetime import date
from otrosi import tipos, campos, masivo, documento
tipo = tipos.cargar("teletrabajo_hibrido")
assert not tipos.validar(tipo)[0]
xlsx = masivo.construir_plantilla(tipo)
registros, errores = masivo.leer_libro(tipo, xlsx, date.today())
paquete, fallos, generados = masivo.generar_zip(tipo, registros)
assert "streamlit" not in sys.modules
```

**The regression that matters most** when touching the renderer: render
`teletrabajo_hibrido` with a fixed payload for both gender branches and diff the
Markdown against a saved reference. The migration from Jinja to markers was verified
byte-for-byte that way, and it is the only thing that guarantees the signed document
did not change.

## Conventions

- Spanish throughout: identifiers, UI labels, docstrings, comments. Keep it that way.
- `snake_case` for functions/variables; Spanish domain terms (`otrosi`, `plantillas`,
  `concordancia`, `teletrabajador`, `marcadores`) are the established vocabulary —
  don't rename to English equivalents.
- One-line docstrings with an `input -> output` example on helpers; `_`-prefixed
  privates.
- Comments are rare and used only for non-obvious caveats (the `isinstance(True, int)`
  ordering inside `campos._texto` and friends, why justification is per-paragraph rather
  than on the `Normal` style, why the header tab stop is recomputed, why the Excel
  dropdowns point at a range instead of an inline list, why the filter separator is `:`,
  why `data_editor` is handed a fixed baseline, why the phrases table labels its columns
  `Si es «X»`). Match that density.
- **`campos._INVISIBLES` must keep its `\uXXXX` escapes.** Writing those characters
  literally is invisible in a diff and un-editable; it has already regressed twice.
- Spanish identifiers include the accented ones: `_pestaña_individual`,
  `HOJA_DATOS = "Otrosíes"`. Python 3 allows them and the repo uses them.
