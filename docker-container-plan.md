# Plan: Containerize the Teams Polling Agent

## Context

The Teams agent (`run_agent.py`) runs as a long-lived polling loop — no HTTP server, no exposed ports. PostgreSQL already runs as a Docker container. Redis is **not required** — `MsalTokenProvider` falls back to a file-based lock next to `token_cache.enc` when `TEAMS_TOKEN_LOCK_URL` is omitted. The goal is to add one `agent` container alongside PostgreSQL, orchestrated by `docker-compose.yml`, so the whole stack is reproducible with `docker compose up`.

The Streamlit web UI is a **separate interface** and is NOT part of this containerization.

---

## Design Decisions

| Question | Decision | Why |
|----------|----------|-----|
| Base image | `python:3.13-slim-bookworm` | Matches current Python 3.13; slim is ~150 MB; alpine breaks `psycopg2-binary` wheels |
| Multi-stage? | No | Only compiled dep is `psycopg2-binary` (pre-built wheel). Single stage keeps it simple |
| Include PG in compose? | Yes | One `docker compose up` for the whole stack. Compose overrides `DATABASE_URL` to use Docker DNS name |
| Redis? | No | `TEAMS_TOKEN_LOCK_URL` is optional; `MsalTokenProvider` uses a file-based lock fallback. Single-process agent doesn't need distributed locking |
| Split requirements? | Yes → `requirements-agent.txt` | Drops `streamlit` (~80 MB unused by agent). Original `requirements.txt` stays for the Streamlit app |
| Health check | Heartbeat file + `healthcheck.py` | No HTTP port to probe. Agent writes timestamp to `/tmp/heartbeat` each cycle; script checks freshness |
| Logging | stdout/stderr (already default) | Docker captures natively. No file logging needed |
| Restart policy | `unless-stopped` | Auto-restart on crash; stops on explicit `docker stop` |
| Token bootstrap | Manual, outside Docker | `MsalTokenProvider` never does interactive auth — mount pre-existing `token_cache.enc` |
| Timezone | `TZ=America/Bogota` | `date.today()` in campos/masivo matters for Colombian contracts |

---

## Files to Create

### 1. `requirements-agent.txt` (new)

Copy of `requirements.txt` minus the `streamlit==1.60.0` line:

```
python-docx==1.2.0
openpyxl==3.1.5
requests==2.34.2
langchain>=0.3
langchain-google-genai>=2.0
langgraph>=0.4
python-dotenv>=1.0
markdown>=3.5
teams_core @ git+https://github.com/MichiMoments/MiddlewareGraph-Azure.git@main
psycopg2-binary>=2.9
```

### 2. `Dockerfile` (new)

```dockerfile
FROM python:3.13-slim-bookworm

ENV TZ=America/Bogota
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-agent.txt .
RUN pip install --no-cache-dir -r requirements-agent.txt

COPY agent/ agent/
COPY otrosi/ otrosi/
COPY citaciones/ citaciones/
COPY run_agent.py .
COPY healthcheck.py .

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["python", "run_agent.py"]
```

- `git` is needed for `pip install` of `teams_core` from GitHub.
- Deps layer cached separately from code.
- No `EXPOSE` — the agent opens no ports.

### 3. `.dockerignore` (new)

```
venv/
.venv/
__pycache__/
*.pyc
.env
token_cache.enc
output/
datos_prueba/
.git/
.streamlit/
otrosi_app_frontend.py
documentos/
*.md
```

### 4. `healthcheck.py` (new, ~15 lines)

Reads `/tmp/heartbeat`, checks if timestamp is < 60s old. Exits 0 (healthy) or 1 (unhealthy).

### 5. `docker-compose.yml` (new)

Two services on a shared bridge network:

- **postgres** — `postgres:16-alpine`, mounts `citaciones/schema.sql` into `/docker-entrypoint-initdb.d/` for auto-init, named volume `pgdata`.
- **agent** — built from `Dockerfile`, `env_file: .env`, with environment overrides:
  - `DATABASE_URL=postgresql://citaciones:citaciones@postgres:5432/citaciones`
  - `TEAMS_TOKEN_CACHE_PATH=/data/token_cache.enc`
  - No `TEAMS_TOKEN_LOCK_URL` — omitted so `MsalTokenProvider` uses file-based lock
- Volumes:
  - `./token_cache.enc:/data/token_cache.enc` (bind mount, read+write — agent refreshes tokens)
  - Named volume `plantillas` → `/app/otrosi/plantillas/personalizadas` (persists custom templates)
- `depends_on` with health condition so agent waits for PG.
- `restart: unless-stopped`.

### 6. Modify `agent/runner.py` (existing, 1 change)

After [runner.py:400](agent/runner.py#L400) (`time.sleep(INTERVALO_POLLING)`), add heartbeat write:

```python
        try:
            with open("/tmp/heartbeat", "w") as _hb:
                _hb.write(str(time.time()))
        except OSError:
            pass
```

`OSError` catch ensures this is harmless when running outside Docker.

---

## Volume Strategy

| Data | Mount type | Persists? | Why |
|------|-----------|-----------|-----|
| `token_cache.enc` | Bind mount → `/data/token_cache.enc` | Yes | Pre-seeded on host; agent reads+writes; host is bootstrap source |
| `otrosi/plantillas/personalizadas/` | Named volume `plantillas` | Yes | Agent-created templates must survive rebuilds |
| `output/`, `output/.staging/` | Not mounted | No | Ephemeral; files uploaded to Azure Blob immediately |
| PostgreSQL data | Named volume `pgdata` | Yes | Standard |

---

## Operational Notes

**First-time setup:**
1. Ensure `token_cache.enc` exists on host (run interactive OAuth bootstrap locally first).
2. Ensure `.env` has all required variables.
3. `docker compose up -d` — PG auto-initializes schema, agent polls.

**Rebuild after code changes:**
```
docker compose up -d --build agent
```

**Logs:**
```
docker compose logs -f agent
```

**Token re-bootstrap (~every 90 days of inactivity):**
1. Run interactive OAuth on a machine with a browser → get new `token_cache.enc`.
2. Copy to project root on host.
3. `docker compose restart agent`.

---

## Verification

1. `docker compose up -d` — both services start, agent shows `healthy` in `docker compose ps`.
2. Agent logs show polling cycles: `docker compose logs -f agent`.
3. Send a message to the bot in Teams — verify it responds.
4. Check heartbeat: `docker compose exec agent cat /tmp/heartbeat` — should show recent timestamp.
5. Kill the agent process inside the container — verify Docker restarts it (`unless-stopped`).
6. Create a custom template via Teams bot — restart agent — verify template persists (named volume).

---

## Out of Scope

- Streamlit containerization (separate interface, separate concern).
- CI/CD pipeline (can be added later).
- Token bootstrap automation (requires browser; document the manual process).
- `teams_core` GitHub repo auth — currently accessible without credentials; if it becomes private, pass a `GITHUB_TOKEN` build arg.
