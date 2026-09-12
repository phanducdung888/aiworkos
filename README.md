# AI WorkOS

An AI-native work management platform. The **Work Core** is a deterministic system of record that
works with AI switched off; the **Intelligence Layer** observes activity and proposes structure, and
never touches the database.

**Current state: Phase 1, Checkpoint 2 complete.** Schema, domain rules, authorization model, tenancy,
audit and outbox exist and are tested. There is no API layer and no UI yet. 151 tests pass.

---

## 1. Read this first

`CLAUDE.md` in the repository root is the working agreement: the non-negotiable rules, the layering,
and where every answer lives. Read it before writing code. The rules in it are architectural
invariants, not preferences.

Binding product decisions are in `docs/decisions/decision-pack-v1.0.md` and
`resolution-pack-v1.1.md`. Do not reinterpret them; if an implementation detail conflicts with one,
stop and report the conflict.

## 2. Prerequisites

- Python 3.12+
- Docker Desktop (for PostgreSQL, and later Redis, MinIO, Keycloak)
- VS Code with the extensions in `.vscode/extensions.json` (VS Code will offer to install them)

## 3. Setup

```bash
git init && git add -A && git commit -m "AI WorkOS: Phase 1 checkpoints 1-2"

cp .env.example .env

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
cd backend && pip install -e ".[dev]" && cd ..

make up          # PostgreSQL, Redis, MinIO
make dev-db      # create workos_owner and workos_app, hand them the development schema
make test-db     # same, for workos_test
make migrate     # apply migrations 0001-0005
make seed        # a development organization, its people and a team
make check       # backend gate + frontend gate

cd frontend && npm ci && cp .env.example .env.local && npm run dev
make e2e         # the curated journeys, in a real browser
```

`make dev-db` is only needed on a database created before `ops/db/dev-roles.sql` last changed; a
fresh volume runs the same script from `docker-entrypoint-initdb.d`. It is idempotent either way.

In VS Code select `.venv` as the interpreter (Ctrl/Cmd+Shift+P → *Python: Select Interpreter*). The
Testing panel is pre-configured to run pytest from `backend/`.

If you do not want Docker, any local PostgreSQL 16 works. Run `ops/db/dev-roles.sql` as a superuser
first: it creates the two roles the stack uses and hands schema `public` to the first of them.

Neither role is the cluster superuser, and that is a control rather than hygiene. `FORCE ROW LEVEL
SECURITY` is not enforced against a superuser or a `BYPASSRLS` role — the policies remain, the
planner ignores them. An owner with either attribute would leave tenant isolation resting on
application scoping alone while every isolation test still passed, so
`test_rls_isolation.py::test_neither_role_can_bypass_row_level_security` asserts the attribute
directly.

| Role | Purpose | Attributes |
|---|---|---|
| `workos_owner` | owns the schema, runs migrations | `LOGIN CREATEROLE NOSUPERUSER NOBYPASSRLS` |
| `workos_app` | serves requests | `LOGIN` only; owns nothing |

## 3a. The frontend

React + TypeScript + Vite, TanStack Query for every piece of server state and no client-side copy of
it. The API client is generated from `backend/openapi.json`; `npm run generate:api:check` fails the
build when the two have drifted, which is what keeps the spec honest rather than decorative.

Sign-in is OIDC authorization-code with PKCE against the Compose Keycloak realm. The access token is
held in memory only. The organization arrives in an `X-Organization-Id` header on every request and
is never inferred from the token, because a human may work for more than one.

| Command | What it does |
|---|---|
| `npm run dev` | Vite, proxying `/api` to the backend on 8000 |
| `npm run test` | L5 component tests, including axe checks |
| `npm run e2e` | L6 journeys; starts the API and the dev server itself |
| `npm run generate:api` | Regenerate the client from the committed OpenAPI snapshot |

## 4. Layout

```
CLAUDE.md                  working agreement — read first
docs/                      architecture, domain, security, testing, ADRs
backend/
  app/platform/            db, authz, audit, outbox, actor, config
  app/contexts/identity/   Organization, Person, Team, Department, roles
  app/contexts/work/       Project, Milestone, Work, WorkAssignment, Dependency
  app/api/                 empty until Checkpoint 4
  migrations/versions/     0001-0005
  tests/{unit,integration,architecture}/
ops/db/dev-roles.sql       role bootstrap (environment, not migration)
docker-compose.yml         five networks; `data` is a security boundary
```

## 5. Commands

| Command | What it does |
|---|---|
| `make test` | Full suite against PostgreSQL |
| `make test-unit` | Domain and authorization only, no database, sub-second |
| `make lint` / `make types` / `make imports` | ruff, mypy --strict, architecture contracts |
| `make check` | Everything CI runs |
| `make migrate` / `make downgrade` | Alembic up and down |

## 6. Things that will fail the build, by design

These are the invariants the architecture tests protect. They are cheap to honour and expensive to
retrofit:

- An assignee or owner column on `work`. `WorkAssignment` is canonical (ADR-0032).
- A required `project_id`, or a synthetic General/Default/Unassigned project (ADR-0029).
- A `comment` table. Comments are Events (ADR-0033).
- A tenant-scoped table without `org_id`, an RLS policy, or a registry entry.
- A router importing SQLAlchemy, or a context importing FastAPI.
- The string `auto_apply`, `level_3` or `ai_autonomous` anywhere in the backend. The MVP autonomy
  ceiling is `level_2_approved_execution` (BR-AI-30, BR-AI-31).
- Any future `agent/` code importing the data layer, or an agent service on the `data` network.

## 7. Where to pick up

`docs/development/progress.md` holds the checkpoint log and the open-questions register.

Next is **Checkpoint 3**: application services for the five Work Core aggregates, wiring
`authorize` → domain validation → repository → audit → outbox. That is where the authorization
matrix stops being theoretical and the first useful error messages appear.

Two open items to settle before Checkpoint 4, neither blocking Checkpoint 3: the outbox relay is
currently org-scoped and a cross-org dispatcher needs a decision, and `tenant_scoped_table` is
maintained by hand in each migration.
