# Citaciones Mini-ERP — Implementation Plan

## Overview

A jurisdiction citations tracker integrated into the existing Teams agent. Users message the bot to register, query, and manage citations. Data is persisted in PostgreSQL, and a Teams channel receives notifications on new registrations.

## Architecture

```
Teams user message
       |
   runner.py (shared polling loop)
       |
   bot.py (single combined agent: otrosi tools + citation tools)
       |
       +---> otrosi/tools.py   (existing, 6 tools)
       +---> citaciones/tools.py (new, ~4 tools)
                  |
            citaciones/crud.py --> PostgreSQL
                  |
            GraphMessageSender --> Teams channel notification
```

**Single agent, combined tools.** The LangGraph ReAct agent holds both otrosi (~6) and citation (~4) tools. Gemini picks the right tool based on user intent. No routing heuristic needed.

## Phase 2: Citations package

### Files to create

| File | Purpose |
|------|---------|
| `citaciones/models.py` | Pydantic/dataclass models for citations (schema TBD with user) |
| `citaciones/db.py` | PostgreSQL connection pool (`psycopg2.pool.SimpleConnectionPool`), reads `DATABASE_URL` from env |
| `citaciones/crud.py` | Pure SQL functions: `crear_citacion()`, `buscar_citaciones()`, `obtener_citacion()`, `actualizar_estado()` |
| `citaciones/tools.py` | LangChain `@tool` wrappers — same pattern as `otrosi/tools.py` |
| `citaciones/schema.sql` | CREATE TABLE migration for the citations table(s) |

### Proposed tools

1. **`registrar_citacion`** — saves a new citation to DB + sends channel notification
2. **`consultar_citaciones`** — queries citations by filters (date range, status, type, etc.)
3. **`obtener_citacion`** — gets full detail of a single citation by ID
4. **`actualizar_citacion`** — updates status or fields of an existing citation

### Database connection pattern

```python
# citaciones/db.py
import os
from psycopg2.pool import SimpleConnectionPool

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(1, 5, os.environ["DATABASE_URL"])
    return _pool

def get_conn():
    return get_pool().getconn()

def put_conn(conn):
    get_pool().putconn(conn)
```

Tools call `get_conn()` / `put_conn()` in try/finally blocks.

## Phase 3: Agent integration

### runner.py changes

Pass `sender` to `crear_agente` so citation tools can send channel notifications:

```python
# runner.py line ~191
agente = crear_agente(clave_api, sender=sender)
```

### bot.py changes

```python
from otrosi.tools import todas as herramientas_otrosi
from citaciones.tools import crear_herramientas as crear_herramientas_citaciones

def crear_agente(clave_api, modelo=MODELO, sender=None):
    llm = ChatGoogleGenerativeAI(...)
    herramientas_citaciones = crear_herramientas_citaciones(sender) if sender else []
    todas = herramientas_otrosi + herramientas_citaciones
    return create_react_agent(llm, todas, prompt=PROMPT_SISTEMA)
```

### System prompt addition

```
Capacidades de citaciones:
7. Registrar una nueva citacion jurisdiccional.
8. Consultar citaciones existentes por filtros.
9. Obtener detalle de una citacion especifica.
10. Actualizar el estado de una citacion.

Reglas adicionales:
- Si el usuario menciona citaciones, emplazamientos o jurisdiccion, usa las herramientas de citaciones.
- Cuando registres una citacion, confirma los datos antes de guardar.
- Las notificaciones al canal se envian automaticamente al registrar.
```

### Channel notification (inside `registrar_citacion` tool)

```python
from teams_core.domain.models import ConversationRef, ConversationKind, OutboundMessage

channel_conv = ConversationRef(
    kind=ConversationKind.CHANNEL,
    team_id=os.environ["TEAMS_CHANNEL_TEAM_ID"],
    channel_id=os.environ["TEAMS_CHANNEL_ID"],
)
sender.send(channel_conv, OutboundMessage(body_html=notification_html))
```

`teams_core` already supports channel sending — no middleware changes needed.

### New environment variables (`.env`)

```
DATABASE_URL=postgresql://user:password@host:5432/citaciones
TEAMS_CHANNEL_TEAM_ID=...
TEAMS_CHANNEL_ID=...
```

### New dependency (`requirements.txt`)

```
psycopg2-binary>=2.9
```

## Phase 4: Testing

1. Verify otrosi commands still work (no regression)
2. Run `citaciones/schema.sql` against a test Postgres instance
3. Test citation registration via Teams: message parsed -> DB insert -> channel notification
4. Test citation queries: various filters return correct results
5. Test combined conversation: switch between otrosi and citation topics in same chat
6. Verify the channel notification appears in the configured Teams channel
