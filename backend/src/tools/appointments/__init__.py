"""Scheduling (not yet implemented).

Planned services: get_appointment_types, get_available_slots, book_appointment,
reschedule_appointment, propose_cancellation, get_booking_status,
get_patient_appointments, get_appointment_details. Booking must recheck
availability inside a transaction and be idempotent on retry.
"""
