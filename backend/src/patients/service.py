from src.patients.models import Patient
from src.patients.schemas import PatientCreate


def new_patient(data: PatientCreate) -> Patient:
    """Build (but do not persist) a patient; the caller owns the transaction."""
    return Patient(
        first_name=data.first_name,
        last_name=data.last_name,
        birth_date=data.birth_date,
        phone_number=data.phone_number,
    )
