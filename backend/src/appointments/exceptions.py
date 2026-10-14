class AppointmentError(Exception):
    """A business-rule outcome. `str(exc)` is written to be shown to (or paraphrased for) a patient."""

    status = "error"
    code: str | None = None


class NeedsConfirmation(AppointmentError):
    status = "needs_confirmation"


class NotFound(AppointmentError):
    """Used for both missing and not-yours, so ids cannot be probed."""

    status = "not_found"


class NotAuthorized(AppointmentError):
    status = "not_authorized"


class SlotUnavailable(AppointmentError):
    status = "slot_unavailable"


class PolicyViolation(AppointmentError):
    status = "policy_violation"


class RequiresStaff(AppointmentError):
    status = "requires_staff"


class UnknownAppointmentType(AppointmentError):
    status = "error"
    code = "unknown_appointment_type"
