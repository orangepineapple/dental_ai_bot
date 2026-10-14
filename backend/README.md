# Dental Chatbot Backend

FastAPI backend for the dental clinic chat assistant. It owns patient identity,
scheduling rules, and the tools the LLM may call. Patients register and log in
through the Next.js frontend (`../frontend`), which calls the `/auth` endpoints here.

## Layout

The code is organized by domain, following `../skills/fastapi-best-practices`:

```
src/
├── main.py          # FastAPI app, CORS, router registration
├── config.py        # Global settings (DATABASE_URL, ENVIRONMENT, CORS_ORIGINS)
├── database.py      # Async SQLAlchemy engine + DbSession dependency
├── models.py        # ORM Base (naming conventions), TimestampMixin, CustomModel
├── exceptions.py    # Shared HTTP exceptions
├── auth/            # Registration, login, refresh, logout, current user   [implemented]
├── patients/        # Patient records (separate from login accounts)       [model only]
├── appointments/    # Types, availability, book/reschedule/cancel          [implemented]
├── family/          # Family members, consecutive slots, group booking     [planned]
├── emergency/       # Clinic-approved emergency guidance, staff handoff    [planned]
├── clinic/          # Clinic info and approved knowledge                   [planned]
├── chat/            # /chat endpoints, agentic loop, LLM adapter, storage  [implemented]
└── tools/           # Model-facing tool registry + tool handlers           [appointment tools stubbed]
migrations/          # Alembic (async)
tests/unit/          # No database needed
tests/integration/   # Needs a disposable Postgres (TEST_DATABASE_URL)
```

## Setup

Requires Python 3.11+ and Docker (or any Postgres 14+).

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env              # then set AUTH_JWT_SECRET
docker compose up -d db           # first start runs db/init/*.sql: tables + dental_test DB
# (an existing volume skips db/init; reset with: docker compose down -v)

alembic upgrade head              # no-op on a fresh DB (already stamped); applies later migrations
python -m src.appointments.seed   # OPTIONAL: SAMPLE appointment types/providers/hours for development
uvicorn src.main:app --reload     # http://localhost:8000/docs
```

## Chat API

Set `LLM_API_KEY` in `.env` (a DeepSeek key works; `LLM_BASE_URL`/`LLM_MODEL` default to
DeepSeek). Run `alembic upgrade head` to create the chat tables. Do not use real patient
data with a third-party provider during development.

To use Claude instead, `pip install anthropic` and set `LLM_PROVIDER=anthropic`,
`LLM_ANTHROPIC_API_KEY` and (optionally) `LLM_ANTHROPIC_MODEL` (default `claude-sonnet-5-5`).
The default provider remains the OpenAI-compatible one.

| Method | Path                                        | Notes |
|--------|---------------------------------------------|-------|
| POST   | `/chat/messages`                            | `{message, conversation_id?}` -> `{conversation_id, reply, degraded}`. Bearer token required. |
| GET    | `/chat/conversations/{id}/messages`         | User/assistant transcript for the owner only. |

Tools are registered in `src/tools/registry.py`. The appointment tools
(`get_available_slots`, `book_appointment`, `reschedule_appointment`, `cancel_appointment`)
are placeholders in `src/tools/appointments/tools.py` that return `not_implemented`; each
handler's docstring specifies the tables and behavior to implement.

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
