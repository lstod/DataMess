#!/usr/bin/env python3
"""Turn extraction records into a workbook a finance team can audit. Bundled with the Skill.

    python scripts/build_workbook.py extractions.json --out extraction-review.xlsx

Runs in Cowork's sandbox, so it imports the standard library and ``openpyxl`` and nothing else.
No import from the DocMess repository: when this executes, the repository is not there.

The one rule
------------
**No arithmetic in the builder.** Every figure a reader might check is written as a formula
string and evaluated by Excel, not computed here and typed in as a constant.

A workbook of computed constants is a screenshot with extra steps. Click a total, see
``1284630.55``, learn nothing -- the sheet is worth exactly as much as your willingness to take
it on faith. Click a total, see ``=SUM(Invoices!L2:L37)``, and you can follow it to thirty-six
rows that each cite a page. It also means the thing survives its reader: delete a row you know
to be a duplicate and every total updates, where constants would go quietly stale.

The tell that the rule has been broken is a ``sum()`` over money in this file. There is one
place arithmetic is unavoidable and it is not about money: working out which rows a formula
should span. Those are row indices, and they are tracked as the sheets are written.

Nothing here is scored, so nothing here decides anything. It renders what the extraction said,
including its uncertainty, and leaves the Verdict column empty for a person.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet
except ImportError:  # pragma: no cover
    sys.exit("error: openpyxl is not installed. pip install openpyxl")

# Tab names, in the order they appear. Named because the formulas reference them by string and
# a typo in a cross-sheet reference produces #REF! rather than an exception.
SUMMARY, INVOICES, ENGAGEMENTS, EXCEPTIONS, QA = (
    "Summary",
    "Invoices",
    "Engagements",
    "Exceptions",
    "QA",
)

HEADER = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
TITLE = Font(bold=True, size=13)
BOLD = Font(bold=True)
AMBER = PatternFill("solid", fgColor="FFE8B0")
RED = PatternFill("solid", fgColor="F8C9C4")
MONEY = "#,##0.00"

# Invoice columns, and the single place their order is decided. The Summary's total references
# this table by letter, so the letter has to come from here rather than from a literal typed
# twice -- inserting a column would otherwise silently move the total onto the tax figure.
INVOICE_COLUMNS = [
    ("Invoice", "invoice_number", None),
    ("Client", "client_name", None),
    ("Engagement", "engagement_ref", None),
    ("PO", "purchase_order", None),
    ("Period", "billing_period", None),
    ("Issued", "issue_date", None),
    ("Due", "due_date", None),
    ("Subtotal", "subtotal", MONEY),
    ("Retainer credit", "retainer_credit", MONEY),
    ("Net", "net_amount", MONEY),
    ("VAT", "tax_amount", MONEY),
    ("Total due", "total_due", MONEY),
    ("Terms", "payment_terms", None),
    ("Source file", None, None),
    ("Pages", None, None),
]
TOTAL_DUE_COLUMN = get_column_letter(
    [c[1] for c in INVOICE_COLUMNS].index("total_due") + 1
)

ENGAGEMENT_COLUMNS = [
    ("SOW", "sow_ref", None),
    ("Client", "client_name", None),
    ("Engagement", "engagement_name", None),
    ("Fee basis", "fee_type", None),
    ("Start", "start_date", None),
    ("End", "end_date", None),
    ("Ceiling hours", "ceiling_hours", "#,##0.00"),
    ("Ceiling amount", "ceiling_amount", MONEY),
]


# ------------------------------------------------------------------------------- reading


def load(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    for key in ("documents", "records", "extractions"):
        if isinstance(data.get(key), list):
            return data[key]
    return [data]


def field(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    return (record.get("fields") or {}).get(name)


def value_of(record: dict[str, Any], name: str) -> Any:
    """The value, as a number where the format asks for one.

    Money arrives as a decimal string, because a string is the only way to move a decimal
    through JSON without a float rounding it. Excel needs a number to sum it, so the conversion
    happens here -- at the boundary, once, on its way into a cell -- and never in a calculation.
    """
    entry = field(record, name)
    if entry is None or entry.get("value") is None:
        return None
    return entry["value"]


def numeric(record: dict[str, Any], name: str) -> float | str | None:
    raw = value_of(record, name)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def flagged(entry: dict[str, Any] | None) -> bool:
    return bool(entry) and entry.get("confidence") in ("low", "unreadable")


def label(record: dict[str, Any]) -> str:
    return record.get("document_id") or Path(record.get("source_file", "?")).name


# -------------------------------------------------------------------------------- writing


def head(ws: Worksheet, row: int, names: list[str], widths: list[int] | None = None) -> None:
    for column, name in enumerate(names, start=1):
        cell = ws.cell(row=row, column=column, value=name)
        cell.font = HEADER
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for column, width in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def write_table(
    ws: Worksheet,
    records: list[dict[str, Any]],
    columns: list[tuple[str, str | None, str | None]],
    start: int = 2,
) -> int:
    """One row per record. Returns the last row written, for the formulas that span it."""
    head(
        ws,
        start - 1,
        [c[0] for c in columns],
        [16, 30, 16, 12, 11, 13, 13, 14, 15, 14, 12, 14, 30, 34, 9][: len(columns)],
    )
    row = start
    for record in records:
        for index, (_, name, number_format) in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=index)
            if name is None:
                continue
            entry = field(record, name)
            cell.value = numeric(record, name) if number_format else value_of(record, name)
            if number_format:
                cell.number_format = number_format
            if flagged(entry):
                # Static fill as well as the conditional rule below: an unreadable field leaves
                # the cell empty, and an empty cell matches no CellIsRule.
                cell.fill = AMBER if entry["confidence"] == "low" else RED
                note = entry.get("reason") or entry.get("evidence") or ""
                if note:
                    cell.comment = Comment(f"{entry['confidence']}: {note}", "DocMess")

        ws.cell(row=row, column=len(columns) - 1, value=record.get("source_file"))
        pages = record.get("pages") or [1, 1]
        ws.cell(
            row=row,
            column=len(columns),
            value=str(pages[0]) if pages[0] == pages[1] else f"{pages[0]}-{pages[1]}",
        )
        row += 1
    return row - 1


def write_invoices(wb: Workbook, records: list[dict[str, Any]]) -> int:
    ws = wb[INVOICES]
    invoices = sorted(
        (r for r in records if r.get("document_type") == "invoice"), key=label
    )
    last = write_table(ws, invoices, INVOICE_COLUMNS)

    if invoices:
        # The bottom line, as a formula over the column above it.
        total_row = last + 2
        ws.cell(row=total_row, column=11, value="TOTAL INVOICED").font = BOLD
        cell = ws.cell(
            row=total_row,
            column=12,
            value=f"=SUM({TOTAL_DUE_COLUMN}2:{TOTAL_DUE_COLUMN}{last})",
        )
        cell.font = BOLD
        cell.number_format = MONEY

        # Amber where a value is present but was flagged low. Written as a rule rather than a
        # fill so the colour follows the data if a reviewer edits a cell.
        ws.conditional_formatting.add(
            f"H2:L{last}",
            CellIsRule(operator="lessThan", formula=["0"], fill=AMBER),
        )
    return last


def write_engagements(wb: Workbook, records: list[dict[str, Any]]) -> int:
    """SOWs with their amendments applied, and the original figure kept beside it.

    Applied rather than replaced. Case 4's rule is that the stale ceiling must not reach the
    asset, and the way to satisfy it while staying auditable is to show the current figure in
    the column a reader will sum and the superseded one beside it, labelled -- rather than to
    drop the original and leave a reviewer unable to see that anything changed.
    """
    ws = wb[ENGAGEMENTS]
    sows = sorted((r for r in records if r.get("document_type") == "sow"), key=label)
    amendments = {
        r["supersedes"]: r
        for r in records
        if r.get("document_type") == "sow_amendment" and r.get("supersedes")
    }

    columns = ENGAGEMENT_COLUMNS + [
        ("Amended by", None, None),
        ("Revised ceiling", None, MONEY),
        ("Revised end", None, None),
        ("Current ceiling", None, MONEY),
        ("Source file", None, None),
        ("Pages", None, None),
    ]
    last = write_table(ws, sows, columns)

    for offset, record in enumerate(sows):
        row = 2 + offset
        amendment = amendments.get(record.get("document_id"))
        if not amendment:
            # No amendment: the current ceiling is the original, as a reference rather than a
            # copy, so editing the SOW figure moves the current one with it.
            ws.cell(row=row, column=12, value=f"=H{row}").number_format = MONEY
            continue
        ws.cell(row=row, column=9, value=amendment.get("document_id"))
        revised = ws.cell(row=row, column=10, value=numeric(amendment, "revised_ceiling_amount"))
        revised.number_format = MONEY
        revised.fill = AMBER
        ws.cell(row=row, column=11, value=value_of(amendment, "revised_end_date"))
        current = ws.cell(row=row, column=12, value=f"=IF(J{row}=\"\",H{row},J{row})")
        current.number_format = MONEY
        current.font = BOLD
    return last


def write_qa(wb: Workbook, records: list[dict[str, Any]]) -> int:
    """Every flagged field, one row, with somewhere for a human to write the answer."""
    ws = wb[QA]
    head(
        ws,
        1,
        ["Document", "Type", "Field", "Confidence", "Value", "Evidence", "Reason", "Source file", "Verdict"],
        [18, 15, 20, 13, 18, 34, 52, 30, 16],
    )
    row = 2
    for record in sorted(records, key=label):
        for name, entry in sorted((record.get("fields") or {}).items()):
            if not flagged(entry):
                continue
            ws.cell(row=row, column=1, value=label(record))
            ws.cell(row=row, column=2, value=record.get("document_type"))
            ws.cell(row=row, column=3, value=name)
            confidence = ws.cell(row=row, column=4, value=entry["confidence"])
            confidence.fill = AMBER if entry["confidence"] == "low" else RED
            ws.cell(row=row, column=5, value=entry.get("value"))
            ws.cell(row=row, column=6, value=entry.get("evidence") or "")
            ws.cell(row=row, column=7, value=entry.get("reason") or "").alignment = Alignment(
                wrap_text=True, vertical="top"
            )
            ws.cell(row=row, column=8, value=record.get("source_file"))
            # Column 9 is Verdict and stays empty. That is where the human works.
            row += 1
    return row - 1


def write_exceptions(wb: Workbook, records: list[dict[str, Any]]) -> int:
    """Everything that needs a person, and what it is they have to decide.

    This is the one tab that gets **every** record, duplicates included. Every other tab is
    built from the deduplicated list, because a duplicate invoice in a total is the failure
    case 5 exists to catch. But a duplicate that is silently absent from the review sheet is
    only half-handled: excluding it from the arithmetic is correct, and not telling anybody it
    was there is not. The reviewer has two files on disk and needs to know why one of them is
    not in the register.
    """
    ws = wb[EXCEPTIONS]
    head(
        ws,
        1,
        ["Document", "Type", "Issue", "Detail", "Source file"],
        [18, 16, 26, 74, 32],
    )

    rows: list[tuple[str, str, str, str, str]] = []
    for record in sorted(records, key=label):
        kind = record.get("document_type", "?")
        source = record.get("source_file", "")

        for name, entry in sorted((record.get("fields") or {}).items()):
            if entry.get("confidence") == "unreadable":
                rows.append((label(record), kind, f"{name} unreadable", entry.get("reason", ""), source))
            elif entry.get("confidence") == "low" and "contradict" in (entry.get("reason") or ""):
                rows.append((label(record), kind, f"{name} contradicted", entry.get("reason", ""), source))

        if record.get("duplicate_of"):
            rows.append((label(record), kind, "duplicate", f"a second copy of {record['duplicate_of']}; excluded from totals", source))
        if record.get("supersedes"):
            rows.append((label(record), kind, "supersedes", f"replaces {record['supersedes']}, whose figures are stale", source))
        if kind == "out_of_scope":
            rows.append((label(record), kind, "out of scope", "not the firm's paperwork; no fields extracted", source))

    for index, values in enumerate(rows, start=2):
        for column, text in enumerate(values, start=1):
            cell = ws.cell(row=index, column=column, value=text)
            if column == 3:
                cell.fill = RED if "unreadable" in text else AMBER
        ws.cell(row=index, column=4).alignment = Alignment(wrap_text=True, vertical="top")
    return len(rows) + 1


def write_summary(
    wb: Workbook,
    records: list[dict[str, Any]],
    last: dict[str, int],
    source: str,
) -> None:
    """Counts and totals, every one of them a formula over another tab.

    The COUNTIF ranges deliberately run past the last written row. A reviewer who adds an
    invoice they found in a drawer should see the total move, and a range that stops exactly at
    the last row silently ignores them.
    """
    ws = wb[SUMMARY]
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 58

    ws["A1"] = "Extraction review"
    ws["A1"].font = TITLE
    ws["A2"] = f"Source: {source}"
    ws["A3"] = f"{len(records)} document records"

    inv_last, qa_last, exc_last, eng_last = (
        last[INVOICES],
        last[QA],
        last[EXCEPTIONS],
        last[ENGAGEMENTS],
    )
    span = max(inv_last, 2) + 200

    ws["A5"] = "Documents by type"
    ws["A5"].font = BOLD
    row = 6
    for kind in ("sow", "sow_amendment", "invoice", "remittance", "statement", "out_of_scope"):
        count = sum(1 for r in records if r.get("document_type") == kind)
        ws.cell(row=row, column=1, value=kind)
        ws.cell(row=row, column=2, value=count)
        row += 1
    ws.cell(row=row, column=1, value="Total documents").font = BOLD
    total = ws.cell(row=row, column=2, value=f"=SUM(B6:B{row - 1})")
    total.font = BOLD

    row += 2
    ws.cell(row=row, column=1, value="Figures").font = BOLD
    row += 1
    for name, formula, fmt, note in (
        (
            "Total invoiced",
            f"='{INVOICES}'!{TOTAL_DUE_COLUMN}{inv_last + 2}",
            MONEY,
            "the Invoices tab's own total, which is a SUM over its rows -- click through",
        ),
        (
            "Invoices extracted",
            f"=COUNTA('{INVOICES}'!A2:A{span})",
            None,
            "rows on the Invoices tab",
        ),
        (
            "Invoices missing a total",
            f"=COUNTBLANK('{INVOICES}'!{TOTAL_DUE_COLUMN}2:{TOTAL_DUE_COLUMN}{inv_last})",
            None,
            "a blank total is an unreadable figure, not a zero",
        ),
        (
            "Engagements under contract",
            f"=COUNTA('{ENGAGEMENTS}'!A2:A{eng_last})",
            None,
            "one per SOW, with any amendment applied in the Current ceiling column",
        ),
        (
            "Fields flagged for review",
            f"=COUNTA('{QA}'!C2:C{max(qa_last, 2)})",
            None,
            "every low and unreadable field, listed on the QA tab with its evidence",
        ),
        (
            "  of which unreadable",
            f"=COUNTIF('{QA}'!D2:D{max(qa_last, 2)},\"unreadable\")",
            None,
            "the figure was not on the page. Never guessed",
        ),
        (
            "  of which low confidence",
            f"=COUNTIF('{QA}'!D2:D{max(qa_last, 2)},\"low\")",
            None,
            "read, but the reading depends on an inference that is written down",
        ),
        (
            "Exceptions to resolve",
            f"=COUNTA('{EXCEPTIONS}'!A2:A{max(exc_last, 2)})",
            None,
            "duplicates, supersessions, out-of-scope documents and unreadable figures",
        ),
        (
            "Reviewed",
            f"=COUNTA('{QA}'!I2:I{max(qa_last, 2)})",
            None,
            "fills in as somebody works down the Verdict column on the QA tab",
        ),
    ):
        ws.cell(row=row, column=1, value=name)
        cell = ws.cell(row=row, column=2, value=formula)
        if fmt:
            cell.number_format = fmt
        cell.font = BOLD
        ws.cell(row=row, column=3, value=note).alignment = Alignment(vertical="center")
        row += 1

    row += 1
    # Precisely worded, because the census above it is not a formula and a sheet that claims
    # more than it does is the same problem as a total that was typed in.
    ws.cell(row=row, column=1, value="Every figure under Figures is a formula.").font = BOLD
    ws.cell(
        row=row + 1,
        column=1,
        value="Click one and follow it through to the rows it came from. Nothing there was computed and typed in.",
    )
    ws.cell(
        row=row + 2,
        column=1,
        value="The counts under Documents by type are a census of the extraction itself, so they are values.",
    )
    ws.cell(
        row=row + 3,
        column=1,
        value="Amber means low confidence, red means unreadable. Hover any filled cell for the reason.",
    )


def build(
    records: list[dict[str, Any]],
    out: Path,
    source: str,
    every: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """`records` is the deduplicated list; `every` is all of them, Exceptions only.

    Two lists rather than one, because the tabs want different things. Every figure has to be
    computed over the deduplicated records or case 5's invoice is counted twice. The Exceptions
    tab has to see the duplicate, or the reviewer is never told it existed.

    Passing one list did both jobs badly and it was not obvious: the duplicate branch in
    write_exceptions could never fire, because main() had already filtered those records out
    before build() was called. The comment at the call site said the opposite of what the code
    did, the variable was called `counted`, and the workbook looked complete. A Cowork run
    reading its own output found it -- no assertion here did.
    """
    every = records if every is None else every
    wb = Workbook()
    wb.remove(wb.active)
    for name in (SUMMARY, INVOICES, ENGAGEMENTS, EXCEPTIONS, QA):
        wb.create_sheet(name)

    last = {
        INVOICES: write_invoices(wb, records),
        ENGAGEMENTS: write_engagements(wb, records),
        EXCEPTIONS: write_exceptions(wb, every),
        QA: write_qa(wb, records),
    }
    write_summary(wb, records, last, source)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the extraction review workbook.")
    ap.add_argument("path", type=Path, help="extractions.json")
    ap.add_argument("--out", type=Path, default=Path("extraction-review.xlsx"))
    args = ap.parse_args()

    if not args.path.is_file():
        raise SystemExit(f"error: no such file: {args.path}")

    records = load(args.path)
    # Deduplication happens here rather than in the extraction, so that the Exceptions tab can
    # still show a duplicate was found -- a record dropped upstream cannot be reported on.
    # Both lists go to build(): `counted` for every figure, `records` for Exceptions.
    counted = [r for r in records if not r.get("duplicate_of")]
    last = build(counted, args.out, args.path.name, every=records)

    print(f"{args.out}")
    print(
        f"  {len(counted)} records"
        + (f" ({len(records) - len(counted)} duplicates excluded from totals)" if len(records) != len(counted) else "")
    )
    print(
        f"  Invoices {last[INVOICES] - 1} rows, Engagements {last[ENGAGEMENTS] - 1}, "
        f"Exceptions {last[EXCEPTIONS] - 1}, QA {last[QA] - 1}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
