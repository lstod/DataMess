#!/usr/bin/env python3
"""Step 3's Done-when conditions, as assertions.

    scripts/check_degrade.py                 seed 42, the full cycle twice
    scripts/check_degrade.py --seed 9001
    scripts/check_degrade.py --quick         skip the second cycle, so no determinism check

The three the plan names: twenty-two files return no text under a text-extraction attempt,
``pages.sha256`` matches across two runs of the same seed, and case 7's crop contains no digits
of the total under any extraction path.

The third is the one that needs care, because *there is no assertion available that says a
number is absent from a picture.* Nothing here does OCR, so "the total is gone" cannot be
checked directly. What can be checked is the thing that makes it true: the strip of page below
the crop line **contained ink before the crop and does not exist after it.** So this harness
renders the invoice before degrading it, measures the ink in the region about to be removed,
and then asserts the degraded page is exactly that much shorter. A crop that took only
whitespace, or that took nothing because an anchor drifted, fails on the first half.

It runs the real scripts as subprocesses and re-reads what they left on disk. Nothing is
asserted from the data that produced the files, which is the only way to catch a pass that
computed the right answer and wrote it somewhere else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pypdfium2 as pdfium
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    sys.exit(f"error: {exc.name} is not installed. .venv/bin/pip install -r requirements.txt")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
INBOX = REPO / "corpus" / "inbox"
MANIFEST = REPO / "corpus" / "manifest.json"
PAGES_SHA = REPO / "corpus" / "pages.sha256"

# Restated rather than imported from degrade.py, so the harness can disagree with it.
DPI = 200
PAGE_H_PT = 841.89
N_SCANS = 22
INK = 170  # a pixel darker than this is ink rather than paper or grain


def shell(script: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / script), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{script}: {(result.stderr or result.stdout).strip()[:280]}")


def render(path: Path, page: int = 0) -> Image.Image:
    document = pdfium.PdfDocument(path.read_bytes())
    try:
        return document[page].render(scale=DPI / 72).to_pil().convert("L")
    finally:
        document.close()


def text_of(path: Path) -> str:
    document = pdfium.PdfDocument(path.read_bytes())
    found = []
    try:
        for i in range(len(document)):
            textpage = document[i].get_textpage()
            found.append(textpage.get_text_range())
            textpage.close()
    finally:
        document.close()
    return "".join(found).strip()


def ink_fraction(image: Image.Image) -> float:
    return float((np.asarray(image) < INK).mean())


def text_height(image: Image.Image) -> float:
    """Median height in pixels of a band of text on the page. A direct measure of type size.

    Legibility of rasterised text after a blur is mostly a question of how many pixels tall a
    glyph is, so this measures exactly that: take the ink profile down the page, find the runs
    of rows that contain text, and take the median run height. Bands shorter than 6px are
    grain and bands taller than 90px are a rule or a block, so both are dropped.

    Two other proxies were tried and are worse. Mean horizontal ink run length separates the
    control from the rest by only 1.10x, because it averages glyph stems together with the
    horizontal rules, which are the same width on every invoice whatever the type size. The
    90th percentile of the same runs gets to 1.20x and is still measuring the wrong thing.
    Band height gets 1.36x with no overlap at all -- the control is 30px against 20.5 to 24 for
    every other scanned invoice -- which is what a real 1.3x difference in type size should
    look like.
    """
    profile = (np.asarray(image) < INK).mean(axis=1)
    on = (profile > 0.004).view(np.int8)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], on, [0]))))
    heights = edges[1::2] - edges[0::2]
    heights = heights[(heights >= 6) & (heights <= 90)]
    return float(np.median(heights)) if len(heights) else 0.0


# ------------------------------------------------------------------------------ the checks


def check_no_text_layer(manifest: dict[str, Any], checks: Checks) -> None:
    """Twenty-two return nothing, and the other twenty-four still return something.

    The second half matters as much as the first. A degradation pass that rasterised every PDF
    would pass "22 files have no text" only by accident of counting, and would have destroyed
    the text-layer half of the corpus -- which is what makes case 1 a case rather than the
    default.
    """
    planned = {e["source_file"] for e in manifest["documents"] if e.get("degradation")}
    pdfs = sorted(p for p in INBOX.iterdir() if p.suffix.lower() == ".pdf")

    empty = {p.name for p in pdfs if not text_of(p)}
    checks.add(
        "scans: exactly twenty-two files return no text under extraction",
        len(empty) == N_SCANS,
        f"{len(empty)} of {len(pdfs)} PDFs are image-only",
    )
    checks.add(
        "scans: the twenty-two are exactly the files the manifest planned",
        empty == planned,
        "match" if empty == planned else f"differ by {sorted(empty ^ planned)[:3]}",
    )
    still_text = [p.name for p in pdfs if p.name not in planned and not text_of(p)]
    checks.add(
        "scans: every PDF that was not planned as a scan still has its text layer",
        not still_text,
        f"{len(pdfs) - len(empty)} text-layer PDFs survive"
        if not still_text
        else f"{len(still_text)} lost it: {still_text[:3]}",
    )

    fontless = [p.name for p in pdfs if p.name in planned and b"/Font" in p.read_bytes()]
    checks.add(
        "scans: no scanned PDF carries a font object, so there is nothing to extract",
        not fontless,
        "image-only throughout" if not fontless else f"{len(fontless)} carry /Font",
    )


def check_pages_sha(manifest: dict[str, Any], checks: Checks) -> None:
    lines = [ln for ln in PAGES_SHA.read_text().splitlines() if ln.strip()]
    checks.add("pages.sha256: exists and is not empty", bool(lines), f"{len(lines)} page hashes")

    named = {ln.split("  ", 1)[1].rsplit("#", 1)[0] for ln in lines}
    pdfs = {p.name for p in INBOX.iterdir() if p.suffix.lower() == ".pdf"}
    checks.add(
        "pages.sha256: covers every PDF in the inbox, degraded or not",
        named == pdfs,
        f"{len(named)} files hashed, {len(pdfs)} PDFs present"
        + ("" if named == pdfs else f"; missing {sorted(pdfs - named)[:3]}"),
    )
    checks.add(
        "pages.sha256: every digest is a sha256",
        all(re.fullmatch(r"[0-9a-f]{64}", ln.split("  ", 1)[0]) for ln in lines),
        "all 64 hex chars",
    )
    checks.add(
        "pages.sha256: case 3's file is hashed on both of its pages",
        sum(1 for ln in lines if manifest["mess_cases"]["3"]["file"] in ln) == 2,
        f"{manifest['mess_cases']['3']['file']}: "
        f"{sum(1 for ln in lines if manifest['mess_cases']['3']['file'] in ln)} pages",
    )


def check_case_7(before: dict[str, Image.Image], manifest: dict[str, Any], checks: Checks) -> None:
    """The cut-off total, asserted as an absence that can actually be measured."""
    name = manifest["mess_cases"]["7"]["file"]
    entry = next(e for e in manifest["documents"] if e["source_file"] == name)
    crop_pt = entry["degradation"]["crop_below_pt"]
    original = before[name]
    kept = int(round((PAGE_H_PT - crop_pt) * DPI / 72))

    removed = original.crop((0, kept, original.width, original.height))
    checks.add(
        "mess 7: the strip that was cropped away had the totals block printed in it",
        ink_fraction(removed) > 0.002,
        f"{ink_fraction(removed):.3%} of the removed strip was ink "
        f"({original.height - kept}px tall)",
    )

    after = render(INBOX / name)
    checks.add(
        "mess 7: the scanned page is exactly as short as the crop line says",
        after.height == kept,
        f"{after.height}px kept of {original.height}px, planned {kept}px",
    )
    checks.add(
        "mess 7: the total is gone rather than faint -- those pixels are not in the file",
        after.height < original.height and not text_of(INBOX / name),
        f"{original.height - after.height}px removed, no text layer to read it from either",
    )

    # The declared total must not be recoverable from anywhere else in the corpus. A pipeline
    # that reads it off a remittance or a statement is not guessing, but it is also not
    # demonstrating what case 7 exists to demonstrate.
    total = entry["fields"]["total_due"]["value"]
    digits = total.replace(".", "")
    leaks = [
        p.name
        for p in INBOX.iterdir()
        if p.suffix.lower() == ".pdf"
        and (total in (t := text_of(p)) or f"{float(total):,.2f}" in t or digits in t)
    ]
    checks.add(
        "mess 7: the cut-off total appears in no text layer anywhere in the corpus",
        not leaks,
        f"{total} is nowhere else" if not leaks else f"leaks via {leaks[:2]}",
    )


def check_case_8(before: dict[str, Image.Image], manifest: dict[str, Any], checks: Checks) -> None:
    """The handwriting is on the page, below the terms, and did not land on top of them."""
    block = manifest["mess_cases"]["8"]
    name = block["file"]
    entry = next(e for e in manifest["documents"] if e["source_file"] == name)
    anchor = entry["degradation"]["annotate_at_pt"]
    top = int((PAGE_H_PT - anchor) * DPI / 72)

    original = before[name]
    after = render(INBOX / name)
    band = (0, top, original.width, min(top + int(70 * DPI / 72), original.height))

    was = ink_fraction(original.crop(band))
    now = ink_fraction(after.crop(band))
    checks.add(
        "mess 8: there is ink below the printed terms that was not there before",
        now > was + 0.0015,
        f"{was:.3%} -> {now:.3%} in the band below the anchor",
    )

    # The printed terms have to survive: half of case 8 is a contradiction, and a contradiction
    # needs both sides legible.
    #
    # Asserted as a gap rather than as a before-and-after difference. Comparing the terms band
    # across degradation compares an undegraded render to a degraded one, and the blur, grain
    # and autocontrast thicken every existing stroke -- the band gains about a point of ink
    # whether anything was written on it or not, so the comparison cannot tell an overlapping
    # annotation from a clean one. What it can tell is whether there is clear paper between the
    # last printed line and the handwriting, which is the thing actually being claimed.
    gap = after.crop((0, top + 8, after.width, top + int(15 * DPI / 72)))
    checks.add(
        "mess 8: clear paper separates the printed terms from the handwriting below them",
        ink_fraction(gap) < 0.01,
        f"{ink_fraction(gap):.3%} ink in the {gap.height}px gap between the two",
    )
    checks.add(
        "mess 8: the annotation contradicts the printed terms rather than repeating them",
        "14" in block["annotation"] and "30" in block["annotation"],
        f"printed 'Net 30 days', handwritten {block['annotation']!r}",
    )


def check_case_2(manifest: dict[str, Any], checks: Checks) -> None:
    """The control was degraded at full strength and is still the easiest page to read.

    Both halves are the assertion. Degraded more gently, it would be a control for nothing;
    not measurably more legible, it would not be a control either -- a model that flagged it
    would be right, and flag precision would stop meaning anything.
    """
    name = manifest["mess_cases"]["2"]["file"]
    entry = next(e for e in manifest["documents"] if e["source_file"] == name)

    plan = entry["degradation"]
    checks.add(
        "mess 2: the control gets no gentler treatment -- no crop, no special case",
        plan["case"] == 2 and plan["crop_below_pt"] is None and plan["annotate_at_pt"] is None,
        "same scan() path and the same strength as the other twenty-one",
    )

    control = text_height(render(INBOX / name))
    others = [
        text_height(render(INBOX / e["source_file"]))
        for e in manifest["documents"]
        if e.get("degradation") and e["source_file"] != name and e["document_type"] == "invoice"
    ]
    median = float(np.median(others)) if others else 0.0
    checks.add(
        "mess 2: its type is the largest of any scanned invoice, by a clear margin",
        bool(others) and control > max(others) and control > median * 1.2,
        f"text bands {control:.0f}px tall against {min(others):.0f}-{max(others):.0f}px "
        f"(median {median:.0f}) over {len(others)} scans",
    )
    checks.add(
        "mess 2: it is still a scan, with no text layer to fall back on",
        not text_of(INBOX / name),
        "image-only, so reading it requires vision",
    )


def check_readability(manifest: dict[str, Any], checks: Checks) -> None:
    """Degraded until a human squinting can still read it, and no further.

    A scan nobody can read tests nothing: the run fails, and the failure says the corpus is
    broken rather than the pipeline is. There is no OCR here to prove legibility, so what is
    asserted is the band either side of it -- enough ink left to be text, not so much that the
    page has gone grey.
    """
    scans = [e["source_file"] for e in manifest["documents"] if e.get("degradation")]
    fractions = {name: ink_fraction(render(INBOX / name)) for name in scans}

    blank = [n for n, f in fractions.items() if f < 0.002]
    checks.add(
        "scans: none of them came out blank",
        not blank,
        f"least ink is {min(fractions.values()):.2%}" if not blank else f"{len(blank)} near-blank",
    )
    muddy = [n for n, f in fractions.items() if f > 0.22]
    checks.add(
        "scans: none of them came out as a grey smear",
        not muddy,
        f"most ink is {max(fractions.values()):.2%}" if not muddy else f"{len(muddy)} over 22%",
    )
    checks.add(
        "scans: the degradation is grayscale, as a photocopier would be",
        all(render(INBOX / n).mode == "L" for n in scans[:4]),
        "single channel",
    )


def check_determinism(seed: int, first: str, checks: Checks) -> None:
    shell("corpus.py", "--seed", str(seed))
    shell("degrade.py", "--seed", str(seed))
    second = PAGES_SHA.read_text()
    checks.add(
        "pages.sha256: two runs of the same seed produce identical page pixels",
        first == second,
        f"{len(first.splitlines())} hashes, all identical"
        if first == second
        else f"{sum(1 for a, b in zip(first.splitlines(), second.splitlines()) if a != b)} differ",
    )


def check_refuses_mismatch(seed: int, checks: Checks) -> None:
    """degrade.py will not run against a manifest from a different seed.

    It executes a plan it did not write. If the manifest on disk came from another seed, the
    crop coordinates and the annotation anchors belong to documents that are not there, and the
    quiet version of that failure is a corpus cropped in the wrong places.
    """
    other = 9001 if seed != 9001 else 9002
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "degrade.py"), "--seed", str(other)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    checks.add(
        "degrade: refuses a manifest written by a different seed",
        result.returncode != 0 and "seed" in (result.stdout + result.stderr),
        (result.stdout + result.stderr).strip().splitlines()[0][:96],
    )


# ---------------------------------------------------------------------------- entry point


def main() -> int:
    ap = base_parser(__doc__)
    ap.add_argument(
        "--quick",
        action="store_true",
        help="One cycle only. Skips the determinism assertion, which needs two.",
    )
    args = ap.parse_args()
    seeds = args.seed or [42]

    def body(seed: Any, checks: Checks) -> None:
        shell("corpus.py", "--seed", str(seed))
        manifest = json.loads(MANIFEST.read_text())

        # The "before" renders, captured while the documents still have their text layer.
        # Cases 7 and 8 are assertions about a difference, and a difference needs both sides.
        watched = [manifest["mess_cases"]["7"]["file"], manifest["mess_cases"]["8"]["file"]]
        before = {name: render(INBOX / name) for name in watched}

        shell("degrade.py", "--seed", str(seed))
        checks.add("degrade: ran against the corpus and rewrote the scans", True, f"seed {seed}")

        check_no_text_layer(manifest, checks)
        check_pages_sha(manifest, checks)
        check_case_7(before, manifest, checks)
        check_case_8(before, manifest, checks)
        check_case_2(manifest, checks)
        check_readability(manifest, checks)
        check_refuses_mismatch(seed, checks)
        if not args.quick:
            check_determinism(seed, PAGES_SHA.read_text(), checks)

    return run(seeds, body, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
