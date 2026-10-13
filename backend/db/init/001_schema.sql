-- Initial schema for the patient and auth tables.
--
-- Mirrors src/patients/models.py, src/auth/models.py and the Alembic revision
-- 0001_patient_auth. Postgres runs this once, on first start of an empty data
-- volume (docker-entrypoint-initdb.d). To re-run it: docker compose down -v
--
-- The alembic_version row at the bottom marks the database as already at
-- revision 0001_patient_auth, so `alembic upgrade head` stays a safe no-op.
-- Keep this file in sync with the models whenever a new migration is added.

BEGIN;

CREATE TABLE patient (
    id           UUID                     NOT NULL,
    first_name   VARCHAR(128)             NOT NULL,
    last_name    VARCHAR(128)             NOT NULL,
    birth_date   DATE                     NOT NULL,
    phone_number VARCHAR(32),
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT patient_pkey PRIMARY KEY (id)
);

CREATE TABLE auth_user (
    id            UUID                     NOT NULL,
    email         VARCHAR(320)             NOT NULL,
    password_hash VARCHAR(255)             NOT NULL,
    patient_id    UUID                     NOT NULL,
    is_active     BOOLEAN                  NOT NULL DEFAULT true,
    last_login_at TIMESTAMP WITH TIME ZONE,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT auth_user_pkey PRIMARY KEY (id),
    CONSTRAINT auth_user_email_key UNIQUE (email),
    CONSTRAINT auth_user_patient_id_key UNIQUE (patient_id),
    CONSTRAINT auth_user_email_lowercase_check CHECK (email = lower(email)),
    CONSTRAINT auth_user_patient_id_fkey
        FOREIGN KEY (patient_id) REFERENCES patient (id) ON DELETE RESTRICT
);

CREATE TABLE auth_refresh_token (
    id         UUID                     NOT NULL,
    user_id    UUID                     NOT NULL,
    token_hash VARCHAR(64)              NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT auth_refresh_token_pkey PRIMARY KEY (id),
    CONSTRAINT auth_refresh_token_token_hash_key UNIQUE (token_hash),
    CONSTRAINT auth_refresh_token_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES auth_user (id) ON DELETE CASCADE
);

CREATE INDEX auth_refresh_token_user_id_idx ON auth_refresh_token (user_id);

-- Alembic bookkeeping (see header).
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
INSERT INTO alembic_version (version_num) VALUES ('0001_patient_auth');

COMMIT;
