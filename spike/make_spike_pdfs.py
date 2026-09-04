#!/usr/bin/env python3
"""Step 0's test documents: a scan that can only be read by looking at it.

    spike/make_spike_pdfs.py                one scan and one text-layer control
    spike/make_spike_pdfs.py --count 10     ten scans, distinct figures, for question 3

Step 0 asks whether a Cowork subagent can read an image-only PDF. The thing that makes
the question hard to answer honestly is a subagent that *appears* to read the scan while
actually inferring the answer from the filename, from the prompt, or from a sibling
document in the same folder. A right answer arrived at that way is indistinguishable from
a right answer that was read, and it would send the whole build down a design that does
not work.

So every figure planted here is unguessable and appears nowhere but in the pixels:

  * The filename is a scanner default. It carries no invoice number, no client name and
    no date that appears in the document.
  * The verification code is random. It cannot be computed, inferred or guessed from
    anything else on the page -- it is the probe that settles the question.
  * The total is five digits and not round, so a lucky guess is not available.
  * The text-layer control carries a *different* client and different figures, so an
    answer sourced from the wrong file is visibly wrong rather than coincidentally right.

The answer key is written to spike/answer-key.json, deliberately outside spike/inbox/.
Point Cowork at the inbox and at nothing above it, or the run grades itself.

Requires: reportlab, pypdfium2, Pillow. The scan is made the same way step 3 will make
its 22 -- rasterise with pypdfium2, degrade with Pillow, save back as an image-only PDF --
so a surprise here is cheaper now than on day two.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

try:
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"error: {exc.name} is not installed.\n"
        "  python3.12 -m venv .venv && .venv/bin/pip install reportlab pypdfium2 Pillow"
    )

HERE = Path(__file__).resolve().parent
INBOX = HERE / "inbox"
ANSWER_KEY = HERE / "answer-key.json"

PAGE_W, PAGE_H = A4

# An invented firm. BizData's portfolio has clients but no identity for the consultancy
# issuing the work -- no name, no address, no bank details -- so the letterhead is made up
# here and will be made up again, the same way, in the corpus generator.
FIRM = "Hollingsworth Advisory Partners LLP"
FIRM_ADDR = ["4th Floor, Cranmere House", "18-22 Gresham Street", "London EC2V 7AN"]

# Clients and deliverables in BizData's own vocabulary, so the spike documents look like
# the corpus that will replace them rather than like a different project.
CLIENTS = [
    ("Alderwick Health Holdings", "Kestrel Wharf, Bristol BS1 6QH"),
    ("Granthorpe Financial Partners", "12 Kingsway, Leeds LS1 2HQ"),
    ("Dunmarch Maritime Group", "Harbour Court, Southampton SO14 3TJ"),
    ("Pentland Insurance Corporation", "9 St Andrew Square, Edinburgh EH2 2AF"),
    ("Fernhollow Utilities Collective", "Ridgeway Point, Cardiff CF10 1EP"),
    ("Merrivale Media Corporation", "1 Quay Street, Manchester M3 3JE"),
    ("Havelock Energy Holdings", "Beacon House, Aberdeen AB10 1YG"),
    ("Stonebridge Municipal Holdings", "Civic Centre, Coventry CV1 5RR"),
    ("Oakhaven Retail Group", "The Exchange, Nottingham NG1 2DT"),
    ("Northfield Utilities Collective", "Trinity Gate, Sheffield S1 2JB"),
    ("Vantage Financial Collective", "60 Broad Quay, Bristol BS1 4DA"),
]

DELIVERABLES = [
    "Current state assessment",
    "Target operating model",
    "Data migration plan",
    "Integration build",
    "Cutover rehearsal",
    "Solution design",
    "Test strategy",
    "Vendor evaluation",
    "Benefits case",
    "Go-live readiness review",
]

# Rates carry odd cents, and hours are recorded in tenths, so that every amount on the
# page lands on messy cents and the total cannot be arrived at by guessing a round number.
# A total ending .00 is a figure a model can produce without having read anything.
ROLES = [
    ("Principal", Decimal("337.50")),
    ("Senior consultant", Decimal("264.75")),
    ("Consultant", Decimal("196.25")),
    ("Analyst", Decimal("148.80")),
]


# --------------------------------------------------------------------- the figures


# Four characters are excluded, and each exclusion was measured rather than assumed.
#
# I and O, because capital I is the *same bitmap* as lowercase l in Helvetica-Bold -- same
# 2.502pt advance, same 11x54 ink box at 600 dpi, zero differing pixels -- and O sits close
# enough to 0 to want the same treatment. No resolution separates I from l, so a reader who
# lands on the right character has inferred it from the code being uppercase rather than
# read it. Found by the step-0 run, where both readers flagged the ambiguity themselves.
#
# C and G, because they are the one pair in the remaining alphabet that does not separate on
# width: 7.8% apart with 77% ink overlap, against 13% or more for S/5, B/8, Z/2, G/6 and
# D/0. G's crossbar is what distinguishes them and it is the first thing a degraded scan
# fills in. The fixed "VC-" prefix still carries a C, which is fine -- it is a known literal
# and no probe depends on reading it.
#
# The point of excluding them is not tidiness. Question 3 asks whether answer quality decays
# as the fan-out widens, and that question is unanswerable if a wrong answer might instead be
# a glyph nobody could have read. Every remaining character fails only under width.
CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "IOCG")


def verification_code(rng: random.Random) -> str:
    """A token with no structure to infer and no arithmetic behind it.

    This is the probe that actually settles question 2. A total can in principle be
    reconstructed from line items, and line items can in principle be guessed at from a
    plausible-looking invoice; this cannot be arrived at by any route except reading it.
    """
    block = CODE_ALPHABET
    return (
        "VC-"
        + "".join(rng.choice(block) for _ in range(4))
        + "-"
        + "".join(rng.choice(string.digits) for _ in range(4))
        + "-"
        + "".join(rng.choice(block) for _ in range(4))
    )


def build_document(rng: random.Random, client_row: tuple[str, str] | None = None) -> dict:
    """One invoice's declared figures, before anything is rendered.

    Written first and rendered second, which is the same order the corpus generator uses
    at step 2: the fields are the truth and the page is a rendering of them, never the
    other way round.

    ``client_row`` is supplied by the caller so a batch can hold distinct clients. Question
    3 fans out over ten scans and asks whether all ten came back correct; two scans sharing
    a client is two answers that cannot be told apart when one is attributed to the wrong
    document, which is exactly the failure widening the fan-out is supposed to surface.
    """
    client, client_addr = client_row if client_row is not None else rng.choice(CLIENTS)
    issued = date(2026, 8, 1) + timedelta(days=rng.randint(0, 27))

    lines = []
    for deliverable in rng.sample(DELIVERABLES, rng.randint(3, 4)):
        role, rate = rng.choice(ROLES)
        hours = Decimal(rng.randrange(225, 1300)) / 10  # tenths, the granularity BizData uses
        lines.append(
            {
                "deliverable": deliverable,
                "role": role,
                "hours": hours,
                "rate": rate,
                "amount": (hours * rate).quantize(Decimal("0.01")),
            }
        )

    subtotal = sum((line["amount"] for line in lines), Decimal("0.00"))

    # A retainer draw-down, which is the honest way to make the total something other than
    # a number you get by adding up the column. It has to be read; it cannot be derived.
    retainer = (Decimal(rng.randrange(120000, 980000)) / 100).quantize(Decimal("0.01"))
    net = subtotal - retainer
    vat = (net * Decimal("0.20")).quantize(Decimal("0.01"))
    total = net + vat

    return {
        "client": client,
        "client_addr": client_addr,
        "invoice_number": f"INV-2026-{rng.randrange(100, 9999):04d}",
        "purchase_order": f"PO-{rng.randrange(10000, 99999)}",
        "engagement": f"SOW-2026-{rng.randrange(1, 40):03d}",
        "issued": issued,
        "due": issued + timedelta(days=30),
        "lines": lines,
        "subtotal": subtotal,
        "retainer": retainer,
        "net": net,
        "vat": vat,
        "total": total,
        "verification_code": verification_code(rng),
    }


# --------------------------------------------------------------------- the rendering


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_invoice(doc: dict) -> bytes:
    """One page of A4, laid out as an invoice a finance team would recognise."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    left, right = 22 * mm, PAGE_W - 22 * mm
    y = PAGE_H - 24 * mm

    c.setFont("Helvetica-Bold", 15)
    c.drawString(left, y, FIRM)
    c.setFont("Helvetica", 8.5)
    for i, line in enumerate(FIRM_ADDR):
        c.drawString(left, y - 15 - i * 11, line)

    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(right, y, "INVOICE")

    y -= 56
    c.setLineWidth(0.8)
    c.line(left, y, right, y)

    y -= 22
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(left, y, "BILL TO")
    c.setFont("Helvetica", 10)
    c.drawString(left, y - 15, doc["client"])
    c.setFont("Helvetica", 8.5)
    c.drawString(left, y - 28, doc["client_addr"])

    meta = [
        ("Invoice number", doc["invoice_number"]),
        ("Engagement", doc["engagement"]),
        ("Purchase order", doc["purchase_order"]),
        ("Issue date", doc["issued"].strftime("%d %B %Y")),
        ("Payment due", doc["due"].strftime("%d %B %Y")),
    ]
    for i, (label, value) in enumerate(meta):
        row = y - i * 13
        c.setFont("Helvetica", 8.5)
        c.drawRightString(right - 46 * mm, row, label)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(right, row, value)

    y -= 84

    # Description, grade, then three right-aligned numeric columns. The grade column is wide
    # enough for "Senior consultant" at 9pt with clearance before the hours figure -- at
    # 74mm the two collide, and a total that touches its neighbour is a reading problem the
    # spike would wrongly score as a vision problem.
    cols = (left, left + 68 * mm, left + 110 * mm, left + 134 * mm, right)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(cols[0], y, "DESCRIPTION")
    c.drawString(cols[1], y, "GRADE")
    c.drawRightString(cols[2], y, "HOURS")
    c.drawRightString(cols[3], y, "RATE")
    c.drawRightString(cols[4], y, "AMOUNT (GBP)")
    y -= 6
    c.setLineWidth(0.5)
    c.line(left, y, right, y)

    y -= 15
    c.setFont("Helvetica", 9)
    for line in doc["lines"]:
        c.drawString(cols[0], y, line["deliverable"])
        c.drawString(cols[1], y, line["role"])
        c.drawRightString(cols[2], y, f"{line['hours']:,.1f}")
        c.drawRightString(cols[3], y, money(line["rate"]))
        c.drawRightString(cols[4], y, money(line["amount"]))
        y -= 15

    y -= 4
    c.line(cols[2] - 10 * mm, y, right, y)
    y -= 16

    totals = [
        ("Subtotal", doc["subtotal"], False),
        ("Less retainer applied on account", doc["retainer"], False),
        ("Net", doc["net"], False),
        ("VAT at 20%", doc["vat"], False),
        ("TOTAL DUE", doc["total"], True),
    ]
    for label, value, emphasis in totals:
        if emphasis:
            y -= 5
            c.setLineWidth(0.8)
            c.line(cols[2] - 10 * mm, y + 12, right, y + 12)
            c.setFont("Helvetica-Bold", 12)
        else:
            c.setFont("Helvetica", 9)
        prefix = "-" if label.startswith("Less") else ""
        c.drawRightString(cols[3], y, label)
        c.drawRightString(cols[4], y, f"{prefix}{money(value)}")
        y -= 17

    y -= 26
    c.setFont("Helvetica", 8)
    c.drawString(left, y, "Payment by transfer within 30 days. Late payment interest applies at 8% above base rate.")
    c.drawString(left, y - 11, f"Please quote {doc['invoice_number']} and the purchase order number with payment.")

    # Bottom of the page on purpose. If a subagent reads only the top of a scan, or only
    # the region a heuristic thinks an invoice keeps its total in, this is what misses.
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, 26 * mm, f"Document verification code: {doc['verification_code']}")
    c.setFont("Helvetica", 7.5)
    c.drawString(left, 26 * mm - 11, "Quote this code when querying this invoice. It is unique to this document.")

    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------- the degradation


def degrade(pdf_bytes: bytes, rng: random.Random, dpi: int = 200) -> bytes:
    """Rasterise, skew, speckle, grayscale, and save back with no text layer.

    Deliberately not pdf2image, which shells out to poppler: a system binary in the
    dependency chain of a public repo is an install failure waiting for somebody else's
    machine. pypdfium2 rasterises and Pillow writes the PDF, so the whole path is wheels.

    Degraded until it looks photocopied and no further. A scan nobody can read tests
    nothing -- the run fails and the failure says the corpus is broken rather than the
    pipeline is.
    """
    pdf = pdfium.PdfDocument(pdf_bytes)
    pages = []

    for index in range(len(pdf)):
        image = pdf[index].render(scale=dpi / 72).to_pil().convert("L")

        # A page fed slightly crooked. A fraction of a degree, not a tilt.
        image = image.rotate(
            rng.uniform(-0.9, 0.9), resample=Image.BICUBIC, fillcolor=255, expand=False
        )

        # Scanner optics, then sensor grain. The grain is blended rather than added so it
        # sits in the greys as well as the whites, which is what a photocopy looks like.
        image = image.filter(ImageFilter.GaussianBlur(0.4))
        grain = Image.effect_noise(image.size, 14).convert("L")
        image = Image.blend(image, grain, 0.10)

        # Dust on the platen.
        draw = ImageDraw.Draw(image)
        w, h = image.size
        for _ in range(int(w * h / 26000)):
            x, y = rng.randrange(w), rng.randrange(h)
            r = rng.choice((0, 0, 0, 1, 1, 2))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=rng.randrange(40, 130))

        # A photocopier's uneven exposure: one edge fractionally darker than the other.
        gradient = Image.linear_gradient("L").resize(image.size).rotate(
            rng.choice((0, 90, 180, 270))
        )
        image = ImageChops.multiply(image, Image.blend(Image.new("L", image.size, 255), gradient, 0.06))

        image = ImageOps.autocontrast(image, cutoff=0.4)
        pages.append(image)

    pdf.close()

    out = BytesIO()
    pages[0].save(out, "PDF", resolution=float(dpi), save_all=True, append_images=pages[1:])
    return out.getvalue()


def text_layer(pdf_bytes: bytes) -> str:
    """Everything a text extractor can get out of a PDF, for confirming there is nothing.

    Step 0's first instruction is to confirm the scan really has no text layer before
    trusting anything downstream, because every finding after it is conditional on that.
    """
    pdf = pdfium.PdfDocument(pdf_bytes)
    found = []
    for index in range(len(pdf)):
        page = pdf[index]
        textpage = page.get_textpage()
        found.append(textpage.get_text_range())
        textpage.close()
    pdf.close()
    return "".join(found).strip()


# --------------------------------------------------------------------- entry point


def answer_for(doc: dict, filename: str, kind: str) -> dict:
    return {
        "filename": filename,
        "kind": kind,
        "probes": {
            "verification_code": doc["verification_code"],
            "total_due": money(doc["total"]),
            "invoice_number": doc["invoice_number"],
            "purchase_order": doc["purchase_order"],
            "client": doc["client"],
            "issue_date": doc["issued"].isoformat(),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--count", type=int, default=1, help="Scans to write. 10 answers question 3.")
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    INBOX.mkdir(parents=True, exist_ok=True)
    for stale in INBOX.glob("*.pdf"):
        stale.unlink()

    answers = []

    # One client each, drawn without replacement, so every document in the batch is
    # distinguishable by something other than its figures. The control needs one too, hence
    # count + 1. Past the roster it falls back to sampling with replacement and says so.
    wanted = args.count + 1
    if wanted <= len(CLIENTS):
        roster = rng.sample(CLIENTS, wanted)
    else:
        print(
            f"warning: {wanted} documents against {len(CLIENTS)} clients, so clients repeat "
            "and a misattributed answer may not be detectable",
            file=sys.stderr,
        )
        roster = [rng.choice(CLIENTS) for _ in range(wanted)]

    # The scans. Scanner-default filenames, minutes apart, carrying nothing: no invoice
    # number, no client, and a date that is not the invoice's issue date.
    stamp = 113255
    for index in range(args.count):
        doc = build_document(rng, roster[index])
        name = f"scan_20260814_{stamp:06d}.pdf"
        stamp += rng.randrange(140, 900)

        scanned = degrade(render_invoice(doc), rng, args.dpi)
        (INBOX / name).write_bytes(scanned)

        leaked = text_layer(scanned)
        if leaked:
            print(f"error: {name} still has a text layer ({len(leaked)} chars)", file=sys.stderr)
            return 1
        answers.append(answer_for(doc, name, "image-only"))

    # The control, with a text layer. Different client and different figures, so an answer
    # taken from the wrong file reads as wrong rather than as coincidentally right.
    control = build_document(rng, roster[-1])
    control_name = f"{control['invoice_number']}.pdf"
    control_bytes = render_invoice(control)
    (INBOX / control_name).write_bytes(control_bytes)
    if not text_layer(control_bytes):
        print(f"error: {control_name} has no text layer and is supposed to", file=sys.stderr)
        return 1
    answers.append(answer_for(control, control_name, "text-layer"))

    ANSWER_KEY.write_text(
        json.dumps(
            {
                "seed": args.seed,
                "dpi": args.dpi,
                "inbox": str(INBOX),
                "warning": "Never put this file, or anything above inbox/, in front of Cowork.",
                "documents": answers,
            },
            indent=2,
        )
        + "\n"
    )

    print(f"{INBOX}")
    for entry in answers:
        size = (INBOX / entry["filename"]).stat().st_size
        print(
            f"  {entry['filename']:<34} {entry['kind']:<11} {size / 1024:6.0f} KB"
            f"  {entry['probes']['verification_code']}  {entry['probes']['total_due']:>12}"
        )
    print(f"\nanswer key: {ANSWER_KEY}  (outside the inbox, keep it that way)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
