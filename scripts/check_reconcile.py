#!/usr/bin/env python3
"""Step 8's Done-when conditions, as assertions.

    scripts/check_reconcile.py
    scripts/check_reconcile.py -v

Everything here scores into a scratch directory via `--scorecard`, so the harness never
appends its own throwaway runs to the real `scores.jsonl`. That file's only promise is that
nothing is ever removed from it, and a harness that had to clean up after itself would break
the promise it exists to protect.

The first assertion is the plan's gate: **a perfect input scores perfectly.** It sounds trivial
and it is the one that catches most reconciler bugs, because every off-by-one in the join, every
comparison that is too strict about a date format, and every field the manifest declares but the
projection does not produce shows up as a perfect run scoring 99-point-something. A reconciler
that cannot score a flawless run flawlessly will report model errors that are its own.

Two claims worth asserting rather than asserting around
-------------------------------------------------------
*Typed comparison earns its keep.* Rather than unit-testing the comparator, the harness reformats
a correct run -- money as `1,200.00`, dates as `09/03/2026`, an identifier with `I` typed as `l`
-- and asserts it still scores 100%. Those are all *rendering* differences, and a reconciler that
scored them as wrong would blame the model for the corpus's typography.

*The scorecard needs four numbers, not one.* Asserted quantitatively: the run that guesses at
case 7's cropped total and the run that declines it differ by less than half a point on accuracy
and by twenty-five points on flag recall. If that gap ever closes, the metric set has stopped
doing its job and the README's argument is no longer true.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "corpus" / "manifest.json"
RECONCILE = REPO / "scripts" / "reconcile.py"
SCORECARD_PY = REPO / "scripts" / "scorecard.py"
REFERENCE = REPO / "scripts" / "reference_run.py"
VALIDATOR = REPO / "skills" / "extract-to-schema" / "scripts" / "validate.py"

# Restated, not imported, so the harness can disagree with reconcile.py.
CLASSES = [
    "correct",
    "correctly_flagged",
    "wrong",
    "hallucinated",
    "missed",
    "over_flagged",
]
METRICS = ["field_accuracy", "coverage", "flag_precision", "flag_recall"]

# Restated too, and deliberately in the fixed order -- fold, then case. reconcile.py had this
# the other way round and it cost a legitimate read its mark.
_CONFUSABLE = str.maketrans({"I": "1", "l": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"})


def _fold(text: str) -> str:
    return text.translate(_CONFUSABLE).upper()


CASES = [str(n) for n in range(1, 11)]
DOCUMENTS = 74
IDENTIFIED = 70  # the 74 less the four out-of-scope, which have no identity to join on
INSTANCES = 75


def score(source: Path, name: str, scratch: Path) -> dict[str, Any]:
    """Shell out to the real reconciler and read the artifact back off disk."""
    result = subprocess.run(
        [sys.executable, str(RECONCILE), "--run", name, "--in", str(source),
         "--scorecard", str(scratch), "--quiet"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"reconcile.py exited {result.returncode}: {result.stderr[-300:]}")
    return json.loads((scratch / f"{name}.json").read_text())


def write(records: Any, path: Path, kind: str = "reference") -> Path:
    payload = records if isinstance(records, dict) else {"kind": kind, "documents": records}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


# ------------------------------------------------------------------ the gate: perfect is perfect


def check_perfect(result: dict[str, Any], checks: Checks) -> None:
    for name in METRICS:
        checks.add(
            f"perfect: {name} is exactly 100%",
            result["metrics"][name] == 1.0,
            f"{result['metrics'][name]}",
        )

    for name in ("wrong", "hallucinated", "missed", "over_flagged"):
        checks.add(
            f"perfect: nothing lands in {name}",
            result["counts"][name] == 0,
            f"{result['counts'][name]}",
        )

    # 70, not 74. The four out-of-scope documents carry no document_id -- there is nothing to
    # identify a lunch receipt as -- so they are scored at document level by case 10 instead of
    # joining. 70 identified + 4 out of scope is the 74, and the harness checks the arithmetic
    # closes rather than asserting a number that looks right.
    checks.add(
        "perfect: every identified document was matched",
        result["counts"]["documents_matched"] == result["counts"]["documents_declared"] == IDENTIFIED,
        f"{result['counts']['documents_matched']} of {result['counts']['documents_declared']} matched",
    )
    checks.add(
        "perfect: identified plus out-of-scope accounts for all 74",
        result["counts"]["documents_declared"] + len(result["documents"]["out_of_scope_declared"])
        == DOCUMENTS,
        f"{result['counts']['documents_declared']} identified + "
        f"{len(result['documents']['out_of_scope_declared'])} out of scope",
    )
    checks.add(
        "perfect: 75 instances merged to 74 records",
        result["counts"]["document_instances"] == INSTANCES,
        f"{result['counts']['document_instances']} instances in the run",
    )

    held = [n for n in CASES if result["mess_cases"][n]["verdict"] == "held"]
    checks.add(
        "perfect: all ten mess cases held",
        len(held) == 10,
        "all held" if len(held) == 10
        else "not held: " + ", ".join(
            f"{n} {result['mess_cases'][n]['verdict']}" for n in CASES if n not in held
        ),
    )
    checks.add(
        "perfect: the per-case table carries all ten, including the three with no fields",
        sorted(result["mess_cases"], key=int) == CASES,
        f"cases present: {', '.join(sorted(result['mess_cases'], key=int))}",
    )
    checks.add(
        "perfect: some fields were correctly flagged, so 100% is not an empty denominator",
        result["counts"]["correctly_flagged"] > 0 and result["counts"]["genuinely_hard_fields"] > 0,
        f"{result['counts']['correctly_flagged']} flagged of "
        f"{result['counts']['genuinely_hard_fields']} genuinely hard",
    )


# ------------------------------------------------------------------- typed comparison, end to end


def reformat(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Re-render correct answers the way a different reader would have typed them.

    None of these changes the value. Every one of them breaks string equality.
    """
    out = copy.deepcopy(records)
    hits = {"money": 0, "date": 0, "period": 0, "glyph": 0, "space": 0}
    for record in out:
        for field in (record.get("fields") or {}).values():
            value = field.get("value")
            if not isinstance(value, str):
                continue
            if re.fullmatch(r"-?\d+\.\d{2}", value):  # 12400.00 -> 12,400.00
                whole, cents = value.split(".")
                field["value"] = f"{int(whole):,}.{cents}"
                hits["money"] += 1
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):  # 2026-09-03 -> 03/09/2026
                y, m, d = value.split("-")
                field["value"] = f"{d}/{m}/{y}"
                hits["date"] += 1
            elif re.fullmatch(r"\d{4}-\d{2}", value):  # 2026-08 -> August 2026
                y, m = value.split("-")
                field["value"] = f"{['','January','February','March','April','May','June','July','August','September','October','November','December'][int(m)]} {y}"
                hits["period"] += 1
            elif re.fullmatch(r"[A-Z]{3}-\d{4}-\d{3,4}(-A\d)?", value):  # I typed as l
                if "1" in value:
                    field["value"] = value.replace("1", "l", 1)
                    hits["glyph"] += 1
            elif " " in value:  # a non-breaking space and a curly apostrophe
                field["value"] = value.replace(" ", "\u00a0", 1).replace("'", "\u2019")
                hits["space"] += 1
    return out, hits


def check_typed(records: list[dict[str, Any]], scratch: Path, checks: Checks) -> None:
    reformatted, hits = reformat(records)
    checks.add(
        "typed: the reformatter actually changed money, dates, a period and an identifier",
        all(hits[k] for k in ("money", "date", "glyph")) and hits["period"] + hits["space"] > 0,
        ", ".join(f"{k} {v}" for k, v in hits.items()),
    )

    path = write(reformatted, scratch / "reformatted.json")
    result = score(path, "check-reformatted", scratch)
    checks.add(
        "typed: a correct run rendered differently still scores 100% on every metric",
        all(result["metrics"][name] == 1.0 for name in METRICS),
        ", ".join(f"{n} {result['metrics'][n]}" for n in METRICS),
    )
    wrong = [f for f in result["fields"] if f["outcome"] == "wrong"]
    checks.add(
        "typed: and nothing was scored wrong",
        not wrong,
        "0 wrong" if not wrong
        else "; ".join(
            f"{f['field']}: declared {f['declared']!r} produced {f['produced']!r}"
            for f in wrong[:2]
        ),
    )

    # The other direction: the fold must not make everything equal to everything. Only the
    # records the run kept can be scored, so the duplicate instance is excluded from the count.
    broken = copy.deepcopy(records)
    changed = 0
    for record in broken:
        total = (record.get("fields") or {}).get("total_due")
        if total and isinstance(total.get("value"), str) and total.get("confidence") != "unreadable":
            total["value"] = "9999.99"
            if not record.get("duplicate_of"):
                changed += 1
    result = score(write(broken, scratch / "broken.json"), "check-broken", scratch)
    checks.add(
        "typed: genuinely different figures are still scored wrong",
        result["counts"]["wrong"] == changed > 0,
        f"{result['counts']['wrong']} wrong for {changed} altered totals on kept records",
    )

    # The fold is only safe if it cannot merge two identifiers the corpus means to keep apart.
    # The generator excludes I, O, C and G from the codes it mints for exactly this reason, so
    # this asserts the property rather than trusting it.
    identifiers = sorted(
        {
            str(field["value"])
            for record in records
            for name, field in (record.get("fields") or {}).items()
            if name.endswith(("_number", "_ref")) and isinstance(field.get("value"), str)
        }
    )
    folded = {_fold(i) for i in identifiers}
    checks.add(
        "typed: folding the confusables keeps every declared identifier distinct",
        len(folded) == len(identifiers) > 0,
        f"{len(identifiers)} identifiers fold to {len(folded)} distinct strings",
    )


# --------------------------------------------------------------- why there are four metrics


def check_four_metrics(records: list[dict[str, Any]], manifest: dict[str, Any], scratch: Path, checks: Checks) -> None:
    """The run that guesses and the run that declines, side by side."""
    case7 = manifest["mess_cases"]["7"]
    guessed = copy.deepcopy(records)
    found = False
    for record in guessed:
        if record.get("document_id") == case7["document_id"]:
            record["fields"][case7["field"]] = {
                "value": "18400.00",
                "confidence": "high",
                "evidence": {"pages": [1], "quote": "Total due 18,400.00"},
            }
            found = True
    checks.add(
        "four metrics: case 7's cropped total was found in the run to overwrite",
        found,
        f"{case7['document_id']}.{case7['field']}",
    )
    if not found:
        return

    declined = score(write(records, scratch / "declined.json"), "check-declined", scratch)
    invented = score(write(guessed, scratch / "guessed.json"), "check-guessed", scratch)

    accuracy_gap = abs(declined["metrics"]["field_accuracy"] - invented["metrics"]["field_accuracy"])
    recall_gap = abs(declined["metrics"]["flag_recall"] - invented["metrics"]["flag_recall"])

    checks.add(
        "four metrics: guessing a cropped total barely dents accuracy (<0.5 points)",
        accuracy_gap < 0.005,
        f"{declined['metrics']['field_accuracy']:.1%} -> "
        f"{invented['metrics']['field_accuracy']:.1%}, gap {accuracy_gap * 100:.2f} points",
    )
    # Expressed as a ratio, not as a number of points. It read `>= 0.2` and was called
    # "twenty-five points", which was true only while the corpus had four genuinely hard fields:
    # losing one of four is 25 points, losing one of eight is 12.5, and the assertion failed
    # when the statements were correctly declared unreadable. The size of the penalty depends on
    # the corpus. The claim that survives is the one worth asserting -- that guessing is cheap
    # on accuracy and expensive on recall, which is the whole argument for reading recall first.
    hard = sum(
        1
        for e in manifest["documents"]
        for field in (e.get("fields") or {}).values()
        if field.get("expected_confidence", "high") != "high"
    )
    checks.add(
        "four metrics: and costs it a full share of flag recall, orders more than accuracy",
        recall_gap >= 0.9 / hard and recall_gap > accuracy_gap * 20,
        f"{declined['metrics']['flag_recall']:.1%} -> {invented['metrics']['flag_recall']:.1%}, "
        f"gap {recall_gap * 100:.1f} points on {hard} hard fields, "
        f"{recall_gap / accuracy_gap:.0f}x the accuracy penalty"
        if accuracy_gap
        else f"gap {recall_gap * 100:.1f} points, accuracy unmoved",
    )

    # Two real runs of the same Skills scored an identical 100% on all four metrics while one
    # handed a reviewer 8 rows and the other 12 -- the extra four being the rest of the totals
    # block on the cropped invoice, undeclared by the manifest because they are not on the page.
    # `declined` was neutral to every metric, so the measurement could not see fifty percent more
    # review work. It is charged to precision now, and these two assertions are what stop it
    # drifting back: a decline of an undeclared field must cost precision and must not cost
    # accuracy, because it puts a row in front of a human without asserting anything.
    padded = copy.deepcopy(records)
    block = ("subtotal", "tax_amount", "net_amount", "retainer_credit")
    added = 0
    for record in padded:
        if record.get("document_id") == case7["document_id"]:
            for name in block:
                record["fields"][name] = {
                    "value": None,
                    "confidence": "unreadable",
                    "reason": "the totals block is below the scan margin",
                }
                added += 1
    reciting = score(write(padded, scratch / "reciting.json"), "check-reciting", scratch)

    checks.add(
        "declined: naming the rest of a cropped block costs flag precision",
        added == len(block)
        and reciting["metrics"]["flag_precision"] < declined["metrics"]["flag_precision"],
        f"{added} undeclared fields declined: "
        f"{declined['metrics']['flag_precision']:.1%} -> "
        f"{reciting['metrics']['flag_precision']:.1%}",
    )
    checks.add(
        "declined: and costs nothing on accuracy, having asserted no value",
        reciting["metrics"]["field_accuracy"] == declined["metrics"]["field_accuracy"],
        f"accuracy unmoved at {reciting['metrics']['field_accuracy']:.1%}",
    )
    checks.add(
        "four metrics: the invented figure is hallucinated, not wrong",
        invented["counts"]["hallucinated"] == 1 and invented["counts"]["wrong"] == 0,
        f"hallucinated {invented['counts']['hallucinated']}, wrong {invented['counts']['wrong']}",
    )
    checks.add(
        "four metrics: case 7's verdict reads 'guessed'",
        invented["mess_cases"]["7"]["verdict"] == "guessed",
        invented["mess_cases"]["7"]["verdict"],
    )

    # Case 2 is the legible control, and it is the only thing flag precision can fall on.
    cautious = copy.deepcopy(records)
    lowered = 0
    for record in cautious:
        for field in (record.get("fields") or {}).values():
            if field.get("confidence") == "high":
                field["confidence"] = "low"
                lowered += 1
    timid = score(write(cautious, scratch / "cautious.json"), "check-cautious", scratch)
    checks.add(
        "four metrics: flagging everything scores full recall -- caution is not free elsewhere",
        timid["metrics"]["flag_recall"] == 1.0,
        f"recall {timid['metrics']['flag_recall']:.1%} after lowering {lowered} fields",
    )
    checks.add(
        "four metrics: ...and flag precision is the number that collapses",
        timid["metrics"]["flag_precision"] < 0.1,
        f"precision {timid['metrics']['flag_precision']:.1%}, "
        f"over_flagged {timid['counts']['over_flagged']}",
    )
    checks.add(
        "four metrics: a run that flags nothing has null precision, not zero",
        _no_flags(records, scratch)["metrics"]["flag_precision"] is None,
        f"{_no_flags(records, scratch)['metrics']['flag_precision']}",
    )


def _no_flags(records: list[dict[str, Any]], scratch: Path) -> dict[str, Any]:
    confident = copy.deepcopy(records)
    for record in confident:
        for name in list((record.get("fields") or {})):
            field = record["fields"][name]
            if field.get("confidence") != "high":
                del record["fields"][name]
    return score(write(confident, scratch / "noflags.json"), "check-noflags", scratch)


# --------------------------------------------------------------------------------- the join


def check_join(records: list[dict[str, Any]], manifest: dict[str, Any], scratch: Path, checks: Checks) -> None:
    renamed = copy.deepcopy(records)
    for index, record in enumerate(renamed):
        record["source_file"] = f"anything-{index}.pdf"
    result = score(write(renamed, scratch / "renamed.json"), "check-renamed", scratch)
    checks.add(
        "join: rewriting every filename changes nothing -- the join is on document_id",
        all(result["metrics"][name] == 1.0 for name in METRICS),
        ", ".join(f"{n} {result['metrics'][n]}" for n in METRICS),
    )

    # The plan's reason for never joining on client name, asserted against this corpus.
    names = [
        e["fields"]["client_name"]["value"]
        for e in manifest["documents"]
        if "client_name" in e.get("fields", {})
    ]
    tails: dict[str, set[str]] = {}
    for name in names:
        tails.setdefault(" ".join(str(name).split()[-2:]), set()).add(str(name))
    collisions = {tail: v for tail, v in tails.items() if len(v) > 1}
    checks.add(
        "join: client names really are near-twins on this seed, so a name join would misjoin",
        bool(collisions),
        f"{len(collisions)} shared two-word tails, e.g. {sorted(next(iter(collisions.values())))[:2]}"
        if collisions else "no collisions on this seed",
    )

    # Case 5: the reconciler must not repair the duplicate on the run's behalf.
    unmerged = copy.deepcopy(records)
    stripped = 0
    for record in unmerged:
        if record.get("duplicate_of"):
            del record["duplicate_of"]
            stripped += 1
    result = score(write(unmerged, scratch / "unmerged.json"), "check-unmerged", scratch)
    checks.add(
        "join: a run that failed to dedupe is not silently repaired -- case 5 double-counts",
        stripped > 0 and result["mess_cases"]["5"]["verdict"] == "double-counted",
        f"{stripped} marker(s) stripped, case 5 reads {result['mess_cases']['5']['verdict']!r}",
    )
    checks.add(
        "join: and the double-count says what it costs",
        "detail" in result["mess_cases"]["5"],
        result["mess_cases"]["5"].get("detail", "no detail recorded"),
    )


# ------------------------------------------------------------------ the flawed run and the log


def check_flawed(result: dict[str, Any], scratch: Path, checks: Checks) -> None:
    exercised = [name for name in CLASSES if result["counts"][name] > 0]
    checks.add(
        "flawed: all six outcome classes are exercised",
        len(exercised) == 6,
        "all six" if len(exercised) == 6
        else "missing: " + ", ".join(n for n in CLASSES if n not in exercised),
    )
    checks.add(
        "flawed: the flag metrics both fell",
        result["metrics"]["flag_precision"] < 1.0 and result["metrics"]["flag_recall"] < 1.0,
        f"precision {result['metrics']['flag_precision']:.0%}, "
        f"recall {result['metrics']['flag_recall']:.0%}",
    )
    verdicts = {n: b["verdict"] for n, b in result["mess_cases"].items()}
    distinct = {v for v in verdicts.values() if v != "held"}
    checks.add(
        "flawed: at least four distinct non-held verdicts appear",
        len(distinct) >= 4,
        ", ".join(f"{n} {v}" for n, v in verdicts.items() if v != "held"),
    )
    checks.add(
        "flawed: a document-level case failed, not only field-level ones",
        any(verdicts[n] in ("double-counted", "force-fitted", "split-wrong", "missed")
            for n in ("3", "4", "5", "10")),
        ", ".join(f"{n} {verdicts[n]}" for n in ("3", "4", "5", "10")),
    )

    # The point of the whole schema-plus-reconciler split: shape and sense are different checks.
    if VALIDATOR.is_file():
        flawed_file = scratch / "flawed.json"
        validated = subprocess.run(
            [sys.executable, str(VALIDATOR), str(flawed_file)], capture_output=True, text=True
        )
        checks.add(
            "flawed: it is schema-valid, so only the reconciler can catch it",
            validated.returncode == 0,
            "validator exits 0" if validated.returncode == 0
            else validated.stdout.strip().splitlines()[-1][:110] if validated.stdout else "rejected",
        )


def check_log(perfect_path: Path, scratch: Path, checks: Checks) -> None:
    scores = scratch / "scores.jsonl"
    before = scores.read_text()
    lines_before = len([x for x in before.splitlines() if x.strip()])

    score(perfect_path, "check-append", scratch)
    after = scores.read_text()
    lines_after = len([x for x in after.splitlines() if x.strip()])

    checks.add(
        "log: scoring a run appends exactly one line",
        lines_after == lines_before + 1,
        f"{lines_before} -> {lines_after}",
    )
    checks.add(
        "log: and every earlier line is untouched",
        after.startswith(before),
        "existing bytes are a prefix" if after.startswith(before) else "earlier lines were rewritten",
    )

    entries = [json.loads(x) for x in after.splitlines() if x.strip()]
    checks.add(
        "log: each line carries the run name, the four metrics and the ten verdicts",
        all(
            e.get("run") and sorted(e.get("metrics", {})) == sorted(METRICS)
            and sorted(e.get("mess_cases", {}), key=int) == CASES
            for e in entries
        ),
        f"{len(entries)} lines, keys {sorted(entries[-1])}",
    )
    checks.add(
        "log: re-scoring the same input twice gives the same result",
        _stable(entries[-1], json.loads(scores.read_text().splitlines()[-1])),
        "identical apart from scored_at",
    )


def _stable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return {k: v for k, v in a.items() if k != "scored_at"} == {
        k: v for k, v in b.items() if k != "scored_at"
    }


# ------------------------------------------------------------------------------- the scorecard


def check_scorecard(scratch: Path, checks: Checks) -> None:
    result = subprocess.run(
        [sys.executable, str(SCORECARD_PY), "--out", str(scratch), "--scores", str(scratch / "scores.jsonl")],
        capture_output=True, text=True,
    )
    checks.add(
        "scorecard: scorecard.py ran",
        result.returncode == 0,
        "exit 0" if result.returncode == 0 else result.stderr[-140:],
    )
    if result.returncode != 0:
        return

    md = (scratch / "scorecard.md").read_text()
    png = scratch / "scorecard.png"

    checks.add(
        "scorecard: both reference runs appear in the table",
        "reference-perfect" in md and "reference-flawed" in md,
        "perfect and flawed both listed",
    )
    checks.add(
        "scorecard: there is a per-mess-case column with all ten rows",
        all(re.search(rf"^\| {n} \|", md, re.M) for n in CASES),
        f"{sum(1 for n in CASES if re.search(rf'^\| {n} \|', md, re.M))} of 10 case rows",
    )
    checks.add(
        "scorecard: the four metric names are all on the page",
        all(name.replace("_", " ").title().replace("Field Accuracy", "Field accuracy") in md
            or name.replace("_", " ") in md.lower() for name in METRICS),
        "all four named",
    )
    checks.add(
        "scorecard: it embeds the png",
        "scorecard.png" in md,
        "referenced from the markdown",
    )
    checks.add(
        "scorecard: the png is a real png of a plausible size",
        png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size > 30_000,
        f"{png.stat().st_size // 1024} KB" if png.is_file() else "missing",
    )


def main() -> int:
    ap = base_parser(__doc__)
    args = ap.parse_args()

    def body(_label: Any, checks: Checks) -> None:
        if not MANIFEST.is_file():
            checks.add("reconcile: there is a manifest to score against", False,
                       "run scripts/corpus.py --seed 42")
            return
        manifest = json.loads(MANIFEST.read_text())
        scratch = Path(tempfile.mkdtemp(prefix="docmess-reconcile-"))
        try:
            # Projected into scratch rather than read from extractions.json. This harness needs
            # a run it knows to be perfect, and extractions.json is whatever was scored last --
            # after the first Cowork run it held a real extraction, and every assertion below
            # would have been quietly measuring that instead. It happened to score 100%, so
            # nothing would have gone red.
            perfect_path = scratch / "perfect.json"
            subprocess.run(
                [sys.executable, str(REFERENCE), "--perfect", "--out", str(perfect_path)],
                capture_output=True, text=True, check=True,
            )
            payload = json.loads(perfect_path.read_text())
            records = payload["documents"] if isinstance(payload, dict) else payload

            perfect = score(perfect_path, "reference-perfect", scratch)
            check_perfect(perfect, checks)

            subprocess.run(
                [sys.executable, str(REFERENCE), "--flawed", "--out", str(scratch / "flawed.json")],
                capture_output=True, text=True, check=True,
            )
            flawed = score(scratch / "flawed.json", "reference-flawed", scratch)
            check_flawed(flawed, scratch, checks)

            check_typed(records, scratch, checks)
            check_four_metrics(records, manifest, scratch, checks)
            check_join(records, manifest, scratch, checks)
            check_log(perfect_path, scratch, checks)
            check_scorecard(scratch, checks)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    return run(["reconcile"], body, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
