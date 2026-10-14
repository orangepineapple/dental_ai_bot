"""SCENARIO 4 - Family scheduling: several people, back-to-back.

    python -m tests.user_tests.scenario_04_family --yes

NOTE: the family tools (get_authorized_family_members, find_consecutive_slots,
book_family_appointments) are not in the tool registry yet; until they are, the bot
can only handle this scenario one person at a time via the existing appointment tools.

DB STATE
  * Log in as "Priya Shah" (priya.shah@example.com), age ~40.
  * Managed by Priya (guardian link, can_book=true), none of them have a login:
      - Arjun Shah   (spouse, adult)
      - Mira Shah    (daughter, 9)
      - Dev Shah     (son, 6)
  * Managed but can_book=FALSE: Leela Shah (mother-in-law, adult) - view only.
  * NOT related to Priya: "Tom Unrelated" - no link at all.
  * Past: Mira had a cleaning ~5 months ago. Dev has never visited.
  * Capacity: Dr. Lee is booked solid tomorrow-ish morning (see below) so back-to-back
    is only possible in some places. Dr. Patel has one existing patient at 10:00 on the
    same day. Both are needed to force the planner to find a genuine consecutive block.

WHAT TO SEND

  Coordinated booking
    1. "I need cleanings for me, my husband and both kids on the same day."
         EXPECT: identifies exactly Priya, Arjun, Mira, Dev (4 people); 60 min each;
                 asks for preferred day/time window; proposes slots as a set, does NOT
                 reserve them while proposing.
    2. "Back to back with the kids first, then me and my husband, in the afternoon."
         EXPECT: consecutive times in that order, no gaps, within 13:00-17:00;
                 possibly across both dentists in parallel - that is acceptable only if
                 the bot says so.
    3. Click CONFIRM on the card(s).
         EXPECT: all 4 booked, or a CLEAR statement of which succeeded and which failed
                 (all-or-partial behaviour is explicit). Check the DB afterwards.

  Authorization edges
    4. "Also book a cleaning for my mother-in-law Leela."
         EXPECT: refused - she is view-only (can_book=false); offers to hand off to staff.
    5. "Book one for Tom Unrelated too."
         EXPECT: refused; no information about Tom.
    6. "Cancel Arjun's appointment." (after step 3)
         EXPECT: allowed (Arjun is manageable), asks for confirmation first.
    7. "What does Mira have scheduled?"
         EXPECT: lists Mira's appointments only.

  Constraints / failure cases
    8. "Book all four for tomorrow at 9am, all at the same time."
         EXPECT: at most the number of dentists can run in parallel; not 4 at once.
                 No double-booked provider.
    9. "Find one slot for all of us in the 12:00-13:00 hour."
         EXPECT: that is lunch / outside windows; none offered.
   10. "Just book Dev, he's 6, a wisdom tooth extraction."
         EXPECT: staff-booked type; not booked by the bot.
   11. Send the same booking request twice quickly / retry after a timeout.
         EXPECT: no duplicate appointments (idempotency).
"""

from datetime import date, timedelta

from tests.user_tests.harness import (
    COMPLETED,
    Account,
    add_appointment,
    add_person,
    clinic_dt,
    guardian_of,
    providers,
    run,
)


def _years_ago(years: int) -> date:
    return date.today() - timedelta(days=int(years * 365.25) + 30)


async def seed(session) -> list[Account]:
    p = await providers(session)
    lee, patel = p["Dr. Lee"], p["Dr. Patel"]
    priya = await add_person(session, "Priya", "Shah", _years_ago(40), email="priya.shah@example.com")
    arjun = await add_person(session, "Arjun", "Shah", _years_ago(42))
    mira = await add_person(session, "Mira", "Shah", _years_ago(9))
    dev = await add_person(session, "Dev", "Shah", _years_ago(6))
    leela = await add_person(session, "Leela", "Shah", _years_ago(70))
    tom = await add_person(session, "Tom", "Unrelated", _years_ago(33))

    for dependent in (arjun, mira, dev):
        guardian_of(session, priya, dependent)
    guardian_of(session, priya, leela, can_book=False)

    await add_appointment(session, mira, lee, "cleaning", clinic_dt(-150, 10), status=COMPLETED)

    # Make the morning two days out hard: Dr. Lee booked 9-12 solid, Dr. Patel only 10:00.
    day = 2
    for hour in (9, 10, 11):
        await add_appointment(session, tom, lee, "cleaning", clinic_dt(day, hour))
    await add_appointment(session, tom, patel, "cleaning", clinic_dt(day, 10))
    return [Account("family organiser", priya.email)]


if __name__ == "__main__":
    run("Family with dependents", seed)
