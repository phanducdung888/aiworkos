-- Development and test credentials. Two roles, neither of them the cluster superuser.
--
-- Migration 0001 creates no roles: credentials are an environment concern, never a migration
-- concern. This script is that environment concern for local work. Production issues both
-- passwords through the secret manager and never through a file in the repository.
--
--   workos_owner  owns the schema and runs migrations. NOSUPERUSER and NOBYPASSRLS, because
--                 FORCE ROW LEVEL SECURITY is silently inert for a superuser or a BYPASSRLS role:
--                 the policies exist, the planner skips them, and the isolation tests would be
--                 proving nothing. CREATEROLE so that this script can be re-run by the owner
--                 itself (the test harness does exactly that) without needing the superuser.
--   workos_app    serves requests. No ownership, no CREATEROLE, so isolation holds even if a
--                 policy is ever mis-specified.
--   workos_worker runs the job worker and nothing else (ADR-0046). It holds one extra policy, on
--                 `job`, so it can claim work before it knows which tenant the work belongs to.
--                 On every other table it is exactly as constrained as workos_app — the exemption
--                 is an identity with one privilege, not a session state that switches isolation
--                 off wherever it is forgotten.
--
-- Neither block alters an existing role. Re-running this as workos_owner must be a no-op rather
-- than a permission error, and the ownership statements below are skipped unless the caller is the
-- superuser that can execute them.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workos_owner') THEN
        CREATE ROLE workos_owner LOGIN PASSWORD 'workos_owner'
            NOSUPERUSER NOBYPASSRLS NOINHERIT CREATEROLE;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workos_app') THEN
        CREATE ROLE workos_app LOGIN PASSWORD 'workos_app'
            NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workos_worker') THEN
        CREATE ROLE workos_worker LOGIN PASSWORD 'workos_worker'
            NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
    END IF;
END
$$;

-- Hand the schema to the owner role. Only the superuser (or the current owner) can do this, and the
-- superuser is who runs this script at initdb and from `make test-db`. CREATE on the database is
-- what lets the test harness drop and recreate `public` between runs.
DO $$
BEGIN
    IF current_setting('is_superuser') = 'on' THEN
        EXECUTE 'ALTER SCHEMA public OWNER TO workos_owner';
        EXECUTE format('GRANT CREATE, CONNECT ON DATABASE %I TO workos_owner', current_database());
    END IF;
END
$$;

-- Converge a database that was provisioned before the owner role existed. Without this the script
-- is only correct on a fresh volume: every developer with an older `workos` database would keep a
-- schema whose tables are owned by the superuser, where FORCE ROW LEVEL SECURITY does nothing.
-- Ownership is metadata, so this rewrites no rows and drops nothing.
DO $$
DECLARE
    rel  record;
    proc record;
BEGIN
    IF current_setting('is_superuser') <> 'on' THEN
        RETURN;
    END IF;

    FOR rel IN
        SELECT c.relkind, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
          AND c.relowner <> 'workos_owner'::regrole
    LOOP
        EXECUTE format(
            'ALTER %s public.%I OWNER TO workos_owner',
            CASE rel.relkind
                WHEN 'S' THEN 'SEQUENCE'
                WHEN 'v' THEN 'VIEW'
                WHEN 'm' THEN 'MATERIALIZED VIEW'
                ELSE 'TABLE'
            END,
            rel.relname
        );
    END LOOP;

    FOR proc IN
        SELECT p.oid::regprocedure AS signature
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prokind IN ('f', 'p')
          AND p.proowner <> 'workos_owner'::regrole
    LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO workos_owner', proc.signature);
    END LOOP;
END
$$;
