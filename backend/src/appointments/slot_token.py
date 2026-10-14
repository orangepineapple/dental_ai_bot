"""Server-signed, short-lived slot tokens.

A token is NOT a reservation. It only proves the server offered this (provider, start,
type), so the model cannot invent slots. Availability is re-checked when booking.
"""

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from src.appointments.exceptions import SlotUnavailable

_DOMAIN = b"appointments-slot-token-v1"


@dataclass(frozen=True, slots=True)
class SlotClaims:
    provider_id: uuid.UUID
    appointment_type: str
    starts_at: datetime  # aware, UTC
    expires_at: datetime  # aware, UTC


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), _DOMAIN, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_slot_token(secret: str, claims: SlotClaims) -> str:
    payload = json.dumps(
        {
            "p": str(claims.provider_id),
            "t": claims.appointment_type,
            "s": int(claims.starts_at.timestamp()),
            "x": int(claims.expires_at.timestamp()),
        },
        separators=(",", ":"),
    ).encode()
    body = _b64(payload)
    signature = _b64(hmac.new(_key(secret), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_slot_token(secret: str, token: str, now: datetime, *, allow_expired: bool = False) -> SlotClaims:
    """Return the claims or raise SlotUnavailable (the model should search again)."""
    invalid = SlotUnavailable("That time slot is not valid. Search for available times again.")
    try:
        body, signature = token.split(".", 1)
        expected = _b64(hmac.new(_key(secret), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise invalid
        data = json.loads(_unb64(body))
        claims = SlotClaims(
            provider_id=uuid.UUID(data["p"]),
            appointment_type=str(data["t"]),
            starts_at=datetime.fromtimestamp(int(data["s"]), UTC),
            expires_at=datetime.fromtimestamp(int(data["x"]), UTC),
        )
    except SlotUnavailable:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise invalid from exc
    if not allow_expired and claims.expires_at <= now:
        raise SlotUnavailable("That time slot offer has expired. Search for available times again.")
    return claims
