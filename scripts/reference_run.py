#!/usr/bin/env python3
"""Project the manifest into an extractions.json, so the downstream steps can be built.

    scripts/reference_run.py --perfect                    a flawless run
    scripts/reference_run.py --flawed --run reference-02  one with each failure class in it
    scripts/reference_run.py --perfect --out out/x.json

Steps 6, 7 and 8 consume `extractions.json`, which comes from a Cowork run. This produces one
without a run, for two different reasons.

``--perfect`` is the gate the plan demands of the reconciler: **it must score a perfect input
perfectly.** A reconciler with an off-by-one in its join, a units mismatch, or a comparison
that trips on `"1200.00"` against `"1200.0"` will report a plausible eighty-something percent
against a real run and look like a finding about the model. Run it against an input that is
correct by construction and any score below 100% is a bug in the scorer. This is the single
most useful test in the repo and it costs one function.

``--flawed`` injects each of the six outcome classes deliberately, so `scorecard.py` has a
second run to render against and so the per-case column has something other than green in it.
The injections are chosen to look like plausible model behaviour rather than random damage --
guessing a cropped total, flagging the legible control, deduplicating on the filename -- because
a scorecard that only ever shows a perfect run and a randomly-corrupted one demonstrates
nothing about which failures matter.

**Neither is a substitute for the real run**, and both write `"kind": "reference"` into
`scores.jsonl` so they cannot later be mistaken for one.

The projection is not a second implementation of the truth
----------------------------------------------------------
It is a mechanical restatement of the manifest in the extraction schema's shape: `where`
becomes `evidence`, `expected_confidence` becomes `confidence`, and the value is copied across
unchanged. Nothing here decides what a document says. If it did, it would be a second author
of the ground truth, and the two would disagree exactly where it mattered.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from random import Random
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import DEMO_SEED  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "corpus" / "manifest.json"
DEFAULT_OUT = REPO / "extractions.json"


# ------------------------------------------------------------------------------ projection


def project_field(entry: dict[str, Any]) -> dict[str, Any]:
    """One manifest field, in the extraction schema's three-shape contract.

    The manifest says what the document asserts and where. The schema wants the same thing plus
    a confidence, and the manifest already carries the confidence a correct reading would
    produce -- defaulting to `high`, and named explicitly on the fields belonging to cases 7, 8
    and 9. So this is a rename and a default, not a judgement.
    """
    confidence = entry.get("expected_confidence", "high")
    if confidence == "unreadable":
        return {
            "value": None,
            "confidence": "unreadable",
            "reason": entry.get("note") or "not legible on the page",
        }
    field = {"value": entry["value"], "confidence": confidence, "evidence": entry["where"]}
    if confidence == "low":
        field["reason"] = entry.get("note") or "the reading depends on an inference"
    return field


def project(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Every declared document as a record, plus the one that exists twice.

    Seventy-five records out of seventy-four documents. Case 5's invoice is emitted a second
    time under its other filename, carrying `duplicate_of`, because that is what a correct run
    hands back: it *found* two instances and *recognised* them as one document. A projection
    that emitted seventy-four would be projecting the merged answer rather than the extraction,
    and step 8's deduplication metric would have nothing to measure.
    """
    records: list[dict[str, Any]] = []
    for entry in manifest["documents"]:
        record = {
            "source_file": entry["source_file"],
            "pages": entry["pages"],
            "document_type": entry["document_type"],
            "document_id": entry["document_id"],
            "supersedes": entry["supersedes"],
            "duplicate_of": None,
            "fields": {name: project_field(f) for name, f in entry["fields"].items()},
        }
        records.append(record)

        if entry.get("also_filed_as"):
            twin = json.loads(json.dumps(record))
            twin["source_file"] = entry["also_filed_as"]
            twin["duplicate_of"] = entry["document_id"]
            records.append(twin)

    return records


# ------------------------------------------------------------------------------- the flaws


def find(records: list[dict[str, Any]], document_id: str) -> dict[str, Any]:
    return next(r for r in records if r.get("document_id") == document_id and not r.get("duplicate_of"))


def flaw(records: list[dict[str, Any]], manifest: dict[str, Any], rng: Random) -> list[str]:
    """Inject one of each outcome class, and report what was done.

    Six classes, six injections, each chosen to be a mistake a competent model would plausibly
    make. Random corruption would be easier and would demonstrate nothing: the interesting
    question is not whether the scorer notices damage, it is whether the scorecard distinguishes
    a guess from a gap and a useful flag from a nervous one.
    """
    cases = manifest["mess_cases"]
    done: list[str] = []

    # 1. WRONG -- a value that is present and incorrect. A transposition, which is what
    #    misreading a degraded figure actually looks like.
    scans = [
        r for r in records
        if r["document_type"] == "invoice" and "total_due" in r["fields"]
        and r["fields"]["total_due"]["confidence"] == "high"
        and next(e for e in manifest["documents"] if e["document_id"] == r["document_id"]).get("scanned")
    ]
    victim = scans[rng.randrange(len(scans))]
    original = victim["fields"]["total_due"]["value"]
    digits = list(original)
    i = next(n for n, c in enumerate(digits) if c.isdigit() and n + 1 < len(digits) and digits[n + 1].isdigit())
    digits[i], digits[i + 1] = digits[i + 1], digits[i]
    victim["fields"]["total_due"]["value"] = "".join(digits)
    done.append(f"wrong: {victim['document_id']} total_due {original} -> {''.join(digits)} (transposed)")

    # 2. HALLUCINATED -- the one the brief is about. Case 7's cropped total, guessed at high
    #    confidence by adding up the figures that are visible. Defensible arithmetic, and
    #    exactly the behaviour the confidence contract exists to catch.
    seven = find(records, cases["7"]["document_id"])
    truth = Decimal(
        next(e for e in manifest["documents"] if e["document_id"] == cases["7"]["document_id"])
        ["fields"]["total_due"]["value"]
    )
    guess = (truth * Decimal("0.994")).quantize(Decimal("0.01"))
    seven["fields"]["total_due"] = {
        "value": str(guess),
        "confidence": "high",
        "evidence": "page 1, totals block",
    }
    done.append(f"hallucinated: {seven['document_id']} total_due guessed {guess} against {truth}, at high confidence")

    # 3. MISSED -- a field the document asserts, simply absent from the output.
    missed = next(r for r in records if r["document_type"] == "sow" and "ceiling_hours" in r["fields"])
    missed["fields"].pop("ceiling_hours")
    done.append(f"missed: {missed['document_id']} ceiling_hours dropped")

    # 4. CORRECTLY FLAGGED, but on the wrong document -- the control, flagged. A model that
    #    has learned to be careful and has not learned when to stop. This is the false positive
    #    that keeps flag precision honest, and without case 2 there would be no way to produce it.
    control = next(r for r in records if r["source_file"] == cases["2"]["file"])
    control["fields"]["total_due"] = {
        "value": control["fields"]["total_due"]["value"],
        "confidence": "low",
        "evidence": control["fields"]["total_due"]["evidence"],
        "reason": "the scan is skewed and speckled, so the figure may be misread",
    }
    done.append(f"false flag: {control['document_id']} total_due flagged low on the legible control")

    # 5. Deduplication failed -- the duplicate is present and not marked. Which is what happens
    #    when a merge keys on the filename, since the two filenames genuinely differ.
    twin = next(r for r in records if r.get("duplicate_of"))
    twin["duplicate_of"] = None
    done.append(f"dedupe missed: {twin['document_id']} filed as {twin['source_file']} not marked as a duplicate")

    # 6. Misclassified -- a remittance read as an invoice. The two are confusable and the
    #    consequence is real: it lands in the accounts receivable as money owed rather than
    #    money received.
    remittance = next(
        r for r in records
        if r["document_type"] == "remittance" and r["source_file"] != cases["3"]["file"]
    )
    remittance["document_type"] = "invoice"
    done.append(f"misclassified: {remittance['document_id']} remittance read as an invoice")

    # 7. Case 10 force-fitted. Not a scored class of its own, but it is the failure the schema's
    #    out_of_scope constraint exists to prevent, and a run that does it should be visible.
    sandwich = next(r for r in records if r["document_type"] == "out_of_scope")
    sandwich["document_type"] = "invoice"
    sandwich["document_id"] = "INV-2026-9999"
    sandwich["fields"] = {
        "total_due": {"value": "31.25", "confidence": "high", "evidence": "receipt total"},
        "client_name": {"value": "The Gresham Kitchen", "confidence": "high", "evidence": "top of receipt"},
    }
    done.append(f"force-fitted: {sandwich['source_file']} read as an invoice for 31.25")

    return done


# ---------------------------------------------------------------------------- entry point


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--perfect", action="store_true", help="A flawless projection of the manifest.")
    mode.add_argument("--flawed", action="store_true", help="One of each outcome class, injected.")
    ap.add_argument("--run", default=None, help="Run name. Defaults to reference-perfect or reference-flawed.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=DEMO_SEED)
    args = ap.parse_args()

    if not MANIFEST.is_file():
        raise SystemExit(f"error: no manifest at {MANIFEST}.\n  scripts/corpus.py --seed {args.seed}")

    manifest = json.loads(MANIFEST.read_text())
    records = project(manifest)
    injected: list[str] = []
    if args.flawed:
        injected = flaw(records, manifest, Random(args.seed ^ 0xFEED))

    name = args.run or ("reference-perfect" if args.perfect else "reference-flawed")
    payload = {
        "run": name,
        "kind": "reference",
        "note": (
            "Projected from corpus/manifest.json by scripts/reference_run.py. NOT a Cowork run. "
            "The perfect variant exists to prove the reconciler scores a correct input at 100% -- "
            "any shortfall against it is a bug in the scorer, not a finding about a model."
        ),
        "seed": manifest["seed"],
        "period": manifest["period"],
        "injected": injected,
        "documents": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    duplicates = sum(1 for r in records if r.get("duplicate_of"))
    print(f"{args.out}")
    print(
        f"  run {name}: {len(records)} document instances, "
        f"{len(records) - duplicates} after the merge, {duplicates} marked duplicate"
    )
    print(f"  {sum(len(r['fields']) for r in records)} field values")
    for line in injected:
        print(f"  injected  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
