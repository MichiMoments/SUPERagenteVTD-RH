# Opciones de captura de mensajes en Teams — Exploración

## Problema actual

El bot de Teams (`run_agent.py` → `agent/runner.py`) captura mensajes con un **loop de polling síncrono**:
cada 10 segundos recorre todos los chats activos, pide los últimos 20 mensajes de cada uno vía
`GET /chats/{id}/messages`, y filtra en Python cuáles son nuevos (comparando `message_id` contra un
watermark local). Esto funciona, pero tiene costos evidentes:

| Aspecto | Estado actual |
|---------|--------------|
| Latencia | 0–10 s dependiendo de cuándo cae el ciclo |
| Llamadas a Graph API por ciclo | 1 (lista de chats) + N (una por chat activo) |
| Carga a escala | Lineal con el número de chats — 50 chats = ~51 llamadas cada 10 s |
| Throttling | Graph aplica rate limits por app+tenant; a ~300 req/min se empieza a recibir 429 |
| Eficiencia | El 95%+ de las llamadas no devuelve nada nuevo — tráfico desperdiciado |
| Proceso | Un único hilo síncrono, bloqueado en `time.sleep()` entre ciclos |

---

## Opción 1: Graph Change Notifications (webhooks de Graph API)

### ¿Qué es?

Microsoft Graph permite crear **suscripciones** (`POST /subscriptions`) sobre un recurso
(por ejemplo `/chats/{id}/messages`). Cuando el recurso cambia, Graph envía un `POST` HTTP
a una URL pública que tú controlas (`notificationUrl`). El payload contiene metadatos del cambio
(quién, qué recurso, qué tipo de cambio) pero **no el contenido del mensaje** si
`includeResourceData: false` — en ese caso, tu servidor recibe la notificación y luego llama a
`GET /chats/{id}/messages/{messageId}` para obtener el mensaje real.

### ¿Ya tenemos soporte?

**Sí, parcialmente.** El middleware `teams_core` ya incluye una clase `SubscriptionManager`
(`teams_core.adapters.graph.subscriptions`) con métodos `create()`, `renew()`, `delete()` y
`list_active()`. Las variables de entorno `TEAMS_NOTIFICATION_URL`, `TEAMS_LIFECYCLE_URL` y
`TEAMS_CLIENT_STATE` ya están en `.env` (con valores placeholder). `GraphMessageReader` ya tiene
un método `get_one(conv, message_id)` que obtiene un mensaje individual por ID — exactamente lo
que se necesita al recibir una notificación.

Lo que **no** existe hoy:
- Un servidor HTTP que reciba los callbacks de Graph
- El handler de validación (Graph envía `?validationToken=...` al crear la suscripción; hay que
  responderle con ese token en texto plano)
- Lógica de renovación automática (las suscripciones expiran en máximo 60 minutos para
  `/chats/messages`; `SubscriptionManager.create()` usa 55 min por defecto)
- Lifecycle notifications handler (Graph avisa cuando una suscripción está por expirar o fue
  revocada)

### Arquitectura propuesta

```
Microsoft Graph
      |
      | POST /teams/notifications  (cambio detectado)
      v
[Servidor HTTP público]  ←  FastAPI / Flask / aiohttp
      |
      | 1. Valida clientState
      | 2. Llama reader.get_one(conv, message_id) para obtener el mensaje
      | 3. Pasa el mensaje al agente
      | 4. Envía la respuesta por sender.send()
      v
[Agente LangGraph]
```

### Pros

- **Latencia ~1–3 s** — el mensaje llega casi en tiempo real
- **Cero tráfico desperdiciado** — solo se hace una llamada a Graph cuando hay un mensaje nuevo
- **Escala sin esfuerzo** — 1 chat o 500 chats, Graph notifica igual; no hay N llamadas por ciclo
- **Alineado con la práctica recomendada de Microsoft** para bots en producción
- **`SubscriptionManager` ya existe** en `teams_core` — no hay que implementar la capa de
  suscripciones desde cero

### Contras

- **Requiere un endpoint HTTPS público** — el servidor debe ser accesible desde internet con un
  certificado TLS válido. En desarrollo esto significa ngrok, Cloudflare Tunnel, o similar. En
  producción, un App Service, VM con dominio, o contenedor expuesto.
- **Complejidad operacional nueva**: hay que gestionar la renovación de suscripciones (cada ≤55
  min), manejar lifecycle notifications, y tener un plan si el endpoint cae (Graph reintenta con
  backoff exponencial, pero eventualmente desactiva la suscripción).
- **Una suscripción por chat** — para escuchar N chats se necesitan N suscripciones. Graph tiene
  un límite de ~10,000 suscripciones por app, pero gestionar cientos de suscripciones activas
  con renovación individual añade código.
- **Cambio arquitectónico significativo**: pasar de un script síncrono sin servidor a una
  aplicación web desplegada. El runner.py actual es un proceso que arrancas y listo; con webhooks
  necesitas infraestructura de hosting.
- **`includeResourceData: false`** — la notificación no trae el contenido del mensaje, solo dice
  "hubo un cambio en `/chats/{id}/messages`". Hay que hacer un fetch adicional. Con
  `includeResourceData: true` se puede recibir el contenido cifrado, pero requiere registrar
  un certificado de cifrado en la app de Azure AD — complejidad adicional considerable.
- **Validación de tokens en el handshake** — Graph envía un `validationToken` al crear la
  suscripción que debe ser devuelto en ≤10 s. Si tu servidor no responde, la suscripción no se
  crea.

### Esfuerzo estimado

- Añadir un framework web (FastAPI + uvicorn recomendado, ~15 líneas de setup)
- Handler de notificaciones (~40-60 líneas: validación, dedup, fetch de mensaje, dispatch al agente)
- Handler de lifecycle (~20 líneas: autorenewal al recibir `reauthorizationRequired`)
- Lógica de bootstrap: crear suscripciones para cada chat al arrancar, renovar periódicamente
- Configuración de deployment (ngrok para dev, App Service/similar para prod)
- **Estimado total: 1–2 días** para una implementación funcional, 3–4 días con deployment
  productivo y manejo robusto de errores

---

## Opción 2: Bot Framework SDK (Azure Bot Service)

### ¿Qué es?

El **Bot Framework** es el SDK oficial de Microsoft para construir bots de Teams (y otros canales).
En vez de leer mensajes con la Graph API, registras un **Bot** en Azure, lo vinculas a Teams, y
Azure Bot Service enruta cada mensaje del usuario directamente a tu endpoint como un
`Activity` (un objeto JSON con el mensaje, el autor, la conversación, etc.). Tu servidor procesa
el `Activity` y responde.

### Arquitectura propuesta

```
Teams ←→ Azure Bot Service ←→ [Tu servidor HTTP]
                                      |
                                [BotFrameworkAdapter]
                                      |
                                [Agente LangGraph]
```

### Pros

- **El estándar oficial** para bots de Teams — toda la documentación de Microsoft asume este modelo
- **Mensajes llegan push, en tiempo real**, con el contenido completo incluido (no hay que hacer
  un fetch adicional como con Graph subscriptions)
- **No requiere crear suscripciones por chat** — Azure Bot Service hace el routing automáticamente
  a nivel de tenant
- **Soporte nativo para tarjetas adaptivas, respuestas proactivas, extensiones de mensajería**,
  y otros patrones que con Graph API son difíciles o imposibles
- **SDK maduro** (`botbuilder-core`, `botbuilder-integration-aiohttp`) con manejo de
  autenticación, turn context, y middleware ya resueltos

### Contras

- **Cambio arquitectónico radical** — el modelo de autenticación actual (MSAL con refresh token
  de usuario delegado, `teams_core`) es completamente diferente al del Bot Framework (app-level
  auth con `MicrosoftAppId`/`MicrosoftAppPassword`). Migrar implica:
  - Registrar un nuevo Bot en Azure
  - Configurar el Bot Channel Registration en el portal de Azure
  - Instalar el bot como una app en Teams (manifiesto de app, sideloading o publicación)
  - Reemplazar toda la capa de auth y lectura/envío de mensajes
- **`teams_core` quedaría obsoleto** — el middleware actual (`GraphClient`, `GraphMessageReader`,
  `GraphMessageSender`, `MsalTokenProvider`) deja de usarse para mensajería. Solo sobreviviría
  `BlobStorageUploader` y posiblemente `GraphEmailSender`.
- **Requiere igualmente un endpoint HTTPS público** — mismo requisito que los webhooks de Graph
- **Requiere un registro de Azure Bot** ($0 en el tier gratuito, pero añade un recurso al tenant
  de Azure)
- **Esfuerzo de migración grande**: todo `runner.py` se reescribe, la lógica de polling
  desaparece, el manejo de adjuntos cambia (Bot Framework usa su propio modelo de attachments,
  no el mismo que Graph API), y el historial de conversación necesita un nuevo mecanismo de
  persistencia
- **Dos modelos de auth coexistiendo** si se quiere mantener `teams_core` para emails, blob
  storage, o channel notifications — el Bot Framework no usa Graph API para enviar mensajes de
  vuelta, usa su propio connector

### Esfuerzo estimado

- **5–10 días** para una migración completa, incluyendo re-registrar el bot, reescribir runner.py,
  adaptar el manejo de adjuntos, y testear en Teams
- Alto riesgo de regresiones en funcionalidad existente (adjuntos, channel notifications,
  historial, citaciones)

---

## Opción 3: Graph Delta Queries (polling optimizado)

### ¿Qué es?

En vez de pedir los últimos 20 mensajes de cada chat y filtrar en Python, se usa el endpoint de
**delta** de Graph API: `GET /chats/{id}/messages/delta`. La primera llamada devuelve todos los
mensajes y un `deltaLink`. En llamadas posteriores, se envía el `deltaLink` y Graph solo devuelve
los mensajes **nuevos o modificados** desde la última consulta — payload mínimo.

### Pros

- **Cambio mínimo al código actual** — `runner.py` sigue siendo un loop de polling, solo cambia
  el endpoint y se guarda un `deltaLink` en vez de un watermark de `message_id`
- **Reduce drásticamente el tráfico** — en vez de recibir 20 mensajes por chat por ciclo, solo
  llegan los nuevos (típicamente 0-1)
- **No requiere servidor web, HTTPS, ni infraestructura nueva**
- **No requiere cambios de auth** — usa las mismas credenciales de Graph API actuales
- **Compatible con el modelo síncrono actual** — sin async, sin framework web

### Contras

- **Sigue siendo polling** — la latencia sigue siendo 0–N segundos según el intervalo. Se puede
  bajar el intervalo a 2–3 s, pero sigue siendo polling.
- **Sigue haciendo N llamadas por ciclo** (una por chat) — no elimina el problema de escala
  lineal con el número de chats, solo reduce el payload de cada llamada
- **`/chats/{id}/messages/delta`** requiere permiso `Chat.Read` o `Chat.ReadWrite` — ya lo
  tenemos (`Chat.Read` está en los scopes de `teams_core`)
- **El `deltaLink` expira** si no se usa en ~30 días — en la práctica esto no es un problema
  si el bot corre continuamente, pero hay que manejar el caso de un cold start largo
- **No resuelve el problema de fondo** — sigue siendo tráfico periódico que crece con los chats

### Esfuerzo estimado

- **Medio día** — modificar `GraphMessageReader` (o añadir un método `delta()`) y cambiar
  `runner.py` para guardar `deltaLink` en vez de `ultimo_visto`

---

## Opción 4: Híbrido — Webhooks de Graph + polling como fallback

### ¿Qué es?

Combinar la Opción 1 (webhooks) con un polling reducido como red de seguridad. El webhook maneja
el caso normal (notificación instantánea → fetch → respuesta). Un polling lento (cada 60–120 s)
cubre el caso de que una notificación se pierda (Graph no garantiza entrega exactly-once) o la
suscripción expire sin renovarse a tiempo.

### Pros

- **Baja latencia en el caso normal** (webhook: ~1–3 s)
- **Resiliencia** — si el webhook falla o una notificación se pierde, el polling de respaldo la
  atrapa en ≤120 s
- **Transición gradual** — se puede implementar el webhook primero solo para chats de alto
  volumen y dejar el polling para el resto

### Contras

- **Doble complejidad** — se mantienen dos mecanismos de entrega, hay que deduplicar mensajes
  procesados entre ambos
- **Sigue requiriendo el endpoint HTTPS** (mismo costo de infra que la Opción 1 pura)
- **Overengineering posible** — si los webhooks son confiables (Microsoft los reintenta con
  backoff exponencial antes de desactivar), el fallback de polling puede no justificar su
  costo de mantenimiento

### Esfuerzo estimado

- **2–3 días** (Opción 1 + lógica de dedup + polling con intervalo largo)

---

## Comparación resumida

| Criterio | Polling actual | Delta queries | Webhooks Graph | Bot Framework | Híbrido |
|----------|---------------|---------------|----------------|---------------|---------|
| Latencia | 0–10 s | 0–10 s (configurable) | ~1–3 s | ~1 s | ~1–3 s |
| Llamadas API/ciclo | N+1 | N | 1 por mensaje | 0 (push) | 1 por mensaje + 1/120s×N |
| Requiere HTTPS público | No | No | Sí | Sí | Sí |
| Requiere servidor web | No | No | Sí | Sí | Sí |
| Cambio de auth | Ninguno | Ninguno | Ninguno | Total | Ninguno |
| Esfuerzo | Ya hecho | ~0.5 días | 1–2 días | 5–10 días | 2–3 días |
| Escala (100+ chats) | Mala | Mala (menos payload) | Buena | Excelente | Buena |
| Resiliencia | Alta (simple) | Alta (simple) | Media (depende de renewal) | Alta (Azure lo maneja) | Alta |
| Infra existente | ✅ Todo listo | ✅ Solo código | ⚠️ SubscriptionManager existe, falta server | ❌ Nada existe | ⚠️ Parcial |

---

## Recomendación

### Para el corto plazo (hoy): **Delta queries (Opción 3)**

El bot tiene pocos chats activos y funciona como un proceso local. Delta queries es la mejora de
mayor impacto con menor riesgo: reduce el tráfico sin cambiar la arquitectura, se implementa en
medio día, y no requiere infraestructura nueva.

### Para el mediano plazo (si escala): **Webhooks de Graph (Opción 1)**

Cuando el número de chats crezca o la latencia de 10 s sea inaceptable, migrar a webhooks es el
paso natural. `SubscriptionManager` ya existe en `teams_core`, las variables de entorno ya están
preparadas, y `reader.get_one()` ya soporta fetch de mensaje individual. Solo falta el servidor
HTTP y la lógica de lifecycle. **No** recomendaría el Bot Framework (Opción 2) salvo que se
necesiten features exclusivas de Bot Framework (tarjetas adaptivas con acciones, extensiones de
mensajería, etc.) — el costo de migración es desproporcionado para lo que el bot hace hoy.

### No recomendado: Bot Framework como reemplazo

La migración al Bot Framework implica reescribir toda la capa de comunicación, cambiar el modelo
de autenticación, y abandonar `teams_core`. Si el bot solo necesita recibir mensajes de texto,
procesar con el agente, y responder, Graph API (con polling o webhooks) es suficiente y mucho
más simple. El Bot Framework se justifica cuando se necesitan patrones avanzados de interacción
que Graph API no soporta.
