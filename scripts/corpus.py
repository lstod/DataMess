#!/usr/bin/env python3
"""Seventy-four documents and the manifest, from one seed, in one pass.

    scripts/corpus.py                          seed 42, period 2026-08
    scripts/corpus.py --seed 9001              a fixture portfolio
    scripts/corpus.py --manifest-only          re-derive the manifest without writing files

**The manifest is written by this pass and never by a reader.** It is the one rule the whole
project rests on. A manifest derived from a rendered document is an extraction, and an
extraction cannot be the ground truth for an extraction -- if the two ever come from the same
place, the scorecard measures the corpus against itself, gets a high number, and means nothing.
Every builder in documents.py returns rendered bytes and declared fields together, and this
file writes both or neither.

Nothing here reads a PDF back to find out what it says. The one place a written file is
reopened is ``documents.page_count``, which counts pages in a concatenation and asks nothing
about their contents.

The composition, exact rather than approximate
----------------------------------------------
Eighteen SOWs, six amendments, thirty-six invoices, eight remittances, two statements and four
out-of-scope. Seventy-four documents.

Seventy-four documents also land as seventy-four *files*, and the two numbers coinciding is
arithmetic rather than design: case 3 puts two documents in one file and case 5 puts one
document in two files, so the offsets cancel. They are different counts and a check that
assumes otherwise is asserting nothing -- see check_corpus.py, and step 5's runbook, where the
number in the middle is seventy-five.

Determinism
-----------
Asserted on the manifest, not on the file bytes. reportlab stamps a document id, Pillow stamps
a creation date, and python-docx writes a zip whose member order is stable but whose timestamps
are not -- so two runs of identical code produce different files for reasons that have nothing
to do with content. ``manifest.json`` is byte-identical across runs of the same seed, and
step 3 adds a pixel hash per rendered page. Those two together are the contract; the container
bytes are allowed to differ, and check_corpus.py says so in its docstring rather than asserting
something it cannot hold.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import documents as doc  # noqa: E402
from bizdata import portfolio  # noqa: E402
from checks import DEMO_PERIOD, DEMO_SEED  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "corpus"
INBOX = CORPUS / "inbox"
MANIFEST = CORPUS / "manifest.json"

# The composition. Exact, and asserted by check_corpus.py against these names rather than
# against literals typed a second time.
N_SOW = 18  # one per active engagement, which is BizData's N_ACTIVE
N_AMENDMENT = 6
N_INVOICE = 36
N_REMITTANCE = 8
N_STATEMENT = 2
N_OUT_OF_SCOPE = 4
N_DOCUMENTS = N_SOW + N_AMENDMENT + N_INVOICE + N_REMITTANCE + N_STATEMENT + N_OUT_OF_SCOPE

# Roughly thirty percent, and load-bearing rather than decoration: it is the difference between
# a parsing exercise and the problem the brief describes.
N_SCANS = 22


# ------------------------------------------------------------------------------- selection


def select_invoices(p: Any) -> list[dict[str, Any]]:
    """The thirty-six.

    BizData produces around 176 invoices over a twelve-month window, of which about ninety sit
    on active engagements. The corpus is one month of paper, not a year of it, so it takes the
    most recent thirty-six on live engagements.

    Ordered by ``(period, id)`` and not by ``issued_at``, because the period is what the
    invoice is *for* and the issue date is when it happened to be raised -- a fixed-fee invoice
    is issued a random two to ten days after its deliverable's due date, so sorting on the
    issue date interleaves months. Verified to yield exactly thirty-six on seeds 42, 9001, 9002
    and 9003; the assertion in check_corpus.py holds it on all seventeen.
    """
    active = {e["id"] for e in p.active_engagements()}
    on_active = [i for i in p.invoices if i["engagement_id"] in active]
    on_active.sort(key=lambda row: (row["period"], row["id"]))
    return on_active[-N_INVOICE:]


def with_addresses(p: Any) -> dict[int, dict[str, Any]]:
    """Clients, with an address attached by id.

    By id rather than by name, because BizData composes names per seed from a shared pool of
    stems, qualifiers and suffixes. A name-keyed table would miss on sixteen of the seventeen
    fixture seeds, and worse, would sometimes hit the wrong client -- two names differing only
    in their first word occur on eleven of them.
    """
    return {
        row["id"]: {**row, "address": doc.ADDRESSES[(row["id"] - 1) % len(doc.ADDRESSES)]}
        for row in p.clients
    }


# ------------------------------------------------------------------------------- the plan


def plan_mess(rng: Random, p: Any, invoices: list[dict[str, Any]]) -> dict[str, Any]:
    """Decide every planted case before a single document is rendered.

    Three of these change how a document is *drawn* -- case 2 needs large type, case 6 needs
    European separators, case 9 needs a day-first date -- so they cannot be chosen after the
    fact. Deciding all of them here, in one function, off one seeded stream, is also what makes
    the choice reproducible and the manifest a plan rather than a report.

    The cases are held apart on purpose. No document carries two of them, and the four planted
    by degradation are kept off the documents planted here. If a run gets case 5 wrong on a
    document that is also a scan in a foreign number format, there is no way to say which of
    the three defeated it, and the per-case column in the scorecard becomes a guess.
    """
    by_client: dict[int, list[dict[str, Any]]] = {}
    engagements = p.engagement_by_id()
    for invoice in invoices:
        client_id = engagements[invoice["engagement_id"]]["client_id"]
        by_client.setdefault(client_id, []).append(invoice)

    # Case 6 -- one client's invoices in 1.234,56 format. A client with two to four invoices
    # in the corpus, so the case has some weight without swallowing a tenth of it.
    candidates = sorted(cid for cid, rows in by_client.items() if 2 <= len(rows) <= 4)
    case_6_client = candidates[rng.randrange(len(candidates))] if candidates else sorted(by_client)[0]
    case_6_ids = {i["id"] for i in by_client[case_6_client]}

    def free(row: dict[str, Any], taken: set[int]) -> bool:
        return row["id"] not in case_6_ids and row["id"] not in taken

    taken: set[int] = set()

    # Case 9 -- an ambiguous date. Only meaningful where the day is 12 or less, because
    # 03/09/2026 is genuinely two dates and 24/09/2026 is only one.
    ambiguous = [i for i in invoices if i["issued_at"].day <= 12 and free(i, taken)]
    case_9 = ambiguous[rng.randrange(len(ambiguous))]
    taken.add(case_9["id"])

    # Case 2 -- the legible control. Rendered large, degraded at full strength.
    pool = [i for i in invoices if free(i, taken)]
    case_2 = pool[rng.randrange(len(pool))]
    taken.add(case_2["id"])

    # Case 7 -- the total cut off by the scanner margin.
    pool = [i for i in invoices if free(i, taken)]
    case_7 = pool[rng.randrange(len(pool))]
    taken.add(case_7["id"])

    # Case 8 -- a handwritten note contradicting the printed payment terms.
    pool = [i for i in invoices if free(i, taken)]
    case_8 = pool[rng.randrange(len(pool))]
    taken.add(case_8["id"])

    # Case 5 -- the same invoice filed twice under a second name.
    pool = [i for i in invoices if free(i, taken)]
    case_5 = pool[rng.randrange(len(pool))]
    taken.add(case_5["id"])

    # Case 3 -- an invoice with a remittance behind it, in one file. The invoice is chosen
    # here; which remittance goes behind it is settled once the remittances exist.
    pool = [i for i in invoices if free(i, taken)]
    case_3 = pool[rng.randrange(len(pool))]
    taken.add(case_3["id"])

    return {
        "case_2_invoice_id": case_2["id"],
        "case_3_invoice_id": case_3["id"],
        "case_5_invoice_id": case_5["id"],
        "case_6_client_id": case_6_client,
        "case_6_invoice_ids": sorted(case_6_ids),
        "case_7_invoice_id": case_7["id"],
        "case_8_invoice_id": case_8["id"],
        "case_9_invoice_id": case_9["id"],
        # Held out of the degradation pool so that each case stays attributable.
        "exempt_invoice_ids": sorted(case_6_ids | {case_3["id"], case_5["id"], case_9["id"]}),
    }


HANDWRITING = "net 14 days not 30 -- agreed w/ MJ 12/8"


# -------------------------------------------------------------------------------- building


def build(seed: int, period: str) -> dict[str, Any]:
    """Every document, every declared field, and the plan degrade.py will execute."""
    rng = Random(seed)
    p = portfolio(seed, period)
    clients = with_addresses(p)
    engagements = p.engagement_by_id()
    invoices = select_invoices(p)
    mess = plan_mess(rng, p, invoices)

    year = p.period_end.year
    numbers = doc.mint_invoice_numbers(rng, [row["id"] for row in invoices], year)
    if len(set(numbers.values())) != len(numbers):
        raise SystemExit(
            f"error: minted invoice numbers collided on seed {seed}. The natural key has to be "
            "unique or case 5's deduplication is untestable."
        )

    built: list[doc.Document] = []

    # --- 18 SOWs, one per active engagement -------------------------------------------
    active = sorted(p.active_engagements(), key=lambda e: e["id"])
    for engagement in active:
        built.append(
            doc.build_sow(
                engagement,
                clients[engagement["client_id"]],
                p.line_items_for(engagement["id"]),
                p.rate_for(engagement),
            )
        )

    # --- 6 amendments, case 4 ----------------------------------------------------------
    amended = active[:: max(1, len(active) // N_AMENDMENT)][:N_AMENDMENT]
    amendment_of: dict[str, str] = {}
    for n, engagement in enumerate(amended, start=1):
        ref = f"{engagement['sow_ref']}-A{n}"
        effective = engagement["start_date"] + timedelta(days=rng.randrange(40, 200))
        built.append(
            doc.build_amendment(rng, engagement, clients[engagement["client_id"]], ref, effective)
        )
        amendment_of[engagement["sow_ref"]] = ref

    # --- 36 invoices -------------------------------------------------------------------
    settled: dict[int, list[dict[str, Any]]] = {}
    for invoice in invoices:
        engagement = engagements[invoice["engagement_id"]]
        client = clients[engagement["client_id"]]
        deliverables = [li["deliverable"] for li in p.line_items_for(engagement["id"])]
        document = doc.build_invoice(
            rng,
            invoice,
            engagement,
            client,
            deliverables or list(doc.ADDRESSES[:3]),
            numbers[invoice["id"]],
            euro_format=invoice["id"] in set(mess["case_6_invoice_ids"]),
            ambiguous_issue_date=invoice["id"] == mess["case_9_invoice_id"],
            large_print=invoice["id"] == mess["case_2_invoice_id"],
            cut_off_totals=invoice["id"] == mess["case_7_invoice_id"],
        )
        built.append(document)
        if invoice["status"] == "paid" and invoice["paid_at"]:
            settled.setdefault(client["id"], []).append(
                {
                    "number": numbers[invoice["id"]],
                    "issued": invoice["issued_at"],
                    "paid": invoice["paid_at"],
                    "total": invoice["amount"],
                    "invoice_id": invoice["id"],
                }
            )

    # --- 8 remittances -----------------------------------------------------------------
    # One per client with paid invoices, most-settled first, so the eight cover as much of the
    # paid population as the corpus allows rather than eight arbitrary single-invoice advices.
    payers = sorted(settled.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:N_REMITTANCE]
    if len(payers) < N_REMITTANCE:
        raise SystemExit(
            f"error: seed {seed} has only {len(payers)} clients with paid invoices among the "
            f"{N_INVOICE} selected, and the composition needs {N_REMITTANCE} remittances."
        )
    remittances: list[doc.Document] = []
    for n, (client_id, rows) in enumerate(payers, start=1):
        rows = sorted(rows, key=lambda r: (r["paid"], r["number"]))[:3]
        ref = f"REM-{year}-{rng.randrange(1000, 9999)}"
        document = doc.build_remittance(rng, clients[client_id], rows, ref, max(r["paid"] for r in rows))
        remittances.append(document)
        built.append(document)

    # --- 2 statements ------------------------------------------------------------------
    unpaid: dict[int, list[dict[str, Any]]] = {}
    for invoice in invoices:
        if invoice["status"] != "issued":
            continue
        client_id = engagements[invoice["engagement_id"]]["client_id"]
        unpaid.setdefault(client_id, []).append(
            {"number": numbers[invoice["id"]], "issued": invoice["issued_at"], "total": invoice["amount"]}
        )
    owing = sorted(unpaid.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:N_STATEMENT]
    for n, (client_id, rows) in enumerate(owing, start=1):
        built.append(
            doc.build_statement(
                clients[client_id],
                f"STM-{year}-{100 + n:03d}",
                p.period_end,
                p.period_start,
                sorted(rows, key=lambda r: r["number"]),
            )
        )

    # --- 4 out of scope, case 10 --------------------------------------------------------
    for which in range(N_OUT_OF_SCOPE):
        built.append(doc.build_out_of_scope(rng, which, p.period_start + timedelta(days=3 + which * 5)))

    if len(built) != N_DOCUMENTS:
        raise SystemExit(f"error: composed {len(built)} documents, expected {N_DOCUMENTS}")

    return {
        "portfolio": p,
        "documents": built,
        "mess": mess,
        "numbers": numbers,
        "remittances": remittances,
        "amendment_of": amendment_of,
    }


# ------------------------------------------------------------------------------- assembly


def assemble(rng: Random, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn seventy-four documents into seventy-four files, planting cases 3 and 5.

    Returns one entry per *file*, each carrying the documents inside it and the page each one
    starts on. A document is not a file and the manifest has to be able to say so.
    """
    built: list[doc.Document] = state["documents"]
    mess = state["mess"]
    numbers = state["numbers"]

    by_id = {d.document_id: d for d in built if d.document_id}
    case_3_number = numbers[mess["case_3_invoice_id"]]
    case_5_number = numbers[mess["case_5_invoice_id"]]

    # Case 3 -- an invoice with a remittance behind it. The remittance loses its own file, so
    # the file count drops by one. Chosen as the remittance for a *different* client than the
    # invoice, because two documents in one file that happen to belong together is a filing
    # convention; two that do not is the thing that catches a pipeline reading one per file.
    invoice_3 = by_id[case_3_number]
    remittances: list[doc.Document] = state["remittances"]
    invoice_client = invoice_3.fields["client_name"]["value"]
    others = [r for r in remittances if r.fields["client_name"]["value"] != invoice_client]
    remittance_3 = others[rng.randrange(len(others))] if others else remittances[0]

    files: list[dict[str, Any]] = []
    merged_ids = {remittance_3.document_id}

    for document in built:
        if document.document_id in merged_ids:
            continue

        if document.document_id == case_3_number:
            media = doc.concat_pdfs([document.media, remittance_3.media])
            pages = doc.page_count(media)
            behind = doc.page_count(document.media)
            files.append(
                {
                    "filename": document.filename,
                    "media": media,
                    "page_count": pages,
                    "contents": [
                        {"document": document, "pages": [1, behind]},
                        {"document": remittance_3, "pages": [behind + 1, pages]},
                    ],
                }
            )
            continue

        files.append(
            {
                "filename": document.filename,
                "media": document.media,
                "page_count": document.page_count,
                "contents": [{"document": document, "pages": [1, document.page_count]}],
            }
        )

    # Case 5 -- the same invoice under a second filename. The bytes are identical; only the
    # name differs, which is what makes deduplicating on the filename pass every schema check
    # and silently double-count the total.
    original = next(f for f in files if f["contents"][0]["document"].document_id == case_5_number)
    stem = Path(original["filename"]).stem
    files.append(
        {
            "filename": f"{stem} (1).pdf",
            "media": original["media"],
            "page_count": original["page_count"],
            "contents": [{"document": original["contents"][0]["document"], "pages": [1, original["page_count"]]}],
            "duplicate_of_file": original["filename"],
        }
    )

    mess["case_3_file"] = invoice_3.filename
    mess["case_3_documents"] = [case_3_number, remittance_3.document_id]
    mess["case_5_files"] = [original["filename"], f"{stem} (1).pdf"]
    return files


def plan_degradation(rng: Random, files: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Choose the twenty-two, and rename them the way a scanner would.

    Decided here rather than in degrade.py, and that is a deliberate departure from the plan's
    wording. The plan has step 3 select which documents degrade. But a scanned file is called
    ``scan_20260814_113255.pdf`` and not ``INV-2026-4487.pdf``, and case 7 changes what the
    document *asserts* -- so if the choice were made at step 3, the manifest written here would
    carry the wrong filename and the wrong fields, and step 3 would have to rewrite it. A
    manifest rewritten by a later pass is a manifest with two authors, which is the failure
    this whole design exists to avoid.

    So corpus.py plans and degrade.py executes. The choice is still one seeded stream and still
    reproducible; it is just made where its consequences can be written down truthfully.
    """
    mess = state["mess"]
    numbers = state["numbers"]
    forced = {
        numbers[mess["case_2_invoice_id"]]: 2,
        numbers[mess["case_7_invoice_id"]]: 7,
        numbers[mess["case_8_invoice_id"]]: 8,
    }
    exempt = {numbers[i] for i in mess["exempt_invoice_ids"]}

    scannable = [
        f
        for f in files
        if f["filename"].lower().endswith(".pdf")
        and not any(c["document"].document_id in exempt for c in f["contents"])
        and "duplicate_of_file" not in f
    ]
    chosen = [f for f in scannable if f["contents"][0]["document"].document_id in forced]
    rest = [f for f in scannable if f not in chosen]
    rng.shuffle(rest)
    chosen += rest[: N_SCANS - len(chosen)]

    if len(chosen) != N_SCANS:
        raise SystemExit(
            f"error: only {len(chosen)} files are eligible to become scans, and the corpus "
            f"needs {N_SCANS}."
        )

    # Scanner-default names: a date and a time, minutes apart, carrying nothing. No invoice
    # number, no client, and a date that is not the document's own.
    stamp = 113255
    for entry in sorted(chosen, key=lambda f: f["filename"]):
        document = entry["contents"][0]["document"]
        case = forced.get(document.document_id, 1)
        entry["filename"] = f"scan_20260814_{stamp:06d}.pdf"
        stamp += rng.randrange(140, 900)
        entry["degradation"] = {
            "case": case,
            "crop_below_pt": document.anchors.get("totals_top_pt") if case == 7 else None,
            "annotate_at_pt": document.anchors.get("terms_pt") if case == 8 else None,
            "annotation": HANDWRITING if case == 8 else None,
        }
        if case not in document.mess_cases:
            document.mess_cases.append(case)

        if case == 8:
            document.fields["payment_terms"] = doc.declare(
                doc.STANDARD_TERMS,
                "page 1, below the totals block",
                "low",
                "a handwritten annotation beside the printed terms reads "
                f"'{HANDWRITING}' and contradicts them. Record the printed value, flag the "
                "conflict, and do not silently prefer either",
            )
            mess["case_8_annotation"] = HANDWRITING

    mess["case_2_file"] = next(
        f["filename"] for f in chosen if f["contents"][0]["document"].document_id == numbers[mess["case_2_invoice_id"]]
    )
    mess["case_7_file"] = next(
        f["filename"] for f in chosen if f["contents"][0]["document"].document_id == numbers[mess["case_7_invoice_id"]]
    )
    mess["case_8_file"] = next(
        f["filename"] for f in chosen if f["contents"][0]["document"].document_id == numbers[mess["case_8_invoice_id"]]
    )


# -------------------------------------------------------------------------- the manifest


def manifest_for(
    seed: int, period: str, files: list[dict[str, Any]], state: dict[str, Any]
) -> dict[str, Any]:
    """Declared truth, per document, per field.

    Keyed by document and not by file, because the document is the unit. Where the two differ
    the manifest says so: case 3's remittance carries the invoice's filename and a page range
    starting at 2, and case 5's invoice carries a second filename it can also be found under.
    """
    mess = state["mess"]
    entries: list[dict[str, Any]] = []
    duplicates = {f["duplicate_of_file"]: f["filename"] for f in files if "duplicate_of_file" in f}

    for entry in files:
        if "duplicate_of_file" in entry:
            continue
        for held in entry["contents"]:
            document: doc.Document = held["document"]
            record: dict[str, Any] = {
                "document_id": document.document_id,
                "document_type": document.document_type,
                "source_file": entry["filename"],
                "pages": held["pages"],
                "supersedes": document.supersedes,
                "duplicate_of": None,
                "fields": document.fields,
            }
            if entry["filename"] in duplicates:
                # The same document, reachable through a second file. Deduplication has to
                # collapse these to one record; a merge keyed on the filename will not.
                record["also_filed_as"] = duplicates[entry["filename"]]
            if document.mess_cases:
                record["mess_cases"] = sorted(document.mess_cases)
            if entry.get("degradation"):
                record["scanned"] = True
                record["degradation"] = entry["degradation"]
            entries.append(record)

    entries.sort(key=lambda r: (r["document_type"], r["document_id"] or "", r["source_file"]))

    scans = [e for e in entries if e.get("scanned")]
    return {
        "seed": seed,
        "period": period,
        "schema": "schema/extraction.schema.json",
        "generated_by": "scripts/corpus.py",
        "note": (
            "Declared truth. Written by the pass that wrote the documents, never by reading "
            "them back. Values are the figures put on the page, from the same variable in the "
            "same function -- money as decimal strings and dates as ISO, so that case 6's "
            "1.234,56 and case 9's 03/09/2026 live on the page and never in here."
        ),
        "counts": {
            "documents": len(entries),
            "files": len(files),
            "document_instances": len(entries) + len(duplicates),
            "scans": len(scans),
            "by_type": {
                t: sum(1 for e in entries if e["document_type"] == t)
                for t in ("sow", "sow_amendment", "invoice", "remittance", "statement", "out_of_scope")
            },
        },
        "mess_cases": {
            "1": {"rule": "empty text extraction is not an empty document", "files": [e["source_file"] for e in scans]},
            "2": {"rule": "degraded is not unreadable; high is the correct answer", "file": mess["case_2_file"], "expected_confidence": "high"},
            "3": {"rule": "a file is not a document", "file": mess["case_3_file"], "documents": mess["case_3_documents"]},
            "4": {"rule": "set supersedes; the stale ceiling must not reach the asset", "amendments": sorted(state["amendment_of"].values())},
            "5": {"rule": "dedupe on the natural key, never the filename", "files": mess["case_5_files"], "document_id": state["numbers"][mess["case_5_invoice_id"]]},
            "6": {"rule": "normalise the locale and round-trip to the cent", "documents": [state["numbers"][i] for i in mess["case_6_invoice_ids"]]},
            "7": {"rule": "unreadable, with a reason; never a guess", "file": mess["case_7_file"], "document_id": state["numbers"][mess["case_7_invoice_id"]], "field": "total_due"},
            "8": {"rule": "record both, flag the conflict, escalate", "file": mess["case_8_file"], "document_id": state["numbers"][mess["case_8_invoice_id"]], "field": "payment_terms", "annotation": mess.get("case_8_annotation")},
            "9": {"rule": "resolve from the billing period if it can be resolved; flag it if it cannot", "document_id": state["numbers"][mess["case_9_invoice_id"]], "field": "issue_date"},
            "10": {"rule": "out_of_scope; do not force-fit it to the schema", "files": [e["source_file"] for e in entries if e["document_type"] == "out_of_scope"]},
        },
        "documents": entries,
    }


# ------------------------------------------------------------------------------ the write


def write(seed: int, period: str, manifest_only: bool) -> dict[str, Any]:
    state = build(seed, period)
    rng = Random(seed ^ 0x5F5F)
    files = assemble(rng, state)
    plan_degradation(rng, files, state)
    manifest = manifest_for(seed, period, files, state)

    if not manifest_only:
        if INBOX.exists():
            shutil.rmtree(INBOX)
        INBOX.mkdir(parents=True)
        for entry in files:
            (INBOX / entry["filename"]).write_bytes(entry["media"])

    CORPUS.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def _relocate(root: Path) -> None:
    """Point this script at a corpus other than the default one.

    The paths are module globals because every function in here reaches for them directly, and
    threading a root through twenty call sites to support a flag used by one would be a worse
    trade. Rebinding them once, before any work starts, keeps the change to three lines and the
    blast radius to this function.

    The reason the flag exists at all: every Skill edit so far was made while looking at seed 42,
    so seed 42 can no longer answer whether an edit generalises or was fitted to the documents in
    front of it. A held-out corpus is the only thing that can, and it has to be buildable without
    disturbing the one the recording depends on.
    """
    global CORPUS, INBOX, MANIFEST
    CORPUS = root
    INBOX = root / "inbox"
    MANIFEST = root / "manifest.json"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seed", type=int, default=DEMO_SEED)
    ap.add_argument(
        "--corpus",
        type=Path,
        help="Build into this directory instead of corpus/. For a held-out seed, which "
        "must not overwrite the corpus the workbook and the recording are built from.",
    )
    ap.add_argument("--period", default=DEMO_PERIOD, help="YYYY-MM. The demo month.")
    ap.add_argument(
        "--manifest-only",
        action="store_true",
        help="Rewrite corpus/manifest.json without touching corpus/inbox/. For asserting that "
        "two runs of a seed agree, without paying to render them twice.",
    )
    args = ap.parse_args()
    if args.corpus:
        _relocate(args.corpus.resolve())

    manifest = write(args.seed, args.period, args.manifest_only)
    counts = manifest["counts"]
    print(f"{INBOX if not args.manifest_only else MANIFEST}")
    print(
        f"  seed {args.seed}, period {args.period}: "
        f"{counts['documents']} documents in {counts['files']} files, "
        f"{counts['document_instances']} instances, {counts['scans']} planned as scans"
    )
    print("  " + ", ".join(f"{n} {t}" for t, n in counts["by_type"].items()))
    if not args.manifest_only:
        print(f"\n  next: scripts/degrade.py --seed {args.seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
