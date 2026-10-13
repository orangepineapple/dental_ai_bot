import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Server-side identity and state for one tool call.

    Authorization data lives here and NEVER in model-supplied arguments: the
    model cannot choose whose records it acts on. Dependents/family members are
    resolved from `patient_id` through the family service, not trusted from args.
    """

    session: AsyncSession
    user_id: uuid.UUID
    patient_id: uuid.UUID
    conversation_id: uuid.UUID
    # Provider-issued id of this call; useful for logging and idempotency keys.
    tool_call_id: str


class ToolArgs(BaseModel):
    """Base for tool arguments. Unknown fields are rejected (additionalProperties: false)."""

    model_config = {"extra": "forbid"}


ToolHandler = Callable[[Any, ToolContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    args_model: type[ToolArgs]
    handler: ToolHandler

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }
