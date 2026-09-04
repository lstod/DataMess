#!/usr/bin/env python3
"""Load extractions.json, and a scored run, into Postgres.

    scripts/load.py                                  extractions.json + every scored run
    scripts/load.py --run reference-perfect          just that run
    scripts/load.py --csv out/csv/                   no database needed
    scripts/load.py --dsn postgresql://...           somewhere other than 5434

The load runs from the repo, never from inside the Cowork sandbox
-----------------------------------------------------------------
The sandbox has no route to `localhost:5434`. A sibling project established that it can make an
outbound HTTPS request and essentially nothing else, so a design where the agent writes to the
database directly does not work and is not attempted here. **The agent produces JSON; the repo
loads it.**

That is not a workaround. It is the better answer to the question a client will ask about what
the agent is allowed to write to, and it means the whole pipeline can be re-run against a
corrected JSON file without the agent being involved at all.

Idempotence, and what it actually has to survive
------------------------------------------------
Re-running this must be a no-op, not a duplicate-key error and not a second copy of the asset.
Every write is an upsert on a natural key and the whole load is one transaction. Three things
that are easy to get wrong and are handled explicitly:

- **The schema is applied on every run**, rather than trusting the container's
  `docker-entrypoint-initdb.d`. That only fires on an empty volume, so on the second `up` it
  does not run, and a loader that assumed it had fails in a way that reads like a bad DSN.
- **A document's fields are deleted before insert**, not upserted one by one. A re-run of a
  *changed* extraction must not leave a field behind that the new run no longer produces --
  that stale row would then be counted by every query downstream.
- **`--csv` writes the same tables**, so cutting the database costs a flag rather than a
  rewrite. It is the sacrificial step in the build and is built to be sacrificed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
EXTRACTIONS = REPO / "extractions.json"
SCHEMA = REPO / "db" / "schema.sql"
SCORECARD = REPO / "scorecard"

DSN = os.environ.get("DOCMESS_DSN", "postgresql://docmess:docmess@localhost:5434/docmess")

# Kept in the order the tables have to be written in, since document_fields and exceptions both
# reference documents. --csv emits the same names.
TABLES = [
    "documents",
    "document_fields",
    "exceptions",
    "runs",
    "field_outcomes",
    "case_verdicts",
]


def key(record: dict[str, Any]) -> str:
    """The key for one loaded *instance*.

    Identity where the document has one, filename where it does not. Not `source_file` alone --
    case 3 puts two documents in one file. Not `document_id` alone -- the four out-of-scope
    documents have none, and minting one for them would undo case 10, whose point is that the
    thing does not belong rather than that it is a document of unknown type.

    The third clause is case 5, and it is the interesting one. The duplicate instance shares its
    identity with the original -- that is what makes it a duplicate -- so identity alone cannot
    key both, and the first load failed on exactly that. Two ways out: drop the discarded
    instance, or store it under a disambiguated key.

    It is stored. The filename is appended, which is the one thing that differs, and this is not
    a contradiction of case 5's rule that you dedupe on the natural key and never the filename:
    the filename is not being used to *decide* anything, only to give an already-rejected row
    somewhere to sit. Keeping it means the database can show that the duplicate was seen and
    discarded. Dropping it would leave a load that looks identical whether the run caught the
    duplicate or never encountered it, and that distinction is the whole mess case.
    """
    identity = record.get("document_id") or record["source_file"]
    if record.get("duplicate_of"):
        return f"{identity}|{record['source_file']}"
    return identity


# --------------------------------------------------------------------------- shaping the rows


def document_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_key": key(r),
            "document_id": r.get("document_id"),
            "document_type": r["document_type"],
            "source_file": r["source_file"],
            "pages": r.get("pages") or [1, 1],
            "supersedes": r.get("supersedes"),
            "duplicate_of": r.get("duplicate_of"),
        }
        for r in records
    ]


def field_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        for name, field in (record.get("fields") or {}).items():
            value = field.get("value")
            rows.append(
                {
                    "document_key": key(record),
                    "field_name": name,
                    # Lists arrive for invoices_settled. Stored as JSON text rather than as a
                    # Postgres array, because the column also holds money and dates and one of
                    # those representations has to give; JSON round-trips without losing order.
                    "value": json.dumps(value) if isinstance(value, list) else value,
                    "confidence": field.get("confidence", "high"),
                    "evidence": field.get("evidence"),
                    "reason": field.get("reason"),
                }
            )
    return rows


def exception_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The exceptions a reviewer has to work through, derived from the records themselves.

    Derived here rather than read from a list the agent supplied, so the two cannot disagree.
    An agent that forgot to mention a duplicate still gets one in this table, because the
    duplicate is visible in the record it wrote.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        base = {
            "document_key": key(record),
            "document_id": record.get("document_id"),
            "document_type": record["document_type"],
            "source_file": record["source_file"],
        }
        if record["document_type"] == "out_of_scope":
            rows.append({**base, "kind": "out of scope",
                         "detail": "not the firm's paperwork; no fields extracted"})
        if record.get("duplicate_of"):
            rows.append({**base, "kind": "duplicate",
                         "detail": f"the same document as {record['duplicate_of']}, "
                                   "filed under a second name"})
        if record.get("supersedes"):
            rows.append({**base, "kind": "supersedes",
                         "detail": f"replaces {record['supersedes']}, whose figures are stale"})
        for name, field in (record.get("fields") or {}).items():
            if field.get("confidence") == "unreadable":
                rows.append({**base, "kind": f"{name} unreadable",
                             "detail": field.get("reason")})
            elif field.get("confidence") == "low":
                rows.append({**base, "kind": f"{name} low confidence",
                             "detail": field.get("reason") or field.get("evidence")})
    return rows


def run_rows(scored: dict[str, Any]) -> tuple[dict[str, Any], list[dict], list[dict]]:
    metrics = scored["metrics"]
    run = {
        "run": scored["run"],
        "kind": scored.get("kind"),
        "seed": scored.get("seed"),
        "period": scored.get("period"),
        "source": scored.get("source"),
        "scored_at": scored.get("scored_at"),
        "field_accuracy": metrics.get("field_accuracy"),
        "coverage": metrics.get("coverage"),
        "flag_precision": metrics.get("flag_precision"),
        "flag_recall": metrics.get("flag_recall"),
    }
    outcomes = [
        {
            "run": scored["run"],
            "document_id": f["document_id"],
            "field_name": f["field"],
            "outcome": f["outcome"],
            "expected_confidence": f.get("expected_confidence", "high"),
            "produced_confidence": f.get("confidence"),
        }
        for f in scored.get("fields", [])
    ]
    verdicts = [
        {
            "run": scored["run"],
            "mess_case": int(number),
            "verdict": bucket["verdict"],
            "detail": bucket.get("detail"),
        }
        for number, bucket in scored.get("mess_cases", {}).items()
    ]
    return run, outcomes, verdicts


# ------------------------------------------------------------------------------- the database


def load_postgres(dsn: str, tables: dict[str, list[dict[str, Any]]], apply_schema: bool) -> None:
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        sys.exit(
            "error: psycopg is not installed.\n"
            "  .venv/bin/pip install -r requirements.txt\n"
            "  or use --csv, which needs no database"
        )

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            if apply_schema:
                # Every run, not just the first. The container entrypoint only applies this to
                # an empty volume, so the second `docker compose up` leaves the database with
                # whatever the first one created -- including nothing, if it was never up.
                cur.execute(SCHEMA.read_text())

            documents = tables["documents"]
            if documents:
                # Fields are replaced wholesale per document rather than upserted row by row.
                # An upsert leaves behind any field the previous run produced and this one does
                # not, and a stale field is then counted by every query downstream -- the kind
                # of drift that shows up as a coverage figure nobody can reproduce.
                keys = [d["document_key"] for d in documents]
                cur.execute("delete from document_fields where document_key = any(%s)", (keys,))
                cur.execute("delete from exceptions where document_key = any(%s)", (keys,))

            _upsert(cur, "documents", tables["documents"], ["document_key"])
            _insert(cur, "document_fields", tables["document_fields"])
            _insert(cur, "exceptions", tables["exceptions"])

            for run in tables["runs"]:
                # Cascades to field_outcomes and case_verdicts, so a re-scored run replaces its
                # own rows and cannot leave half of an older scoring behind.
                cur.execute("delete from runs where run = %s", (run["run"],))
            _insert(cur, "runs", tables["runs"])
            _insert(cur, "field_outcomes", tables["field_outcomes"])
            _insert(cur, "case_verdicts", tables["case_verdicts"])
        conn.commit()


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    return list(rows[0])


def _insert(cur: Any, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = _columns(rows)
    placeholders = ", ".join(["%s"] * len(cols))
    cur.executemany(
        f"insert into {table} ({', '.join(cols)}) values ({placeholders})",
        [tuple(r[c] for c in cols) for r in rows],
    )


def _upsert(cur: Any, table: str, rows: list[dict[str, Any]], conflict: list[str]) -> None:
    if not rows:
        return
    cols = _columns(rows)
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in conflict)
    cur.executemany(
        f"insert into {table} ({', '.join(cols)}) values ({placeholders}) "
        f"on conflict ({', '.join(conflict)}) do update set {updates}, loaded_at = now()",
        [tuple(r[c] for c in cols) for r in rows],
    )


# ------------------------------------------------------------------------------------ the csv


def load_csv(out: Path, tables: dict[str, list[dict[str, Any]]]) -> None:
    """The same tables, as files. The reason step 7 is safe to cut.

    Postgres is the most cuttable thing in the build, so it should not be the only way to get
    the asset out. These CSVs load with `\\copy` and open in Excel, and the column names match
    the schema exactly so the two paths cannot drift apart.
    """
    out.mkdir(parents=True, exist_ok=True)
    for name in TABLES:
        rows = tables[name]
        path = out / f"{name}.csv"
        if not rows:
            path.write_text("")
            continue
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_columns(rows))
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        k: ("" if v is None else
                            "{" + ",".join(str(x) for x in v) + "}" if isinstance(v, list) else v)
                        for k, v in row.items()
                    }
                )


# ---------------------------------------------------------------------------- entry point


def gather(source: Path, runs: Iterable[Path]) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(source.read_text())
    records = payload["documents"] if isinstance(payload, dict) else payload

    tables: dict[str, list[dict[str, Any]]] = {
        "documents": document_rows(records),
        "document_fields": field_rows(records),
        "exceptions": exception_rows(records),
        "runs": [],
        "field_outcomes": [],
        "case_verdicts": [],
    }
    for path in runs:
        scored = json.loads(path.read_text())
        # A superseded scoring is not loaded. It was measured against a manifest that has since
        # been corrected, so the database -- which holds exactly one manifest, the current one
        # -- cannot reproduce its figures, and the SQL gate would fail on the honest difference
        # between what was true then and what is true now. It stays in scores.jsonl and on the
        # scorecard, which are records of what happened rather than of what is.
        if scored.get("kind") == "superseded":
            continue
        run, outcomes, verdicts = run_rows(scored)
        tables["runs"].append(run)
        tables["field_outcomes"].extend(outcomes)
        tables["case_verdicts"].extend(verdicts)
    return tables


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--in", dest="source", type=Path, default=EXTRACTIONS)
    ap.add_argument("--run", action="append", help="Repeatable. Every scored run when omitted.")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--csv", type=Path, metavar="DIR", help="Write CSVs instead. No database.")
    ap.add_argument("--no-schema", action="store_true", help="Skip applying db/schema.sql.")
    ap.add_argument("--quiet", "-q", action="store_true")
    args = ap.parse_args()

    if not args.source.is_file():
        raise SystemExit(
            f"error: no extractions at {args.source}.\n"
            "  scripts/reference_run.py --perfect     (a projection, not a run)"
        )

    if args.run:
        paths = [SCORECARD / f"{name}.json" for name in args.run]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            raise SystemExit(
                f"error: not scored: {', '.join(p.stem for p in missing)}.\n"
                f"  scripts/reconcile.py --run {missing[0].stem}"
            )
    else:
        paths = sorted(p for p in SCORECARD.glob("*.json") if p.name != "scorecard.json")

    tables = gather(args.source, paths)

    if args.csv:
        load_csv(args.csv, tables)
        target = str(args.csv)
    else:
        load_postgres(args.dsn, tables, apply_schema=not args.no_schema)
        target = args.dsn.rsplit("@", 1)[-1]

    if not args.quiet:
        print(f"loaded into {target}")
        for name in TABLES:
            print(f"  {name:<16} {len(tables[name]):>5} rows")
        if tables["runs"]:
            print("  runs: " + ", ".join(r["run"] for r in tables["runs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
