#!/usr/bin/env python3
"""Score a run against the manifest, field by field, into seven outcome classes.

    scripts/reconcile.py --run cowork-01
    scripts/reconcile.py --run reference-perfect --in extractions.json
    scripts/reconcile.py --run x --quiet          write the result, print nothing

Reads `extractions.json` and `corpus/manifest.json`, writes `scorecard/<run>.json` and appends
one line to `scorecard/scores.jsonl`.

Accuracy alone is a vibe
------------------------
A single percentage cannot separate the two behaviours that matter most, and they are
opposites. Take case 7, whose total is physically cropped off the page. A run that answers
``unreadable`` is doing the right thing; a run that produces a confident figure has invented
one, which is the exact failure the brief asks the pipeline to prevent.

Measured on this corpus, the difference between those two runs is:

    field accuracy      100.0%  ->   99.9%      0.14 points
    flag recall         100.0%  ->   75.0%     25    points

Accuracy moves by a seventh of a percentage point, because 715 of 716 fields are still right --
one invented figure is a rounding error against a corpus of legible pages, and it always will
be. Anybody shown only that number would sign the pipeline off. So the number that has to carry
the finding is flag recall, and check_reconcile.py asserts both figures above.

So four metrics, and the interesting ones are the last two:

    field accuracy       of the values it gave, how many were right
    coverage             of the values the corpus declares, how many it produced at all
    flag precision       of the fields it flagged, how many were genuinely hard
    flag recall          of the genuinely hard fields, how many it flagged

**Precision and recall are the pair that stops caution being free.** Nine of the ten mess cases
reward flagging, so a run that flagged every field would score perfectly on recall while being
useless -- and flag precision is the only number that falls when it does. Case 2, the legible
control, exists to give precision something to fall on.

The join
--------
On ``document_id`` and nothing else. Not the filename -- case 5 puts one document under two of
them, and case 3 puts two documents under one. And **never the client name**: BizData composes
names from a shared pool, and on eleven of seventeen fixture seeds two clients share every word
but the first, so a name join matches the wrong client and reports it as a model error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import DEMO_SEED  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "corpus" / "manifest.json"
EXTRACTIONS = REPO / "extractions.json"
SCORECARD = REPO / "scorecard"
SCORES = SCORECARD / "scores.jsonl"

# The seven outcome classes, in the order they appear on the scorecard. Every field the corpus
# declares or the run produced lands in exactly one of them.
CLASSES = [
    "correct",             # a value, and it matches
    "correctly_flagged",   # flagged, and the corpus agrees it is hard
    "wrong",               # a value, and it does not match
    "hallucinated",        # a confident value where the corpus says the figure is not readable
    "missed",              # the corpus declares it, the run did not produce it
    "over_flagged",        # flagged, and the corpus says it was perfectly legible
    "declined",            # named a field the corpus does not declare, and gave it no value
]

# `declined` exists because the six classes above had no room for a run that says "subtotal:
# unreadable, the page is cropped" on a document whose subtotal the manifest does not declare
# *because* the page is cropped. That was landing in `hallucinated` -- the class reserved for
# inventing a figure -- and dragging the whole mess case to a verdict of `guessed`, which was the
# exact opposite of what the run did. A field with no value asserts nothing, and nothing cannot
# be a hallucination. So it stays out of accuracy.
#
# It was briefly out of *every* metric, and that was wrong. Two runs of the same Skills scored
# an identical 100% on all four while handing a reviewer 8 rows and 12 rows respectively -- the
# extra four being `subtotal`, `tax_amount`, `net_amount` and `retainer_credit` on the one
# invoice whose totals block is below the scan margin, each row saying the same thing a fifth
# row already said. A measurement that cannot see fifty percent more review work is not
# measuring review. It now counts in `flag_precision`'s denominator: no value asserted, so
# accuracy is untouched, but the row still costs someone a look.
#
# There is a sharper reason than reviewer load, and it is why this is a fault and not a taste.
# Naming the four fields of a block you cannot see is a claim about what was cropped, and the
# crop is precisely what cannot be observed. The page is cut mid-line-item: there may have been
# a fourth line item, a discount, a late fee. A run that lists exactly the fields a typical
# invoice carries is not reading the page, it is reciting the template -- the thing the Skill
# already forbids under "do not invent a field because similar documents have one".

# The order the per-field list is written in -- not CLASSES, and not alphabetical. The artifact
# carries every field so that SQL can rebuild the denominators, and `correct` sorting first
# would bury the twenty lines somebody actually wants under seven hundred that are fine.
REPORT_ORDER = [
    "hallucinated",
    "declined",
    "wrong",
    "missed",
    "over_flagged",
    "correctly_flagged",
    "correct",
]

# Glyphs that are the same bitmap, or nearly, in the fonts these documents are set in.
# Step 0's spike found this and it is not theoretical: I and l are indistinguishable in
# Helvetica-Bold, so a reference read as SOW-2O25-Ol9 is a *rendering* problem being scored as
# a model error. The generator already excludes I, O, C and G from the codes it mints, so this
# only fires on a genuine misread -- but it fires the right way.
CONFUSABLE = str.maketrans({"I": "1", "l": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"})


# ------------------------------------------------------------------------------ comparison


def as_decimal(value: Any) -> Decimal | None:
    """A money string as a Decimal, or None if it is not money.

    Handles the European format too. A run that failed to normalise case 6 and handed back
    ``"1.234,56"`` should be scored as *wrong*, not as a crash, and the only way to know it is
    wrong rather than unparseable is to parse it.
    """
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    text = value.strip().replace(" ", "").replace("\u00a0", "")
    text = re.sub(r"^[A-Z]{0,3}[£$€]?", "", text)
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d{2}", text):  # 1.234,56
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def matches(declared: Any, produced: Any) -> bool:
    """Typed comparison, chosen by what the declared value is.

    Naive string equality fails in four ways that are all the corpus's fault rather than the
    model's, and each would show up on the scorecard as a wrong answer:

    - ``"1200.00"`` against ``"1200.0"``, or against the number ``1200.0``
    - ``"1.234,56"`` correctly normalised to ``"1234.56"``
    - a date rendered ``03/09/2026`` and correctly resolved to ``2026-09-03``
    - a client name with a non-breaking space or a curly apostrophe in it

    Money compares as Decimal, dates as dates, lists as sets of compared elements, and text
    after normalising whitespace, case and Unicode form. Identifiers additionally fold the
    glyphs that are the same bitmap in these fonts -- see CONFUSABLE.
    """
    if declared is None or produced is None:
        return declared == produced

    if isinstance(declared, list):
        if not isinstance(produced, list) or len(declared) != len(produced):
            return False
        return all(matches(a, b) for a, b in zip(sorted(map(str, declared)), sorted(map(str, produced))))

    if isinstance(declared, bool) or isinstance(produced, bool):
        return declared == produced

    left, right = as_decimal(declared), as_decimal(produced)
    if left is not None and right is not None:
        return left == right

    a, b = str(declared).strip(), str(produced).strip()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a):
        return _same_date(a, b)
    if re.fullmatch(r"\d{4}-\d{2}", a):
        return b.strip()[:7] == a or _same_month(a, b)

    a = unicodedata.normalize("NFKC", a)
    b = unicodedata.normalize("NFKC", b)
    a = re.sub(r"\s+", " ", a).replace("\u2019", "'")
    b = re.sub(r"\s+", " ", b).replace("\u2019", "'")
    if a.casefold() == b.casefold():
        return True

    # Only for things that look like identifiers, and only after plain comparison has failed.
    # Applying the fold to prose would make "Ollie" and "0llie" the same client.
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-/]{3,}", a, re.IGNORECASE):
        return fold(a) == fold(b)
    return False


def fold(text: str) -> str:
    """Collapse the glyphs that share a bitmap, then case.

    The order matters and getting it wrong is silent. Upper-casing first turns a misread ``l``
    into ``L``, which is not in the table, so ``INV-2026-09l2`` stops folding onto
    ``INV-2026-0912`` and gets scored as a wrong answer -- the exact mis-scoring step 0 said to
    avoid, reintroduced by a method call in the wrong order. Translate on the original case,
    where ``l`` and ``I`` are still distinguishable from ``L``, then upper-case the rest.
    """
    return text.translate(CONFUSABLE).upper()


def _same_date(iso: str, other: str) -> bool:
    other = other.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(other, fmt).date().isoformat() == iso
        except ValueError:
            continue
    return False


def _same_month(period: str, other: str) -> bool:
    for fmt in ("%Y-%m", "%B %Y", "%b %Y", "%m/%Y"):
        try:
            return datetime.strptime(other.strip(), fmt).strftime("%Y-%m") == period
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------- the scoring


def merge(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop what the run marked as a duplicate. Returns the kept records and the dropped ones.

    Deduplication is the *run's* job and this only honours it. A reconciler that merged by
    ``document_id`` itself would silently repair case 5 and then score the run as having got it
    right, which is the reconciler grading its own correction.
    """
    kept = [r for r in records if not r.get("duplicate_of")]
    return kept, [r for r in records if r.get("duplicate_of")]


def classify_field(declared: dict[str, Any], produced: dict[str, Any] | None) -> str:
    """One field into one of the six classes a *declared* field can land in.

    The ordering of the branches is the argument. A confident value on a field the corpus says
    is unreadable is ``hallucinated`` and not ``wrong``, because those are different failures:
    wrong is a misreading, hallucinated is an invention presented as a reading, and the second
    is the one the confidence contract exists to prevent. Collapsing them would hide the only
    result the brief actually asks about.
    """
    expected = declared.get("expected_confidence", "high")

    if produced is None:
        return "missed"

    confidence = produced.get("confidence")
    value = produced.get("value")

    if confidence == "unreadable" or value is None:
        # It declined to give a value. Right if the corpus agrees the figure is not there,
        # and an over-flag if it was perfectly legible.
        return "correctly_flagged" if expected == "unreadable" else "over_flagged"

    if expected == "unreadable":
        # The corpus says this figure is not on the page and the run produced one anyway.
        return "hallucinated"

    if not matches(declared["value"], value):
        return "wrong"

    if confidence == "low":
        # Right, and flagged. Caution is correct where the corpus expected it and a false
        # positive where it did not -- this is the branch case 2 exists to exercise.
        return "correctly_flagged" if expected == "low" else "over_flagged"

    return "correct"


def score(manifest: dict[str, Any], payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    records = payload["documents"] if isinstance(payload, dict) else payload
    kept, dropped = merge(records)

    truth = {e["document_id"]: e for e in manifest["documents"] if e["document_id"]}
    by_id: dict[str, dict[str, Any]] = {}
    extra: list[dict[str, Any]] = []
    for record in kept:
        key = record.get("document_id")
        if key and key in truth and key not in by_id:
            by_id[key] = record
        else:
            extra.append(record)

    counts = {name: 0 for name in CLASSES}
    fields: list[dict[str, Any]] = []
    # Seeded with all ten, because three of them are not about a field value and would
    # otherwise never appear. Case 3 is about page ranges, case 5 about deduplication and case
    # 10 about classification -- none of those documents has a field to score, so a per-case
    # column built only from the field loop shows seven cases and looks complete.
    per_case: dict[str, dict[str, int]] = {
        str(n): {name: 0 for name in CLASSES} for n in range(1, 11)
    }

    def note(case_numbers: list[int], outcome: str) -> None:
        for number in case_numbers:
            per_case[str(number)][outcome] += 1

    for document_id, declared_doc in truth.items():
        produced_doc = by_id.get(document_id)
        cases = declared_doc.get("mess_cases", [])

        for name, declared in declared_doc["fields"].items():
            produced = (produced_doc.get("fields") or {}).get(name) if produced_doc else None
            outcome = classify_field(declared, produced)
            counts[outcome] += 1
            note(cases, outcome)
            # Every field, not only the failures. A run artifact that lists what went wrong
            # cannot be audited by anything except itself, because a denominator cannot be
            # reconstructed from a list of numerators -- and step 7 recomputes all four metrics
            # in SQL specifically so that two implementations have to agree.
            fields.append(
                {
                    "document_id": document_id,
                    "field": name,
                    "outcome": outcome,
                    "declared": declared["value"],
                    "expected_confidence": declared.get("expected_confidence", "high"),
                    "produced": (produced or {}).get("value"),
                    "confidence": (produced or {}).get("confidence"),
                    "mess_cases": cases,
                }
            )

        # A field the run invented. Scored separately from `wrong`, because inventing a field
        # the document does not carry is a different mistake from misreading one it does --
        # unless it gave it no value, in which case it invented nothing. See `declined`.
        for name in (produced_doc.get("fields") or {}) if produced_doc else {}:
            if name not in declared_doc["fields"]:
                entry = produced_doc["fields"][name]
                asserts_nothing = (
                    entry.get("value") is None or entry.get("confidence") == "unreadable"
                )
                outcome = "declined" if asserts_nothing else "hallucinated"
                counts[outcome] += 1
                note(cases, outcome)
                fields.append(
                    {
                        "document_id": document_id,
                        "field": name,
                        "outcome": outcome,
                        "declared": None,
                        "expected_confidence": "absent",
                        "produced": entry.get("value"),
                        "confidence": entry.get("confidence"),
                        "mess_cases": cases,
                    }
                )

    # --- document-level outcomes ------------------------------------------------------
    classified = {
        document_id: (record or {}).get("document_type")
        for document_id, record in ((k, by_id.get(k)) for k in truth)
    }
    misclassified = [
        {"document_id": k, "declared": truth[k]["document_type"], "produced": v}
        for k, v in classified.items()
        if v is not None and v != truth[k]["document_type"]
    ]
    not_found = [k for k in truth if k not in by_id]

    declared_dupes = {
        e["document_id"] for e in manifest["documents"] if e.get("also_filed_as")
    }
    caught = {r["duplicate_of"] for r in dropped if r.get("duplicate_of")}
    force_fitted = [
        {"source_file": r.get("source_file"), "produced": r.get("document_type")}
        for r in extra
        if r.get("document_type") != "out_of_scope" and r.get("fields")
    ]
    declared_oos = {
        e["source_file"] for e in manifest["documents"] if e["document_type"] == "out_of_scope"
    }
    found_oos = {r.get("source_file") for r in kept if r.get("document_type") == "out_of_scope"}

    # --- the four metrics ---------------------------------------------------------------
    valued = counts["correct"] + counts["wrong"] + counts["hallucinated"]
    declared_total = sum(len(e["fields"]) for e in truth.values())
    produced_total = declared_total - counts["missed"]
    # Everything that puts a row in front of a human. `declined` is in here for the same reason
    # `over_flagged` is: the manifest does not declare the field, so the row is work the ground
    # truth says was not needed. It is out of `valued` and so cannot touch accuracy -- a field
    # with no value still asserts nothing -- but review is the cost it does impose, and precision
    # is the metric that charges for it.
    flagged = counts["correctly_flagged"] + counts["over_flagged"] + counts["declined"]
    genuinely_hard = sum(
        1
        for e in truth.values()
        for f in e["fields"].values()
        if f.get("expected_confidence", "high") != "high"
    )
    caught_hard = counts["correctly_flagged"]

    # Recall asks whether the run raised its hand on the hard fields; precision asks whether it
    # was right to. So recall counts any hedge -- `low` or `unreadable` -- and not only the one
    # the corpus expected.
    #
    # The two are deliberately different questions, and collapsing them lost a real distinction.
    # One run reported both statement totals `unreadable`; another supplied the derived figure at
    # `low`, saying the cell holds an uncalculated =SUM() and the number is computed rather than
    # read. The second is worse -- it asserts a total the document never states, and it is still
    # counted as `hallucinated` in accuracy, where the damage actually is -- but it is not the
    # same as saying nothing. Both send a reviewer to the same cell with the same warning. Scored
    # against `correctly_flagged` alone it registered as a total failure to flag, which describes
    # a silence that did not happen.
    hand_raised = sum(
        1
        for f in fields
        if f["expected_confidence"] in ("low", "unreadable")
        and f.get("confidence") in ("low", "unreadable")
    )

    metrics = {
        "field_accuracy": _ratio(counts["correct"], valued),
        "coverage": _ratio(produced_total, declared_total),
        "flag_precision": _ratio(caught_hard, flagged),
        "flag_recall": _ratio(hand_raised, genuinely_hard),
    }

    document_verdicts = document_level(manifest, kept, by_id, truth, caught, force_fitted, found_oos)

    return {
        "seed": manifest["seed"],
        "period": manifest["period"],
        "kind": payload.get("kind", "run") if isinstance(payload, dict) else "run",
        "counts": {
            **counts,
            "declared_fields": declared_total,
            "documents_declared": len(truth),
            "documents_matched": len(by_id),
            "document_instances": len(records),
            "genuinely_hard_fields": genuinely_hard,
            "flagged_fields": flagged,
            "hard_fields_flagged": hand_raised,
        },
        "metrics": metrics,
        "documents": {
            "not_found": sorted(not_found),
            "misclassified": misclassified,
            "unrecognised": [r.get("source_file") for r in extra],
            "duplicates_declared": sorted(declared_dupes),
            "duplicates_caught": sorted(caught),
            "out_of_scope_declared": sorted(declared_oos),
            "out_of_scope_found": sorted(x for x in found_oos if x),
            "force_fitted": force_fitted,
        },
        "mess_cases": {
            number: {
                **bucket,
                "verdict": worst(verdict(bucket), document_verdicts.get(number, "held")),
                **({"detail": document_verdicts[number + "!"]} if number + "!" in document_verdicts else {}),
            }
            for number, bucket in sorted(per_case.items(), key=lambda kv: int(kv[0]))
        },
        "fields": sorted(
            fields,
            key=lambda f: (REPORT_ORDER.index(f["outcome"]), f["document_id"], f["field"]),
        ),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    """None rather than zero when there is nothing to divide by.

    A run that flagged nothing has no flag precision -- not a precision of 0%. Reporting zero
    would make a run that never flagged look worse than one that flagged badly, and it would
    put a number on the scorecard that no observation supports.
    """
    return None if not denominator else round(numerator / denominator, 4)


def verdict(bucket: dict[str, int]) -> str:
    """One word per mess case, from its field outcomes.

    `declined` is deliberately absent, and stays absent now that it counts against precision.
    Case 7 asks one question -- did the run guess a total it could not see -- and a run that
    declined the total held it, whatever else it declined alongside. Letting `declined` set a
    verdict is how case 7 came to be reported as `guessed` on a run that held it.

    The over-claiming that comes with it is real and is charged for, once, in `flag_precision`.
    Charging it here as well would price the same mistake twice and would blur what the grid is
    for: the grid says which defect was survived, the metrics say at what cost.
    """
    if bucket["hallucinated"]:
        return "guessed"
    if bucket["wrong"]:
        return "misread"
    if bucket["missed"]:
        return "missed"
    if bucket["over_flagged"]:
        return "over-flagged"
    return "held"


# Worst first. A case with a guessed field and a held one is not half held.
SEVERITY = [
    "guessed",
    "double-counted",
    "force-fitted",
    "split-wrong",
    "misread",
    "missed",
    "over-flagged",
    "held",
]


def worst(*verdicts: str) -> str:
    return min(verdicts, key=lambda v: SEVERITY.index(v) if v in SEVERITY else len(SEVERITY))


def document_level(
    manifest: dict[str, Any],
    kept: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    truth: dict[str, dict[str, Any]],
    caught: set[str],
    force_fitted: list[dict[str, Any]],
    found_oos: set[Any],
) -> dict[str, str]:
    """Verdicts for the four cases that are not about a field's value.

    Cases 3, 5 and 10 have no field outcome at all -- the documents involved either carry no
    fields or carry perfectly ordinary ones -- so scoring them from the field loop alone would
    report them as held no matter what the run did with them. Case 4 does have fields, but its
    actual rule is about the `supersedes` link rather than about any figure, and an amendment
    read as a second engagement gets every field right while defeating the case entirely.
    """
    block = manifest["mess_cases"]
    out: dict[str, str] = {}

    # 3 -- a file is not a document. Both documents found, on the right page ranges.
    wanted = [d for d in block["3"]["documents"] if d]
    found = [d for d in wanted if d in by_id]
    pages_right = all(
        by_id[d].get("pages") == truth[d]["pages"] for d in found if d in truth
    )
    if len(found) < len(wanted):
        out["3"] = "missed"
        out["3!"] = f"{len(found)} of {len(wanted)} documents found in {block['3']['file']}"
    elif not pages_right:
        out["3"] = "split-wrong"
        out["3!"] = "both documents found, but the page ranges do not match"
    else:
        out["3"] = "held"

    # 4 -- the amendment supersedes, and says so.
    amendments = [d for d in truth if truth[d]["document_type"] == "sow_amendment"]
    linked = [d for d in amendments if d in by_id and by_id[d].get("supersedes") == truth[d]["supersedes"]]
    if len(linked) < len(amendments):
        out["4"] = "missed"
        out["4!"] = f"{len(linked)} of {len(amendments)} amendments carry the right supersedes link"
    else:
        out["4"] = "held"

    # 5 -- the duplicate. The failure here is silent and expensive: the invoice is counted
    # twice and every total is over by its value.
    five = block["5"]["document_id"]
    if five in caught:
        out["5"] = "held"
    else:
        out["5"] = "double-counted"
        declared = truth[five]["fields"].get("total_due", {}).get("value")
        out["5!"] = f"{five} counted twice; totals over by {declared}"

    # 10 -- the sandwich. Force-fitting is worse than merely missing one, because the schema
    # was built specifically to leave nowhere to force-fit to.
    declared_oos = {e["source_file"] for e in manifest["documents"] if e["document_type"] == "out_of_scope"}
    if force_fitted:
        out["10"] = "force-fitted"
        out["10!"] = ", ".join(
            f"{Path(f['source_file']).name} read as {f['produced']}" for f in force_fitted[:2]
        )
    elif found_oos >= declared_oos:
        out["10"] = "held"
    else:
        out["10"] = "missed"
        out["10!"] = f"{len(declared_oos - found_oos)} of {len(declared_oos)} not recognised as out of scope"

    return out


# ---------------------------------------------------------------------------- entry point


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run", required=True, help="A name for this run. Appears on the scorecard.")
    ap.add_argument("--in", dest="source", type=Path, default=EXTRACTIONS)
    ap.add_argument("--seed", type=int, default=DEMO_SEED)
    ap.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST,
        help="Score against a different corpus's ground truth -- a held-out seed, to find "
        "out whether a Skill edit generalises or was fitted to the documents it was written "
        "while looking at.",
    )
    ap.add_argument(
        "--skill",
        default="v1",
        help="Which version of the Skills produced this run. Runs sharing a label are the same "
        "configuration, so the scorecard can show their spread rather than implying that two "
        "bars of different heights mean something changed.",
    )
    ap.add_argument(
        "--kind",
        choices=("run", "reference", "superseded"),
        help="Override the kind. `superseded` is for a scoring kept for the record but no "
        "longer comparable -- a run measured against a manifest that has since been corrected.",
    )
    # So check_reconcile.py can score into a scratch directory. A harness that appended to the
    # real scores.jsonl would put its own throwaway runs on the README's scorecard, and the
    # only way to get them off again would be to edit a file whose whole promise is append-only.
    ap.add_argument("--scorecard", type=Path, default=SCORECARD)
    ap.add_argument("--quiet", "-q", action="store_true")
    args = ap.parse_args()

    if not args.manifest.is_file():
        raise SystemExit(f"error: no manifest at {args.manifest}.\n  scripts/corpus.py --seed {args.seed}")
    if not args.source.is_file():
        raise SystemExit(
            f"error: no extractions at {args.source}.\n"
            "  scripts/reference_run.py --perfect     (a projection, not a run)\n"
            "  or bank a Cowork run -- see docs/notes/step-5-cowork-runbook.md"
        )

    manifest = json.loads(args.manifest.read_text())
    result = score(manifest, json.loads(args.source.read_text()))
    result["run"] = args.run
    result["skill"] = args.skill
    if args.kind:
        result["kind"] = args.kind
    result["scored_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["source"] = str(args.source.name)

    args.scorecard.mkdir(parents=True, exist_ok=True)
    (args.scorecard / f"{args.run}.json").write_text(json.dumps(result, indent=2) + "\n")

    # Append-only. Every run stays, including the bad ones -- a scorecard that only kept the
    # best result would be marketing, and the before-and-after is the whole point.
    summary = {
        k: result[k]
        for k in ("run", "kind", "skill", "scored_at", "seed", "source", "metrics", "counts")
    }
    summary["mess_cases"] = {n: b["verdict"] for n, b in result["mess_cases"].items()}
    with (args.scorecard / SCORES.name).open("a") as handle:
        handle.write(json.dumps(summary) + "\n")

    if not args.quiet:
        m = result["metrics"]
        c = result["counts"]
        print(f"{args.scorecard / f'{args.run}.json'}")
        print(f"  run {args.run} ({result['kind']}), seed {result['seed']}")
        for name in ("field_accuracy", "coverage", "flag_precision", "flag_recall"):
            value = m[name]
            print(f"  {name:<16} {'n/a' if value is None else f'{value:>7.1%}'}")
        print("  " + "  ".join(f"{name} {c[name]}" for name in CLASSES))
        if result["mess_cases"]:
            print(
                "  cases: "
                + ", ".join(f"{n} {b['verdict']}" for n, b in result["mess_cases"].items())
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
