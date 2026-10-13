## Tech stack
Database is Postgres
Server is FastAPI
Frontend is NextJs
Model uses OpenAI's API, I'm thinking of just using Deepseek with a free API key for testing

## Folder Structure
backend - main folder holding the FastAPI project
frontend - main folder holding the NextJs project, currently just the boilerplate npx create

backend/src - holds all the project files
backend/src/auth - holds all auth related endpoints, models, ect use this as the refence file folder structure per module
backend/src/chat - module relating to the chat endpoints. Users send messaged in the frontend, they are processed here in the agentic loop
backend/src/tools - holds all the tools with no public facing endpoints, internal tools used only by the model
backend/src/appointments - holds all the tool related to booking appointments, getting available appointments and scheduling
backend/src/emergency - holds all the tooling related to if a patient reports an emergency. Notifies staff
backend/src/registration - holds all the tooling allowing patients to register through the chat interface
backend/src/family - holds all the tooling related to checking which patients are a family

## Required domain services and tool capabilities located in src/tools
Implement these as backend services first. Then add it to the models tool registry. The agentic loop should already be created, and located in src/chat. If it does not exist return to the user for further instruction.
### Registration and identity
A note, patients create their account and login through the Nextjs frontend. So if a chat is coming through, then the patient has a registered account
- get_patient_appointments: only for the authenticated and authorized patient.
- get_appointment_details: authorized access only.
Scheduling
- get_appointment_types: return supported types, configured durations, eligibility, and escalation rules. Used as a reference to the different type of services the clinic offers
- get_available_slots: query actual availability using appointment type, date range, duration, provider constraints, and clinic hours.
- book_appointment: require server-side patient authorization and explicit confirmation; recheck availability inside a transaction; make retries idempotent.
- reschedule_appointment: authorize, check policy, confirm new slot, and update atomically.
- cancel_appointment: authorize and apply cancellation policy.
- get_booking_status: optional internal helper for uncertain/retried operations; do not claim success based on an LLM guess.
Family scheduling
- get_authorized_family_members: return only members the authenticated principal may manage.
- find_consecutive_slots: find compatible consecutive slots; proposing times must not reserve them.
- book_family_appointments: validate authorization for each person and book coordinated appointments with explicit all-or-partial failure behavior. Use a DB transaction when all changes share one DB; use compensating actions for external systems.
Emergency and staff handoff
- get_emergency_instructions: return clinic-approved escalation guidance.
- notify_staff_of_emergency: deliver summary through a configured, monitored channel and record delivery status. Do not imply delivery just because a row was inserted.
- handoff_to_staff: create a real handoff with a clear status and follow-up path.
- Do not create diagnostic or treatment recommendations. The clinic must provide and approve emergency wording and escalation policy.
General inquiries
- get_clinic_information: hours, location, contact details and services from configuration or database.
- search_clinic_knowledge: retrieve only approved clinic documents if RAG is implemented.
- Insurance answers must not guarantee coverage or benefits. Explain that eligibility/coverage depends on the plan and may need insurer confirmation.

## LLM and tool-calling requirements
- Use the provider SDK supported by the selected model. Keep provider-specific code behind an adapter.
- Tool schemas must be strict, typed, minimal, and have additionalProperties: false where supported.
- Validate arguments with Pydantic and validate again in service code.
- Use an allowlisted dispatcher; never execute generated Python, SQL, shell commands, or arbitrary tool names.
- Limit tool rounds, tool execution time, input length, and model output length.
- Treat tool outputs and retrieved documents as untrusted data, not instructions.
- Keep system instructions concise; put enforceable business rules in services and database constraints.
- Persist conversation state server-side with a clear retention policy. Do not rely on model memory as the official record.
- Tool-call outputs must not contain unnecessary sensitive fields.
- Use idempotency keys or equivalent safeguards for create/update operations.
- Provide a safe fallback if the model/provider is unavailable.

## Quality gates
Before marking work complete:
1. Create unit tests and integration tests, you do not have to run them. I can run them
2. Report exactly what was implemented, commands run, test results, and any unfinished work. Never claim tests passed if they were not run.

## Agent working rules
- First inspect the repository, existing code, package manager, and tests. Do not overwrite working code or impose a new architecture without checking.
- Make small, reviewable changes; implement one milestone at a time.
- Ask before choosing an external provider or integration that requires credentials, cost, or sensitive-data processing.
- Do not send real patient data to third-party services during development.
- Never weaken authorization or tests to make a demo pass.
- If requirements conflict or a safe implementation depends on clinic policy, create a clearly marked configuration/interface and document the decision rather than inventing policy.
- Do not leave placeholder ... implementations in a feature claimed as complete.
- Keep README setup instructions runnable from a clean checkout.