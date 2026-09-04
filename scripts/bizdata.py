#!/usr/bin/env python3
"""The seam to BizData. One import, no database, and nothing else exported.

    from bizdata import portfolio
    p = portfolio(seed=42, period="2026-08")
    for invoice in p.invoices:
        ...

BizData's ``generate(seed, period) -> Portfolio`` is pure: stdlib imports only, no environment
variables, no argparse at module level, no connection to anything. ``writers.py`` is a separate
loader chosen by ``BIZDATA_DB_BACKEND``, and that split exists precisely so a portfolio can be
built without a database. So DocMess reads the generator and nothing else, and never needs
BizData's Postgres running, its DSN, or its container.

The seam is import-only in both directions. DocMess never writes to BizData, and BizData does
not know DocMess exists.

Set ``DOCMESS_BIZDATA_ROOT`` if the checkout is not at ``../BizData``.


Two things about how this import is done, both of which were found rather than assumed.

**It does not go through sys.path.** The obvious spelling is to put the BizData checkout on
``sys.path`` and ``from scripts.generator import generate``. That works, and it is what the
plan says to do, but ``scripts`` is a PEP 420 namespace package and *both* repositories have a
directory by that name. The result is a merged package:

    scripts.__path__ == ['.../BizData/scripts', '.../DocMess/scripts']

Every DocMess module then resolves through a package whose contents depend on sys.path order,
which is a debugging problem nobody should have to have. Loading the file directly by path
gives the same object with none of that, so ``sys.path`` is left alone.

**The module is registered in sys.modules before it is executed.** Skipping that raises
``AttributeError: 'NoneType' object has no attribute '__dict__'`` from inside ``dataclasses``,
about eight frames deep, with nothing in the traceback pointing at the real cause.
``generator.py`` uses ``from __future__ import annotations``, so ``@dataclass`` resolves its
field types by looking the defining module up in ``sys.modules`` — and a module built by
``module_from_spec`` is not there yet. The two lines are in the required order and there is a
comment saying so, because the failure gives no hint.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

_MODULE_NAME = "docmess_bizdata_generator"
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent / "BizData"

__all__ = ["portfolio", "Portfolio", "bizdata_root"]


def bizdata_root() -> Path:
    return Path(os.environ.get("DOCMESS_BIZDATA_ROOT", str(_DEFAULT_ROOT))).expanduser()


def _load() -> Any:
    """Import BizData's generator, or exit with a sentence a human can act on."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]

    root = bizdata_root()
    source = root / "scripts" / "generator.py"
    if not source.is_file():
        raise SystemExit(
            f"error: no BizData generator at {source}\n"
            "\n"
            "DocMess renders BizData's portfolio as the paper trail it would have produced, so\n"
            "it needs that checkout beside this one. Nothing has to be running -- no container,\n"
            "no AWS profile, no seeded database. The import is the whole dependency.\n"
            "\n"
            "  git clone https://github.com/lstod/BizData ../BizData\n"
            "\n"
            "or point DOCMESS_BIZDATA_ROOT at an existing checkout:\n"
            "\n"
            f"  DOCMESS_BIZDATA_ROOT=/path/to/BizData {Path(sys.argv[0]).name} ...\n"
        )

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, source)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"error: could not load a module spec from {source}")
    module = importlib.util.module_from_spec(spec)

    # Before exec_module, not after. generator.py uses `from __future__ import annotations`,
    # so @dataclass resolves its field types through sys.modules[cls.__module__] -- and a
    # module that is not registered yet resolves to None. The failure is an AttributeError
    # inside dataclasses.py with nothing in the traceback pointing here.
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[_MODULE_NAME]
        raise

    for name in ("generate", "COLUMNS"):
        if not hasattr(module, name):
            raise SystemExit(
                f"error: {source} has no {name}.\n"
                "The BizData checkout is on a commit older than the seam this expects."
            )
    return module


class Portfolio:
    """BizData's rows, with names on them.

    ``Portfolio`` on the BizData side holds ``list[tuple]`` -- bare positional rows, with the
    field names in a separate module-level ``COLUMNS`` dict. That is the right shape for
    something about to be COPYed into Postgres and the wrong shape for something about to be
    laid out on a page, where every access is by name and an off-by-one in a tuple index is a
    plausible-looking document with the wrong figure on it.

    So this zips once, here, and everything downstream reads dicts. The conversion is the only
    liberty DocMess takes with BizData's data: no field is renamed, dropped, added or
    recomputed, and the ``Decimal`` and ``date`` objects are passed through as they arrive.
    Round-tripping money through a float or a string would cost fidelity that step 8 then
    scores as a model error.
    """

    def __init__(self, raw: Any, columns: dict[str, tuple[str, ...]]) -> None:
        self._raw = raw
        self.period_start = raw.period_start
        self.period_end = raw.period_end
        self.window_start = raw.window_start
        self.window_end = raw.window_end
        self.mess = raw.mess

        for table, names in columns.items():
            setattr(self, table, [dict(zip(names, row)) for row in getattr(raw, table)])

    clients: list[dict[str, Any]]
    people: list[dict[str, Any]]
    engagements: list[dict[str, Any]]
    sow_line_items: list[dict[str, Any]]
    time_entries: list[dict[str, Any]]
    invoices: list[dict[str, Any]]

    # ------------------------------------------------------------------ derived views

    def client_by_id(self) -> dict[int, dict[str, Any]]:
        return {row["id"]: row for row in self.clients}

    def engagement_by_id(self) -> dict[int, dict[str, Any]]:
        return {row["id"]: row for row in self.engagements}

    def active_engagements(self) -> list[dict[str, Any]]:
        """The eighteen. One SOW each, which is where the corpus composition starts."""
        return [e for e in self.engagements if e["status"] == "active"]

    def line_items_for(self, engagement_id: int) -> list[dict[str, Any]]:
        return [li for li in self.sow_line_items if li["engagement_id"] == engagement_id]

    def rate_for(self, engagement: dict[str, Any]) -> Decimal:
        """The blended rate the ceiling was priced at.

        BizData computes this and deliberately does not keep it -- ``blended_rate`` is a
        generator-internal on the ``Engagement`` dataclass and never reaches a column. It is
        recoverable exactly, because ceiling_amount and ceiling_hours are both stored and the
        one was derived from the other.
        """
        hours = engagement["ceiling_hours"]
        if not hours:
            return Decimal("0.00")
        return (engagement["ceiling_amount"] / hours).quantize(Decimal("0.01"))


def portfolio(seed: int, period: str) -> Portfolio:
    """One reproducible portfolio, named rather than positional.

    ``period`` is the demo month as YYYY-MM. History runs twelve months back from it.
    """
    module = _load()
    return Portfolio(module.generate(seed, period), module.COLUMNS)


if __name__ == "__main__":  # pragma: no cover - a smoke test, not an entry point
    p = portfolio(42, "2026-08")
    print(f"bizdata: {bizdata_root()}")
    print(f"period {p.period_start} .. {p.period_end}, window from {p.window_start}")
    print(
        f"{len(p.clients)} clients, {len(p.engagements)} engagements "
        f"({len(p.active_engagements())} active), {len(p.invoices)} invoices"
    )
