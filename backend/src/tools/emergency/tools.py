"""Model-facing emergency tools.

Handlers are thin: identity comes from `ctx`, rules live in src/emergency/service.py.
The model never receives patient contact details and never decides wording: it relays
clinic-approved text and the real delivery status.
"""

from typing import Any

from pydantic import Field

from src.emergency import service
from src.emergency.models import DELIVERED
from src.tools.base import ToolArgs, ToolContext, ToolDefinition


class GetEmergencyInstructionsArgs(ToolArgs):
    pass


async def get_emergency_instructions(args: GetEmergencyInstructionsArgs, ctx: ToolContext) -> dict[str, Any]:
    """Clinic-approved escalation guidance. No diagnosis or treatment advice is generated."""
    return {"status": "ok", "instructions": service.get_emergency_instructions()}


class NotifyStaffOfEmergencyArgs(ToolArgs):
    summary: str = Field(
        min_length=10,
        max_length=1000,
        description=(
            "Short plain summary of what the patient reported: what happened, when, symptoms "
            "and pain level, in their own words. No diagnosis, no advice."
        ),
    )


async def notify_staff_of_emergency(args: NotifyStaffOfEmergencyArgs, ctx: ToolContext) -> dict[str, Any]:
    """Send the summary to clinic staff and report whether delivery really happened."""
    outcome = await service.notify_staff_of_emergency(
        ctx.session,
        user_id=ctx.user_id,
        patient_id=ctx.patient_id,
        conversation_id=ctx.conversation_id,
        summary=args.summary,
    )
    if outcome.status == DELIVERED:
        return {
            "status": "staff_notified",
            "message": "Clinic staff have been told about the nature of the emergency.",
        }
    return {
        "status": "delivery_failed",
        "message": (
            "Staff could NOT be reached. Do not say they were notified. Tell the patient to "
            "call the clinic directly (or emergency services if severe) right now."
        ),
    }


EMERGENCY_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_emergency_instructions",
        description="Get the clinic-approved emergency guidance to relay to the patient.",
        args_model=GetEmergencyInstructionsArgs,
        handler=get_emergency_instructions,
    ),
    ToolDefinition(
        name="notify_staff_of_emergency",
        description=(
            "Tell clinic staff about a patient's dental emergency, once you have a short "
            "summary of what happened. Only say staff were notified if the status is "
            "staff_notified."
        ),
        args_model=NotifyStaffOfEmergencyArgs,
        handler=notify_staff_of_emergency,
    ),
]
