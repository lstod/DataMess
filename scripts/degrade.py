#!/usr/bin/env python3
"""Turn twenty-two of the seventy-four into scans, and plant cases 1, 2, 7 and 8.

    scripts/degrade.py                    the plan in corpus/manifest.json, on seed 42
    scripts/degrade.py --seed 9001
    scripts/degrade.py --preview out/     write PNGs of the four planted cases to look at

This executes; it does not decide. Which files become scans, where case 7 crops and what case
8's annotation says are all in ``corpus/manifest.json`` under each document's ``degradation``
block, written by ``corpus.py`` in the same pass that wrote the documents. Nothing here chooses
anything the manifest has not already recorded, because a choice made here would be a fact
about the corpus that its declared truth does not know.

The path is step 0's, unchanged
-------------------------------
pypdfium2 rasterises at 200 DPI, Pillow rotates a fraction of a degree, blurs, grains, dusts,
shades unevenly, and saves back as an image-only PDF. Every step of it was run against the real
product at step 0 and the answer was that a Cowork subagent can read the result -- five of five
probes, then forty-four of forty-four field values over eleven documents. So this is proven
code being reused rather than new code being written, and a surprise in it would have surfaced
in August rather than here.

Deliberately **not** ``pdf2image``, which shells out to poppler. A system binary in the
dependency chain of a public repo is an install failure waiting for somebody else's machine.
Pillow writes multi-page PDFs directly, so there is no second PDF library and no ``img2pdf``.

How hard to degrade
-------------------
Until a human squinting can still read it, then stop. A scan nobody can read tests nothing --
the run fails, and the failure says the corpus is broken rather than the pipeline is. The
honest calibration is that every field should be readable by eye except case 7's total, which
should be unreadable *because it is not there*.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path
from random import Random
from typing import Any

try:
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"error: {exc.name} is not installed.\n"
        "  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import DEMO_SEED  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "corpus"
INBOX = CORPUS / "inbox"
MANIFEST = CORPUS / "manifest.json"
PAGES_SHA = CORPUS / "pages.sha256"
HAND = REPO / "assets" / "fonts" / "Kalam-Regular.ttf"

DPI = 200
PAGE_H_PT = 841.89  # A4, and the coordinate space the manifest's crop anchors are in


# ------------------------------------------------------------------------- the degradation


def scan(image: Image.Image, rng: Random) -> Image.Image:
    """One page, put through a tired office photocopier.

    Lifted from ``spike/make_spike_pdfs.py`` and unchanged in strength, because that strength
    is the one step 0 measured a real subagent against. Turning any of these numbers up is a
    change to what the corpus claims, not a cosmetic edit, and it invalidates the step-0
    evidence that the corpus is readable at all.
    """
    image = image.convert("L")

    # A page fed slightly crooked. A fraction of a degree, not a tilt.
    image = image.rotate(
        rng.uniform(-0.9, 0.9), resample=Image.BICUBIC, fillcolor=255, expand=False
    )

    # Scanner optics, then sensor grain. Blended rather than added, so the grain sits in the
    # greys as well as the whites -- which is what a photocopy actually looks like.
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
    gradient = Image.linear_gradient("L").resize(image.size).rotate(rng.choice((0, 90, 180, 270)))
    image = ImageChops.multiply(image, Image.blend(Image.new("L", image.size, 255), gradient, 0.06))

    return ImageOps.autocontrast(image, cutoff=0.4)


def crop_below(image: Image.Image, point_from_bottom: float) -> Image.Image:
    """Case 7. Cut the page off above a coordinate the renderer handed over.

    The figure has to be *genuinely gone* rather than faint, so this removes pixels rather than
    obscuring them: after this there is no resolution, no contrast adjustment and no model that
    recovers the total, because it is not in the file.

    The crop line arrives as points from the bottom of the page in reportlab's coordinate
    system, captured by ``build_invoice`` immediately before it drew the totals rule. A
    fraction of the page tuned by eye would drift the moment the number of line items changed.
    """
    keep = int(round((PAGE_H_PT - point_from_bottom) * DPI / 72))
    keep = max(1, min(image.height, keep))
    return image.crop((0, 0, image.width, keep))


def annotate(image: Image.Image, point_from_bottom: float, text: str, rng: Random) -> Image.Image:
    """Case 8. Somebody's biro, disagreeing with the printed terms.

    Rotated a few degrees and drawn in a vendored OFL handwriting font rather than a system
    one, for the same reason poppler is not a dependency: ``Bradley Hand`` exists on macOS and
    on nothing else, and a corpus that renders differently on two machines has no determinism
    contract at all.

    Composited through a transparent layer so the rotation does not paint a white box over the
    page, which is what rotating an opaque image and pasting it does.
    """
    if not HAND.is_file():
        raise SystemExit(
            f"error: no handwriting font at {HAND}.\n"
            "Mess case 8 needs it and it is committed to the repo -- see assets/fonts/README.md."
        )

    size = int(34 * DPI / 200)
    font = ImageFont.truetype(str(HAND), size)
    box = font.getbbox(text)
    layer = Image.new("RGBA", (box[2] - box[0] + 40, box[3] - box[1] + 40), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((20, 10), text, font=font, fill=(28, 34, 92, 236))
    layer = layer.rotate(rng.uniform(-4.5, -1.5), resample=Image.BICUBIC, expand=True)

    # Just below the printed terms it contradicts, indented so it reads as marginalia rather
    # than as part of the document. A note nobody connects to the terms tests nothing, and a
    # note written across them obscures the half of the conflict that is printed.
    x = int(46 * DPI / 72)
    y = int((PAGE_H_PT - point_from_bottom) * DPI / 72) + int(16 * DPI / 72)
    y = min(y, image.height - layer.height - 4)

    page = image.convert("RGBA")
    page.alpha_composite(layer, (x, max(0, y)))
    return page.convert("L")


def rasterise(pdf_bytes: bytes) -> list[Image.Image]:
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        return [document[i].render(scale=DPI / 72).to_pil() for i in range(len(document))]
    finally:
        document.close()


def to_pdf(pages: list[Image.Image]) -> bytes:
    buf = BytesIO()
    pages[0].save(buf, "PDF", resolution=float(DPI), save_all=True, append_images=pages[1:])
    return buf.getvalue()


def text_layer(pdf_bytes: bytes) -> str:
    """Everything a text extractor can get out of a PDF, for confirming there is nothing.

    Case 1's rule is that an empty text extraction is not an empty document, and the corpus can
    only make that claim if the extraction really is empty. Asserted here at write time and
    again in check_degrade.py, because it is the premise every other finding rests on.
    """
    document = pdfium.PdfDocument(pdf_bytes)
    found = []
    try:
        for i in range(len(document)):
            page = document[i]
            textpage = page.get_textpage()
            found.append(textpage.get_text_range())
            textpage.close()
    finally:
        document.close()
    return "".join(found).strip()


# ------------------------------------------------------------------------------ the driver


def page_hash(image: Image.Image) -> str:
    """A hash of the pixels, which is where this project's determinism contract lives.

    Not a hash of the file. reportlab and Pillow both stamp times and ids into a PDF container,
    so the bytes differ between runs of identical code -- see check_corpus.py's docstring. The
    pixels do not, and the pixels are what a reader is actually given.
    """
    return hashlib.sha256(image.convert("L").tobytes()).hexdigest()


def run(seed: int, preview: Path | None) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text())
    if manifest["seed"] != seed:
        raise SystemExit(
            f"error: corpus/manifest.json is seed {manifest['seed']} and this run is seed {seed}.\n"
            f"  scripts/corpus.py --seed {seed}"
        )
    if not INBOX.is_dir():
        raise SystemExit(f"error: no corpus at {INBOX}.\n  scripts/corpus.py --seed {seed}")

    # Planned per file, by the pass that wrote the manifest. Sorted so the seeded stream is
    # consumed in a fixed order regardless of how the manifest happens to be ordered.
    planned = {
        e["source_file"]: e["degradation"]
        for e in manifest["documents"]
        if e.get("degradation")
    }

    # Refuse to degrade a corpus that has already been degraded. Rasterising a rasterised page
    # succeeds -- it just does it again, at a slightly different skew, and writes a pages.sha256
    # over the top. So the corpus drifts *and* the file that exists to detect drift drifts with
    # it, which is the one failure this contract cannot survive. Cheap to detect: a planned scan
    # that still has a text layer has not been through here yet.
    already = [
        name
        for name in sorted(planned)
        if (INBOX / name).suffix == ".pdf"
        and not pdfium.PdfDocument(INBOX / name)[0].get_textpage().get_text_range().strip()
    ]
    if already:
        raise SystemExit(
            f"error: {len(already)} of {len(planned)} planned scans already have no text layer, "
            "so this corpus has been degraded already.\n"
            f"  degrading twice changes the pages and rewrites {PAGES_SHA.name} to match, "
            "which hides the drift rather than catching it.\n"
            f"  scripts/corpus.py --seed {seed}   # re-render, then run this again"
        )

    rng = Random(seed ^ 0xD0C)
    hashes: dict[str, list[str]] = {}
    done: list[dict[str, Any]] = []

    for name in sorted(planned):
        plan = planned[name]
        path = INBOX / name
        pages = rasterise(path.read_bytes())

        if plan.get("crop_below_pt") is not None:
            pages[0] = crop_below(pages[0], plan["crop_below_pt"])

        pages = [scan(page, rng) for page in pages]

        # After the degradation, not before. A note written first and then blurred, grained and
        # skewed with the page reads as printed; a note added afterwards reads as ink on a
        # photocopy, which is what it is supposed to be.
        if plan.get("annotate_at_pt") is not None:
            pages[0] = annotate(pages[0], plan["annotate_at_pt"], plan["annotation"], rng)

        media = to_pdf(pages)
        leaked = text_layer(media)
        if leaked:
            raise SystemExit(
                f"error: {name} still has a text layer after rasterising ({len(leaked)} chars).\n"
                "Case 1's whole premise is that it does not."
            )
        path.write_bytes(media)

        hashes[name] = [page_hash(page) for page in pages]
        done.append({"file": name, "case": plan["case"], "pages": len(pages)})

        if preview and plan["case"] in (2, 7, 8):
            preview.mkdir(parents=True, exist_ok=True)
            pages[0].save(preview / f"case-{plan['case']}-{Path(name).stem}.png")

    # Every rendered page in the corpus, not only the degraded ones. A text-layer invoice is
    # also a rendering and also has to be stable between runs, and restricting the hash to the
    # scans would leave fifty-two documents with no determinism contract at all.
    #
    # also_filed_as is in here deliberately. Case 5's duplicate is a second *file* and not a
    # second document, so it has no manifest entry of its own -- and a set built from
    # source_file alone silently leaves it unhashed, which is how it was missed the first time.
    every = {e["source_file"] for e in manifest["documents"]} | {
        e["also_filed_as"] for e in manifest["documents"] if e.get("also_filed_as")
    }
    for entry in sorted(every):
        if entry in hashes or not entry.lower().endswith(".pdf"):
            continue
        hashes[entry] = [page_hash(page) for page in rasterise((INBOX / entry).read_bytes())]

    PAGES_SHA.write_text(
        "\n".join(
            f"{digest}  {name}#{n}"
            for name in sorted(hashes)
            for n, digest in enumerate(hashes[name], start=1)
        )
        + "\n"
    )
    return {"degraded": done, "hashed": sum(len(v) for v in hashes.values()), "files": len(hashes)}


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
    global CORPUS, INBOX, MANIFEST, PAGES_SHA
    CORPUS = root
    INBOX = root / "inbox"
    MANIFEST = root / "manifest.json"
    # PAGES_SHA is derived from CORPUS at import time, so rebinding CORPUS alone left it aimed at
    # the default corpus. Building the held-out seed then overwrote seed 42's page hashes with
    # hashes of documents from a different portfolio -- the drift-detection file, drifted, exactly
    # the failure its own guard exists to prevent. Every path this script writes belongs here.
    PAGES_SHA = root / "pages.sha256"


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
    ap.add_argument(
        "--preview",
        type=Path,
        help="Write a PNG of each planted case here. Case 7 has to be looked at, not asserted "
        "at -- the plan says verify the crop by looking at it.",
    )
    args = ap.parse_args()
    if args.corpus:
        _relocate(args.corpus.resolve())

    result = run(args.seed, args.preview)
    by_case: dict[int, int] = {}
    for entry in result["degraded"]:
        by_case[entry["case"]] = by_case.get(entry["case"], 0) + 1

    print(f"{INBOX}")
    print(
        f"  {len(result['degraded'])} files rasterised at {DPI} DPI and saved back with no text layer"
    )
    print("  " + ", ".join(f"case {c}: {n}" for c, n in sorted(by_case.items())))
    print(f"  {PAGES_SHA}: {result['hashed']} page hashes over {result['files']} files")
    if args.preview:
        print(f"  previews in {args.preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
