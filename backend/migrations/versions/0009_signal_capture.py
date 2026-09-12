"""Signal/Capture: Event, EventParticipant, EventAttachment, Evidence.

The Event is the record of something that happened, so the schema's job is to make it hard to
rewrite. Three mechanisms, deliberately overlapping:

* RLS, so a row is unreachable from another organization even with hand-written SQL (BR-G-01).
* A column-scoped immutability trigger (ADR-0038), so the fields that describe the occurrence are
  frozen while the ones the extraction and retention pipelines own stay writable (BR-E-01).
* Partial unique indexes for the two dedup rules that must hold regardless of application code:
  one Event per `(source_system, source_ref)` (BR-E-02) and one live attachment per object key.

Evidence is created here with its invariants but has no API yet: extraction is a later checkpoint,
and a table with a foreign key and a check constraint is the part that must exist first so the
Events being captured now are citable later without a backfill.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


#: Columns an Event may change after insert, and nothing else (ADR-0038, BR-E-01).
#:
#: Each one belongs to a process that runs *after* capture and does not alter what was observed:
#: extraction owns the processing fields, participant resolution maintains the count, and the
#: retention sweep owns the lifetime fields including purging the raw payload (BR-E-07).
EVENT_MUTABLE = (
    "processing_status",
    "processing_error",
    "participant_count",
    "retention_expires_at",
    "raw_payload_uri",
    "deleted_at",
)

#: `external_handle` is absent on purpose: BR-E-12 requires the raw sender identifier to survive
#: resolution unchanged, so resolving a participant may fill in who they are and may never edit the
#: evidence of how they appeared.
PARTICIPANT_MUTABLE = ("person_id", "match_confidence", "resolved_at")


def _immutability_trigger(table: str, mutable: tuple[str, ...], rule: str) -> str:
    """A BEFORE UPDATE row trigger refusing any change outside `mutable`.

    Written as a comparison of the whole row rather than a list of `IF OLD.x IS DISTINCT FROM NEW.x`
    branches: `to_jsonb` minus the mutable keys leaves exactly the frozen part of the row, so a
    column added by a later migration is frozen by default. Forgetting to classify a new field
    fails closed, which is the only direction worth failing in here.
    """
    keys = ", ".join(f"'{column}'" for column in mutable)
    return f"""
        CREATE OR REPLACE FUNCTION {table}_is_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            frozen_old jsonb;
            frozen_new jsonb;
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION
                    '{table} is immutable ({rule}): % is not permitted', TG_OP
                    USING ERRCODE = 'restrict_violation';
            END IF;
            frozen_old := to_jsonb(OLD) - ARRAY[{keys}];
            frozen_new := to_jsonb(NEW) - ARRAY[{keys}];
            IF frozen_old <> frozen_new THEN
                RAISE EXCEPTION
                    '{table} is immutable ({rule}); a correction is a new row, not an edit'
                    USING ERRCODE = 'restrict_violation',
                          DETAIL = format('changed: %s', (
                              SELECT string_agg(key, ', ' ORDER BY key)
                              FROM jsonb_each(frozen_new)
                              WHERE value IS DISTINCT FROM frozen_old -> key
                          ));
            END IF;
            RETURN NEW;
        END
        $$;
    """


def upgrade() -> None:
    # ------------------------------------------------------------------ event
    op.execute(
        """
        CREATE TABLE event (
            id                     uuid PRIMARY KEY,
            org_id                 uuid NOT NULL REFERENCES organization (id),
            source_system          text NOT NULL,
            source_ref             text,
            content_hash           text NOT NULL,
            origin                 text NOT NULL,
            origin_domain_event_id uuid,
            revision_of_event_id   uuid,
            type                   text NOT NULL,
            channel                text,
            sender_external_id     text,
            occurred_at            timestamptz NOT NULL,
            observed_at            timestamptz NOT NULL DEFAULT now(),
            title                  text,
            body_text              text,
            raw_payload_uri        text,
            sensitivity            text NOT NULL DEFAULT 'normal',
            participant_count      integer NOT NULL DEFAULT 0,
            processing_status      text NOT NULL DEFAULT 'received',
            processing_error       text,
            retention_expires_at   timestamptz,
            deleted_at             timestamptz,
            captured_by_person_id  uuid,
            created_at             timestamptz NOT NULL DEFAULT now(),
            version                integer NOT NULL DEFAULT 1,

            CONSTRAINT event_origin_valid
                CHECK (origin IN ('external', 'internal')),
            CONSTRAINT event_type_valid
                CHECK (type IN ('EXTERNAL_MESSAGE', 'MANUAL_CAPTURE', 'MEETING_NOTE',
                                'COMMENT', 'ATTACHMENT', 'SYSTEM_ACTIVITY')),
            CONSTRAINT event_sensitivity_valid
                CHECK (sensitivity IN ('normal', 'confidential', 'restricted')),
            CONSTRAINT event_processing_status_valid
                CHECK (processing_status IN ('received', 'normalised', 'extracted',
                                             'failed', 'skipped')),
            -- BR-E-03: an Event with neither text nor a payload records nothing.
            CONSTRAINT event_has_content
                CHECK (body_text IS NOT NULL OR raw_payload_uri IS NOT NULL),
            -- BR-E-10: an internal Event is projected from a DomainEvent and says which one.
            CONSTRAINT event_internal_names_its_source
                CHECK (origin = 'external' OR origin_domain_event_id IS NOT NULL),
            -- A revision points at another Event, never at itself (BR-E-02).
            CONSTRAINT event_revision_is_not_self
                CHECK (revision_of_event_id IS NULL OR revision_of_event_id <> id),
            -- Composite, so a reference can never cross a tenant boundary (BR-G-01a).
            CONSTRAINT event_revision_same_org
                FOREIGN KEY (org_id, revision_of_event_id) REFERENCES event (org_id, id),
            CONSTRAINT event_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    # BR-E-02: ingestion is idempotent on (org_id, source_system, source_ref). Partial, because a
    # manual capture has no external ref and two of those are two different observations, not a
    # duplicate. A revision shares the ref with its original by design, so it is excluded too —
    # what the index forbids is a second *original*.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_event_source_ref
        ON event (org_id, source_system, source_ref)
        WHERE source_ref IS NOT NULL AND revision_of_event_id IS NULL AND deleted_at IS NULL
        """
    )
    op.execute("CREATE INDEX ix_event_occurred_at ON event (org_id, occurred_at DESC)")
    op.execute("CREATE INDEX ix_event_type ON event (org_id, type)")
    op.execute(
        "CREATE INDEX ix_event_extractable ON event (org_id, processing_status) "
        "WHERE origin = 'external' AND deleted_at IS NULL"
    )
    op.execute("CREATE INDEX ix_event_revision_of ON event (org_id, revision_of_event_id)")
    op.execute("CREATE INDEX ix_event_content_hash ON event (org_id, content_hash)")

    # ------------------------------------------------------------------ event_participant
    op.execute(
        """
        CREATE TABLE event_participant (
            id               uuid PRIMARY KEY,
            org_id           uuid NOT NULL REFERENCES organization (id),
            event_id         uuid NOT NULL,
            person_id        uuid,
            external_handle  text,
            role             text NOT NULL,
            match_confidence smallint NOT NULL DEFAULT 0,
            resolved_at      timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            version          integer NOT NULL DEFAULT 1,

            CONSTRAINT event_participant_role_valid
                CHECK (role IN ('organiser', 'speaker', 'mentioned', 'recipient')),
            CONSTRAINT event_participant_confidence_range
                CHECK (match_confidence BETWEEN 0 AND 100),
            -- A participant nobody can name is not a participant (BR-E-12).
            CONSTRAINT event_participant_is_identifiable
                CHECK (person_id IS NOT NULL OR external_handle IS NOT NULL),
            -- Confidence describes a resolution, so it is meaningless without one.
            CONSTRAINT event_participant_confidence_needs_a_person
                CHECK (person_id IS NOT NULL OR match_confidence = 0),
            CONSTRAINT event_participant_event_fk
                FOREIGN KEY (org_id, event_id) REFERENCES event (org_id, id),
            CONSTRAINT event_participant_person_fk
                FOREIGN KEY (org_id, person_id) REFERENCES person (org_id, id)
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_event_participant_event ON event_participant (org_id, event_id)"
    )
    op.execute(
        "CREATE INDEX ix_event_participant_person ON event_participant (org_id, person_id) "
        "WHERE person_id IS NOT NULL"
    )
    # One row per person per event per role. Naming the same person twice as `speaker` is a
    # duplicate; naming them `speaker` and `mentioned` is two true facts.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_event_participant_person
        ON event_participant (org_id, event_id, person_id, role)
        WHERE person_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_event_participant_handle
        ON event_participant (org_id, event_id, external_handle, role)
        WHERE person_id IS NULL AND external_handle IS NOT NULL
        """
    )

    # ------------------------------------------------------------------ event_attachment
    op.execute(
        """
        CREATE TABLE event_attachment (
            id                   uuid PRIMARY KEY,
            org_id               uuid NOT NULL REFERENCES organization (id),
            event_id             uuid NOT NULL,
            object_key           text NOT NULL,
            filename             text NOT NULL,
            media_type           text NOT NULL,
            size_bytes           bigint,
            checksum             text,
            status               text NOT NULL DEFAULT 'pending',
            uploaded_by_person_id uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            completed_at         timestamptz,
            version              integer NOT NULL DEFAULT 1,

            CONSTRAINT event_attachment_status_valid
                CHECK (status IN ('pending', 'available', 'purged')),
            -- ADR-0039: size and checksum are the store's report, recorded at completion. A row
            -- claiming to be available without them means the completion step was skipped.
            CONSTRAINT event_attachment_available_is_measured
                CHECK (status <> 'available'
                       OR (size_bytes IS NOT NULL AND checksum IS NOT NULL
                           AND completed_at IS NOT NULL)),
            CONSTRAINT event_attachment_size_non_negative
                CHECK (size_bytes IS NULL OR size_bytes >= 0),
            CONSTRAINT event_attachment_event_fk
                FOREIGN KEY (org_id, event_id) REFERENCES event (org_id, id),
            CONSTRAINT event_attachment_person_fk
                FOREIGN KEY (org_id, uploaded_by_person_id) REFERENCES person (org_id, id),
            CONSTRAINT event_attachment_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_event_attachment_event ON event_attachment (org_id, event_id)"
    )
    # The key is derived from (org_id, event_id, attachment_id) and is never client-supplied, so a
    # collision would mean a bug rather than a conflict. The index is what turns that into a
    # failure instead of two rows pointing at one object.
    op.execute(
        "CREATE UNIQUE INDEX ux_event_attachment_object_key ON event_attachment (object_key)"
    )

    # ------------------------------------------------------------------ evidence
    op.execute(
        """
        CREATE TABLE evidence (
            id                uuid PRIMARY KEY,
            org_id            uuid NOT NULL REFERENCES organization (id),
            event_id          uuid NOT NULL,
            locator           jsonb NOT NULL,
            excerpt           text,
            claim_summary     text,
            target_type       text NOT NULL,
            target_id         uuid NOT NULL,
            assertion         text NOT NULL,
            confidence        smallint NOT NULL DEFAULT 0,
            produced_by_type  text NOT NULL,
            produced_by_id    uuid,
            superseded_by_id  uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            version           integer NOT NULL DEFAULT 1,

            CONSTRAINT evidence_assertion_valid
                CHECK (assertion IN ('creates', 'supports', 'completes', 'updates',
                                     'reassigns', 'reschedules', 'contradicts', 'closes')),
            CONSTRAINT evidence_target_type_valid
                CHECK (target_type IN ('work', 'project', 'milestone', 'dependency',
                                       'commitment', 'risk', 'decision')),
            CONSTRAINT evidence_produced_by_valid
                CHECK (produced_by_type IN ('person', 'ai_interaction')),
            CONSTRAINT evidence_confidence_range
                CHECK (confidence BETWEEN 0 AND 100),
            -- BR-E-05 and BR-E-14: text evidence quotes verbatim, attachment evidence summarises.
            -- Exactly one of the two, never both and never neither.
            CONSTRAINT evidence_quotes_or_summarises
                CHECK ((excerpt IS NULL) <> (claim_summary IS NULL)),
            CONSTRAINT evidence_supersede_is_not_self
                CHECK (superseded_by_id IS NULL OR superseded_by_id <> id),
            -- BR-E-04: Evidence and its Event are in the same organization, structurally.
            CONSTRAINT evidence_event_fk
                FOREIGN KEY (org_id, event_id) REFERENCES event (org_id, id),
            CONSTRAINT evidence_superseded_fk
                FOREIGN KEY (org_id, superseded_by_id) REFERENCES evidence (org_id, id),
            CONSTRAINT evidence_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    op.execute("CREATE INDEX ix_evidence_event ON evidence (org_id, event_id)")
    op.execute("CREATE INDEX ix_evidence_target ON evidence (org_id, target_type, target_id)")
    op.execute(
        "CREATE INDEX ix_evidence_live ON evidence (org_id, target_type, target_id) "
        "WHERE superseded_by_id IS NULL"
    )

    # ------------------------------------------------------------------ immutability (ADR-0038)
    op.execute(_immutability_trigger("event", EVENT_MUTABLE, "BR-E-01"))
    op.execute(
        """
        CREATE TRIGGER trg_event_immutable
        BEFORE UPDATE OR DELETE ON event
        FOR EACH ROW EXECUTE FUNCTION event_is_immutable();
        """
    )
    op.execute(_immutability_trigger("event_participant", PARTICIPANT_MUTABLE, "BR-E-12"))
    op.execute(
        """
        CREATE TRIGGER trg_event_participant_immutable
        BEFORE UPDATE OR DELETE ON event_participant
        FOR EACH ROW EXECUTE FUNCTION event_participant_is_immutable();
        """
    )
    # BR-E-06: Evidence is immutable except for being superseded. A correction is a new row that
    # the old one points forward to, which is how a contradicted claim stays readable.
    op.execute(_immutability_trigger("evidence", ("superseded_by_id",), "BR-E-06"))
    op.execute(
        """
        CREATE TRIGGER trg_evidence_immutable
        BEFORE UPDATE OR DELETE ON evidence
        FOR EACH ROW EXECUTE FUNCTION evidence_is_immutable();
        """
    )
    # TRUNCATE is a statement, not a row operation, so it needs its own trigger on every one of
    # them — a FOR EACH ROW trigger never fires for it and the table would empty silently.
    for table in ("event", "event_participant", "evidence"):
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {table}_no_truncate() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '{table} is immutable: TRUNCATE is not permitted'
                    USING ERRCODE = 'restrict_violation';
            END
            $$;
            """
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_no_truncate()"
        )

    # ------------------------------------------------------------------ RLS
    tables = ("event", "event_participant", "event_attachment", "evidence")
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (org_id = app_current_org())
            WITH CHECK (org_id = app_current_org());
            """
        )

    # No DELETE for the immutable three: the trigger refuses it anyway, and withholding the grant
    # means the refusal happens before a row is ever examined.
    op.execute("SELECT app_grant('event', 'SELECT, INSERT, UPDATE')")
    op.execute("SELECT app_grant('event_participant', 'SELECT, INSERT, UPDATE')")
    op.execute("SELECT app_grant('event_attachment', 'SELECT, INSERT, UPDATE')")
    op.execute("SELECT app_grant('evidence', 'SELECT, INSERT, UPDATE')")

    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) VALUES "
        "('event', '0009'), ('event_participant', '0009'), "
        "('event_attachment', '0009'), ('evidence', '0009')"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM tenant_scoped_table WHERE table_name IN "
        "('event', 'event_participant', 'event_attachment', 'evidence')"
    )
    for table in ("event", "event_participant", "evidence"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_is_immutable()")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_no_truncate()")
    for table in ("evidence", "event_attachment", "event_participant", "event"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
