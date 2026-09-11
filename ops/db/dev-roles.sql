-- Development and test credentials for the application role.
--
-- Migration 0001 creates `workos_app` as NOLOGIN and passwordless: credentials are an environment
-- concern, never a migration concern. This script grants it a login for local work only. Production
-- issues the password through the secret manager and never through a file in the repository.
--
-- The role is deliberately not the table owner. Combined with FORCE ROW LEVEL SECURITY on every
-- tenant-scoped table, isolation holds for the owner as well, so neither mechanism is load-bearing
-- on its own (PQ-2).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workos_app') THEN
        CREATE ROLE workos_app LOGIN PASSWORD 'workos_app';
    ELSE
        ALTER ROLE workos_app LOGIN PASSWORD 'workos_app';
    END IF;
END
$$;
