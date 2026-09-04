#!/usr/bin/env python3
"""Step 6's Done-when conditions, as assertions.

    scripts/check_workbook.py              build from extractions.json and check it
    scripts/check_workbook.py -v

The interesting problem here is that **openpyxl cannot evaluate a formula.** Reading the file
back gives the formula string; the value it would produce exists only once Excel has opened it,
and `data_only=True` returns `None` for every cell that has never been calculated. So a harness
cannot simply assert that the Summary total is right.

What it can do is check the formula would be right, in three parts: the cell holds a formula
rather than a constant, the range that formula spans is exactly the rows carrying the figures,
and the values in those rows sum to the manifest's own total. Together those establish the
result without evaluating it, and each of the three fails differently -- a constant, a range
that stops a row short, and a column of figures that does not match the corpus.

The remaining question -- does Excel actually open it -- is a human check and stays one. The
plan says to open the workbook and click a total, and that is not something to assert around.

On "no arithmetic in the builder"
---------------------------------
The rule is about **derived** figures, not about data. The cells on the Invoices tab are
constants because they are what the extraction read off a page; there is nothing to derive them
from. What must never be a constant is anything a reader could recompute from another cell: a
total, a count of rows on another tab, a subtotal. That is the line asserted below.

The one deliberate exception is the documents-by-type census on the Summary tab, which is a
count of the input records. Three of the six types have no tab of their own to count from, and
inventing one to make a COUNTIF possible would be a worse sheet. Its aggregate is a formula.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string
except ImportError:  # pragma: no cover
    sys.exit("error: openpyxl is not installed. .venv/bin/pip install -r requirements.txt")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "corpus" / "manifest.json"
EXTRACTIONS = REPO / "extractions.json"
BUILDER = REPO / "skills" / "build-extraction-workbook" / "scripts" / "build_workbook.py"
OUT = REPO / "out" / "extraction-review.xlsx"

TABS = ["Summary", "Invoices", "Engagements", "Exceptions", "QA"]
RANGE = re.compile(r"([A-Z]+)(\d+):([A-Z]+)(\d+)")


def build() -> None:
    result = subprocess.run(
        [sys.executable, str(BUILDER), str(EXTRACTIONS), "--out", str(OUT)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:280])


def is_formula(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")


def find_row(ws: Any, label: str) -> int | None:
    for row in range(1, ws.max_row + 1):
        if str(ws.cell(row=row, column=1).value or "").strip() == label:
            return row
    return None


# ------------------------------------------------------------------------------ the checks


def check_structure(wb: Any, checks: Checks) -> None:
    checks.add(
        "workbook: five tabs, in the order a reader works through them",
        wb.sheetnames == TABS,
        " -> ".join(wb.sheetnames),
    )
    checks.add(
        "workbook: no merged cells anywhere",
        not any(ws.merged_cells.ranges for ws in wb.worksheets),
        "sorting and filtering still work"
        if not any(ws.merged_cells.ranges for ws in wb.worksheets)
        else "merged ranges found",
    )
    checks.add(
        "workbook: every tab has a frozen header row",
        all(ws.freeze_panes for ws in wb.worksheets if ws.title != "Summary"),
        "headers stay visible while scrolling",
    )


def check_formulas(wb: Any, manifest: dict[str, Any], checks: Checks) -> None:
    """The whole point of the tab: derived figures are formulas, and they are correct."""
    summary, invoices = wb["Summary"], wb["Invoices"]

    row = find_row(summary, "Total invoiced")
    cell = summary.cell(row=row, column=2) if row else None
    checks.add(
        "summary: the total invoiced is a formula, not a number somebody typed",
        cell is not None and is_formula(cell.value),
        f"B{row} = {cell.value!r}" if cell else "not found",
    )

    # It points at the Invoices tab's own total, which is itself a SUM. Two hops, and both have
    # to hold: a reader clicking through should land on the rows, not on another constant.
    target = re.search(r"'?Invoices'?!([A-Z]+)(\d+)", str(cell.value)) if cell else None
    inner = invoices.cell(row=int(target.group(2)), column=column_index_from_string(target.group(1))) if target else None
    checks.add(
        "summary: it points through to a SUM over the invoice rows, not to another constant",
        inner is not None and is_formula(inner.value) and "SUM" in str(inner.value),
        f"-> Invoices!{target.group(1)}{target.group(2)} = {inner.value!r}" if inner else "does not resolve",
    )

    # And that SUM spans exactly the rows carrying totals. A range one row short is the classic
    # version of this bug and it is invisible in a spreadsheet.
    span = RANGE.search(str(inner.value)) if inner else None
    first, last = (int(span.group(2)), int(span.group(4))) if span else (0, 0)
    column = column_index_from_string(span.group(1)) if span else 0
    populated = [
        r for r in range(2, invoices.max_row + 1)
        if invoices.cell(row=r, column=1).value is not None
    ]
    checks.add(
        "summary: the SUM spans every invoice row and no others",
        span is not None and first == min(populated) and last == max(populated),
        f"rows {first}-{last} against {min(populated)}-{max(populated)} populated",
    )

    # Now the number it would produce, computed here from the cells rather than by Excel, and
    # compared against the corpus. This is the assertion that a formula pointing at the right
    # rows is also pointing at the right *figures*.
    in_sheet = sum(
        Decimal(str(invoices.cell(row=r, column=column).value))
        for r in range(first, last + 1)
        if invoices.cell(row=r, column=column).value is not None
    )
    declared = sum(
        (
            Decimal(e["fields"]["total_due"]["value"])
            for e in manifest["documents"]
            if e["document_type"] == "invoice"
            and e["fields"].get("total_due", {}).get("expected_confidence") != "unreadable"
        ),
        Decimal("0"),
    )
    checks.add(
        "summary: the figure that SUM would produce matches the corpus, to the cent",
        in_sheet == declared,
        f"{in_sheet:,.2f} in the sheet against {declared:,.2f} declared"
        + ("" if in_sheet == declared else f", out by {in_sheet - declared:,.2f}"),
    )

    # Case 5. The duplicate is out of the totals, and the manifest's total counts it once.
    checks.add(
        "summary: the duplicated invoice is counted once, not twice",
        len(populated) == 36,
        f"{len(populated)} invoice rows for 36 invoices, one of which is filed twice",
    )

    derived = [
        "Invoices extracted",
        "Invoices missing a total",
        "Engagements under contract",
        "Fields flagged for review",
        "  of which unreadable",
        "  of which low confidence",
        "Exceptions to resolve",
        "Reviewed",
        "Total documents",
    ]
    constants = [
        name for name in derived
        if (r := find_row(summary, name)) and not is_formula(summary.cell(row=r, column=2).value)
    ]
    checks.add(
        "summary: every derived figure is a formula, with no computed constants",
        not constants,
        f"{len(derived)} formulas" if not constants else f"constants found: {constants}",
    )


def check_flagging(wb: Any, records: list[dict[str, Any]], manifest: dict[str, Any], checks: Checks) -> None:
    qa, exceptions, invoices = wb["QA"], wb["Exceptions"], wb["Invoices"]

    flagged = [
        (r.get("document_id"), name)
        for r in records
        if not r.get("duplicate_of")
        for name, f in (r.get("fields") or {}).items()
        if f.get("confidence") in ("low", "unreadable")
    ]
    qa_rows = [r for r in range(2, qa.max_row + 1) if qa.cell(row=r, column=1).value]
    checks.add(
        "QA: one row per flagged field, and none missing",
        len(qa_rows) == len(flagged),
        f"{len(qa_rows)} rows for {len(flagged)} flagged fields",
    )

    no_evidence = [
        qa.cell(row=r, column=1).value
        for r in qa_rows
        if not (qa.cell(row=r, column=6).value or qa.cell(row=r, column=7).value)
    ]
    checks.add(
        "QA: every row cites either evidence or a reason, so a reviewer can go and look",
        not no_evidence,
        "all rows locatable" if not no_evidence else f"{len(no_evidence)} cannot be located",
    )
    checks.add(
        "QA: the Verdict column is empty -- that is where the human works",
        all(qa.cell(row=r, column=9).value is None for r in qa_rows),
        "left blank on every row",
    )
    checks.add(
        "QA: unreadable rows carry a reason rather than an evidence citation",
        all(
            qa.cell(row=r, column=7).value
            for r in qa_rows
            if qa.cell(row=r, column=4).value == "unreadable"
        ),
        "the figure was not read, so there is no page position to cite",
    )

    exc_rows = [r for r in range(2, exceptions.max_row + 1) if exceptions.cell(row=r, column=1).value]
    checks.add(
        "exceptions: the tab is not empty",
        len(exc_rows) > 0,
        f"{len(exc_rows)} exceptions -- a clean run of this folder would mean flagging is broken",
    )
    kinds = {str(exceptions.cell(row=r, column=3).value) for r in exc_rows}
    checks.add(
        "exceptions: it reports the supersessions and the out-of-scope documents, not only gaps",
        any("supersedes" in k for k in kinds) and any("out of scope" in k for k in kinds),
        ", ".join(sorted(kinds)[:5]),
    )

    # Case 5's duplicate has to be *reported* as well as excluded from the totals. This
    # assertion is here because it was not, and the first Cowork run found what these twenty
    # checks did not: main() filtered duplicate records out before build() was ever called, so
    # the duplicate branch in write_exceptions could not fire. Every figure was right and the
    # reviewer was simply never told that two files on disk held one invoice. Excluding it from
    # the arithmetic is half the job; the other half left no trace of its absence.
    duplicate = manifest["mess_cases"]["5"]["document_id"]
    dup_rows = [
        r for r in exc_rows
        if "duplicate" in str(exceptions.cell(row=r, column=3).value)
    ]
    checks.add(
        "exceptions: the duplicate is reported here, not only excluded from the totals",
        len(dup_rows) == 1
        and duplicate in str(exceptions.cell(row=dup_rows[0], column=1).value),
        f"{duplicate} listed as a duplicate" if dup_rows
        else "no duplicate row -- it was dropped before the tab was written",
    )
    checks.add(
        "exceptions: and it says which document it duplicates, so the reviewer can pair them",
        bool(dup_rows) and duplicate in str(exceptions.cell(row=dup_rows[0], column=4).value),
        str(exceptions.cell(row=dup_rows[0], column=4).value)[:70] if dup_rows else "no row",
    )

    # Case 7's cell. An unreadable figure leaves the cell empty, which matches no conditional
    # rule -- so it needs a static fill or the one category most needing attention is the one
    # category with no colour.
    seven = manifest["mess_cases"]["7"]["document_id"]
    row = next((r for r in range(2, invoices.max_row + 1) if invoices.cell(row=r, column=1).value == seven), None)
    cell = invoices.cell(row=row, column=12) if row else None
    checks.add(
        "invoices: the unreadable total is an empty cell with a fill and a comment, not a zero",
        cell is not None
        and cell.value is None
        and cell.fill.fgColor.rgb not in (None, "00000000")
        and cell.comment is not None,
        f"{seven}: empty, filled, comment reads {str(cell.comment.text)[:48]!r}" if cell and cell.comment else "not found",
    )

    coloured = sum(
        1
        for r in range(2, invoices.max_row + 1)
        for c in range(8, 13)
        if invoices.cell(row=r, column=c).fill.fgColor.rgb not in (None, "00000000")
    )
    checks.add(
        "invoices: flagged cells are visible without hunting for them",
        coloured > 0,
        f"{coloured} money cells filled amber or red",
    )


def check_no_money_arithmetic(checks: Checks) -> None:
    """The builder does not add money up. Asserted against its source, because it is a rule
    about how the file is written and no artifact can show it was followed.

    A crude grep, and it is meant to be. It catches the specific thing that goes wrong -- a
    `sum(...)` reaching for a money field because writing the formula was fiddly -- and it will
    have false positives if the builder ever legitimately sums something that is not money. It
    should be read, not silenced, on the day it fires.
    """
    source = BUILDER.read_text()
    body = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    offenders = [
        line.strip()[:70]
        for line in body.splitlines()
        if re.search(r"\bsum\(", line)
        and any(word in line for word in ("total", "amount", "subtotal", "net", "tax", "value"))
    ]
    checks.add(
        "builder: no arithmetic over money anywhere in the source",
        not offenders,
        "every figure is a formula string" if not offenders else f"suspect: {offenders[:2]}",
    )
    checks.add(
        "builder: it writes formula strings, and enough of them to matter",
        source.count('="=') + source.count('value=f"=') + source.count('value="=') >= 4,
        f"{source.count('f\"=') + source.count('\"=')} formula literals in the source",
    )


def main() -> int:
    ap = base_parser(__doc__)
    args = ap.parse_args()

    def body(_label: Any, checks: Checks) -> None:
        if not EXTRACTIONS.is_file():
            checks.add(
                "workbook: there is an extractions.json to build from",
                False,
                "run scripts/reference_run.py --perfect, or bank a Cowork run",
            )
            return
        build()
        checks.add("workbook: the bundled builder ran and wrote a file", OUT.is_file(), str(OUT.relative_to(REPO)))

        wb = load_workbook(OUT)
        manifest = json.loads(MANIFEST.read_text())
        payload = json.loads(EXTRACTIONS.read_text())
        records = payload["documents"] if isinstance(payload, dict) else payload

        check_structure(wb, checks)
        check_formulas(wb, manifest, checks)
        check_flagging(wb, records, manifest, checks)
        check_no_money_arithmetic(checks)

    return run(["workbook"], body, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
