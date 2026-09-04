#!/usr/bin/env python3
"""Step 2's Done-when conditions, as assertions.

    scripts/check_corpus.py                  every fixture seed, manifest only
    scripts/check_corpus.py --seed 42 -v     one seed, with the files on disk asserted too
    scripts/check_corpus.py --files          render every seed to a temp inbox and check it

**This harness does not assert on PDF bytes, and that is deliberate rather than an omission.**
reportlab stamps a document id, Pillow stamps a creation date, and python-docx writes a zip
whose member timestamps move, so identical code produces different files on every run. A
checksum check written the way BizData's is would go red for a reason that has nothing to do
with the content, and somebody would eventually "fix" it by weakening something real.

Determinism is asserted where it is meaningful: **the manifest is byte-identical across two
runs of the same seed**, and step 3 adds a pixel hash per rendered page. Same content, same
pixels, same declared truth; the container bytes are allowed to differ.

The counts are the other thing worth reading before editing. There are three of them and only
the first and last are the same number -- 74 files, 75 document instances, 74 records after a
merge -- because case 3 splits one file into two documents and case 5 collapses one document
appearing twice. Seventy-four producing seventy-four looks correct and is the most likely way
for the corpus to be wrong while passing, so the seventy-five in the middle is asserted
separately and by name.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from docx import Document as Docx
from openpyxl import load_workbook

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    sys.exit("error: jsonschema is not installed. .venv/bin/pip install -r requirements.txt")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import FIXTURE_SEEDS, PRIMARY_SEED, Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "schema" / "extraction.schema.json"
CORPUS_SCRIPT = REPO / "scripts" / "corpus.py"
MANIFEST = REPO / "corpus" / "manifest.json"
INBOX = REPO / "corpus" / "inbox"

# Restated rather than imported from corpus.py. A harness that reads the composition off the
# thing that produced it asserts only that the generator agrees with itself; these numbers come
# from the decision record and the execution plan, and a change there that is not made here
# fails.
COMPOSITION = {
    "sow": 18,
    "sow_amendment": 6,
    "invoice": 36,
    "remittance": 8,
    "statement": 2,
    "out_of_scope": 4,
}
N_DOCUMENTS = 74
N_FILES = 74
N_INSTANCES = 75
N_SCANS = 22
CASES = [str(n) for n in range(1, 11)]


def generate(seed: int, into: Path | None) -> dict[str, Any]:
    """Run corpus.py as a subprocess and read back what it wrote.

    Shelled out with the real command line rather than imported, so that the harness exercises
    the entry point a person types. An imported ``build()`` skips argument parsing, the file
    write and the manifest serialisation, which is three of the places this step can be wrong.
    """
    cmd = [sys.executable, str(CORPUS_SCRIPT), "--seed", str(seed)]
    if into is None:
        cmd.append("--manifest-only")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:300])
    return json.loads(MANIFEST.read_text())


# ------------------------------------------------------------------------------ the checks


def check_counts(manifest: dict[str, Any], checks: Checks) -> None:
    entries = manifest["documents"]
    counts = manifest["counts"]

    checks.add(
        "corpus: seventy-four documents",
        len(entries) == N_DOCUMENTS,
        f"{len(entries)} documents",
    )

    by_type = Counter(e["document_type"] for e in entries)
    checks.add(
        "corpus: the composition is exact, not approximate",
        dict(by_type) == COMPOSITION,
        ", ".join(f"{n} {t}" for t, n in sorted(by_type.items())),
    )

    checks.add(
        "corpus: seventy-four files, which is arithmetic and not the same fact",
        counts["files"] == N_FILES,
        f"{counts['files']} files, from {N_DOCUMENTS} documents minus case 3 plus case 5",
    )

    # The number in the middle. This is the assertion that catches a corpus which read one
    # document per file and never deduplicated -- the two errors cancel and both mess cases
    # the page-range and natural-key designs exist for go unexamined.
    checks.add(
        "corpus: seventy-five document instances, not seventy-four",
        counts["document_instances"] == N_INSTANCES,
        f"{counts['document_instances']} instances: {N_FILES} files, one holding two documents, "
        "one document filed twice",
    )

    checks.add(
        "corpus: twenty-two files are planned as scans, about thirty percent",
        counts["scans"] == N_SCANS,
        f"{counts['scans']} of {counts['files']} = {counts['scans'] / counts['files']:.0%}",
    )

    ids = [e["document_id"] for e in entries if e["document_id"]]
    checks.add(
        "corpus: every document_id is unique, so the natural key is a key",
        len(ids) == len(set(ids)),
        f"{len(set(ids))} distinct of {len(ids)}",
    )


def check_against_schema(manifest: dict[str, Any], checks: Checks) -> None:
    """Every declared field name is one the extraction schema knows.

    Divergence here is only discovered at step 8, when nothing joins and the accuracy figure
    reads as a model failure.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    vocabulary = set(schema["$defs"]["field_name"]["enum"])
    types = set(schema["properties"]["document_type"]["enum"])

    unknown = sorted(
        {name for e in manifest["documents"] for name in e["fields"]} - vocabulary
    )
    checks.add(
        "manifest: every declared field name is in the schema's vocabulary",
        not unknown,
        "all known" if not unknown else f"unknown: {', '.join(unknown)}",
    )

    bad_types = sorted({e["document_type"] for e in manifest["documents"]} - types)
    checks.add(
        "manifest: every document_type is in the schema's enum",
        not bad_types,
        "all known" if not bad_types else ", ".join(bad_types),
    )

    # A manifest entry is not an extraction record and does not validate as one -- it carries
    # `where` rather than `evidence` and no `confidence`. What must hold is that a *perfect*
    # extraction projected off it would validate, which is the shape reference_run.py builds.
    validator = Draft202012Validator(schema)
    projected = 0
    failures: list[str] = []
    for entry in manifest["documents"]:
        record = {
            "source_file": entry["source_file"],
            "pages": entry["pages"],
            "document_type": entry["document_type"],
            "document_id": entry["document_id"],
            "supersedes": entry["supersedes"],
            "duplicate_of": entry["duplicate_of"],
            "fields": {
                name: (
                    {"value": None, "confidence": "unreadable", "reason": field.get("note") or "declared unreadable"}
                    if field.get("expected_confidence") == "unreadable"
                    else {"value": field["value"], "confidence": "high", "evidence": field["where"]}
                )
                for name, field in entry["fields"].items()
            },
        }
        errs = list(validator.iter_errors(record))
        projected += not errs
        if errs:
            failures.append(f"{entry['document_id']}: {errs[0].message[:60]}")
    checks.add(
        "manifest: a perfect extraction projected off it validates against the schema",
        not failures,
        f"{projected} of {len(manifest['documents'])} project cleanly"
        + ("" if not failures else f" -- {failures[0]}"),
    )


def check_mess_cases(manifest: dict[str, Any], checks: Checks) -> None:
    """Every planted case present and locatable, one assertion per case.

    Named individually rather than counted, because a count of ten passes when one case is
    present ten times. Each of these is a separate promise the corpus makes.
    """
    block = manifest["mess_cases"]
    entries = {e["document_id"]: e for e in manifest["documents"] if e["document_id"]}
    # Case 5's second filename is reachable only through also_filed_as, because the duplicate
    # is not a second document and does not get a manifest entry of its own. Leaving it out
    # here is how the last assertion in this function reported a missing file that is on disk.
    files = {e["source_file"] for e in manifest["documents"]} | {
        e["also_filed_as"] for e in manifest["documents"] if e.get("also_filed_as")
    }

    checks.add(
        "mess: all ten cases are declared in the manifest",
        sorted(block, key=int) == CASES,
        ", ".join(sorted(block, key=int)),
    )

    # 1 -- the scans.
    scans = block["1"]["files"]
    checks.add(
        "mess 1: twenty-two files carry no text layer and are named as scans",
        len(scans) == N_SCANS and all(f.startswith("scan_") for f in scans),
        f"{len(scans)} files, {sum(1 for f in scans if f.startswith('scan_'))} scanner-named",
    )

    # 2 -- the control, and the reason flag precision can move at all.
    control = block["2"]
    control_doc = next((e for e in manifest["documents"] if e["source_file"] == control["file"]), None)
    checks.add(
        "mess 2: the control is a scan whose every field is expected at high confidence",
        control_doc is not None
        and control_doc.get("scanned") is True
        and bool(control_doc["fields"])
        and all(f.get("expected_confidence", "high") == "high" for f in control_doc["fields"].values()),
        f"{control['file']}: "
        + (
            f"{len(control_doc['fields'])} fields, all expected high"
            if control_doc
            else "not found"
        ),
    )

    # 3 -- a file is not a document.
    shared = [f for f in {e["source_file"] for e in manifest["documents"]}
              if sum(1 for e in manifest["documents"] if e["source_file"] == f) == 2]
    two_in_one = [e for e in manifest["documents"] if e["source_file"] == block["3"]["file"]]
    pages_ok = len(two_in_one) == 2 and sorted(e["pages"] for e in two_in_one) == [[1, 1], [2, 2]]
    checks.add(
        "mess 3: exactly one file holds two documents, on page ranges 1 and 2",
        len(shared) == 1 and pages_ok,
        f"{block['3']['file']}: "
        + ", ".join(f"{e['document_type']} pages {e['pages']}" for e in sorted(two_in_one, key=lambda e: e["pages"])),
    )
    checks.add(
        "mess 3: the two documents in that file are different types",
        len({e["document_type"] for e in two_in_one}) == 2,
        " + ".join(sorted(e["document_type"] for e in two_in_one)),
    )

    # 4 -- supersession.
    amendments = [e for e in manifest["documents"] if e["document_type"] == "sow_amendment"]
    superseded = {e["supersedes"] for e in amendments}
    checks.add(
        "mess 4: six amendments, each superseding a SOW that is also in the folder",
        len(amendments) == 6
        and all(e["supersedes"] for e in amendments)
        and superseded <= set(entries),
        f"{len(amendments)} amendments superseding {len(superseded)} SOWs, "
        f"{len(superseded & set(entries))} of which are present",
    )
    stale = [
        e for e in amendments
        if entries[e["supersedes"]]["fields"]["ceiling_amount"]["value"]
        == e["fields"]["revised_ceiling_amount"]["value"]
    ]
    checks.add(
        "mess 4: every amendment actually moves the ceiling it supersedes",
        not stale,
        "all six revise the figure" if not stale else f"{len(stale)} unchanged",
    )

    # 5 -- the duplicate.
    dupes = block["5"]["files"]
    original = next((e for e in manifest["documents"] if e.get("also_filed_as")), None)
    checks.add(
        "mess 5: one document is reachable through two filenames, differing only in the name",
        len(dupes) == 2
        and original is not None
        and original["document_id"] == block["5"]["document_id"]
        and dupes[1] not in {e["source_file"] for e in manifest["documents"]},
        f"{block['5']['document_id']} filed as {dupes[0]} and {dupes[1]}",
    )

    # 6 -- the locale.
    euro = block["6"]["documents"]
    checks.add(
        "mess 6: one client's invoices render in 1.234,56 and declare canonical decimals",
        len(euro) >= 2
        and all(d in entries for d in euro)
        and all(
            _is_decimal(entries[d]["fields"]["total_due"]["value"]) for d in euro
        ),
        f"{len(euro)} invoices: {', '.join(euro)}",
    )

    # 7 -- the cut-off total. The one the brief is really about.
    seven = block["7"]
    cut = entries.get(seven["document_id"])
    checks.add(
        "mess 7: the cut-off total is declared unreadable and its block is gone from the fields",
        cut is not None
        and cut["fields"]["total_due"].get("expected_confidence") == "unreadable"
        and not ({"subtotal", "net_amount", "tax_amount", "retainer_credit"} & set(cut["fields"])),
        f"{seven['document_id']}: total_due expected "
        + (cut["fields"]["total_due"].get("expected_confidence", "high") if cut else "?")
        + f", {len(cut['fields']) if cut else 0} fields survive the crop",
    )
    checks.add(
        "mess 7: the total is still declared in the manifest, so a guess can be caught",
        cut is not None and _is_decimal(cut["fields"]["total_due"]["value"]),
        f"declared {cut['fields']['total_due']['value'] if cut else '?'}",
    )

    # 8 -- the contradiction.
    eight = block["8"]
    annotated = entries.get(eight["document_id"])
    checks.add(
        "mess 8: the printed terms are declared, flagged low, and name the handwriting",
        annotated is not None
        and annotated["fields"]["payment_terms"].get("expected_confidence") == "low"
        and eight["annotation"]
        and eight["annotation"] in annotated["fields"]["payment_terms"].get("note", ""),
        f"{eight['document_id']}: {eight['annotation']!r}",
    )

    # 9 -- the ambiguous date.
    nine = block["9"]
    dated = entries.get(nine["document_id"])
    day = int(dated["fields"]["issue_date"]["value"][8:10]) if dated else 99
    checks.add(
        "mess 9: the ambiguous date is flagged low and its day is genuinely 12 or less",
        dated is not None
        and dated["fields"]["issue_date"].get("expected_confidence") == "low"
        and day <= 12,
        f"{nine['document_id']}: {dated['fields']['issue_date']['value'] if dated else '?'} "
        f"(day {day}, ambiguous)",
    )

    # 10 -- the sandwich.
    oos = [e for e in manifest["documents"] if e["document_type"] == "out_of_scope"]
    checks.add(
        "mess 10: four out-of-scope documents, none carrying an identity or a field",
        len(oos) == 4
        and all(e["document_id"] is None and not e["fields"] for e in oos),
        ", ".join(sorted(e["source_file"] for e in oos)),
    )
    checks.add(
        "mess 10: they are not four copies of the same joke",
        len({Path(e["source_file"]).suffix for e in oos}) >= 2,
        ", ".join(sorted({Path(e["source_file"]).suffix for e in oos})),
    )

    checks.add(
        "mess: every case names a file or document that exists",
        all(
            f in files
            for key in CASES
            for f in ([block[key].get("file")] if block[key].get("file") else block[key].get("files", []))
        ),
        f"{len(files)} filenames in the manifest",
    )


def _is_decimal(value: Any) -> bool:
    """Money is a decimal string. Not a float, and not a locale-formatted one."""
    if not isinstance(value, str):
        return False
    try:
        Decimal(value)
    except Exception:  # noqa: BLE001
        return False
    return True


def check_arithmetic(manifest: dict[str, Any], checks: Checks) -> None:
    """The invoices add up, and the total is not recoverable by summing a column.

    A reader who catches the corpus lying stops trusting the scorecard, so the documents have
    to be internally consistent. And the retainer credit has to actually do its job: if the
    total equalled the subtotal on every invoice, the right answer would fall out of arithmetic
    and the corpus would test less than it appears to.
    """
    invoices = [
        e for e in manifest["documents"]
        if e["document_type"] == "invoice" and "subtotal" in e["fields"]
    ]

    def money(entry: dict[str, Any], name: str) -> Decimal:
        return Decimal(entry["fields"][name]["value"])

    # Plus, not minus: the credit is declared signed, because the invoice prints it signed on a
    # line reading "Less retainer applied on account  -9,814.23". Adding a negative is the same
    # arithmetic the page describes.
    wrong = [
        e["document_id"]
        for e in invoices
        if money(e, "subtotal") + money(e, "retainer_credit") != money(e, "net_amount")
        or money(e, "net_amount") + money(e, "tax_amount") != money(e, "total_due")
    ]
    checks.add(
        "invoices: subtotal plus the credit is net, and net plus tax is the total, to the cent",
        not wrong,
        f"{len(invoices)} invoices reconcile" if not wrong else f"{len(wrong)} do not: {wrong[:3]}",
    )

    same = [e["document_id"] for e in invoices if money(e, "subtotal") == money(e, "total_due")]
    checks.add(
        "invoices: the total is never the sum of the amount column",
        not same,
        "the retainer credit separates them on every invoice"
        if not same
        else f"{len(same)} invoices where it does not",
    )

    bigger = sum(1 for e in invoices if money(e, "subtotal") > money(e, "total_due"))
    checks.add(
        "invoices: on some of them the subtotal exceeds the total, so it is not the biggest figure",
        bigger > 0,
        f"{bigger} of {len(invoices)} have a subtotal above the total due",
    )

    # A credit reduces the bill, so the declared figure is negative. Asserted rather than
    # assumed, because the sign is the whole reason 35 correct readings once scored as wrong:
    # the page said -9,814.23 and the manifest said 9,814.23, and nothing compared the two.
    positive = [e["document_id"] for e in invoices if money(e, "retainer_credit") >= 0]
    checks.add(
        "invoices: every retainer credit is declared negative, the way the page prints it",
        not positive,
        "all negative" if not positive else f"{len(positive)} are zero or positive",
    )


def check_determinism(seed: int, manifest: dict[str, Any], checks: Checks) -> None:
    """Two runs of the same seed produce identical manifest bytes.

    Asserted on the manifest and not on the documents. See the module docstring: PDF and docx
    writers stamp times and ids, so the file bytes differ between runs of identical code, and a
    checksum over them would be a check that fails for the wrong reason. Step 3's pages.sha256
    covers the rendered pixels, which is the part that has to be stable.
    """
    first = MANIFEST.read_bytes()
    again = generate(seed, None)
    second = MANIFEST.read_bytes()
    checks.add(
        "corpus: two runs of the same seed produce byte-identical manifests",
        first == second,
        f"{len(first)} bytes, identical" if first == second else "manifests differ",
    )
    checks.add(
        "corpus: and the same documents, in the same order",
        [e["document_id"] for e in again["documents"]] == [e["document_id"] for e in manifest["documents"]],
        f"{len(again['documents'])} documents in the same order",
    )


def check_files(manifest: dict[str, Any], checks: Checks) -> None:
    """What is actually on disk, when --files asked for it to be written."""
    if not INBOX.is_dir():
        checks.add("inbox: corpus/inbox/ exists", False, "not written -- run without --manifest-only")
        return

    on_disk = sorted(p.name for p in INBOX.iterdir() if p.is_file())
    declared = sorted({e["source_file"] for e in manifest["documents"]} |
                      {e["also_filed_as"] for e in manifest["documents"] if e.get("also_filed_as")})
    checks.add(
        "inbox: the files on disk are exactly the files the manifest declares",
        on_disk == declared,
        f"{len(on_disk)} on disk, {len(declared)} declared"
        + ("" if on_disk == declared else f"; only in one: {sorted(set(on_disk) ^ set(declared))[:3]}"),
    )
    checks.add(
        "inbox: every file has content",
        all((INBOX / name).stat().st_size > 512 for name in on_disk),
        f"smallest is {min((INBOX / n).stat().st_size for n in on_disk) / 1024:.1f} KB",
    )

    # Case 5's two files are byte-identical: the same document, filed twice. Anything keyed on
    # the filename sees two invoices and doubles the total.
    dupes = manifest["mess_cases"]["5"]["files"]
    checks.add(
        "inbox: case 5's two files are byte-identical and differ only in name",
        (INBOX / dupes[0]).read_bytes() == (INBOX / dupes[1]).read_bytes(),
        f"{dupes[0]} == {dupes[1]}",
    )

    named = [n for n in on_disk if n.startswith("scan_")]
    checks.add(
        "inbox: filenames are as a client would have them, not as a schema would",
        len(named) == N_SCANS
        and any("(1)" in n for n in on_disk)
        and any(n.endswith(".docx") for n in on_disk)
        and any(n.endswith(".eml") for n in on_disk),
        f"{len(named)} scanner-named, plus SOWs, a duplicate and an .eml",
    )

    check_declared_against_page(manifest, checks)


# --------------------------------------------------------- the manifest against the document


def page_text(path: Path) -> str:
    """Everything a reader could see in the file, flattened to one string."""
    if path.suffix == ".pdf":
        pdf = pdfium.PdfDocument(path)
        return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    if path.suffix == ".docx":
        document = Docx(path)
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts += [cell.text for cell in row.cells]
        return "\n".join(parts)
    if path.suffix == ".xlsx":
        sheet = load_workbook(path).active
        return "\n".join(
            str(c.value) for row in sheet.iter_rows() for c in row if c.value is not None
        )
    return path.read_text(errors="ignore")


def surface_forms(value: str) -> set[str]:
    """Every way the renderer might have printed one declared value.

    The manifest stores ``-9814.23`` and ``2027-05-02``; the page says ``-9,814.23`` and
    ``02 May 2027``. Comparing them needs the renderer's formatting rules, so this mirrors
    ``money``, ``long_date`` and ``ambiguous_date`` in documents.py -- including case 6's
    swapped separators and case 9's day-first dates, because a value printed in either of those
    forms is still printed.
    """
    forms = {value}
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        pass
    else:
        plain = f"{amount:,.2f}"
        forms |= {plain, plain.translate(str.maketrans(",.", ".,")), f"{amount:.2f}"}

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        when = date(*(int(part) for part in value.split("-")))
        forms |= {f"{when.day:02d} {when:%B %Y}", f"{when:%d/%m/%Y}"}
    elif re.fullmatch(r"\d{4}-\d{2}", value):
        when = date(int(value[:4]), int(value[5:]), 1)
        forms |= {f"{when:%B %Y}", f"{when:%b %Y}"}
    return forms


def appears_in(form: str, text: str) -> bool:
    """Is this exact surface form on the page, as its own figure?

    Substring matching is not enough for numbers, and the reason is the bug that started all of
    this: ``9,814.23`` is a substring of ``-9,814.23``. A plain ``in`` test says the unsigned
    figure is on a page that only ever printed the signed one, which is precisely the
    disagreement this function exists to detect -- so it would have passed while wrong.

    Numbers therefore match only when not butted against a digit, a separator or a minus.
    """
    form = re.sub(r"\s+", " ", form)
    if re.fullmatch(r"-?[\d,.]+", form):
        return re.search(rf"(?<![\d,.\-]){re.escape(form)}(?![\d])", text) is not None
    return form in text


def check_declared_against_page(manifest: dict[str, Any], checks: Checks) -> None:
    """Every declared value is on the page, and every unreadable one is not.

    This is the harness that was missing, and its absence is the most expensive thing this
    project got wrong. Everything else checked the manifest against *itself* -- its schema, its
    arithmetic, its counts -- and all of that passed while the manifest disagreed with the
    documents in four places: an unsigned retainer the page printed signed, a `fixed` the page
    printed as `Fixed fee`, an Original column the page showed and the manifest omitted, and two
    statement totals the manifest asserted and the file does not contain.

    The first Cowork run found all four by reading the documents, which is the one thing no
    assertion here did. It scored 89.5% for being right.

    The rule underneath it: the manifest is a claim about bytes on disk, so it has to be checked
    against bytes on disk. A ground truth that is only ever compared to itself is a preference.
    """
    fresh = {p.name for p in INBOX.iterdir() if p.suffix in (".pdf", ".docx", ".xlsx")}

    cache: dict[str, str] = {}

    def flat(name: str) -> str:
        if name not in cache:
            cache[name] = re.sub(r"\s+", " ", page_text(INBOX / name))
        return cache[name]

    absent: list[str] = []
    present: list[str] = []
    checked = 0
    for entry in manifest["documents"]:
        name = entry["source_file"]
        if entry["document_type"] == "out_of_scope" or name not in fresh:
            continue
        text = flat(name)
        for field, declared in (entry.get("fields") or {}).items():
            raw = declared["value"]
            # A list is declared as its elements, not as its repr. `invoices_settled` is the
            # only one, and the remittance prints the numbers down a column.
            parts = [str(v) for v in raw] if isinstance(raw, list) else [str(raw)]
            found = all(
                any(appears_in(form, text) for form in surface_forms(part)) for part in parts
            )
            value = ", ".join(parts)

            if declared.get("expected_confidence") == "unreadable":
                # Only the documents degrade.py will not touch. Case 7's total is still on the
                # page at this point -- it is the crop, one step later, that removes it, and
                # check_degrade.py is where that is asserted. Checking it here would fail for
                # the one reason that is not a defect.
                #
                # And only values long enough for absence to mean something. `invoice_count` is
                # `3`, and a single digit cannot be shown missing from a spreadsheet full of
                # dates; the claim there is that no cell *asserts* the count, which a text
                # search cannot express. The money figure beside it carries the assertion.
                if entry.get("scanned") or len(value) < 4:
                    continue
                if found:
                    present.append(f"{entry['document_id']}.{field}={value}")
            else:
                checked += 1
                if not found:
                    absent.append(f"{entry['document_id']}.{field}={value}")

    checks.add(
        "manifest: every declared value appears in the text of its own document",
        not absent,
        f"{checked} declared values found on the page"
        if not absent
        else f"{len(absent)} declared but not present: {absent[:3]}",
    )
    checks.add(
        "manifest: and every value declared unreadable is genuinely not in the file",
        not present,
        "the unreadable figures are absent from the text, not merely awkward"
        if not present
        else f"{len(present)} are readable after all: {present[:3]}",
    )


# ---------------------------------------------------------------------------- entry point


def main() -> int:
    ap = base_parser(__doc__)
    ap.add_argument(
        "--files",
        action="store_true",
        help="Render the documents too and assert on what landed in corpus/inbox/. Slower.",
    )
    args = ap.parse_args()
    seeds = args.seed or list(FIXTURE_SEEDS)

    def body(seed: Any, checks: Checks) -> None:
        with tempfile.TemporaryDirectory() as _:
            manifest = generate(seed, INBOX if args.files else None)
        checks.add("corpus: the generator ran and wrote a manifest", True, f"{len(manifest['documents'])} documents")
        check_counts(manifest, checks)
        check_against_schema(manifest, checks)
        check_mess_cases(manifest, checks)
        check_arithmetic(manifest, checks)
        if args.files:
            check_files(manifest, checks)
        check_determinism(seed, manifest, checks)

    ok = run(seeds, body, args.verbose)

    # --files renders each fixture seed over corpus/inbox/ in turn, so the last one checked is
    # the corpus left on disk -- seed 9015, silently, where seed 42 used to be. Nothing warns
    # you, every downstream script still runs, and the figures quietly belong to a different
    # portfolio. Restored here so that verifying the corpus cannot replace it.
    if args.files and seeds and seeds[-1] != PRIMARY_SEED:
        generate(PRIMARY_SEED, INBOX)
        print(f"\ncorpus/inbox restored to seed {PRIMARY_SEED} (--files left seed {seeds[-1]} there)")
        print(f"  the pages are undegraded: run scripts/degrade.py --seed {PRIMARY_SEED}")

    return ok


if __name__ == "__main__":
    sys.exit(main())
