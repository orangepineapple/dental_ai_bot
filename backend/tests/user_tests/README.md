# User tests (manual, seeded scenarios)

Each `scenario_*.py` resets the **development** database, seeds one known state and prints
the login for the frontend. The header comment of each file is the script of messages to
send to the bot and what to expect.

```bash
cd backend
python -m tests.user_tests.scenario_01_new_patient --yes
```

`--yes` is required because the script TRUNCATES every table in `DATABASE_URL`.
Files are named `scenario_*` on purpose so pytest does not collect them.

| File | Covers |
| --- | --- |
| scenario_01_new_patient | New patient, cleaning, general checkup, unsupported/staff-only types, insurance |
| scenario_02_new_patient_emergency | Emergency summary, staff notification, non-emergency, red-flag symptoms |
| scenario_03_existing_patient | Identity, follow-up, reschedule, cancel, cross-patient access |
| scenario_04_family | Spouse + kids, back-to-back, view-only dependents, unrelated patients |
| scenario_05_edge_cases | Closures, full days, ambiguity, prompt injection, fallback, handoff |

All logins use the password `Test-Password-123`.
