#!/usr/bin/env python3
"""Step 5's gate: the reference run is a fair stand-in for a Cowork run.

    scripts/check_reference.py
    scripts/check_reference.py -v

Two things have to hold, and the second is the one worth stating.

**The perfect projection validates and holds the right counts.** That is the plan's Done-when,
and it is what makes `--perfect` usable as the reconciler's calibration input at step 8.

**The flawed projection also validates.** Every injected flaw is schema-*valid*: a hallucinated
total is a well-formed high-confidence field, a misclassified remittance is a well-formed
invoice. If the flawed run failed validation, the flaws would be shape errors and the schema
would already be catching them -- and the scorecard would be measuring nothing that the
validator did not already know. **The whole reason step 8 exists is that these two categories
are different**, and this file is where that separation is asserted rather than assumed.

Both are run as subprocesses and validated by the Skill's own bundled validator, not by an
importable copy of it, so the assertion covers the thing that will actually be run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "corpus" / "manifest.json"
VALIDATOR = REPO / "skills" / "extract-to-schema" / "scripts" / "validate.py"
TMP = REPO / ".check_reference_tmp.json"

N_DOCUMENTS = 74
N_INSTANCES = 75
OUTCOME_CLASSES = 6


def generate(*flags: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "reference_run.py"), *flags, "--out", str(TMP)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:280])
    return json.loads(TMP.read_text())


def validate() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(TMP)], capture_output=True, text=True, cwd=REPO
    )


def check_perfect(manifest: dict[str, Any], checks: Checks) -> None:
    payload = generate("--perfect")
    records = payload["documents"]

    checks.add(
        "perfect: seventy-five document instances, one per thing a run would find",
        len(records) == N_INSTANCES,
        f"{len(records)} instances",
    )
    merged = [r for r in records if not r["duplicate_of"]]
    checks.add(
        "perfect: seventy-four records after the merge",
        len(merged) == N_DOCUMENTS,
        f"{len(merged)} after dropping {len(records) - len(merged)} marked duplicate",
    )
    checks.add(
        "perfect: the duplicate is emitted rather than pre-merged away",
        sum(1 for r in records if r["duplicate_of"]) == 1,
        "a correct run finds two instances and recognises them as one document",
    )

    outcome = validate()
    checks.add(
        "perfect: validates against the schema, through the Skill's own validator",
        outcome.returncode == 0,
        (outcome.stdout or outcome.stderr).strip().splitlines()[-1][:96],
    )
    checks.add(
        "perfect: it was this validator that said so",
        "docmess-validate/1" in outcome.stdout,
        outcome.stdout.strip().splitlines()[0][:96] if outcome.stdout else "no output",
    )

    # Every declared field, projected. A projection that quietly dropped fields would make the
    # reconciler's coverage metric read 100% against an input that was missing half of them.
    declared = sum(len(e["fields"]) for e in manifest["documents"])
    projected = sum(len(r["fields"]) for r in merged)
    checks.add(
        "perfect: every field the manifest declares is in the projection",
        projected == declared,
        f"{projected} projected against {declared} declared",
    )

    # And it agrees with the manifest field by field, which is what "correct by construction"
    # has to mean if step 8's 100% is going to mean anything.
    truth = {e["document_id"]: e for e in manifest["documents"] if e["document_id"]}
    wrong = [
        f"{r['document_id']}.{name}"
        for r in merged
        if r["document_id"] in truth
        for name, field in r["fields"].items()
        if field["value"] != truth[r["document_id"]]["fields"][name]["value"]
        and field["confidence"] != "unreadable"
    ]
    checks.add(
        "perfect: every projected value equals the declared value",
        not wrong,
        f"{projected} values agree" if not wrong else f"{len(wrong)} differ: {wrong[:3]}",
    )

    flags = [
        (r["document_id"], name)
        for r in merged
        for name, field in r["fields"].items()
        if field["confidence"] != "high"
    ]
    # Counted from the manifest rather than written here as a number. It was `== 4`, and when
    # the two statement totals were correctly reclassified as unreadable this assertion failed
    # for describing the corpus as it used to be. A literal in a harness is a second copy of the
    # ground truth, and the copy goes stale.
    expected = {
        (e["document_id"], name)
        for e in manifest["documents"]
        for name, field in (e.get("fields") or {}).items()
        if field.get("expected_confidence", "high") != "high"
    }
    checks.add(
        "perfect: it flags exactly the fields the corpus expects to be flagged, and no others",
        set(flags) == expected,
        f"{len(flags)} flags, matching the manifest's {len(expected)}"
        if set(flags) == expected
        else f"differ: {sorted(set(flags) ^ expected)[:3]}",
    )


def check_flawed(manifest: dict[str, Any], checks: Checks) -> None:
    payload = generate("--flawed")
    injected = payload["injected"]

    checks.add(
        "flawed: one injection per outcome class, plus the force-fit",
        len(injected) >= OUTCOME_CLASSES,
        f"{len(injected)} injections",
    )
    kinds = {line.split(":", 1)[0] for line in injected}
    checks.add(
        "flawed: the injections are distinct failure modes, not the same one repeated",
        len(kinds) == len(injected),
        ", ".join(sorted(kinds)),
    )

    # The load-bearing one. See the module docstring.
    outcome = validate()
    checks.add(
        "flawed: it validates too -- every flaw is schema-valid and semantically wrong",
        outcome.returncode == 0,
        "which is exactly why step 8 exists and the validator is not enough",
    )

    records = payload["documents"]
    seven = manifest["mess_cases"]["7"]
    guessed = next(r for r in records if r["document_id"] == seven["document_id"])
    checks.add(
        "flawed: case 7's cropped total is guessed at high confidence, as a model would",
        guessed["fields"]["total_due"]["confidence"] == "high"
        and guessed["fields"]["total_due"]["value"]
        != next(e for e in manifest["documents"] if e["document_id"] == seven["document_id"])["fields"]["total_due"]["value"],
        f"{guessed['fields']['total_due']['value']} asserted at high confidence, and wrong",
    )

    control = next(r for r in records if r["source_file"] == manifest["mess_cases"]["2"]["file"])
    checks.add(
        "flawed: the legible control is flagged, which is the false positive case 2 exists for",
        control["fields"]["total_due"]["confidence"] == "low",
        "flag precision has something to move against",
    )
    checks.add(
        "flawed: the duplicate is left unmarked, as a filename-keyed merge would leave it",
        not any(r.get("duplicate_of") for r in records),
        f"{len(records)} records, none marked duplicate, so the total double-counts",
    )


def check_labelling(checks: Checks) -> None:
    """A reference run must be impossible to mistake for a Cowork run.

    The whole project's claim is that a model was measured against ground truth it did not have.
    A projection of the answer key sitting in scores.jsonl without a label would quietly falsify
    that, and it would be an easy mistake to make months later.
    """
    payload = generate("--perfect")
    checks.add(
        "labelling: the payload says it is a reference run and not a model run",
        payload.get("kind") == "reference",
        f"kind={payload.get('kind')!r}",
    )
    checks.add(
        "labelling: and says so in prose, for a reader who is not looking at the key",
        "NOT a Cowork run" in payload.get("note", ""),
        payload.get("note", "")[:88] + "...",
    )


def main() -> int:
    ap = base_parser(__doc__)
    args = ap.parse_args()

    def body(_label: Any, checks: Checks) -> None:
        manifest = json.loads(MANIFEST.read_text())
        try:
            check_perfect(manifest, checks)
            check_flawed(manifest, checks)
            check_labelling(checks)
        finally:
            TMP.unlink(missing_ok=True)

    return run(["reference run"], body, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
