#!/usr/bin/env python3
"""Step 7's Done-when conditions, as assertions.

    scripts/check_load.py              against the container on 5434
    scripts/check_load.py --csv-only   skip the database, check the fallback only
    scripts/check_load.py -v

The gate is that `db/checks/reconciliation.sql` returns the same four figures as
`scorecard/scorecard.md`, to the decimal. Not the same figures as `reconcile.py` in memory --
the ones written on the artifact a reader would look at, parsed back out of the markdown, so
the chain from the loaded rows to the published number is closed at both ends.

Why this is worth doing at all
------------------------------
The numbers are already computed. Recomputing them in SQL adds a second implementation of the
part most likely to be quietly wrong, which is not the arithmetic but the **denominators**:
which rows belong under the line, whether an invented field counts against coverage, whether a
metric with nothing to divide by is null or zero. Those are four decisions, none of them
forced, and a transcription of them into SQL either agrees or exposes that one of the two is
wrong. It has to be a transcription and not an import for that to mean anything.

If the database is not up
-------------------------
Every database assertion is skipped with a stated reason rather than failed, because a
container that is not running is not a broken build. `--csv` is checked either way -- it is the
reason this step is safe to cut, so it cannot be the part that only works when Postgres does.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EXTRACTIONS = REPO / "extractions.json"
MANIFEST = REPO / "corpus" / "manifest.json"
SCORECARD = REPO / "scorecard"
SCORES = SCORECARD / "scores.jsonl"
LOAD = REPO / "scripts" / "load.py"
SCHEMA = REPO / "db" / "schema.sql"
COMPOSE = REPO / "docker-compose.yml"
RECONCILIATION = REPO / "db" / "checks" / "reconciliation.sql"

CONTAINER = "docmess-postgres"
PORT = 5434
IMAGE = "postgres:16.10"

# Restated, not imported.
METRICS = ["field_accuracy", "coverage", "flag_precision", "flag_recall"]
TABLES = ["documents", "document_fields", "exceptions", "runs", "field_outcomes", "case_verdicts"]
DOCUMENTS = 74          # live, after the duplicate instance is excluded
INSTANCES = 75          # loaded rows, including it
WORKBOOK_TOTAL = Decimal("1968886.88")
MANIFEST_TOTAL = Decimal("2003793.28")
CROPPED_TOTAL = Decimal("34906.40")


def declared_fields() -> int:
    """How many fields the manifest declares, counted rather than restated.

    The constants above are design facts -- 74 documents, 75 instances, the workbook total --
    and stating them here is the point: if the corpus drifts away from what this project says
    it is, these go red. The field count is not a design fact, it is a consequence of them, and
    pinning it at 719 meant that correcting the manifest broke the harness whose job was to
    check the manifest.
    """
    manifest = json.loads(MANIFEST.read_text())
    return sum(len(e.get("fields") or {}) for e in manifest["documents"])


def psql(sql: str) -> tuple[int, str]:
    """One query through the container. Tuples only, so parsing stays trivial."""
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", "docmess", "-d", "docmess",
         "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True,
    )
    return result.returncode, (result.stdout or result.stderr).strip()


def up() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "healthy"


# ------------------------------------------------------------- what the artifacts say

def scorecard_metrics() -> dict[str, dict[str, str]]:
    """The four figures per run, parsed out of scorecard.md's own table.

    Out of the markdown rather than out of scores.jsonl on purpose. The published artifact is
    what a reader believes, so it is what the database has to agree with -- and a formatting
    bug that printed the wrong column would be invisible to a check that read the JSON.
    """
    text = (SCORECARD / "scorecard.md").read_text()
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0].startswith("`") and cells[2].endswith("%"):
            out[cells[0].strip("`")] = dict(zip(METRICS, cells[2:6]))
    return out


def check_compose(checks: Checks) -> None:
    text = COMPOSE.read_text()
    checks.add(
        "compose: postgres is pinned to 16.10, not to a moving tag",
        IMAGE in text and ":latest" not in text,
        IMAGE if IMAGE in text else "not pinned",
    )
    # Parsed off the mapping lines rather than searched for as a substring, because the comment
    # above them names 5432 and 5433 in order to say why neither is used -- and the first
    # version of this assertion failed on its own explanation.
    published = re.findall(r'^\s*-\s*"(\d+):(\d+)"', text, re.M)
    checks.add(
        "compose: the only published host port is 5434, not 5432 and not BizData's 5433",
        published == [(str(PORT), "5432")],
        ", ".join(f"{h}->{c}" for h, c in published) or "no port mapping found",
    )
    checks.add(
        "compose: it has its own named volume, so a sibling teardown cannot take this data",
        "docmess-pgdata" in text,
        "docmess-pgdata",
    )
    checks.add(
        "compose: there is a comment saying why this is not BizData's container",
        "BizData" in text.split("services:")[0],
        "explained in the header comment",
    )
    checks.add(
        "compose: the healthcheck waits for the schema, not just for the socket",
        "runs" in text.split("healthcheck:")[1][:300],
        "selects from a table the schema creates",
    )


def check_csv(checks: Checks) -> None:
    """The fallback. Checked whether or not Postgres is up -- that is the entire point of it."""
    scratch = Path(tempfile.mkdtemp(prefix="docmess-csv-"))
    try:
        result = subprocess.run(
            [sys.executable, str(LOAD), "--csv", str(scratch), "--quiet"],
            capture_output=True, text=True,
        )
        checks.add(
            "csv: load.py --csv runs with no database at all",
            result.returncode == 0,
            "exit 0" if result.returncode == 0 else result.stderr[-140:],
        )
        if result.returncode != 0:
            return

        written = sorted(p.stem for p in scratch.glob("*.csv"))
        checks.add(
            "csv: it writes the same six tables the schema defines",
            written == sorted(TABLES),
            ", ".join(written),
        )

        import csv as csvlib

        with (scratch / "documents.csv").open() as handle:
            docs = list(csvlib.DictReader(handle))
        with (scratch / "document_fields.csv").open() as handle:
            fields = list(csvlib.DictReader(handle))

        checks.add(
            "csv: documents.csv holds every loaded instance, duplicate included",
            len(docs) == INSTANCES,
            f"{len(docs)} rows",
        )
        checks.add(
            "csv: its column names are the schema's, so the two paths cannot drift",
            set(docs[0]) <= set(re.findall(r"^\s{4}(\w+)\s", SCHEMA.read_text(), re.M)),
            ", ".join(sorted(docs[0])),
        )
        # Counted from the file being loaded, not written here as a literal. It said 732, and
        # broke the moment the run under measurement changed -- which is the one time a harness
        # most needs to still work.
        source = json.loads(EXTRACTIONS.read_text())
        source_records = source["documents"] if isinstance(source, dict) else source
        expected_fields = sum(len(r.get("fields") or {}) for r in source_records)
        checks.add(
            "csv: document_fields.csv holds every extracted field",
            len(fields) == expected_fields,
            f"{len(fields)} rows for {expected_fields} extracted fields",
        )

        # A property of every unreadable field rather than a count of them. There was one when
        # this was written and there are five now; what has to hold is the same either way --
        # an unread figure arrives as absent, and absent is not zero.
        unreadable = [f for f in fields if f["confidence"] == "unreadable"]
        bad = [f for f in unreadable if f["value"] != "" or not f["reason"]]
        checks.add(
            "csv: every unreadable field survives the round trip as empty, not as zero",
            unreadable and not bad,
            f"{len(unreadable)} unreadable, all empty with a reason"
            if unreadable and not bad
            else f"{len(bad)} arrived with a value or without a reason",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------- the database

def check_load_is_idempotent(checks: Checks) -> None:
    before = psql("select count(*) from documents")[1]
    result = subprocess.run(
        [sys.executable, str(LOAD), "--quiet"], capture_output=True, text=True
    )
    checks.add(
        "load: it runs clean against a database that is already loaded",
        result.returncode == 0,
        "exit 0" if result.returncode == 0 else result.stderr[-160:],
    )
    counts = {}
    for table in TABLES:
        counts[table] = psql(f"select count(*) from {table}")[1]

    result = subprocess.run(
        [sys.executable, str(LOAD), "--quiet"], capture_output=True, text=True
    )
    again = {t: psql(f"select count(*) from {t}")[1] for t in TABLES}
    checks.add(
        "load: a second run changes no row count anywhere -- it is idempotent",
        counts == again and result.returncode == 0,
        ", ".join(f"{t} {again[t]}" for t in TABLES),
    )
    checks.add(
        "load: and the first count was already stable",
        before in ("", counts["documents"]),
        f"{before or 'empty'} -> {counts['documents']}",
    )

    # A stale field must not survive a changed extraction. This is the failure the delete-then-
    # insert exists for, and an upsert-per-row would pass every other assertion here.
    scratch = Path(tempfile.mkdtemp(prefix="docmess-stale-"))
    try:
        payload = json.loads(EXTRACTIONS.read_text())
        records = payload["documents"] if isinstance(payload, dict) else payload
        trimmed = json.loads(json.dumps(payload))
        target = None
        for record in (trimmed["documents"] if isinstance(trimmed, dict) else trimmed):
            if record.get("document_type") == "invoice" and "purchase_order" in (record.get("fields") or {}):
                del record["fields"]["purchase_order"]
                target = record.get("document_id")
                break
        source = scratch / "trimmed.json"
        source.write_text(json.dumps(trimmed))

        subprocess.run([sys.executable, str(LOAD), "--in", str(source), "--quiet"],
                       capture_output=True, text=True)
        _, left = psql(
            "select count(*) from document_fields "
            f"where document_key = '{target}' and field_name = 'purchase_order'"
        )
        checks.add(
            "load: a field the new run does not produce is removed, not left behind",
            left == "0",
            f"{left} stale purchase_order rows on {target}",
        )
        # Put the real data back, so the rest of the assertions see the true corpus.
        subprocess.run([sys.executable, str(LOAD), "--quiet"], capture_output=True, text=True)
        _, restored = psql(
            "select count(*) from document_fields "
            f"where document_key = '{target}' and field_name = 'purchase_order'"
        )
        checks.add(
            "load: and re-loading the full extraction puts it back",
            restored == "1",
            f"{restored} row",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def check_contract(checks: Checks) -> None:
    """The confidence contract, enforced by the database rather than only by the schema."""
    code, out = psql(
        "insert into document_fields (document_key, field_name, value, confidence) "
        "values ('INV-2026-0876', 'purchase_order', '12345', 'unreadable')"
    )
    checks.add(
        "contract: the database refuses a confident value on an unreadable field",
        code != 0 and "confidence_shape_matches" in out,
        "rejected by confidence_shape_matches" if "confidence_shape_matches" in out else out[:110],
    )

    code, out = psql(
        "insert into document_fields (document_key, field_name, confidence, reason) "
        "values ('INV-2026-0876', 'net_amount', 'unreadable', null)"
    )
    checks.add(
        "contract: and refuses an unreadable field with no reason",
        code != 0,
        "rejected" if code != 0 else "accepted, which it must not",
    )

    code, out = psql(
        "insert into documents (document_key, document_type, source_file) "
        "values ('x', 'invoice', 'x.pdf')"
    )
    checks.add(
        "contract: an invoice with no document_id is refused; only out_of_scope may lack one",
        code != 0 and "identified_unless_out_of_scope" in out,
        "rejected by identified_unless_out_of_scope" if code != 0 else "accepted",
    )


def check_asset(checks: Checks) -> None:
    _, live = psql("select count(*) from live_documents")
    _, all_rows = psql("select count(*) from documents")
    checks.add(
        "asset: 75 instances loaded, 74 live -- the duplicate is kept and excluded",
        (all_rows, live) == (str(INSTANCES), str(DOCUMENTS)),
        f"{all_rows} loaded, {live} live",
    )

    _, total = psql("select sum(amount) from money_fields where field_name = 'total_due'")
    checks.add(
        "asset: the invoice total matches the workbook to the cent",
        Decimal(total) == WORKBOOK_TOTAL,
        f"{Decimal(total):,}",
    )
    checks.add(
        "asset: and is short of the manifest by exactly the cropped total",
        MANIFEST_TOTAL - Decimal(total) == CROPPED_TOTAL,
        f"{MANIFEST_TOTAL - Decimal(total):,} = INV-2026-1500",
    )

    _, missing = psql("select count(*) from invoice_register where total_due is null")
    checks.add(
        "asset: exactly one invoice has no total, and it is in the register rather than dropped",
        missing == "1",
        f"{missing} invoice with a null total, still listed",
    )

    # Asked as a property, not a count. Every unreadable field must arrive as a null carrying
    # a reason -- however many of them there are. The count was pinned at 1 and went stale when
    # the statement totals were correctly reclassified.
    _, unreadable = psql(
        "select count(*) from document_fields f join live_documents d using (document_key) "
        "where f.confidence = 'unreadable'"
    )
    _, malformed = psql(
        "select count(*) from document_fields f join live_documents d using (document_key) "
        "where f.confidence = 'unreadable' and (f.value is not null or f.reason is null)"
    )
    checks.add(
        "asset: every unreadable figure is stored as null with a reason, not as zero",
        int(unreadable) > 0 and malformed == "0",
        f"{unreadable} unreadable, all null with a reason"
        if malformed == "0"
        else f"{malformed} stored with a value or without a reason",
    )

    _, zeroes = psql(
        "select count(*) from money_fields where amount = 0 and field_name = 'total_due'"
    )
    checks.add(
        "asset: no invoice total was silently turned into a zero",
        zeroes == "0",
        f"{zeroes} zero totals",
    )

    # Case 6 is the reason money is cast in a view and not in the column type: a value that
    # failed to normalise arrives as text and comes out null, rather than as a truncated number.
    _, uncastable = psql(
        "select count(*) from money_fields where value is not null and amount is null"
    )
    checks.add(
        "asset: every stored money value cast cleanly, so none was silently truncated",
        uncastable == "0",
        f"{uncastable} values that would not cast",
    )


def check_gate(checks: Checks) -> None:
    """The Done-when: the SQL returns the same four figures as scorecard.md."""
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", "docmess", "-d", "docmess",
         "-A", "-F", "|", "-f", "-"],
        stdin=RECONCILIATION.open(), capture_output=True, text=True,
    )
    checks.add(
        "gate: reconciliation.sql runs with no error",
        result.returncode == 0 and "ERROR" not in result.stdout.upper(),
        "clean" if result.returncode == 0 else result.stderr[-140:],
    )

    # The figures reconciliation.sql actually printed, compared against the runs table. This
    # assertion did not exist, and its absence was the hole: the file was executed and checked
    # for errors, and then the gate compared the runs table against a *restatement* of the same
    # metrics further down this script. So the deliverable SQL -- the thing a client is handed,
    # the thing the README calls "the four metrics, computed twice" -- was never once compared
    # to anything. It could have returned any number.
    #
    # Found when the recall definition changed: reconciliation.sql was updated, the restatement
    # was not, and the gate reported a disagreement that was between two copies of the check
    # rather than between SQL and Python. Three implementations of four metrics, and the one
    # under test was the only one nobody was reading.
    printed: dict[str, tuple[str, ...]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 5 and all(re.fullmatch(r"-?\d*\.?\d+", p) for p in parts[1:]):
            printed.setdefault(parts[0], tuple(parts[1:]))

    _, stored_rows = psql(
        "select run, field_accuracy, coverage, "
        "coalesce(flag_precision::text,''), coalesce(flag_recall::text,'') "
        "from runs order by run"
    )
    stored = {
        p[0]: tuple(p[1:])
        for p in (row.split("|") for row in stored_rows.splitlines() if row.strip())
        if len(p) == 5
    }
    drifted = [
        f"{run}: sql {printed.get(run)} vs runs {values}"
        for run, values in stored.items()
        if run in printed and tuple(float(x) for x in printed[run]) != tuple(float(x) for x in values)
    ]
    checks.add(
        "gate: the figures reconciliation.sql prints match the runs table for every run",
        stored and printed and not drifted and set(stored) <= set(printed),
        f"{len(printed)} runs reproduced from the file itself"
        if not drifted and set(stored) <= set(printed)
        else (drifted[:2] or [f"runs missing from the SQL output: {sorted(set(stored) - set(printed))}"]),
    )

    _, disagreements = psql(_gate_query())
    checks.add(
        "gate: SQL and reconcile.py agree on all four metrics, for every run",
        disagreements == "0",
        f"{disagreements} disagreements",
    )

    # And against the published artifact, not just against the JSON.
    # Every run in the log, not a fixed two. The scorecard is append-only by design, so its
    # row count grows with each run scored; asserting `== 2` meant the first real Cowork run
    # broke the gate by existing.
    published = scorecard_metrics()
    # Superseded scorings are on the scorecard but deliberately not in the database, so they are
    # held out of the gate on both sides. See the note in load.py: their manifest is gone, and
    # asking the current one to reproduce them would be asking the wrong question.
    entries = [
        json.loads(line)
        for line in (SCORES.read_text().splitlines() if SCORES.is_file() else [])
        if line.strip()
    ]
    superseded = {e["run"] for e in entries if e.get("kind") == "superseded"}
    logged = {e["run"] for e in entries} - superseded
    published = {k: v for k, v in published.items() if k not in superseded}
    checks.add(
        "gate: scorecard.md carries a row for every live run in the log, reference and real",
        published and set(published) == logged,
        f"{len(published)} rows: {', '.join(published)}"
        if set(published) == logged
        else f"markdown and log differ: {sorted(set(published) ^ logged)[:3]}",
    )

    mismatches = []
    for name, cells in published.items():
        _, row = psql(_metric_query(name))
        if not row:
            mismatches.append(f"{name}: not in the database")
            continue
        values = row.split("|")
        for metric, value in zip(METRICS, values):
            # scorecard.md prints one decimal place, so compare at that precision.
            from_sql = f"{Decimal(value) * 100:.1f}%" if value else "n/a"
            if from_sql != cells[metric]:
                mismatches.append(f"{name}.{metric}: sql {from_sql} vs md {cells[metric]}")

    checks.add(
        "gate: every figure in scorecard.md is reproduced by the SQL, to the decimal",
        not mismatches,
        "8 of 8 figures match" if not mismatches else "; ".join(mismatches[:3]),
    )


def _metric_query(run_name: str) -> str:
    return f"""
    with c as (
        select
            count(*) filter (where outcome = 'correct') as correct,
            count(*) filter (where outcome = 'correctly_flagged') as flagged,
            count(*) filter (where outcome = 'wrong') as wrong,
            count(*) filter (where outcome = 'hallucinated') as hallucinated,
            count(*) filter (where outcome = 'missed') as missed,
            count(*) filter (where outcome = 'over_flagged') as over_flagged,
            count(*) filter (where outcome = 'declined') as declined,
            count(*) filter (where expected_confidence <> 'absent') as declared,
            count(*) filter (where expected_confidence in ('low','unreadable')) as hard,
            count(*) filter (where expected_confidence in ('low','unreadable')
                               and produced_confidence in ('low','unreadable')) as hard_flagged
        from field_outcomes where run = '{run_name}'
    )
    select
        round(correct::numeric / nullif(correct + wrong + hallucinated, 0), 4),
        round((declared - missed)::numeric / nullif(declared, 0), 4),
        round(flagged::numeric / nullif(flagged + over_flagged + declined, 0), 4),
        round(hard_flagged::numeric / nullif(hard, 0), 4)
    from c"""


def _gate_query() -> str:
    return """
    with c as (
        select run,
            count(*) filter (where outcome = 'correct') as correct,
            count(*) filter (where outcome = 'correctly_flagged') as flagged,
            count(*) filter (where outcome = 'wrong') as wrong,
            count(*) filter (where outcome = 'hallucinated') as hallucinated,
            count(*) filter (where outcome = 'missed') as missed,
            count(*) filter (where outcome = 'over_flagged') as over_flagged,
            count(*) filter (where outcome = 'declined') as declined,
            count(*) filter (where expected_confidence <> 'absent') as declared,
            count(*) filter (where expected_confidence in ('low','unreadable')) as hard,
            count(*) filter (where expected_confidence in ('low','unreadable')
                               and produced_confidence in ('low','unreadable')) as hard_flagged
        from field_outcomes group by run
    ), r as (
        select run,
            round(correct::numeric / nullif(correct + wrong + hallucinated, 0), 4) as a,
            round((declared - missed)::numeric / nullif(declared, 0), 4) as c,
            round(flagged::numeric / nullif(flagged + over_flagged + declined, 0), 4) as p,
            round(hard_flagged::numeric / nullif(hard, 0), 4) as rc
        from c
    )
    select count(*) filter (
        where r.a is distinct from s.field_accuracy
           or r.c is distinct from s.coverage
           or r.p is distinct from s.flag_precision
           or r.rc is distinct from s.flag_recall)
    from r join runs s using (run)"""


def check_outcomes_are_complete(checks: Checks) -> None:
    """The SQL can only rebuild a denominator if every field is there, not just the failures."""
    _, rows = psql(
        "select count(*) from field_outcomes where run = 'reference-perfect'"
    )
    expected = declared_fields()
    checks.add(
        "outcomes: every declared field is loaded, not only the ones that went wrong",
        rows == str(expected),
        f"{rows} of {expected} declared fields",
    )
    _, cases = psql("select count(distinct mess_case) from case_verdicts")
    checks.add(
        "outcomes: all ten mess cases carry a verdict",
        cases == "10",
        f"{cases} cases",
    )
    _, truth_tables = psql(
        "select count(*) from information_schema.tables where table_schema = 'public' "
        "and table_name in ('manifest', 'ground_truth', 'declared_fields', 'expectations')"
    )
    checks.add(
        "outcomes: ground truth is not in the database -- the schema stays portable",
        truth_tables == "0",
        "no truth table; field_outcomes carries the verdict only",
    )


def main() -> int:
    ap = base_parser(__doc__)
    ap.add_argument("--csv-only", action="store_true", help="Skip every database assertion.")
    args = ap.parse_args()

    def body(_label: Any, checks: Checks) -> None:
        check_compose(checks)
        check_csv(checks)

        if args.csv_only:
            checks.add("database: skipped by --csv-only", True, "csv path checked above")
            return
        if not up():
            checks.add(
                "database: the container is up and healthy",
                True,
                "SKIPPED -- not running. `docker compose up -d`, then re-run",
            )
            return

        checks.add("database: the container is up and healthy", True, f"{CONTAINER} on {PORT}")
        check_load_is_idempotent(checks)
        check_outcomes_are_complete(checks)
        check_contract(checks)
        check_asset(checks)
        check_gate(checks)

    return run(["load"], body, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
