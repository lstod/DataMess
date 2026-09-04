-- The step 8 figures, recomputed in SQL.
--
--     docker exec -i docmess-postgres psql -U docmess -d docmess -f - < db/checks/reconciliation.sql
--     psql postgresql://docmess:docmess@localhost:5434/docmess -f db/checks/reconciliation.sql
--
-- Two implementations agreeing is worth more than one, and that is only true if the second
-- cannot see the first one's answer. So every figure below is built from field_outcomes --
-- one row per field, carrying what the corpus expected and what the run produced -- and the
-- four numbers reconcile.py already computed are read exactly once, in the last query, to be
-- diffed. No query derives anything from them.
--
-- What this is really checking is the denominators. Numerators are easy and all four of these
-- share the same six outcome classes; the places a metric goes quietly wrong are which rows
-- belong in the bottom half, and whether a metric with nothing to divide by reports null or
-- zero. Both are restated here in SQL rather than imported, so the two can disagree.
--
-- Ground truth is not in this database. field_outcomes says a field was hard without saying
-- what the answer was, which is enough for all four metrics and keeps the schema portable to
-- an environment that has no manifest.

\pset null '(null)'
\timing off

\echo
\echo ==============================================================================
\echo  1. The four metrics, recomputed from field_outcomes
\echo ==============================================================================

with counted as (
    select
        run,
        count(*) filter (where outcome = 'correct')            as correct,
        count(*) filter (where outcome = 'correctly_flagged')  as correctly_flagged,
        count(*) filter (where outcome = 'wrong')              as wrong,
        count(*) filter (where outcome = 'hallucinated')       as hallucinated,
        count(*) filter (where outcome = 'missed')             as missed,
        count(*) filter (where outcome = 'over_flagged')       as over_flagged,
        count(*) filter (where outcome = 'declined')           as declined,
        -- The corpus declared these. An invented field is marked 'absent' and is deliberately
        -- outside the denominator: a run cannot improve its coverage by producing fields
        -- nobody asked for.
        count(*) filter (where expected_confidence <> 'absent') as declared,
        -- Genuinely hard, per the corpus rather than per the run. This is the denominator that
        -- makes flag recall mean something: recall against the run's own idea of what was hard
        -- would be 100% by construction.
        count(*) filter (where expected_confidence in ('low', 'unreadable')) as genuinely_hard,
        -- Raised its hand at all, in either flavour. Recall asks whether the run warned a
        -- reviewer about a hard field; precision asks whether it was right to. A run that
        -- reported a derived total at 'low' rather than declining it outright still sends
        -- somebody to the same cell, and counting only 'correctly_flagged' recorded that as a
        -- silence which did not happen. The over-confidence is still punished, in accuracy,
        -- where the assertion actually is.
        count(*) filter (where expected_confidence in ('low', 'unreadable')
                           and produced_confidence in ('low', 'unreadable')) as hard_flagged
    from field_outcomes
    group by run
)
select
    run,
    -- nullif on every denominator, not coalesce to zero. A run that flagged nothing has no
    -- flag precision -- not a precision of 0% -- and reporting zero would rank it below a run
    -- that flagged badly while putting a number on the page no observation supports.
    round(correct::numeric           / nullif(correct + wrong + hallucinated, 0), 4) as field_accuracy,
    round((declared - missed)::numeric / nullif(declared, 0), 4)                     as coverage,
    round(correctly_flagged::numeric / nullif(correctly_flagged + over_flagged + declined, 0), 4) as flag_precision,
    round(hard_flagged::numeric / nullif(genuinely_hard, 0), 4)                 as flag_recall
from counted
order by run;

\echo
\echo ==============================================================================
\echo  2. The same four, as the scorecard prints them
\echo ==============================================================================

with counted as (
    select
        run,
        count(*) filter (where outcome = 'correct')            as correct,
        count(*) filter (where outcome = 'correctly_flagged')  as correctly_flagged,
        count(*) filter (where outcome = 'wrong')              as wrong,
        count(*) filter (where outcome = 'hallucinated')       as hallucinated,
        count(*) filter (where outcome = 'missed')             as missed,
        count(*) filter (where outcome = 'over_flagged')       as over_flagged,
        count(*) filter (where outcome = 'declined')           as declined,
        count(*) filter (where expected_confidence <> 'absent') as declared,
        count(*) filter (where expected_confidence in ('low', 'unreadable')) as genuinely_hard,
        -- Raised its hand at all, in either flavour. Recall asks whether the run warned a
        -- reviewer about a hard field; precision asks whether it was right to. A run that
        -- reported a derived total at 'low' rather than declining it outright still sends
        -- somebody to the same cell, and counting only 'correctly_flagged' recorded that as a
        -- silence which did not happen. The over-confidence is still punished, in accuracy,
        -- where the assertion actually is.
        count(*) filter (where expected_confidence in ('low', 'unreadable')
                           and produced_confidence in ('low', 'unreadable')) as hard_flagged
    from field_outcomes
    group by run
)
select
    run,
    to_char(100.0 * correct           / nullif(correct + wrong + hallucinated, 0), '990.9%') as accuracy,
    to_char(100.0 * (declared - missed) / nullif(declared, 0), '990.9%')                     as coverage,
    to_char(100.0 * correctly_flagged / nullif(correctly_flagged + over_flagged + declined, 0), '990.9%') as precision,
    to_char(100.0 * hard_flagged / nullif(genuinely_hard, 0), '990.9%')                 as recall
from counted
order by run;

\echo
\echo ==============================================================================
\echo  3. The six outcome classes
\echo ==============================================================================

select
    run,
    count(*) filter (where outcome = 'correct')           as correct,
    count(*) filter (where outcome = 'correctly_flagged') as flagged_ok,
    count(*) filter (where outcome = 'wrong')             as wrong,
    count(*) filter (where outcome = 'hallucinated')      as hallucinated,
    count(*) filter (where outcome = 'missed')            as missed,
    count(*) filter (where outcome = 'over_flagged')      as over_flagged,
    count(*)                                              as total_fields
from field_outcomes
group by run
order by run;

\echo
\echo ==============================================================================
\echo  4. Per mess case
\echo ==============================================================================

select
    mess_case,
    max(verdict) filter (where run = 'reference-perfect') as perfect,
    max(verdict) filter (where run = 'reference-flawed')  as flawed
from case_verdicts
group by mess_case
order by mess_case;

\echo
\echo ==============================================================================
\echo  5. The asset, independently of any run
\echo ==============================================================================
-- psql treats an apostrophe inside \echo as an unterminated quoted string, so the prose in
-- these banners is written without them.
\echo -- The Summary figures from the workbook, recomputed from the loaded rows. total_invoiced
\echo -- is 34,906.40 below the manifest total, and that is correct: case 7 has its total below
\echo -- the scanner margin, so the field is null rather than a guess.

select
    (select count(*) from live_documents)                                   as documents,
    (select count(*) from documents where duplicate_of is not null)         as duplicates_discarded,
    (select count(*) from live_documents where document_type = 'invoice')   as invoices,
    (select sum(amount) from money_fields where field_name = 'total_due')   as total_invoiced,
    (select count(*) from invoice_register where total_due is null)         as invoices_missing_a_total,
    (select count(*) from document_fields f join live_documents d using (document_key)
      where f.confidence <> 'high')                                         as fields_flagged,
    (select count(*) from document_fields f join live_documents d using (document_key)
      where f.confidence = 'unreadable')                                    as fields_unreadable,
    (select count(*) from exceptions)                                       as exceptions;

\echo
\echo ==============================================================================
\echo  6. The exception worth looking at
\echo ==============================================================================

select
    invoice_number,
    source_file,
    total_due,
    total_due_confidence as confidence,
    left(total_due_reason, 68) as reason
from invoice_register
where total_due is null or total_due_confidence <> 'high'
order by invoice_number;

\echo
\echo ==============================================================================
\echo  7. THE GATE -- does the SQL agree with reconcile.py, to four decimal places
\echo ==============================================================================
\echo -- The only place the stored metrics are read. Any row reading DISAGREES means the two
\echo -- implementations differ and the scorecard is not evidence until it is resolved.

with counted as (
    select
        run,
        count(*) filter (where outcome = 'correct')            as correct,
        count(*) filter (where outcome = 'correctly_flagged')  as correctly_flagged,
        count(*) filter (where outcome = 'wrong')              as wrong,
        count(*) filter (where outcome = 'hallucinated')       as hallucinated,
        count(*) filter (where outcome = 'missed')             as missed,
        count(*) filter (where outcome = 'over_flagged')       as over_flagged,
        count(*) filter (where outcome = 'declined')           as declined,
        count(*) filter (where expected_confidence <> 'absent') as declared,
        count(*) filter (where expected_confidence in ('low', 'unreadable')) as genuinely_hard,
        -- Raised its hand at all, in either flavour. Recall asks whether the run warned a
        -- reviewer about a hard field; precision asks whether it was right to. A run that
        -- reported a derived total at 'low' rather than declining it outright still sends
        -- somebody to the same cell, and counting only 'correctly_flagged' recorded that as a
        -- silence which did not happen. The over-confidence is still punished, in accuracy,
        -- where the assertion actually is.
        count(*) filter (where expected_confidence in ('low', 'unreadable')
                           and produced_confidence in ('low', 'unreadable')) as hard_flagged
    from field_outcomes
    group by run
),
recomputed as (
    select
        run,
        round(correct::numeric           / nullif(correct + wrong + hallucinated, 0), 4) as field_accuracy,
        round((declared - missed)::numeric / nullif(declared, 0), 4)                     as coverage,
        round(correctly_flagged::numeric / nullif(correctly_flagged + over_flagged + declined, 0), 4) as flag_precision,
        round(hard_flagged::numeric / nullif(genuinely_hard, 0), 4)                 as flag_recall
    from counted
),
compared as (
    select
        r.run,
        m.name,
        m.sql_value,
        m.py_value
    from recomputed r
    join runs s on s.run = r.run
    cross join lateral (values
        ('field_accuracy', r.field_accuracy, s.field_accuracy),
        ('coverage',       r.coverage,       s.coverage),
        ('flag_precision', r.flag_precision, s.flag_precision),
        ('flag_recall',    r.flag_recall,    s.flag_recall)
    ) as m(name, sql_value, py_value)
)
select
    run,
    name as metric,
    sql_value as from_sql,
    py_value  as from_reconcile_py,
    case
        -- `is not distinct from` and not `=`, so that null equals null. A metric with no
        -- denominator is null in both implementations and that is agreement, not a gap; `=`
        -- would return null there and the row would read neither agree nor disagree.
        when sql_value is not distinct from py_value then 'agree'
        else 'DISAGREES'
    end as verdict
from compared
order by run, name;

\echo
\echo -- One row. n_disagreements must be 0.

with counted as (
    select
        run,
        count(*) filter (where outcome = 'correct')            as correct,
        count(*) filter (where outcome = 'correctly_flagged')  as correctly_flagged,
        count(*) filter (where outcome = 'wrong')              as wrong,
        count(*) filter (where outcome = 'hallucinated')       as hallucinated,
        count(*) filter (where outcome = 'missed')             as missed,
        count(*) filter (where outcome = 'over_flagged')       as over_flagged,
        count(*) filter (where outcome = 'declined')           as declined,
        count(*) filter (where expected_confidence <> 'absent') as declared,
        count(*) filter (where expected_confidence in ('low', 'unreadable')) as genuinely_hard,
        -- Raised its hand at all, in either flavour. Recall asks whether the run warned a
        -- reviewer about a hard field; precision asks whether it was right to. A run that
        -- reported a derived total at 'low' rather than declining it outright still sends
        -- somebody to the same cell, and counting only 'correctly_flagged' recorded that as a
        -- silence which did not happen. The over-confidence is still punished, in accuracy,
        -- where the assertion actually is.
        count(*) filter (where expected_confidence in ('low', 'unreadable')
                           and produced_confidence in ('low', 'unreadable')) as hard_flagged
    from field_outcomes
    group by run
),
recomputed as (
    select
        run,
        round(correct::numeric           / nullif(correct + wrong + hallucinated, 0), 4) as field_accuracy,
        round((declared - missed)::numeric / nullif(declared, 0), 4)                     as coverage,
        round(correctly_flagged::numeric / nullif(correctly_flagged + over_flagged + declined, 0), 4) as flag_precision,
        round(hard_flagged::numeric / nullif(genuinely_hard, 0), 4)                 as flag_recall
    from counted
)
select
    count(*) filter (
        where r.field_accuracy is distinct from s.field_accuracy
           or r.coverage       is distinct from s.coverage
           or r.flag_precision is distinct from s.flag_precision
           or r.flag_recall    is distinct from s.flag_recall
    ) as n_disagreements,
    count(*) as runs_compared
from recomputed r
join runs s on s.run = r.run;

\echo
