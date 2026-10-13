import uuid
from datetime import date

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base, TimestampMixin


class Patient(TimestampMixin, Base):
    """A person who can receive care.

    Kept separate from the login identity (auth_user) so that family members
    without their own account can be patients managed by a guardian.
    """

    __tablename__ = "patient"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[str] = mapped_column(String(128))
    birth_date: Mapped[date]
    phone_number: Mapped[str | None] = mapped_column(String(32))
