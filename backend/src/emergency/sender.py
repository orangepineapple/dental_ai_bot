"""Transport to the staff notification channel (currently a plain HTTP POST to a dummy URL)."""

from typing import Any

import httpx

from src.emergency.config import EmergencyConfig, emergency_settings


class StaffChannelError(Exception):
    """Delivery did not happen. The message is short, safe to store and contains no payload."""


async def post_to_staff_channel(
    payload: dict[str, Any],
    *,
    idempotency_key: str,
    config: EmergencyConfig = emergency_settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """POST the payload. Returns only on a 2xx answer; raises StaffChannelError otherwise."""
    headers = {"Idempotency-Key": idempotency_key}
    if config.STAFF_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {config.STAFF_WEBHOOK_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=config.STAFF_WEBHOOK_TIMEOUT_SECONDS, transport=transport) as client:
            response = await client.post(config.STAFF_WEBHOOK_URL, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise StaffChannelError("staff channel timed out") from exc
    except httpx.HTTPError as exc:
        raise StaffChannelError(f"staff channel unreachable ({type(exc).__name__})") from exc
    if not response.is_success:
        raise StaffChannelError(f"staff channel answered HTTP {response.status_code}")
