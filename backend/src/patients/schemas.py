import uuid
from typing import Annotated

from pydantic import ConfigDict, Field, PastDate, StringConstraints

from src.models import CustomModel

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
PhoneNumber = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 ()\-]{7,20}$")
]


class PatientCreate(CustomModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Name
    last_name: Name
    birth_date: PastDate
    phone_number: PhoneNumber | None = Field(default=None)


class PatientResponse(CustomModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
