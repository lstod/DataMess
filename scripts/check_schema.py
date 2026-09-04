#!/usr/bin/env python3
"""Step 1's Done-when conditions, as assertions.

    scripts/check_schema.py            the four rejections, and the shapes that must pass
    scripts/check_schema.py -v         print the passing assertions too

The schema is the contract every later step is written against rather than around, so what
this harness is really asserting is that the contract has teeth. Four shapes must be rejected,
and **each must be rejected for the reason it is supposed to be rejected for.** That
distinction is the whole point of the file.

A record with a null value and no reason also happens to be a record the model should not have
produced for six other reasons, and a harness that only asserts "validation failed" passes
whether the confidence contract is in the schema or not. So every rejection below is asserted
on the validator's own error path -- the keyword that fired and the instance location it fired
at -- and a rejection that arrives from the wrong keyword is reported as a failure even though
the document was, in the end, rejected.

The four are the plan's:

    a null value with no reason
    a non-null value with no evidence
    an unknown document_type
    a pages range that runs backwards

Two more are asserted alongside them because they are the same contract seen from the other
side: a low with no reason, and an out_of_scope carrying fields.

At step 4 this harness grew a second half, which runs the same rejections through
skills/extract-to-schema/scripts/validate.py as a subprocess rather than through the schema
directly. That half is what fails when the Skill ships without its bundled script -- see
check_validator() -- and it is skipped with a stated reason rather than silently when the
script is absent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

try:
    from jsonschema import Draft202012Validator, ValidationError, validators
except ImportError:  # pragma: no cover
    sys.exit(
        "error: jsonschema is not installed.\n"
        "  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import Checks, base_parser, run  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "schema" / "extraction.schema.json"
VALIDATOR_SCRIPT = REPO / "skills" / "extract-to-schema" / "scripts" / "validate.py"


# --------------------------------------------------------------- the pagesAscending keyword

# Restated here rather than imported from the thing under test. That is the point: a harness
# that imports the implementation's own extension asserts that the implementation agrees with
# itself. skills/extract-to-schema/scripts/validate.py carries its own copy, and a change to
# one that is not made to the other fails at check_validator().
#
# The keyword exists because JSON Schema cannot compare two items of the same array. There is
# no draft 2020-12 spelling of "the second element is not less than the first", so the choice
# was between a custom keyword and moving the rule into prose. Prose is not a contract.


def _pages_ascending(
    validator: Any, value: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    if value is not True:
        return
    if not isinstance(instance, list) or len(instance) != 2:
        return  # type, minItems and maxItems already own those failures
    first, last = instance
    if any(isinstance(p, bool) or not isinstance(p, int) for p in (first, last)):
        return  # so does items.type
    if last < first:
        yield ValidationError(
            f"page range runs backwards: last page {last} precedes first page {first}"
        )


DocMessValidator = validators.extend(
    Draft202012Validator, {"pagesAscending": _pages_ascending}
)


# --------------------------------------------------------------------------- the fixtures

# A record that must validate. Every rejection below is this record with exactly one thing
# changed, so a rejection can be attributed to the change rather than to the fixture.
VALID: dict[str, Any] = {
    "source_file": "scan_20260814_113255.pdf",
    "pages": [1, 1],
    "document_type": "invoice",
    "document_id": "INV-2026-4487",
    "supersedes": None,
    "duplicate_of": None,
    "fields": {
        "invoice_number": {
            "value": "INV-2026-4487",
            "confidence": "high",
            "evidence": "page 1, header block, right",
        },
        "total_due": {
            "value": None,
            "confidence": "unreadable",
            "reason": "the totals block is cut off by the scan margin",
        },
        "issue_date": {
            "value": "2026-08-09",
            "confidence": "low",
            "evidence": "page 1, header block, right",
            "reason": "written 03/09/2026 and resolved from the August billing period",
        },
    },
}


def altered(**changes: Any) -> dict[str, Any]:
    """VALID with the top level replaced key by key."""
    record = json.loads(json.dumps(VALID))
    record.update(changes)
    return record


def with_field(name: str, field: Any) -> dict[str, Any]:
    """VALID carrying exactly one field, so nothing else can produce the error."""
    record = json.loads(json.dumps(VALID))
    record["fields"] = {name: field}
    return record


# ------------------------------------------------------------------------------ the checks


def errors(schema: dict[str, Any], instance: Any) -> list[ValidationError]:
    return list(DocMessValidator(schema).iter_errors(instance))


def fired(errs: list[ValidationError], keyword: str, path: str) -> tuple[bool, str]:
    """Whether ``keyword`` fired at ``path``, and what actually fired if it did not.

    Errors from an if/then branch are nested one level down, so both the error and its
    descendants are searched. The detail returned on a miss lists every keyword that did fire,
    because "rejected, but by additionalProperties" is a different finding from "not rejected".
    """

    def walk(err: ValidationError) -> Iterator[ValidationError]:
        yield err
        for child in err.context or []:
            yield from walk(child)

    seen = []
    for err in errs:
        for node in walk(err):
            seen.append(f"{node.validator}@{node.json_path}")
            if node.validator == keyword and node.json_path == path:
                return True, node.message[:96]
    if not seen:
        return False, "validated cleanly -- nothing rejected it"
    return False, "fired instead: " + ", ".join(sorted(set(seen))[:4])


def check_schema_document(schema: dict[str, Any], checks: Checks) -> None:
    """The schema is itself a valid schema, and says what it claims to say."""
    try:
        DocMessValidator.check_schema(schema)
        checks.add("schema: is a valid draft 2020-12 schema", True, schema["$schema"])
    except Exception as exc:  # noqa: BLE001
        checks.add("schema: is a valid draft 2020-12 schema", False, str(exc)[:120])
        return

    types = schema["properties"]["document_type"]["enum"]
    checks.add(
        "schema: document_type is a closed enum of the six types",
        types == ["sow", "sow_amendment", "invoice", "remittance", "statement", "out_of_scope"],
        ", ".join(types),
    )

    states = schema["$defs"]["field"]["properties"]["confidence"]["enum"]
    checks.add(
        "schema: confidence is three named states and never a number",
        states == ["high", "low", "unreadable"],
        ", ".join(states),
    )
    names = schema["$defs"]["field_name"]["enum"]
    checks.add(
        "schema: the field-name vocabulary is closed and sorted",
        names == sorted(names) and len(names) == len(set(names)),
        f"{len(names)} names, {names[0]}..{names[-1]}",
    )


def check_valid_shapes(schema: dict[str, Any], checks: Checks) -> None:
    """The three confidence shapes, and the two documents that carry no identity."""
    for label, instance in (
        ("the reference record", VALID),
        (
            "a high field",
            with_field("total_due", {"value": "1200.00", "confidence": "high", "evidence": "p1"}),
        ),
        (
            "a low field carrying its inference",
            with_field(
                "issue_date",
                {"value": "2026-09-03", "confidence": "low", "evidence": "p1", "reason": "day-first"},
            ),
        ),
        (
            "an unreadable field carrying its reason",
            with_field("total_due", {"value": None, "confidence": "unreadable", "reason": "cropped"}),
        ),
        (
            "an out_of_scope document with no fields and no identity",
            altered(document_type="out_of_scope", document_id=None, fields={}),
        ),
        (
            "an amendment that supersedes a SOW",
            altered(document_type="sow_amendment", document_id="SOW-2026-002-A1", supersedes="SOW-2026-002"),
        ),
        ("a two-page document", altered(pages=[3, 4])),
    ):
        errs = errors(schema, instance)
        checks.add(
            f"schema: accepts {label}",
            not errs,
            "" if not errs else f"{errs[0].validator}@{errs[0].json_path}: {errs[0].message[:72]}",
        )


def check_rejections(schema: dict[str, Any], checks: Checks) -> None:
    """The four the plan names, and two more from the same contract.

    Each is asserted on the keyword that fired and where it fired, not on the fact that
    something did. A record rejected by the wrong keyword is a record the schema is failing to
    describe, and it will be rejected for the right reason by accident until the day it is not.
    """
    cases = [
        (
            "a null value with no reason",
            with_field("total_due", {"value": None, "confidence": "unreadable"}),
            "required",
            "$.fields.total_due",
        ),
        (
            "a non-null value with no evidence",
            with_field("total_due", {"value": "1200.00", "confidence": "high"}),
            "required",
            "$.fields.total_due",
        ),
        (
            "an unknown document_type",
            altered(document_type="purchase_order"),
            "enum",
            "$.document_type",
        ),
        (
            "a pages range that runs backwards",
            altered(pages=[4, 3]),
            "pagesAscending",
            "$.pages",
        ),
        (
            "a low with no stated inference",
            with_field("issue_date", {"value": "2026-09-03", "confidence": "low", "evidence": "p1"}),
            "required",
            "$.fields.issue_date",
        ),
        (
            "an out_of_scope document force-fitted to the schema",
            altered(document_type="out_of_scope", document_id=None, supersedes=None, duplicate_of=None),
            "maxProperties",
            "$.fields",
        ),
        # A list of invoices flattened into one string. Two real runs disagreed about this --
        # one emitted ["INV-1", "INV-2"] and the other "INV-1, INV-2" -- and with `value`
        # untyped the schema accepted both, so the disagreement surfaced two steps downstream
        # as eight fields scored `wrong` for content that was identical. The validator is the
        # cheapest place to settle a question of shape, and it was declining to have an opinion.
        (
            "a settled-invoice list flattened into a comma-joined string",
            with_field(
                "invoices_settled",
                {"value": "INV-2026-1062, INV-2026-1068", "confidence": "high", "evidence": "p1"},
            ),
            "type",
            "$.fields.invoices_settled.value",
        ),
        # And the same field as a bare number, which is the other way a plural field goes wrong.
        (
            "a settled-invoice list given as a single scalar",
            with_field(
                "invoices_settled",
                {"value": 1062, "confidence": "high", "evidence": "p1"},
            ),
            "type",
            "$.fields.invoices_settled.value",
        ),
    ]

    for label, instance, keyword, path in cases:
        ok, detail = fired(errors(schema, instance), keyword, path)
        checks.add(f"schema: rejects {label}, by {keyword} at {path}", ok, detail)

    # The contract read from the other side. An unreadable field is not allowed to also claim
    # it saw where the figure was, because that is a guess wearing a citation.
    ok, detail = fired(
        errors(schema, with_field("total_due", {"value": None, "confidence": "unreadable", "reason": "r", "evidence": "p1"})),
        "not",
        "$.fields.total_due",
    )
    checks.add("schema: rejects an unreadable field that cites evidence, by not", ok, detail)

    # The float that reads as precision and is a vibe.
    ok, detail = fired(
        errors(schema, with_field("total_due", {"value": "1200.00", "confidence": 0.82, "evidence": "p1"})),
        "enum",
        "$.fields.total_due.confidence",
    )
    checks.add("schema: rejects a numeric confidence of 0.82, by enum", ok, detail)

    # An unknown field name. This is the assertion that makes check_corpus.py's
    # "every manifest field name is valid against the schema" mean something. propertyNames
    # reports against the object rather than the offending key, so the path is $.fields.
    ok, detail = fired(
        errors(schema, with_field("vibe", {"value": "x", "confidence": "high", "evidence": "p1"})),
        "enum",
        "$.fields",
    )
    checks.add("schema: rejects a field name outside the vocabulary, by enum", ok, detail)

    # Only an amendment supersedes anything.
    ok, detail = fired(errors(schema, altered(supersedes="INV-2026-0001")), "type", "$.supersedes")
    checks.add("schema: rejects an invoice that supersedes something, by type", ok, detail)


def check_keyword_is_load_bearing(schema: dict[str, Any], checks: Checks) -> None:
    """A stock validator cannot see the backwards page range, and that is worth stating.

    Unknown keywords are ignored by design in JSON Schema, so a reader who validates this
    schema with a plain Draft202012Validator gets a pass on [4, 3] and no warning. Everything
    in this repo that validates uses the extended validator; this assertion exists so that the
    day somebody swaps it for the stock one, a test says why that is wrong rather than the
    corpus quietly gaining backwards page ranges.
    """
    stock = list(Draft202012Validator(schema).iter_errors(altered(pages=[4, 3])))
    checks.add(
        "schema: pagesAscending is load-bearing -- a stock validator passes [4, 3]",
        not stock,
        "stock validator found no error, as expected" if not stock else f"{stock[0].validator}",
    )


def check_validator(schema: dict[str, Any], checks: Checks) -> None:
    """The same rejections, through the script the Skill bundles.

    Step 4's finding, asserted rather than trusted: a Skill uploaded as a bare SKILL.md ships
    without its bundled scripts and the model writes a substitute rather than failing. The
    substitute works, which is what makes it expensive. So the contract is asserted against
    validate.py's own exit code and its own fingerprint line, and if the script is absent this
    says so in the table rather than passing quietly.
    """
    # "Step 4 has not run" and "step 4 ran and the script is missing" are different findings
    # and only the second is a failure. The Skill directory existing is what tells them apart,
    # so the absence is reported either way rather than skipped into silence.
    skill = VALIDATOR_SCRIPT.parent.parent
    if not skill.exists():
        checks.add(
            "validator: step 4 has not run, so there is no bundled script to assert against",
            True,
            f"no {skill.relative_to(REPO)}/ -- this becomes a failure once the Skill exists",
        )
        return

    if not VALIDATOR_SCRIPT.exists():
        checks.add(
            "validator: skills/extract-to-schema/scripts/validate.py is bundled",
            False,
            "the Skill exists and its script does not -- the contract is unenforced",
        )
        return

    checks.add(
        "validator: skills/extract-to-schema/scripts/validate.py is bundled",
        True,
        str(VALIDATOR_SCRIPT.relative_to(REPO)),
    )

    # The Skill carries its own copy of the schema, because it runs in a sandbox where this
    # repository does not exist. Two copies can drift; package_skill.sh refreshes it and this
    # asserts the refresh happened, so a divergence fails here rather than shipping.
    bundled = skill / "schema" / "extraction.schema.json"
    checks.add(
        "validator: the Skill's bundled schema is byte-identical to the repo's",
        bundled.is_file() and bundled.read_bytes() == SCHEMA_PATH.read_bytes(),
        "in step with schema/extraction.schema.json"
        if bundled.is_file() and bundled.read_bytes() == SCHEMA_PATH.read_bytes()
        else "missing or stale -- run scripts/package_skill.sh",
    )

    def invoke(records: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
        payload = REPO / ".check_schema_tmp.json"
        payload.write_text(json.dumps(records, indent=2))
        try:
            return subprocess.run(
                [sys.executable, str(VALIDATOR_SCRIPT), str(payload)],
                capture_output=True,
                text=True,
            )
        finally:
            payload.unlink(missing_ok=True)

    good = invoke([VALID])
    checks.add(
        "validator: accepts the reference record and exits 0",
        good.returncode == 0,
        (good.stdout or good.stderr).strip().splitlines()[-1][:96] if (good.stdout or good.stderr) else "",
    )

    # The fingerprint. A substitute validator produces a plausible message; it does not
    # produce this one, and that is the only way to tell the two apart from the outside.
    checks.add(
        "validator: prints the fingerprint a substitute would not reproduce",
        "docmess-validate/1" in (good.stdout + good.stderr),
        (good.stdout + good.stderr).strip().splitlines()[0][:96] if (good.stdout + good.stderr) else "no output",
    )

    bad = invoke([with_field("total_due", {"value": None, "confidence": "unreadable"})])
    checks.add(
        "validator: rejects a null value with no reason and exits non-zero",
        bad.returncode != 0,
        f"exit {bad.returncode}",
    )
    # The pointer carries the record's index, because a run hands back seventy-odd of them and
    # "$.fields.total_due" would not say which. That is the string asserted on.
    output = bad.stdout + bad.stderr
    checks.add(
        "validator: names the failing record and pointer, not just that it failed",
        "$[0].fields.total_due" in output,
        next((ln.strip() for ln in output.splitlines() if "FAIL" in ln), "no FAIL line")[:96],
    )
    checks.add(
        "validator: explains the rule rather than quoting the keyword that fired",
        "unreadable field has to say why" in output,
        "the message names the contract, not 'required property'",
    )

    backwards = invoke([altered(pages=[4, 3])])
    checks.add(
        "validator: carries its own pagesAscending and rejects [4, 3]",
        backwards.returncode != 0 and "pages" in (backwards.stdout + backwards.stderr),
        f"exit {backwards.returncode}: "
        + ((backwards.stdout + backwards.stderr).strip().splitlines()[-1][:80] if (backwards.stdout + backwards.stderr) else ""),
    )


def run_all(_label: Any, checks: Checks) -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    check_schema_document(schema, checks)
    check_valid_shapes(schema, checks)
    check_rejections(schema, checks)
    check_keyword_is_load_bearing(schema, checks)
    check_validator(schema, checks)


def main() -> int:
    ap = base_parser(__doc__)
    args = ap.parse_args()
    if args.seed:
        ap.error("the schema does not vary by seed; --seed is not meaningful here")
    return run(["schema"], run_all, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
