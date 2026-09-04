#!/usr/bin/env python3
"""One builder per document type. Each returns the rendered bytes and the declared fields.

Not two functions, and not two passes. **The manifest is written by the same code that writes
the document**, which is the rule the whole project rests on: a manifest derived from a
rendered document is an extraction, and an extraction cannot be the ground truth for an
extraction. Every builder here returns a ``Document`` carrying ``media`` and ``fields``
together, and ``corpus.py`` writes both or neither.

The practical consequence is a prohibition. Nothing in this file may read back a PDF, a docx or
a workbook it has just written in order to work out what it says. If a figure is in
``fields`` it is because it was put on the page from the same variable, in the same function,
a few lines apart. A helper that "just re-reads the PDF to confirm" turns truth into a guess
that happens to be right.

What BizData supplies, and what this file invents
-------------------------------------------------
BizData's portfolio has clients, engagements, SOW line items and invoices. An invoice row is
``(id, engagement_id, period, amount, status, issued_at, paid_at)`` and that is all of it:
**no invoice number, no due date, no currency, no tax, no line items, and no remittances.**
There is also no identity for the firm issuing the work -- no name, no address, no bank.

So this file composes all of it, deterministically from the seeded stream, and every composed
figure is reconciled back to the one BizData number that is real: ``invoice.amount`` is the
total due, and the subtotal, credit, net and tax are arranged around it rather than replacing
it. The firm and the client addresses are the ones the step-0 spike invented, carried over
unchanged so the corpus matches the documents already banked under ``docs/evidence/step-0/``.

The retainer credit is why that arrangement matters. It sits between subtotal and net, so the
total due is **not** the sum of the amount column and is sometimes smaller than the subtotal
above it. Step 0's run found this by accident on the spike documents and reconciled the
arithmetic rather than reading the totals block alone; here it is deliberate. A corpus where
the right answer falls out of adding up a column tests less than it appears to.
"""

from __future__ import annotations

import email.message
import email.policy
import email.utils
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from random import Random
from typing import Any

import pypdfium2 as pdfium
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Font as XlFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

CENTS = Decimal("0.01")
TENTH = Decimal("0.1")
PAGE_W, PAGE_H = A4

# The issuing firm. Invented at step 0 and repeated here verbatim rather than re-invented,
# because docs/evidence/step-0/ already carries documents on this letterhead.
FIRM = "Hollingsworth Advisory Partners LLP"
FIRM_ADDR = ["4th Floor, Cranmere House", "18-22 Gresham Street", "London EC2V 7AN"]
FIRM_REG = "Registered in England & Wales, OC# 419 8802.  VAT GB 284 5591 03"
FIRM_BANK = "Sterling account 41-08-27 / 60294417.  IBAN GB94 SLNG 4108 2760 2944 17"

# Addresses are assigned to clients by id rather than by name. BizData composes client names
# per seed from a shared pool, so a name-keyed table would miss on every seed but one.
ADDRESSES = [
    "Kestrel Wharf, Bristol BS1 6QH",
    "12 Kingsway, Leeds LS1 2HQ",
    "Harbour Court, Southampton SO14 3TJ",
    "9 St Andrew Square, Edinburgh EH2 2AF",
    "Ridgeway Point, Cardiff CF10 1EP",
    "1 Quay Street, Manchester M3 3JE",
    "Beacon House, Aberdeen AB10 1YG",
    "Civic Centre, Coventry CV1 5RR",
    "The Exchange, Nottingham NG1 2DT",
    "Trinity Gate, Sheffield S1 2JB",
    "60 Broad Quay, Bristol BS1 4DA",
    "Marlowe Point, Norwich NR1 1AA",
]

# Rates carry odd cents so that every amount lands on messy cents and no total can be arrived
# at by guessing a round number. A total ending .00 is a figure a model can produce without
# having read anything.
ROLES = [
    ("Partner", Decimal("452.40")),
    ("Principal", Decimal("337.50")),
    ("Senior consultant", Decimal("264.75")),
    ("Consultant", Decimal("196.25")),
    ("Analyst", Decimal("148.80")),
]

VAT_RATE = Decimal("0.20")
STANDARD_TERMS = "Net 30 days from date of issue"


# --------------------------------------------------------------------------- declarations


def declare(
    value: Any,
    where: str,
    expected_confidence: str = "high",
    note: str | None = None,
) -> dict[str, Any]:
    """One manifest field: what the document says, and where it says it.

    ``expected_confidence`` defaults to ``high`` because a legible field read off a clean page
    should be read at high confidence, and a corpus that expected less of every field would
    make flag precision free. The three places it is not high are mess cases 7, 8 and 9, and
    each of those carries a ``note`` saying why -- which is also the sentence a human grading
    the run reads when deciding whether the model's own reason was any good.
    """
    entry: dict[str, Any] = {"value": _plain(value), "where": where}
    if expected_confidence != "high":
        entry["expected_confidence"] = expected_confidence
    if note:
        entry["note"] = note
    return entry


def _plain(value: Any) -> Any:
    """JSON-safe, and lossless for the two types that matter.

    Money goes out as a decimal *string* and never as a float, because 0.1 + 0.2 is not 0.3 and
    a cent lost in the manifest is a miss scored against the model at step 8 for something the
    model got right. Dates go out as ISO, so that case 9's ambiguity lives on the page and
    never in the truth.
    """
    if isinstance(value, Decimal):
        return str(value.quantize(CENTS))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


@dataclass
class Document:
    """A rendered document and everything true about it, produced together."""

    document_id: str | None
    document_type: str
    filename: str
    media: bytes
    fields: dict[str, dict[str, Any]]
    page_count: int = 1
    supersedes: str | None = None
    duplicate_of: str | None = None
    mess_cases: list[int] = dc_field(default_factory=list)
    # Set by corpus.py when it decides this document's file becomes a scan. Recorded in the
    # manifest so that degrade.py executes a plan rather than making a second set of choices
    # the manifest never saw.
    degradation: dict[str, Any] | None = None
    # Page geometry the degradation pass needs and cannot work out for itself: where the
    # totals rule sits, so case 7 crops below a known line rather than a guessed fraction, and
    # where the payment terms sit, so case 8's annotation lands beside the sentence it
    # contradicts. Points from the bottom of the page, reportlab's own coordinate system.
    anchors: dict[str, float] = dc_field(default_factory=dict)


# ------------------------------------------------------------------------------ formatting


def money(value: Decimal, euro: bool = False) -> str:
    """``1,234.56``, or mess case 6's ``1.234,56``.

    The separators are swapped rather than a locale being set, because ``locale.setlocale`` is
    process-global, is not thread-safe, and depends on which locales the host happens to have
    generated -- three ways for the corpus to differ between machines.
    """
    text = f"{value.quantize(CENTS):,.2f}"
    return text.translate(str.maketrans(",.", ".,")) if euro else text


def long_date(value: date) -> str:
    return f"{value.day:02d} {value:%B %Y}"


def ambiguous_date(value: date) -> str:
    """Mess case 9. Day-first, and only ever used where the day is 12 or less.

    ``03/09/2026`` is the third of September to most of the world and the ninth of March to a
    US reader, and nothing on the page settles it. The billing period does, which is why the
    correct answer is ``low`` with the inference written down rather than ``unreadable``.
    """
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def mint_invoice_numbers(rng: Random, invoice_ids: list[int], year: int) -> dict[int, str]:
    """``INV-2026-4487``, one per invoice. BizData has no invoice number, so DocMess makes one.

    This is the natural key. Step 5 deduplicates on it and step 8 joins on it, and step 0
    settled that it must not be the filename and must not contain a client name -- BizData
    composes names from a shared pool and two clients differing by one word occur on eleven of
    the seventeen fixture seeds, so a join on the name matches the wrong client most of the
    time.

    An affine function of the BizData invoice id, so the same portfolio row always mints the
    same number, offset and strided so the corpus does not read as a dense sequence starting
    at one.

    **The stride and the base are drawn once for the whole run, and that is the entire point of
    this taking a list.** The first version of this function took one id and drew both inside
    itself, which made the mapping ``base_i + id_i * stride_i`` -- a different affine function
    per invoice, and no longer injective. Seed 9015 produced two invoices with the same number,
    which would have made case 5 untestable on that seed: deduplication cannot be scored when
    two genuinely different invoices share the key it deduplicates on. Drawn once, the mapping
    is injective in the id and a collision is not reachable.
    """
    stride = rng.randrange(3, 19)
    base = rng.randrange(100, 900)
    return {i: f"INV-{year}-{base + i * stride:04d}" for i in invoice_ids}


# ------------------------------------------------------------------------- the PDF chrome


def _canvas(buf: BytesIO) -> canvas.Canvas:
    # invariant=1 drops reportlab's CreationDate and document id. It does not make the bytes
    # reproducible on its own and no check in this repo asserts that they are -- determinism
    # is asserted on the manifest here and on page pixels at step 3 -- but there is no reason
    # to stamp a timestamp into a file generated from a seed.
    return canvas.Canvas(buf, pagesize=A4, invariant=1)


def _letterhead(c: canvas.Canvas, title: str) -> float:
    y = PAGE_H - 24 * mm
    c.setFont("Helvetica-Bold", 15)
    c.drawString(22 * mm, y, FIRM)
    c.setFont("Helvetica", 8.5)
    for i, line in enumerate(FIRM_ADDR):
        c.drawString(22 * mm, y - 15 - i * 11, line)
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(PAGE_W - 22 * mm, y, title)
    y -= 56
    c.setLineWidth(0.8)
    c.line(22 * mm, y, PAGE_W - 22 * mm, y)
    return y


def _footer(c: canvas.Canvas) -> None:
    c.setFont("Helvetica", 7)
    c.drawString(22 * mm, 16 * mm, FIRM_REG)


# ------------------------------------------------------------------------------- invoices


def _invoice_lines(
    rng: Random, deliverables: list[str], net: Decimal
) -> list[dict[str, Any]]:
    """Line items whose amounts sum to somewhat more than the net, so a credit is owed.

    The direction matters. The subtotal is built from hours times rate and the credit is then
    whatever reconciles it to the invoice's real total -- rather than the subtotal being
    reverse-engineered from the total, which would leave line items whose hours and rate do
    not multiply out. Somebody clicking down the column has to find it consistent, because a
    reader who catches the corpus lying stops trusting the scorecard.
    """
    n = max(3, min(len(deliverables), rng.randint(3, 5)))
    picks = (deliverables * 3)[:n] if len(deliverables) < n else rng.sample(deliverables, n)
    target = (net * Decimal(rng.randrange(104, 128)) / 100).quantize(CENTS)

    weights = [rng.randrange(15, 60) for _ in range(n)]
    total_weight = sum(weights)

    lines: list[dict[str, Any]] = []
    for deliverable, weight in zip(picks, weights):
        role, rate = ROLES[rng.randrange(len(ROLES))]
        share = target * Decimal(weight) / Decimal(total_weight)
        hours = max(TENTH * 5, (share / rate).quantize(TENTH, rounding=ROUND_HALF_UP))
        lines.append(
            {
                "deliverable": deliverable,
                "role": role,
                "hours": hours,
                "rate": rate,
                "amount": (hours * rate).quantize(CENTS),
            }
        )

    # The credit has to be a credit. Rounding to tenths of an hour can in principle land the
    # subtotal under the net, which would render as a negative "less retainer" line and read
    # as a bug rather than as an invoice. Lift the largest line until it does not.
    while sum(line["amount"] for line in lines) <= net + Decimal("50.00"):
        biggest = max(lines, key=lambda line: line["amount"])
        biggest["hours"] += Decimal("2.5")
        biggest["amount"] = (biggest["hours"] * biggest["rate"]).quantize(CENTS)

    return lines


def build_invoice(
    rng: Random,
    invoice: dict[str, Any],
    engagement: dict[str, Any],
    client: dict[str, Any],
    deliverables: list[str],
    invoice_number: str,
    *,
    euro_format: bool = False,
    ambiguous_issue_date: bool = False,
    large_print: bool = False,
    cut_off_totals: bool = False,
) -> Document:
    """One invoice, and the figures it asserts.

    ``invoice["amount"]`` is BizData's and is the total due. Everything else on the page is
    arranged around it: net and tax are a division of it, the line items are built upward from
    the net, and the retainer credit is the difference. So the document is internally
    consistent and its bottom line is still the portfolio's own number.

    ``large_print`` is mess case 2, the control. The page is set in larger type with more
    leading and a generous margin, so that it survives the *same* degradation strength that
    makes the rest of the corpus hard. It has to be legible, because it is the only thing
    keeping flag precision honest: nine of the ten cases reward caution, and a model that
    flagged everything would pass all nine. Flagging this one is the wrong answer.

    ``cut_off_totals`` is mess case 7, and it changes what the document *asserts* rather than
    only how it looks. The whole totals block is below the crop line, so subtotal, credit, net,
    tax and the payment terms are not on the scanned page at all and are absent from the
    declared fields -- absence is measured as coverage, not as a miss. ``total_due`` stays,
    declared as expected-``unreadable``, because it is the one figure the pipeline is supposed
    to notice it cannot read and say so.

    The crop takes the whole block rather than the last line for a reason worth keeping. With
    net and VAT still visible the total is recoverable by addition, and a model that added them
    up would be doing something defensible; the case would then be testing arithmetic rather
    than testing whether the pipeline admits to a figure it could not read.
    """
    total_due = invoice["amount"]
    net = (total_due / (1 + VAT_RATE)).quantize(CENTS, rounding=ROUND_HALF_UP)
    tax = total_due - net

    lines = _invoice_lines(rng, deliverables, net)
    subtotal = sum((line["amount"] for line in lines), Decimal("0.00"))
    retainer = subtotal - net
    # The retainer is a deduction and the invoice prints it as one. Negated here, once, so that
    # the figure measured for the column width, the figure drawn on the page and the figure the
    # manifest declares are the same object and cannot disagree.
    retainer_shown = -retainer

    issued: date = invoice["issued_at"]
    due = issued + timedelta(days=30)
    period: date = invoice["period"]
    purchase_order = f"PO-{rng.randrange(10000, 99999)}"

    # Case 9 only goes on a date that is genuinely ambiguous. Caller checks the day is 12 or
    # less before asking for it; asserted here so a future caller cannot plant a case that
    # reads unambiguously and then be scored for failing to flag it.
    if ambiguous_issue_date and issued.day > 12:
        raise ValueError(
            f"case 9 needs a day of 12 or less to be ambiguous; {issued} is not"
        )

    # Case 2's control is set larger, with a wider margin and one column fewer. Everything
    # below is expressed in terms of these, so the two layouts are one layout and cannot drift.
    s = 1.3 if large_print else 1.0
    margin = 28 * mm if large_print else 22 * mm

    buf = BytesIO()
    c = _canvas(buf)
    left, right = margin, PAGE_W - margin
    y = _letterhead(c, "INVOICE")

    y -= 22 * s
    c.setFont("Helvetica-Bold", 8.5 * s)
    c.drawString(left, y, "BILL TO")
    c.setFont("Helvetica", 10 * s)
    c.drawString(left, y - 16 * s, client["name"])
    c.setFont("Helvetica", 8.5 * s)
    c.drawString(left, y - 31 * s, client["address"])

    issue_text = ambiguous_date(issued) if ambiguous_issue_date else long_date(issued)
    due_text = ambiguous_date(due) if ambiguous_issue_date else long_date(due)
    meta = [
        ("Invoice number", invoice_number),
        ("Engagement", engagement["sow_ref"]),
        ("Purchase order", purchase_order),
        ("Billing period", f"{period:%B %Y}"),
        ("Issue date", issue_text),
        ("Payment due", due_text),
    ]

    if large_print:
        # Stacked below the bill-to block rather than beside it. At this size the two columns
        # do not both fit across the page, and a client name running into a metadata label is
        # a reading problem the scorecard would record as a vision problem -- which is exactly
        # what this document exists to rule out.
        row = y - 52 * s
        for label, value in meta:
            c.setFont("Helvetica", 8.5 * s)
            c.drawString(left, row, label)
            c.setFont("Helvetica-Bold", 9 * s)
            c.drawRightString(right, row, value)
            row -= 17 * s
        y = row - 14 * s
    else:
        for i, (label, value) in enumerate(meta):
            row = y - i * 13
            c.setFont("Helvetica", 8.5)
            c.drawRightString(right - 46 * mm, row, label)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawRightString(right, row, value)
        y -= 96

    # Column positions are measured rather than chosen as fractions of the page. A fraction
    # that clears at one type size collides at another, and "Senior consultant" landing on top
    # of an hours figure makes a document illegible for a reason no degradation caused.
    def width(text: str, size: float, bold: bool = False) -> float:
        return c.stringWidth(text, "Helvetica-Bold" if bold else "Helvetica", size)

    gap = 10 * s
    body_size, head_size = 9 * s, 8.5 * s
    amounts = [money(line["amount"], euro_format) for line in lines]
    amounts += [money(v, euro_format) for v in (subtotal, retainer_shown, net, tax)]
    amount_w = max(
        max(width(a, body_size) for a in amounts),
        width(money(total_due, euro_format), 12 * s, bold=True),
        width("AMOUNT (GBP)", head_size, bold=True),
    )
    rate_w = max([width(money(line["rate"], euro_format), body_size) for line in lines] + [width("RATE", head_size, True)])
    hours_w = max([width(money(line["hours"], euro_format), body_size) for line in lines] + [width("HOURS", head_size, True)])
    grade_w = 0.0 if large_print else max(
        [width(line["role"], body_size) for line in lines] + [width("GRADE", head_size, True)]
    )

    x_amount = right
    x_rate = x_amount - amount_w - gap
    x_hours = x_rate - rate_w - gap
    x_grade = x_hours - hours_w - gap - grade_w

    # Raise rather than render. A description running into the column beside it makes a figure
    # unreadable for a reason no degradation caused, and the scorecard has no way to tell that
    # apart from a vision failure -- it would read as the model being unable to see, on a
    # document the corpus broke. Better to fail here, where the message says what happened.
    widest = max(width(line["deliverable"], body_size) for line in lines)
    if left + widest + gap > x_grade:
        raise ValueError(
            f"invoice layout collides: '{max((l['deliverable'] for l in lines), key=len)}' is "
            f"{widest:.0f}pt wide and the next column starts {x_grade - left:.0f}pt in. "
            "Widen the margin, drop a column, or shorten the description."
        )

    c.setFont("Helvetica-Bold", head_size)
    c.drawString(left, y, "DESCRIPTION")
    if not large_print:
        c.drawString(x_grade, y, "GRADE")
    c.drawRightString(x_hours, y, "HOURS")
    c.drawRightString(x_rate, y, "RATE")
    c.drawRightString(x_amount, y, "AMOUNT (GBP)")
    y -= 6 * s
    c.setLineWidth(0.5)
    c.line(left, y, right, y)

    y -= 16 * s
    for line in lines:
        c.setFont("Helvetica", body_size)
        c.drawString(left, y, line["deliverable"])
        if not large_print:
            c.drawString(x_grade, y, line["role"])
        c.drawRightString(x_hours, y, money(line["hours"], euro_format))
        c.drawRightString(x_rate, y, money(line["rate"], euro_format))
        c.drawRightString(x_amount, y, money(line["amount"], euro_format))
        y -= 16 * s

    # The totals label column sits clear of the widest amount by construction, for the same
    # reason the table columns do: at 1.3x, "TOTAL DUE" right-aligned on a fraction of the page
    # lands on top of the figure it labels.
    totals_rule_left = min(x_hours - hours_w - gap, left + (right - left) * 0.45)
    x_label = x_rate

    y -= 4 * s
    c.line(totals_rule_left, y, right, y)

    # The crop line for case 7. Captured before anything below it is drawn, so degrade.py cuts
    # at a coordinate this function knows rather than at a fraction somebody tuned by eye.
    totals_top = y + 6 * s

    y -= 18 * s
    for label, value, emphasis in (
        ("Subtotal", subtotal, False),
        ("Less retainer applied on account", retainer_shown, False),
        ("Net", net, False),
        ("VAT at 20%", tax, False),
        ("TOTAL DUE", total_due, True),
    ):
        if emphasis:
            y -= 6 * s
            c.setLineWidth(0.8)
            c.line(totals_rule_left, y + 13 * s, right, y + 13 * s)
            c.setFont("Helvetica-Bold", 12 * s)
        else:
            c.setFont("Helvetica", body_size)
        c.drawRightString(x_label, y, label)
        c.drawRightString(x_amount, y, money(value, euro_format))
        y -= 18 * s

    y -= 26 * s
    terms_y = y
    c.setFont("Helvetica", 8 * s)
    c.drawString(left, y, f"Payment terms: {STANDARD_TERMS}.")
    c.drawString(left, y - 12 * s, FIRM_BANK)
    c.drawString(left, y - 24 * s, f"Please quote {invoice_number} and {purchase_order} with payment.")
    _footer(c)
    c.showPage()
    c.save()

    at_header = "page 1, header block, right"
    at_totals = "page 1, totals block, bottom right"
    locale_note = "rendered in 1.234,56 format and normalised" if euro_format else None

    fields = {
        "invoice_number": declare(invoice_number, at_header),
        "client_name": declare(client["name"], "page 1, bill-to block, upper left"),
        "engagement_ref": declare(engagement["sow_ref"], at_header),
        "purchase_order": declare(purchase_order, at_header),
        "billing_period": declare(f"{period:%Y-%m}", at_header),
        "issue_date": (
            declare(
                issued,
                at_header,
                "low",
                "written day-first as DD/MM/YYYY with a day of 12 or less, so it is "
                "resolvable only from the billing period stated above it",
            )
            if ambiguous_issue_date
            else declare(issued, at_header)
        ),
        "due_date": (
            declare(due, at_header, "low", "day-first, the same ambiguity as the issue date")
            if ambiguous_issue_date
            else declare(due, at_header)
        ),
        "subtotal": declare(subtotal, at_totals, note=locale_note),
        "retainer_credit": declare(retainer_shown, at_totals, note=locale_note),
        "net_amount": declare(net, at_totals, note=locale_note),
        "tax_amount": declare(tax, at_totals, note=locale_note),
        "total_due": declare(total_due, at_totals, note=locale_note),
        "payment_terms": declare(STANDARD_TERMS, "page 1, below the totals block"),
    }

    cases: list[int] = []
    if euro_format:
        cases.append(6)
    if ambiguous_issue_date:
        cases.append(9)
    if large_print:
        cases.append(2)

    if cut_off_totals:
        cases.append(7)
        # The document no longer asserts these: they are below the crop and not on the page a
        # reader is given. Absent from fields entirely, and measured as coverage rather than
        # scored as five misses.
        for gone in ("subtotal", "retainer_credit", "net_amount", "tax_amount", "payment_terms"):
            fields.pop(gone)
        fields["total_due"] = declare(
            total_due,
            at_totals,
            "unreadable",
            "the totals block is below the scanner margin and is not on the page. The figure "
            "cannot be recovered by arithmetic either, because net and VAT went with it",
        )

    if large_print:
        # Stated on every field rather than on the document, because step 8 scores flags per
        # field and a flag on any one of them is a false positive.
        for entry in fields.values():
            entry["expected_confidence"] = "high"
            entry["note"] = (
                "case 2, the control: degraded at full strength but set in large type with a "
                "generous margin, so it is legible and flagging it is the wrong answer"
            )

    return Document(
        document_id=invoice_number,
        document_type="invoice",
        filename=f"{invoice_number}.pdf",
        media=buf.getvalue(),
        fields=fields,
        mess_cases=cases,
        # terms_pt is the *last* line of the payment-terms block, not the first. Case 8's
        # annotation is placed below this anchor, and anchoring on the first line puts biro
        # across the two printed lines underneath it -- which obscures the printed terms the
        # annotation is supposed to be arguing with.
        anchors={"totals_top_pt": round(totals_top, 2), "terms_pt": round(terms_y - 24 * s, 2)},
    )


# ---------------------------------------------------------------------------- remittances


def build_remittance(
    rng: Random,
    client: dict[str, Any],
    settled: list[dict[str, Any]],
    remittance_ref: str,
    payment_date: date,
) -> Document:
    """Payment advice against one or more invoices.

    BizData has no remittance table at all -- payment is the ``paid_at`` date on the invoice
    row -- so the document, its reference and its covering letter are composed here from the
    invoices it settles.
    """
    amount_paid = sum((row["total"] for row in settled), Decimal("0.00"))

    buf = BytesIO()
    c = _canvas(buf)
    left, right = 22 * mm, PAGE_W - 22 * mm
    y = _letterhead(c, "REMITTANCE")

    y -= 22
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(left, y, "PAYMENT RECEIVED FROM")
    c.setFont("Helvetica", 10)
    c.drawString(left, y - 15, client["name"])
    c.setFont("Helvetica", 8.5)
    c.drawString(left, y - 28, client["address"])

    for i, (label, value) in enumerate(
        [
            ("Advice number", remittance_ref),
            ("Payment date", long_date(payment_date)),
            ("Method", "Bank transfer"),
        ]
    ):
        row = y - i * 13
        c.setFont("Helvetica", 8.5)
        c.drawRightString(right - 46 * mm, row, label)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(right, row, value)

    y -= 74
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(left, y, "INVOICE")
    c.drawString(left + 46 * mm, y, "ISSUED")
    c.drawRightString(right, y, "SETTLED (GBP)")
    y -= 6
    c.setLineWidth(0.5)
    c.line(left, y, right, y)

    y -= 15
    c.setFont("Helvetica", 9)
    for row in settled:
        c.drawString(left, y, row["number"])
        c.drawString(left + 46 * mm, y, long_date(row["issued"]))
        c.drawRightString(right, y, money(row["total"]))
        y -= 15

    y -= 4
    c.setLineWidth(0.8)
    c.line(left + 90 * mm, y, right, y)
    y -= 18
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(right - 46 * mm, y, "TOTAL PAID")
    c.drawRightString(right, y, money(amount_paid))

    y -= 34
    c.setFont("Helvetica", 8)
    c.drawString(left, y, "This advice confirms funds received and is not a request for payment.")
    _footer(c)
    c.showPage()
    c.save()

    at_header = "page 1, header block, right"
    return Document(
        document_id=remittance_ref,
        document_type="remittance",
        filename=f"{remittance_ref}.pdf",
        media=buf.getvalue(),
        fields={
            "remittance_ref": declare(remittance_ref, at_header),
            "client_name": declare(client["name"], "page 1, payment-from block, upper left"),
            "payment_date": declare(payment_date, at_header),
            "amount_paid": declare(amount_paid, "page 1, total paid, bottom right"),
            "invoices_settled": declare(
                [row["number"] for row in settled], "page 1, invoice table, left column"
            ),
        },
    )


# ---------------------------------------------------------------------- SOWs and amendments


def _docx_bytes(build: Any) -> bytes:
    document = DocxDocument()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    build(document)
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def _docx_heading(document: Any, title: str) -> None:
    head = document.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = head.add_run(FIRM)
    run.bold = True
    run.font.size = Pt(14)

    sub = document.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(title)
    sub_run.bold = True
    sub_run.font.size = Pt(12)


def _docx_table(document: Any, rows: list[tuple[str, str]]) -> None:
    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value
        cells[0].paragraphs[0].runs[0].bold = True


def build_sow(
    engagement: dict[str, Any],
    client: dict[str, Any],
    line_items: list[dict[str, Any]],
    blended_rate: Decimal,
) -> Document:
    """A statement of work, as Word, because that is what one arrives as.

    ``pages`` for a Word document is always ``[1, 1]``. A docx has no pagination until
    something lays it out, and inventing a page number here would be inventing evidence -- the
    QA sheet cites where a figure was read, and "page 3" of a file with no pages is a citation
    a human cannot go and check.
    """
    fee_label = "Fixed fee" if engagement["fee_type"] == "fixed" else "Time and materials"

    def build(document: Any) -> None:
        _docx_heading(document, "STATEMENT OF WORK")
        document.add_paragraph()
        _docx_table(
            document,
            [
                ("SOW reference", engagement["sow_ref"]),
                ("Client", client["name"]),
                ("Client address", client["address"]),
                ("Engagement", engagement["name"]),
                ("Commencement date", long_date(engagement["start_date"])),
                ("Completion date", long_date(engagement["end_date"])),
                ("Fee basis", fee_label),
                ("Ceiling hours", f"{engagement['ceiling_hours']:,.2f}"),
                ("Ceiling amount", f"GBP {money(engagement['ceiling_amount'])}"),
                ("Blended rate", f"GBP {money(blended_rate)} per hour"),
                ("Payment terms", STANDARD_TERMS),
            ],
        )
        document.add_paragraph()
        document.add_paragraph("1.  Scope of work").runs[0].bold = True
        document.add_paragraph(
            f"{FIRM} shall provide the deliverables set out below in respect of "
            f"{engagement['name']} for {client['name']}. Work shall not exceed the ceiling "
            "hours or the ceiling amount stated above without a written amendment executed by "
            "both parties."
        )

        document.add_paragraph()
        document.add_paragraph("2.  Deliverables").runs[0].bold = True
        table = document.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ("Deliverable", "Hours", "Amount (GBP)")):
            cell.text = text
            cell.paragraphs[0].runs[0].bold = True
        for item in line_items:
            cells = table.add_row().cells
            cells[0].text = item["deliverable"]
            cells[1].text = f"{item['hours_budgeted']:,.2f}"
            cells[2].text = money(item["amount"])

        document.add_paragraph()
        document.add_paragraph("3.  Invoicing").runs[0].bold = True
        document.add_paragraph(
            f"Invoices shall be raised monthly in arrears and are payable {STANDARD_TERMS.lower()}. "
            "A retainer held on account shall be applied against each invoice until exhausted."
        )
        document.add_paragraph()
        document.add_paragraph("Signed for and on behalf of the parties:")
        document.add_paragraph(f"{FIRM}  ..................................    Date  ............")
        document.add_paragraph(f"{client['name']}  ..................................    Date  ............")

    at_summary = "page 1, summary table"
    return Document(
        document_id=engagement["sow_ref"],
        document_type="sow",
        filename=f"{client['name']} - SOW - {engagement['sow_ref']} FINAL v3 (signed).docx",
        media=_docx_bytes(build),
        fields={
            "sow_ref": declare(engagement["sow_ref"], at_summary),
            "client_name": declare(client["name"], at_summary),
            "engagement_name": declare(engagement["name"], at_summary),
            # `fee_label`, not `engagement["fee_type"]`. BizData's internal value is `fixed`;
            # the page says `Fixed fee`, and the page is the ground truth. Declaring the enum
            # meant the manifest expected a normalisation that no Skill and no part of the
            # schema ever asked for -- so it was not a contract, it was my internal
            # representation, and 18 correct readings were scored against it.
            "fee_type": declare(fee_label, at_summary),
            "ceiling_hours": declare(engagement["ceiling_hours"], at_summary),
            "ceiling_amount": declare(engagement["ceiling_amount"], at_summary),
            "start_date": declare(engagement["start_date"], at_summary),
            "end_date": declare(engagement["end_date"], at_summary),
            "payment_terms": declare(STANDARD_TERMS, at_summary),
        },
    )


def build_amendment(
    rng: Random,
    engagement: dict[str, Any],
    client: dict[str, Any],
    amendment_ref: str,
    effective: date,
) -> Document:
    """Mess case 4. The ceiling moves, and the SOW behind it is now stale.

    Both documents stay in the folder, which is the point: the amendment has to be recognised
    as superseding rather than as a second engagement, and the original's ceiling must not
    reach the asset. Nothing in the filename says so -- the SOW is the one marked FINAL.
    """
    uplift = Decimal(rng.randrange(112, 145)) / 100
    revised_hours = (engagement["ceiling_hours"] * uplift).quantize(CENTS)
    revised_amount = (engagement["ceiling_amount"] * uplift).quantize(CENTS)
    revised_end = engagement["end_date"] + timedelta(days=rng.randrange(30, 120))

    def build(document: Any) -> None:
        _docx_heading(document, f"AMENDMENT {amendment_ref.rsplit('-', 1)[-1]} TO STATEMENT OF WORK")
        document.add_paragraph()
        _docx_table(
            document,
            [
                ("Amendment reference", amendment_ref),
                ("Amends SOW", engagement["sow_ref"]),
                ("Client", client["name"]),
                ("Engagement", engagement["name"]),
                ("Effective date", long_date(effective)),
            ],
        )
        document.add_paragraph()
        document.add_paragraph(
            f"This amendment varies statement of work {engagement['sow_ref']} with effect from "
            f"{long_date(effective)}. All other terms of the original statement of work remain "
            "in full force. Where this amendment and the original conflict, this amendment "
            "prevails."
        )
        document.add_paragraph()
        document.add_paragraph("1.  Revised ceiling").runs[0].bold = True
        table = document.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ("", "Original", "Revised")):
            cell.text = text
            cell.paragraphs[0].runs[0].bold = True
        for label, was, now in (
            ("Ceiling hours", f"{engagement['ceiling_hours']:,.2f}", f"{revised_hours:,.2f}"),
            ("Ceiling amount (GBP)", money(engagement["ceiling_amount"]), money(revised_amount)),
            ("Completion date", long_date(engagement["end_date"]), long_date(revised_end)),
        ):
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = was
            cells[2].text = now

        document.add_paragraph()
        document.add_paragraph("Signed for and on behalf of the parties:")
        document.add_paragraph(f"{FIRM}  ..................................    Date  ............")

    at_summary = "page 1, revised ceiling table"
    return Document(
        document_id=amendment_ref,
        document_type="sow_amendment",
        filename=f"{client['name']} - {engagement['sow_ref']} amendment {amendment_ref.rsplit('-', 1)[-1]}.docx",
        media=_docx_bytes(build),
        supersedes=engagement["sow_ref"],
        mess_cases=[4],
        fields={
            "amendment_ref": declare(amendment_ref, "page 1, summary table"),
            "sow_ref": declare(engagement["sow_ref"], "page 1, summary table"),
            "client_name": declare(client["name"], "page 1, summary table"),
            "effective_date": declare(effective, "page 1, summary table"),
            "engagement_name": declare(engagement["name"], "page 1, summary table"),
            # The Original column, declared because it is *printed*. The amendment shows a
            # three-column table -- label, Original, Revised -- so all six figures are on the
            # page in labelled cells, and only the revised three were declared. A run that read
            # the Original column scored 24 fields as `hallucinated`, the worst class on the
            # scorecard, for reading a labelled number off a table.
            #
            # Declaring them also sharpens case 4 rather than weakening it. The case is that
            # the amendment supersedes: the stale ceiling must not reach the asset. That is a
            # question about which figure ends up on the Engagements tab, and it is a more
            # honest test when the document offers both and the pipeline has to choose.
            "ceiling_hours": declare(engagement["ceiling_hours"], at_summary),
            "ceiling_amount": declare(engagement["ceiling_amount"], at_summary),
            "end_date": declare(engagement["end_date"], at_summary),
            "revised_ceiling_hours": declare(revised_hours, at_summary),
            "revised_ceiling_amount": declare(revised_amount, at_summary),
            "revised_end_date": declare(revised_end, at_summary),
        },
    )


# ----------------------------------------------------------------------------- statements


def build_statement(
    client: dict[str, Any],
    statement_ref: str,
    statement_date: date,
    period: date,
    outstanding: list[dict[str, Any]],
) -> Document:
    """A statement of account, as a workbook, because that is how one is usually sent.

    A spreadsheet in the inbox is a different extraction problem from a PDF -- there is no
    layout to read, only cells -- and a pipeline that only knows how to look at pages will
    either skip it or hand it to vision it does not need.
    """
    total = sum((row["total"] for row in outstanding), Decimal("0.00"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    bold = XlFont(bold=True)

    ws["A1"] = FIRM
    ws["A1"].font = XlFont(bold=True, size=14)
    ws["A2"] = ", ".join(FIRM_ADDR)
    ws["A4"] = "STATEMENT OF ACCOUNT"
    ws["A4"].font = XlFont(bold=True, size=12)

    for row, (label, value) in enumerate(
        [
            ("Statement reference", statement_ref),
            ("Account", client["name"]),
            ("Address", client["address"]),
            ("Statement date", long_date(statement_date)),
            ("Billing period", f"{period:%B %Y}"),
        ],
        start=6,
    ):
        ws.cell(row=row, column=1, value=label).font = bold
        ws.cell(row=row, column=2, value=value)

    header = 12
    for col, text in enumerate(
        ["Invoice", "Issued", "Due", "Status", "Amount (GBP)"], start=1
    ):
        ws.cell(row=header, column=col, value=text).font = bold
    for offset, row in enumerate(outstanding, start=1):
        ws.cell(row=header + offset, column=1, value=row["number"])
        ws.cell(row=header + offset, column=2, value=long_date(row["issued"]))
        ws.cell(row=header + offset, column=3, value=long_date(row["issued"] + timedelta(days=30)))
        ws.cell(row=header + offset, column=4, value="Outstanding")
        ws.cell(row=header + offset, column=5, value=float(row["total"]))

    last = header + len(outstanding)
    ws.cell(row=last + 2, column=4, value="TOTAL OUTSTANDING").font = bold
    # A formula, and openpyxl never calculates one -- so these two cells hold `=SUM(...)` with
    # **no cached result**, and the figure is genuinely not in the file. Excel would compute it
    # on open; a reader parsing the bytes gets nothing.
    #
    # Kept, rather than replaced with a literal. A spreadsheet whose totals were never
    # calculated is an ordinary thing to find in a client's folder, and it is the same lesson as
    # case 7 reached by a completely different route: a figure that is referred to but not
    # present. The fields below are declared `unreadable` to match, which is what makes it a
    # real test instead of a trap.
    #
    # It was a trap first. They were declared `high`, the first Cowork run correctly reported
    # them unreadable, and being right cost it half its flag precision.
    ws.cell(row=last + 2, column=5, value=f"=SUM(E{header + 1}:E{last})").font = bold
    ws.cell(row=last + 3, column=4, value="INVOICES OUTSTANDING").font = bold
    ws.cell(row=last + 3, column=5, value=f"=COUNT(E{header + 1}:E{last})").font = bold

    for column, width in zip("ABCDE", (22, 26, 18, 16, 16)):
        ws.column_dimensions[column].width = width

    buf = BytesIO()
    wb.save(buf)

    at_header = "Statement tab, header block, cells A6:B10"
    return Document(
        document_id=statement_ref,
        document_type="statement",
        filename=f"Statement of account - {client['name']} - {period:%b %Y}.xlsx",
        media=buf.getvalue(),
        fields={
            "statement_ref": declare(statement_ref, at_header),
            "client_name": declare(client["name"], at_header),
            "statement_date": declare(statement_date, at_header),
            "billing_period": declare(f"{period:%Y-%m}", at_header),
            # The value is still the truth -- the figure the statement would show if anything
            # had calculated it -- and expected_confidence says a reader cannot get to it. The
            # same shape case 7 uses, where the manifest knows the cropped total and expects
            # the run to refuse it.
            "total_outstanding": declare(
                total,
                f"Statement tab, cell E{last + 2}",
                "unreadable",
                "the cell holds an uncalculated =SUM() with no cached result, so no figure is "
                "stored in the file; it can be recomputed from the rows but it is not asserted",
            ),
            "invoice_count": declare(
                len(outstanding),
                f"Statement tab, cell E{last + 3}",
                "unreadable",
                "the cell holds an uncalculated =COUNT() with no cached result; the rows can be "
                "counted but the document states no total",
            ),
        },
    )


# --------------------------------------------------------------------------- out of scope


def _receipt_pdf(
    vendor: str, address: str, when: date, lines: list[tuple[str, Decimal]], total: Decimal
) -> bytes:
    """A small thermal-till receipt on an A4 page. Nothing about it says consultancy."""
    buf = BytesIO()
    c = _canvas(buf)
    x, y = 26 * mm, PAGE_H - 40 * mm

    c.setFont("Courier-Bold", 11)
    c.drawString(x, y, vendor)
    c.setFont("Courier", 8)
    c.drawString(x, y - 12, address)
    c.drawString(x, y - 24, f"{when:%d/%m/%Y}   14:0{when.day % 9}")
    c.drawString(x, y - 40, "-" * 34)

    y -= 54
    for label, amount in lines:
        c.setFont("Courier", 9)
        c.drawString(x, y, label[:24])
        c.drawRightString(x + 62 * mm, y, f"{amount:.2f}")
        y -= 12

    c.drawString(x, y - 4, "-" * 34)
    c.setFont("Courier-Bold", 10)
    c.drawString(x, y - 20, "TOTAL")
    c.drawRightString(x + 62 * mm, y - 20, f"{total:.2f}")
    c.setFont("Courier", 8)
    c.drawString(x, y - 38, "CARD  VISA ****4417   CONTACTLESS")
    c.drawString(x, y - 50, "VAT INCLUDED WHERE APPLICABLE")
    c.drawString(x, y - 68, "THANK YOU - PLEASE RETAIN FOR YOUR RECORDS")

    c.showPage()
    c.save()
    return buf.getvalue()


def build_out_of_scope(rng: Random, which: int, when: date) -> Document:
    """Mess case 10. Four things that ended up in the folder and are not the firm's paper.

    ``out_of_scope`` is a correct answer and not a failure to classify, and the schema gives
    these nowhere to be force-fitted to: no identity, no supersession, and no fields. A
    pipeline that cannot say "this isn't one of mine" will confidently put a sandwich in the
    accounts receivable.

    Four rather than one, and deliberately not four copies of the same joke. A lunch receipt is
    easy. An expense claim carries a total, a date and a reference and looks a great deal like
    an invoice; a supplier email carries an amount in its body; a car park ticket is a scrap.
    """
    if which == 0:
        media = _receipt_pdf(
            "THE GRESHAM KITCHEN",
            "21 Gresham Street, London EC2V",
            when,
            [("Soup of the day", Decimal("6.50")), ("Chicken schnitzel", Decimal("14.75")),
             ("Sparkling water", Decimal("3.20")), ("Flat white x2", Decimal("6.80"))],
            Decimal("31.25"),
        )
        return Document(None, "out_of_scope", f"receipt_{when:%Y%m%d}_lunch.pdf", media, {}, mess_cases=[10])

    if which == 1:
        media = _receipt_pdf(
            "CITY OF LONDON PARKING",
            "Bay 41, Aldermanbury Square",
            when,
            [("Tariff B  2h 30m", Decimal("11.40"))],
            Decimal("11.40"),
        )
        return Document(None, "out_of_scope", f"IMG_{4100 + which:04d}.pdf", media, {}, mess_cases=[10])

    if which == 2:
        # An expense claim. The one that is genuinely hard: it has a reference, a date, a
        # client name and a total, and every heuristic that says "reference plus total means
        # invoice" puts it in the accounts receivable.
        buf = BytesIO()
        c = _canvas(buf)
        left = 22 * mm
        y = _letterhead(c, "EXPENSE CLAIM")
        y -= 26
        c.setFont("Helvetica", 9)
        for i, (label, value) in enumerate(
            [
                ("Claim reference", f"EXP-2026-{rng.randrange(100, 999)}"),
                ("Claimant", "M. Ashworth-Doyle, Principal"),
                ("Period", f"{when:%B %Y}"),
                ("Cost centre", "Delivery - travel & subsistence"),
            ]
        ):
            c.drawString(left, y - i * 14, f"{label}:  {value}")
        y -= 78
        c.setFont("Helvetica-Bold", 9)
        c.drawString(left, y, "ITEM")
        c.drawRightString(PAGE_W - 22 * mm, y, "AMOUNT (GBP)")
        y -= 14
        c.setFont("Helvetica", 9)
        for label, amount in (
            ("Rail, London to Leeds return", Decimal("218.40")),
            ("Hotel, two nights", Decimal("342.00")),
            ("Subsistence", Decimal("61.15")),
        ):
            c.drawString(left, y, label)
            c.drawRightString(PAGE_W - 22 * mm, y, money(amount))
            y -= 14
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y - 10, "TOTAL CLAIMED")
        c.drawRightString(PAGE_W - 22 * mm, y - 10, money(Decimal("621.55")))
        c.setFont("Helvetica", 8)
        c.drawString(left, y - 34, "Reimbursed through payroll. This is not a client-billable document.")
        _footer(c)
        c.showPage()
        c.save()
        return Document(None, "out_of_scope", "expenses August (signed).pdf", buf.getvalue(), {}, mess_cases=[10])

    # A supplier email, as .eml. Stdlib email, so nothing is added to requirements.txt for one
    # document. It carries an amount and a reference in its body, which is enough for a
    # classifier reading text without looking at what kind of thing it is reading.
    message = email.message.EmailMessage(policy=email.policy.SMTP)
    message["From"] = "accounts@meridian-print.co.uk"
    message["To"] = "ap@hollingsworth-advisory.co.uk"
    message["Subject"] = "Your Meridian Print account - statement 88214 now available"
    message["Date"] = email.utils.format_datetime(
        __import__("datetime").datetime.combine(when, __import__("datetime").time(9, 14))
    )
    message["Message-ID"] = f"<88214.{when:%Y%m%d}@meridian-print.co.uk>"
    message.set_content(
        "Dear Customer,\n\n"
        "Your statement for the period is now available in the Meridian Print portal.\n"
        "Balance outstanding: GBP 1,284.60. Reference 88214.\n\n"
        "Payment is due within 14 days. Please do not reply to this address.\n\n"
        "Meridian Print Services Ltd, Unit 7, Bermondsey Trading Estate, London SE16 3LL\n"
        "Registered in England 08841902. VAT GB 174 4420 51\n"
    )
    return Document(
        None,
        "out_of_scope",
        "FW_ Your Meridian Print account.eml",
        message.as_bytes(),
        {},
        mess_cases=[10],
    )


# ------------------------------------------------------------------------------- assembly


def concat_pdfs(parts: list[bytes]) -> bytes:
    """Mess case 3. Two documents, one file, and a page range that has to be read.

    pypdfium2 rather than a second PDF library: it is already a dependency because step 3
    rasterises with it, and adding pypdf to concatenate two files would be a dependency for
    one line.
    """
    out = pdfium.PdfDocument.new()
    sources = []
    try:
        for raw in parts:
            source = pdfium.PdfDocument(raw)
            sources.append(source)
            out.import_pages(source)
        buf = BytesIO()
        out.save(buf)
        return buf.getvalue()
    finally:
        for source in sources:
            source.close()
        out.close()


def page_count(pdf_bytes: bytes) -> int:
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(document)
    finally:
        document.close()
