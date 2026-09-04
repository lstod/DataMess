-- DocMess: the extracted asset, and the reconciliation of a run against it.
--
-- Two groups of tables, kept apart on purpose.
--
-- The asset -- documents, document_fields, exceptions -- is what a client environment would
-- have. It is the output of the extraction and nothing else.
--
-- The measurement layer -- runs, field_outcomes, case_verdicts -- exists because this corpus
-- has a manifest. **A client will not have one**, so nothing in the asset tables depends on
-- these, and a deployment that drops all three still has a working data asset. That separation
-- is the reason ground truth is not loaded: the declared values live in corpus/manifest.json,
-- there is no table for them to join against, and a schema that assumed one would not port.
--
-- What is loaded is not the truth but the *verdict* -- reconcile.py's per-field outcome, which
-- is a fact about the run rather than about the world. field_outcomes stores that a field was
-- hard without storing what the answer was, and that is still enough to recompute all four
-- metrics in SQL. Which is the point of db/checks/reconciliation.sql.
--
-- Everything here is idempotent. scripts/load.py applies this before every load rather than
-- assuming the container entrypoint did, because on a volume that already existed, it did not.

begin;

-- ---------------------------------------------------------------------------- the asset

create table if not exists documents (
    -- Surrogate, because document_id is legitimately null. An out-of-scope document -- case
    -- 10's lunch receipt -- has no identity in this domain, and inventing one for it would
    -- undo the case: the correct answer is that the thing does not belong, not that it is a
    -- document of unknown type. So identity is the natural key below, and document_id is
    -- nullable data.
    id              bigserial primary key,

    -- What a row is keyed on: the document's identity where it has one, its filename where it
    -- does not, and identity plus filename for case 5's discarded duplicate -- which shares an
    -- identity with the original by definition, and so needs a disambiguator to be storable at
    -- all. See scripts/load.py's key() for why the duplicate is kept rather than dropped.
    document_key    text        not null unique,

    document_id     text,
    document_type   text        not null,
    source_file     text        not null,

    -- [first, last] within source_file. A file is not a document: case 3's invoice and
    -- remittance share a source_file and differ here.
    pages           int[]       not null default '{1,1}',
    page_count      int         generated always as (pages[2] - pages[1] + 1) stored,

    -- Case 4. Deliberately not a foreign key: an amendment can supersede a document the run
    -- failed to find, and a constraint would reject the row recording the more interesting
    -- fact -- turning a finding into a load error.
    supersedes      text,

    -- Case 5. Set on the instance the run discarded. Those rows are loaded but excluded from
    -- every figure, because the entire failure mode is counting them twice.
    duplicate_of    text,

    loaded_at       timestamptz not null default now(),

    constraint identified_unless_out_of_scope
        check (document_id is not null or document_type = 'out_of_scope')
);

create index if not exists documents_type_idx        on documents (document_type);
create index if not exists documents_source_file_idx on documents (source_file);
create index if not exists documents_document_id_idx on documents (document_id);
create index if not exists documents_duplicate_idx   on documents (duplicate_of)
    where duplicate_of is not null;

create table if not exists document_fields (
    document_key    text not null references documents (document_key) on delete cascade,
    field_name      text not null,

    -- Text, not numeric. Money, dates, identifiers and lists all pass through one field table,
    -- and casting on the way in would make the loader decide what a value means. It also has to
    -- be possible to *store* a field that failed to normalise case 6's `1.234,56`, because
    -- otherwise the load rejects it at the door and a data-quality finding arrives disguised as
    -- a loader crash. The cast happens in the money_fields view, where it is visible.
    value           text,
    confidence      text not null,

    -- Where on the page it was read, as prose: 'page 2, totals block, bottom right'. A string
    -- and not a page number plus a quote, because the QA sheet exists so a human can go and
    -- look, and a locator they can act on beats a structure that is easier to query.
    evidence        text,
    reason          text,

    primary key (document_key, field_name),

    constraint confidence_is_known
        check (confidence in ('high', 'low', 'unreadable')),

    -- The confidence contract, restated in the database. Not redundant with the JSON schema:
    -- that one governs what an agent may hand over, this governs what may be *stored*, and a
    -- loader with a bug is exactly what the second one catches. The three branches are the
    -- three in schema/extraction.schema.json, and they have to stay in step.
    --
    --   high         a value and a locator, and no reason -- a high carrying an explanation
    --                is a low that did not admit it
    --   low          a value, a locator, and the inference the reading depends on
    --   unreadable   no value, a reason, and no locator, because nothing was read
    --
    -- The three are mutually exclusive by construction, so confidence is recoverable from the
    -- shape alone. A row whose stated confidence disagrees with its own shape is rejected here
    -- rather than interpreted.
    constraint confidence_shape_matches
        check (
            (confidence = 'high'
                and value is not null and evidence is not null and reason is null)
         or (confidence = 'low'
                and value is not null and evidence is not null and reason is not null)
         or (confidence = 'unreadable'
                and value is null and reason is not null and evidence is null)
        )
);

create index if not exists document_fields_name_idx on document_fields (field_name);
create index if not exists document_fields_flagged_idx on document_fields (confidence)
    where confidence <> 'high';

create table if not exists exceptions (
    id              bigserial primary key,
    document_key    text references documents (document_key) on delete cascade,
    document_id     text,
    document_type   text,
    kind            text not null,
    detail          text,
    source_file     text,
    -- Natural key for the upsert. document_key can be null in principle, so coalesce keeps the
    -- fingerprint total rather than letting two nulls both pass a unique index.
    fingerprint     text generated always as
                        (coalesce(document_key, source_file, '') || '|' || kind) stored,
    loaded_at       timestamptz not null default now()
);

create unique index if not exists exceptions_fingerprint_key on exceptions (fingerprint);
create index if not exists exceptions_kind_idx on exceptions (kind);

-- ------------------------------------------------------------------- the measurement layer

create table if not exists runs (
    run             text primary key,
    kind            text,
    seed            int,
    period          text,
    source          text,
    scored_at       timestamptz,

    -- The four figures as reconcile.py computed them. Stored so the SQL can be compared against
    -- them -- never so the SQL can read them. reconciliation.sql recomputes all four from
    -- field_outcomes and check_load.py asserts the two agree to four decimal places. Two
    -- implementations agreeing is only worth anything if the second cannot see the first's
    -- answer, so no query in db/checks/ selects these columns except to diff them.
    field_accuracy  numeric(6, 4),
    coverage        numeric(6, 4),
    flag_precision  numeric(6, 4),
    flag_recall     numeric(6, 4),

    loaded_at       timestamptz not null default now()
);

create table if not exists field_outcomes (
    run                  text not null references runs (run) on delete cascade,
    document_id          text not null,
    field_name           text not null,
    outcome              text not null,

    -- What the corpus expected of this field, and what the run actually said. The verdict, not
    -- the value: no declared figure is stored, so this table can say a field was hard without
    -- disclosing the answer -- which is what lets the metrics be recomputed here while ground
    -- truth stays out of the database.
    expected_confidence  text not null,
    produced_confidence  text,

    primary key (run, document_id, field_name),

    constraint outcome_is_known
        check (outcome in ('correct', 'correctly_flagged', 'wrong', 'declined',
                           'hallucinated', 'missed', 'over_flagged')),
    constraint expected_confidence_is_known
        check (expected_confidence in ('high', 'low', 'unreadable', 'absent'))
);

-- Re-applied every load, because `create table if not exists` cannot evolve a constraint on a
-- table that already exists. The vocabulary of outcome classes grew once -- `declined` was added
-- after a run named an undeclared field and gave it no value -- and the inline constraint above
-- silently did not apply to the running database, so the load failed against a volume that had
-- been created a day earlier while passing on a fresh one. Dropping and re-adding is idempotent,
-- runs in milliseconds, and means the constraint in this file is the constraint in the database.
alter table field_outcomes drop constraint if exists outcome_is_known;
alter table field_outcomes add  constraint outcome_is_known
    check (outcome in ('correct', 'correctly_flagged', 'wrong', 'declined',
                       'hallucinated', 'missed', 'over_flagged'));

alter table field_outcomes drop constraint if exists expected_confidence_is_known;
alter table field_outcomes add  constraint expected_confidence_is_known
    check (expected_confidence in ('high', 'low', 'unreadable', 'absent'));

create index if not exists field_outcomes_outcome_idx   on field_outcomes (run, outcome);
create index if not exists field_outcomes_expected_idx  on field_outcomes (run, expected_confidence);

create table if not exists case_verdicts (
    run             text not null references runs (run) on delete cascade,
    mess_case       int  not null,
    verdict         text not null,
    detail          text,
    primary key (run, mess_case),
    constraint mess_case_in_range check (mess_case between 1 and 10)
);

-- --------------------------------------------------------------------------------- views

-- The rows every figure is computed over, in one place so no query has to remember the rule.
--
-- Case 5's discarded instance is excluded here rather than in each query. A duplicate invoice
-- left in scope inflates the total by its value and nothing about the resulting number looks
-- wrong, so the exclusion goes somewhere a reader cannot miss it.
create or replace view live_documents as
    select * from documents where duplicate_of is null;

-- Money as numeric, cast in exactly one place, and null where the text is not a plain number.
--
-- The regex matters: a value that failed to normalise case 6's European format arrives here as
-- `1.234,56` and comes out as null rather than as 1.234 silently truncated. A cast that
-- succeeded on the wrong reading would be worse than one that failed.
create or replace view money_fields as
    select
        d.document_key,
        d.document_id,
        d.document_type,
        f.field_name,
        f.value,
        f.confidence,
        case when f.value ~ '^-?[0-9]+(\.[0-9]{1,2})?$' then f.value::numeric(14, 2) end as amount
    from document_fields f
    join live_documents d using (document_key)
    where f.field_name in ('total_due', 'subtotal', 'tax_amount', 'net_amount',
                           'retainer_credit', 'ceiling_amount', 'revised_ceiling_amount',
                           'amount_paid', 'total_outstanding');

-- The invoice register, which is what a finance team would actually look at.
--
-- `total_due is null` is the interesting row, not an error: it is case 7, where the figure is
-- below the scanner margin and was correctly refused. left join, so that invoice appears with
-- a null total rather than vanishing from the register -- a pipeline that dropped it would
-- produce a smaller, tidier, wrong answer.
create or replace view invoice_register as
    select
        d.document_id                                  as invoice_number,
        d.source_file,
        max(f.value) filter (where f.field_name = 'client_name')    as client_name,
        max(f.value) filter (where f.field_name = 'issue_date')     as issue_date,
        max(f.value) filter (where f.field_name = 'due_date')       as due_date,
        max(m.amount) filter (where m.field_name = 'total_due')     as total_due,
        max(f.confidence) filter (where f.field_name = 'total_due') as total_due_confidence,
        max(f.reason) filter (where f.field_name = 'total_due')     as total_due_reason
    from live_documents d
    left join document_fields f using (document_key)
    left join money_fields m on m.document_key = d.document_key and m.field_name = f.field_name
    where d.document_type = 'invoice'
    group by d.document_id, d.source_file;

commit;
