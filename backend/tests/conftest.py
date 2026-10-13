import os

# Settings are read when `src` is imported, so test defaults must be set first.
# Integration tests never use DATABASE_URL; they connect to TEST_DATABASE_URL only.
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dental_unused"
)
os.environ.setdefault("AUTH_JWT_SECRET", "test-only-secret-" + "x" * 40)
os.environ.setdefault("AUTH_SECURE_COOKIES", "false")
