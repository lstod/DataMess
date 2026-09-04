#!/usr/bin/env python3
"""The assertion harness every check_*.py shares.

Not pytest, and deliberately. The assertions in this repo are the documentation of what each
step promised — they are read as prose as often as they are run — and a failure has to say
which of thirty promises broke rather than stopping at the first one and leaving the other
twenty-nine unmeasured. Whether one thing broke or ten is the first question after a red run,
and a harness that exits early cannot answer it.

The output format is fixed across every harness in the repo: a numbered line per assertion,
``t`` or ``F``, the assertion as an English sentence, and the *observed* value as detail. The
detail is what makes a failure legible without a rerun, so it carries what was actually seen
rather than restating what was wanted.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Iterable


class Checks:
    """Collects results so a failure reports every other assertion too.

    ``add`` never raises and never exits. A check function that wants to stop early returns;
    it does not assert.
    """

    def __init__(self, label: str | int) -> None:
        self.label = label
        self.results: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), str(detail)))
        return bool(ok)

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.results if not r[1]]


def report(label: str | int, checks: Checks, verbose: bool) -> bool:
    """Print one seed's results and return whether they all passed."""
    failed = checks.failed
    if verbose or failed:
        print(f"\n{label}")
        print(f"{'':>3}  {'ok':<3} {'assertion':<74} detail")
        for i, (name, ok, detail) in enumerate(checks.results, 1):
            if verbose or not ok:
                print(f"{i:>3}  {'t' if ok else 'F':<3} {name[:74]:<74} {detail}")
    print(
        f"{label}: {len(checks.results) - len(failed)} of {len(checks.results)} assertions passed"
        + ("" if not failed else f" -- {len(failed)} FAILED")
    )
    return not failed


def run(
    labels: Iterable[str | int],
    body: Callable[[str | int, Checks], None],
    verbose: bool,
) -> int:
    """Run ``body`` once per label, catching anything it throws.

    A crash inside a check is reported as a failed assertion rather than as a traceback,
    because a harness that dies halfway through has told you nothing about the assertions it
    did not reach, and the exception is itself a finding worth printing in the table.
    """
    ok = True
    for label in labels:
        checks = Checks(label)
        try:
            body(label, checks)
        except Exception as exc:  # noqa: BLE001 - the point is to catch everything
            checks.add("harness ran to completion", False, f"{type(exc).__name__}: {exc}"[:160])
        ok &= report(label, checks, verbose)

    print()
    print("all checks pass" if ok else "FAILURES above")
    return 0 if ok else 1


def base_parser(doc: str | None) -> argparse.ArgumentParser:
    """The argparse surface every harness shares, so no two disagree about --seed or -v."""
    ap = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--seed",
        type=int,
        action="append",
        help="Repeatable. Every fixture seed when omitted.",
    )
    ap.add_argument(
        "--verbose", "-v", action="store_true", help="Print passing assertions too."
    )
    return ap


# BizData's fixture set, reused rather than reserving a new range. Those seeds already produce
# fifteen reproducible portfolios and sweep.sh already asserts them, and a corpus is a
# rendering of a portfolio -- so a fixture portfolio is a fixture corpus for free. 42 is the
# demo seed and the one every recorded figure in the README comes from.
FIXTURE_SEEDS = (42, 43, *range(9001, 9016))

# The seed the corpus on disk is built from, and the one every figure in the README and the
# recording refers to. Named because scripts that render over corpus/inbox/ have to be able to
# put it back.
PRIMARY_SEED = 42

DEMO_SEED = 42
DEMO_PERIOD = "2026-08"


if __name__ == "__main__":  # pragma: no cover - a module, not a script
    sys.exit("checks.py is imported by the check_*.py harnesses, not run directly.")
