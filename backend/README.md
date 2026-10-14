# Dental Chatbot Backend

FastAPI backend for the dental clinic chat assistant. It owns patient identity,
scheduling rules, and the tools the LLM may call. Patients register and log in
through the Next.js frontend (`../frontend`), which calls the `/auth` endpoints here.

## Layout

The code is organized by domain (one folder per module, same file names in each).

### `src/`

Application entry points and shared code.

- `main.py`  
  FastAPI app, CORS, router registration.
- `config.py`  
  Global settings: `DATABASE_URL`, `ENVIRONMENT`, `CORS_ORIGINS`.
- `database.py`  
  Async SQLAlchemy engine and the `DbSession` dependency.
- `models.py`  
  ORM `Base` with naming conventions, `TimestampMixin`, `CustomModel`.
- `exceptions.py`  
  Shared HTTP exceptions.

### `src/auth/`

Implemented. Registration, login, refresh, logout, current user.

- `router.py`  
  `/auth` endpoints.
- `service.py`  
  Registration, password check, token issue and rotation.
- `models.py`  
  User and refresh token tables.
- `schemas.py`  
  Request and response models.
- `dependencies.py`  
  `CurrentUser` dependency used to protect other routers.
- `utils.py`  
  Password hashing and JWT helpers.
- `config.py`  
  `AUTH_*` settings.
- `constants.py, exceptions.py`  
  Route prefix and auth errors.

### `src/patients/`

Patient records, kept separate from login accounts.

- `models.py`  
  Patient table.
- `schemas.py`  
  Patient summary returned by `/auth/me`.
- `service.py`  
  Patient lookups.

### `src/appointments/`

Implemented. Types, availability, booking, rescheduling and cancelling.

- `models.py`  
  Appointment types, providers, hours, appointments, proposals, slot offers.
- `service.py`  
  Availability queries and book / reschedule / cancel rules.
- `scheduling.py`  
  Pure availability maths (no DB, no clock), unit tested.
- `slot_token.py`  
  Server-signed, short-lived slot tokens. A token is not a reservation.
- `proposals.py`  
  Booking proposals; the patient confirms in the chat UI.
- `cancel_proposals.py`  
  Cancellation proposals; the patient confirms in the chat UI.
- `config.py`  
  `APPT_*` settings.
- `exceptions.py`  
  Appointment errors.
- `seed.py`  
  Sample development data (`python -m src.appointments.seed`).

### `src/chat/`

Implemented. `/chat` endpoints, the agentic loop and the LLM adapter.

- `router.py`  
  Messages, conversations, and proposal confirm / decline endpoints.
- `service.py`  
  Conversation persistence and turn handling.
- `agent.py`  
  The agentic loop: model calls, tool rounds, limits.
- `llm.py`  
  OpenAI-compatible provider adapter (`LLMClient`).
- `patient_context.py`  
  Short server-built patient summary added to the system prompt.
- `models.py, schemas.py`  
  Conversation and message tables; API models.
- `config.py`  
  `LLM_*` settings.
- `constants.py, dependencies.py, exceptions.py`  
  Route prefix, fallback reply, DI wiring, chat errors.

### `src/emergency/`

Implemented. Clinic emergency guidance and staff notification.

- `service.py`  
  Emergency instructions and notification with recorded delivery status.
- `sender.py`  
  HTTP POST to the staff webhook.
- `models.py`  
  Emergency notification table.
- `config.py`  
  `EMERGENCY_*` settings, including placeholder wording the clinic must approve.

### `src/family/`

Partial. Guardian relationships exist; family booking tools are not built yet.

- `models.py`  
  `PatientGuardian` links.
- `service.py`  
  `manageable_patient_ids`: the patient plus dependents they may book for.

### `src/tools/`

Model-facing tools. Handlers are thin and call the services above.

- `base.py`  
  `ToolDefinition` and `ToolContext`.
- `registry.py`  
  Allowlisted dispatcher with validation, timeout and output cap.
- `appointments/tools.py`  
  `get_appointment_types`, `get_patient_appointments`, `get_appointment_details`, `get_booking_status`, `get_available_slots`, `propose_booking`, `reschedule_appointment`, `propose_cancellation`.
- `emergency/tools.py`  
  `get_emergency_instructions`, `notify_staff_of_emergency`.
- `family/, clinic/, registration/`  
  Empty placeholders; no tools registered yet.

### Other folders


- `migrations/`  
  Alembic (async) migrations.
- `db/init/`  
  SQL run on first Postgres start: tables and the `dental_test` database.
- `tests/unit/`  
  No database needed.
- `tests/integration/`  
  Needs a disposable Postgres (`TEST_DATABASE_URL`).
- `tests/user_tests/`  
  Manual seeded scenarios; see its README.

## Setup

Requires Python 3.11+, Docker (or any Postgres 14+), and Node 20+ for the frontend.

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime deps only: requirements.txt

cp .env.example .env              # then edit it, see "Environment" below
docker compose up -d db           # first start runs db/init/*.sql: tables + dental_test DB
# (an existing volume skips db/init; reset with: docker compose down -v)

alembic upgrade head              # no-op on a fresh DB (already stamped); applies later migrations
python -m src.appointments.seed   # OPTIONAL: SAMPLE appointment types/providers/hours for development
uvicorn src.main:app --reload     # http://localhost:8000
```

Frontend (separate terminal):

```bash
cd frontend
npm install
npm run dev                       # http://localhost:3000
```

The frontend calls the backend at `http://localhost:8000`; override with
`NEXT_PUBLIC_API_URL` if needed.

## Environment

All backend settings come from `backend/.env` (git-ignored). A working local file:

```dotenv
ENVIRONMENT=local
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dental
CORS_ORIGINS=["http://localhost:3000"]

# Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
AUTH_JWT_SECRET=<your generated secret, 32+ chars>
AUTH_JWT_EXP_MINUTES=15
# Set to true anywhere served over HTTPS.
AUTH_SECURE_COOKIES=false

# Integration tests only. Tables in this database are DROPPED on every test run.
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dental_test

# --- LLM: any OpenAI-compatible API (see below) ---
LLM_API_KEY=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3-32k
LLM_DEBUG_PRINT=true
```

## Choosing a model

The chat assistant uses the OpenAI Chat Completions API through the `openai` SDK, so it
works with any server or provider that speaks that protocol and supports tool calling.
Three variables select it:

| Variable        | Meaning                                                              | Default                      |
|-----------------|----------------------------------------------------------------------|------------------------------|
| `LLM_API_KEY`   | API key. Required: when unset, chat returns the safe fallback message. Local servers ignore the value but it must be non-empty. | unset |
| `LLM_BASE_URL`  | Base URL of the OpenAI-compatible API (usually ends in `/v1`).       | `http://localhost:11434/v1`  |
| `LLM_MODEL`     | Model name as the provider knows it.                                 | `qwen3-32k`                  |

Examples (put these in `.env`):

```dotenv
# Ollama (local, no cost). Pull a tool-calling model first, e.g. `ollama pull qwen3`.
# Ollama's default context window is small; create a larger variant for the long system
# prompt + tool schemas, e.g. a Modelfile with `FROM qwen3` / `PARAMETER num_ctx 32768`
# built with `ollama create qwen3-32k -f Modelfile`.
LLM_API_KEY=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3-32k

# OpenAI (ChatGPT models). Use an API key from platform.openai.com; a ChatGPT
# subscription alone does not include API access.
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# DeepSeek
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# LM Studio / vLLM / llama.cpp server / OpenRouter etc.: same three variables, e.g.
LLM_API_KEY=lm-studio
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=<model id shown by the server>
```

**Not supported:** Anthropic (Claude) and Google (Gemini) native APIs. Only the
OpenAI-compatible Chat Completions protocol is implemented, and I have no tokens to test
those providers, so they are unsupported. They may work through an OpenAI-compatible
gateway (e.g. OpenRouter), but that is untested.

Optional tuning (all prefixed `LLM_`): `TEMPERATURE` (0.7), `MAX_OUTPUT_TOKENS` (10000; reasoning
models spend this on thinking, so too low gives empty replies), `REQUEST_TIMEOUT_SECONDS` (120),
`MAX_TOOL_ROUNDS` (5), `CLINIC_TIMEZONE` (`America/New_York`), and `DEBUG_PRINT`.

`LLM_DEBUG_PRINT=true` prints raw model output and errors to stdout. It can contain patient
text, so keep it off outside local development. Do not send real patient data to a
third-party provider during development.

Reasoning-model `<think>` blocks in replies are stripped automatically. The code does not read
a `LLM_REASONING_EFFORT` setting; to disable thinking with Ollama, use a model/template that
does so.

## Chat API

Run `alembic upgrade head` to create the chat tables.

All endpoints require `Authorization: Bearer <access_token>` and only touch the caller's own data.

| Method | Path                                          | Notes |
|--------|-----------------------------------------------|-------|
| POST   | `/chat/messages`                              | `{message, conversation_id?}` -> `{conversation_id, reply, degraded}`. |
| GET    | `/chat/conversations`                         | The caller's conversations. |
| GET    | `/chat/conversations/{id}/messages`           | User/assistant transcript for the owner only. |
| GET    | `/chat/conversations/{id}/proposal`           | Pending booking or cancellation confirmation card, if any. |
| POST   | `/chat/proposals/{proposal_id}/confirm`       | Patient confirms a proposed booking (the model cannot call this). |
| POST   | `/chat/proposals/{proposal_id}/decline`       | Patient declines a proposed booking. |
| POST   | `/chat/cancellations/{proposal_id}/confirm`   | Patient confirms a proposed cancellation. |
| POST   | `/chat/cancellations/{proposal_id}/decline`   | Patient keeps the appointment. |

Tools are registered in `src/tools/registry.py` and implemented in `src/tools/appointments/tools.py`
and `src/tools/emergency/tools.py`. The model can only propose a booking or cancellation;
the appointment changes when the patient presses Confirm, which hits the endpoints above.
Family scheduling and clinic-information tools are not built yet.

## Auth API (for the Next.js frontend)

| Method | Path             | Body / credentials                          | Result |
|--------|------------------|---------------------------------------------|--------|
| POST   | `/auth/register` | `email, password, first_name, last_name, birth_date, phone_number?` | 201 user; 409 if email exists |
| POST   | `/auth/login`    | `email, password`                           | `{access_token, token_type, expires_in}` + httpOnly `refresh_token` cookie |
| POST   | `/auth/refresh`  | `refresh_token` cookie                      | New access token; cookie is rotated |
| POST   | `/auth/logout`   | `refresh_token` cookie                      | 204; token revoked, cookie cleared |
| GET    | `/auth/me`       | `Authorization: Bearer <access_token>`      | Current user + own patient summary |

Frontend notes:

- Send `credentials: "include"` on `login`, `refresh`, and `logout` so the browser
  stores and sends the refresh cookie. The cookie path is `/auth`, so it is never
  sent to other endpoints.
- Keep the access token in memory (not `localStorage`) and call `/auth/refresh`
  when it expires or after a page reload.
- Refresh tokens are single-use. Presenting an already-rotated token revokes all of
  that user's sessions, so the frontend should serialize refresh calls (one
  in-flight refresh shared across tabs and requests).
- Other routers protect endpoints with `user: CurrentUser` from
  `src.auth.dependencies`.

## Tests

```bash
pytest tests/unit                     # no database required
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dental_test \
  pytest tests/integration            # DROPS and recreates all tables in that DB
```

Integration tests are skipped when `TEST_DATABASE_URL` is unset. They never use
`DATABASE_URL`.

## Lint

```bash
ruff check src tests migrations
ruff format src tests migrations
```
