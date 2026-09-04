#!/usr/bin/env python3
"""Validate extraction records against the DocMess contract. Bundled with the Skill.

    python scripts/validate.py extractions.json
    python scripts/validate.py extractions.json --quiet     one line per failure, no summary

Exits 0 if every record holds, non-zero otherwise, naming the JSON pointer of each breach.

This runs inside Cowork's sandbox, so it imports the standard library and ``jsonschema`` and
nothing else. There is no import of anything from the DocMess repository, because at the point
this executes the repository is not there -- the Skill is a zip that was uploaded, and this
file and the schema beside it are all of it.

Why a bundled script rather than a paragraph in SKILL.md
--------------------------------------------------------
Because a rule stated in a prompt is a rule the model may decline to follow, and nothing will
notice. A rule enforced by a non-zero exit code is a rule.

The fingerprint on the first line -- ``docmess-validate/1`` -- exists for a specific failure
that is otherwise invisible. **A Skill uploaded as a bare SKILL.md ships without its bundled
scripts, and a capable model asked to run a validator that is not there will write one instead
of failing.** The substitute works. It validates something, it prints something plausible, it
exits 0, and the transcript reads as a clean run. It is not this contract, and the difference
does not show up until the scorecard is inexplicably good.

So there is one string in the output that a reimplementation would have no reason to produce,
and ``check_schema.py`` asserts on it rather than on the exit code alone. If you are reading
this because that assertion failed: the Skill was packaged wrongly. Zip the *directory*.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

FINGERPRINT = "docmess-validate/1"

try:
    from jsonschema import Draft202012Validator, ValidationError, validators
except ImportError:  # pragma: no cover
    sys.exit(f"{FINGERPRINT} error: jsonschema is not installed. pip install jsonschema")

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "extraction.schema.json"


def _pages_ascending(
    validator: Any, value: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    """The one rule JSON Schema cannot express, as a keyword.

    There is no draft 2020-12 spelling of "the second element of this array is not less than
    the first" -- no keyword compares two instance locations, and ``$data`` never landed. The
    choice was a custom keyword or moving the rule into prose, and prose is not a contract.

    The cost is that unknown keywords are *ignored* by design, so a stock Draft202012Validator
    passes ``[4, 3]`` silently. Anything validating this schema has to go through the extended
    validator below. ``check_schema.py`` asserts that a stock validator does not catch it, so
    that the day somebody swaps them a test explains why that is wrong.
    """
    if value is not True:
        return
    if not isinstance(instance, list) or len(instance) != 2:
        return  # type, minItems and maxItems already own those
    first, last = instance
    if any(isinstance(p, bool) or not isinstance(p, int) for p in (first, last)):
        return
    if last < first:
        yield ValidationError(
            f"page range runs backwards: last page {last} precedes first page {first}"
        )


DocMessValidator = validators.extend(
    Draft202012Validator, {"pagesAscending": _pages_ascending}
)


def load(path: Path) -> list[dict[str, Any]]:
    """Accept a bare list, or an object with a ``documents`` or ``records`` key.

    Three shapes because a run may hand back any of them, and rejecting a valid extraction over
    its envelope would teach the model to reshape its output until the validator stopped
    complaining -- which is the opposite of what this is for.
    """
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{FINGERPRINT} error: {path} is not valid JSON -- {exc}")

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("documents", "records", "extractions"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    raise SystemExit(f"{FINGERPRINT} error: {path} is neither a record nor a list of records")


def describe(error: ValidationError, index: int) -> str:
    """A pointer a person can act on, and the contract sentence behind the breach.

    A raw jsonschema message names the keyword that fired, which is accurate and unhelpful:
    "'reason' is a required property" does not say that unreadable means the figure was not
    read and something has to say why. The mapping below turns the keyword back into the rule.
    """
    pointer = error.json_path.replace("$", f"$[{index}]", 1)
    detail = error.message

    field = None
    parts = list(error.absolute_path)
    if len(parts) >= 2 and parts[0] == "fields":
        field = str(parts[1])

    if error.validator == "required" and field:
        missing = detail.split("'")[1] if "'" in detail else "a property"
        if missing == "reason":
            detail = (
                "reason is required. An unreadable field has to say why it could not be read, "
                "and a low has to state the inference the reading depends on"
            )
        elif missing == "evidence":
            detail = (
                "evidence is required on any field that carries a value. It is where on the "
                "page the figure was read, precise enough for a person to go and look"
            )
        elif missing == "value":
            detail = "value is required, and is null only when confidence is unreadable"
    elif error.validator == "not" and field:
        detail = (
            "this shape is not one of the three legal ones: high takes evidence and no reason, "
            "low takes both, unreadable takes reason and no evidence"
        )
    elif error.validator == "enum" and error.absolute_path and error.absolute_path[-1] == "confidence":
        detail = f"{detail}. confidence is one of three words and never a number"
    elif error.validator == "enum" and list(error.absolute_path) == ["fields"]:
        detail = f"{detail} -- this field name is not in the schema's vocabulary"
    elif error.validator == "maxProperties":
        detail = (
            "an out_of_scope document carries no fields. Do not force-fit it to the schema"
        )
    elif error.validator == "pagesAscending":
        detail = f"{detail}. pages is [first, last], inclusive and 1-based"

    return f"{pointer}: {detail}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate DocMess extraction records.")
    ap.add_argument("path", type=Path, help="extractions.json, or any file holding records")
    ap.add_argument("--quiet", "-q", action="store_true", help="Failures only.")
    args = ap.parse_args()

    if not SCHEMA.is_file():
        raise SystemExit(
            f"{FINGERPRINT} error: no schema at {SCHEMA}.\n"
            "This Skill was packaged without its schema/ directory. Zip the Skill directory, "
            "not the SKILL.md."
        )
    if not args.path.is_file():
        raise SystemExit(f"{FINGERPRINT} error: no such file: {args.path}")

    validator = DocMessValidator(json.loads(SCHEMA.read_text()))
    records = load(args.path)

    failures: list[str] = []
    bad_records = 0
    for index, record in enumerate(records):
        errors = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
        if errors:
            bad_records += 1
            failures.extend(describe(error, index) for error in errors)

    print(f"{FINGERPRINT}  {len(records)} records  {args.path}")
    if failures:
        for line in failures:
            print(f"  FAIL  {line}")
        print(
            f"\n{bad_records} of {len(records)} records break the contract "
            f"({len(failures)} breaches). Fix the records, not the validator."
        )
        return 1

    if not args.quiet:
        flagged = sum(
            1
            for r in records
            for f in (r.get("fields") or {}).values()
            if isinstance(f, dict) and f.get("confidence") in ("low", "unreadable")
        )
        fields = sum(len(r.get("fields") or {}) for r in records)
        print(f"  ok    {fields} fields, {flagged} flagged for review, 0 breaches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
