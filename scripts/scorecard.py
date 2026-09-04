#!/usr/bin/env python3
"""Render every scored run as a table and a chart.

    scripts/scorecard.py
    scripts/scorecard.py --out scorecard/

Reads `scorecard/scores.jsonl`, writes `scorecard/scorecard.md` and `scorecard/scorecard.png`.

The chart is the artifact -- it is what goes in the README, and it is the thing somebody looks
at for four seconds before deciding whether to read anything else. So it has to make the
argument on its own, and the argument is not "the number is high".

Four bars, not one
------------------
Field accuracy sits near 100% on almost any run, because most fields on most documents are
legible and easy. On the reference runs here it moves from 100.0% to 99.7% -- a run that
guessed a cropped total, misread another, dropped a field, over-flagged the control, missed the
duplicate and put a lunch receipt in the accounts receivable **loses three tenths of one
percent on accuracy**. Anyone shown only that number would conclude the pipeline was excellent.

The same run loses twenty-five points on flag precision and twenty-five on flag recall. That is
what the fourth panel is for, and it is why accuracy is drawn on the same axis as the other
three rather than given a flattering one of its own: the visual point is that the interesting
movement is not where you would look first.

Nothing is ever removed from scores.jsonl. A scorecard that kept only the best run would be
marketing, and the before-and-after is the entire value of the file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover
    sys.exit("error: matplotlib is not installed. .venv/bin/pip install -r requirements.txt")

REPO = Path(__file__).resolve().parent.parent
SCORECARD = REPO / "scorecard"
SCORES = SCORECARD / "scores.jsonl"

METRICS = [
    ("field_accuracy", "Field accuracy", "acc", "of the values it gave, how many were right"),
    ("coverage", "Coverage", "cov", "of the values the corpus declares, how many it produced"),
    ("flag_precision", "Flag precision", "prec", "of the fields it flagged, how many were genuinely hard"),
    ("flag_recall", "Flag recall", "rec", "of the genuinely hard fields, how many it flagged"),
]

CASES = {
    "1": "no text layer",
    "2": "legible control",
    "3": "two in one file",
    "4": "amendment",
    "5": "duplicate",
    "6": "1.234,56",
    "7": "total cropped",
    "8": "handwriting",
    "9": "03/09/2026",
    "10": "out of scope",
}

# Held is the only good outcome. The rest are graded by how much they would cost somebody.
VERDICT_COLOUR = {
    "held": "#2E7D5B",
    "over-flagged": "#B58A2B",
    "missed": "#B5662B",
    "misread": "#B5662B",
    "split-wrong": "#B5662B",
    "force-fitted": "#A33B2E",
    "double-counted": "#A33B2E",
    "guessed": "#8B1E1E",
}


def load(scores: Path) -> list[dict[str, Any]]:
    if not scores.is_file():
        raise SystemExit(
            f"error: no scores at {scores}.\n"
            "  scripts/reference_run.py --perfect && scripts/reconcile.py --run reference-perfect"
        )
    entries = [json.loads(line) for line in scores.read_text().splitlines() if line.strip()]

    # The log keeps every scoring; the scorecard shows the current one per run. A run gets
    # scored again when the thing it is scored *against* changes -- the manifest was corrected
    # in four places after the first Cowork run read the documents more carefully than it had
    # been written -- and two rows with the same name and different figures is not a history,
    # it is a puzzle. The history stays in scores.jsonl, where nothing is ever rewritten.
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        latest[entry["run"]] = entry
    return list(latest.values())


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def bars_for(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per configuration, in the order the runs first appear.

    A `superseded` run is dropped: it is a scoring kept for the record against a manifest that
    has since been corrected, and averaging it with runs measured against the current one would
    put two different questions in the same bar.

    Real runs group by their `skill` label so that repeat runs of one configuration read as what
    they are -- a sample -- rather than as a trend. References stay one bar each; they are
    projections, they have no variance, and two of them are not a distribution.
    """
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if run.get("kind") == "superseded":
            continue
        key = (
            run["run"]
            if run.get("kind") == "reference"
            else f"Skills {run.get('skill', 'v1')}"
        )
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(run)

    out: list[dict[str, Any]] = []
    for key in order:
        members = buckets[key]
        entry: dict[str, Any] = {"label": key, "n": len(members), "runs": members}
        for stat in ("mean", "low", "high"):
            entry[stat] = {}
        for metric, _, _, _ in METRICS:
            seen = [
                r["metrics"][metric] * 100
                for r in members
                if r["metrics"].get(metric) is not None
            ]
            entry["mean"][metric] = sum(seen) / len(seen) if seen else None
            entry["low"][metric] = min(seen) if seen else None
            entry["high"][metric] = max(seen) if seen else None
        if len(members) > 1:
            entry["label"] = f"{key}  (n={len(members)})"
        out.append(entry)
    return out


# ------------------------------------------------------------------------------- markdown


def markdown(runs: list[dict[str, Any]]) -> str:
    out: list[str] = ["# Scorecard", ""]
    out.append(
        f"{len(runs)} run{'s' if len(runs) != 1 else ''} against seed {runs[-1]['seed']}'s corpus "
        f"of 74 documents. Appended, never replaced -- the bad runs stay."
    )
    out.append("")
    out.append("![scorecard](scorecard.png)")
    out.append("")

    out.append("## Metrics")
    out.append("")
    out.append("| Run | Kind | " + " | ".join(label for _, label, _, _ in METRICS) + " |")
    out.append("|---|---|" + "---|" * len(METRICS))
    for run in runs:
        cells = " | ".join(pct(run["metrics"].get(key)) for key, _, _, _ in METRICS)
        out.append(f"| `{run['run']}` | {run.get('kind', 'run')} | {cells} |")
    out.append("")
    for key, label, _, gloss in METRICS:
        out.append(f"- **{label}** — {gloss}.")
    out.append("")
    out.append(
        "Accuracy barely moves between these two runs and the flag metrics move twenty-five "
        "points. That gap is the reason there are four numbers here rather than one: a single "
        "percentage cannot tell a run that admitted it could not read a figure from one that "
        "guessed, and those are opposite behaviours."
    )
    out.append("")

    out.append("## Outcomes")
    out.append("")
    classes = ["correct", "correctly_flagged", "wrong", "hallucinated", "missed", "over_flagged"]
    out.append("| Run | " + " | ".join(c.replace("_", " ") for c in classes) + " |")
    out.append("|---|" + "---|" * len(classes))
    for run in runs:
        out.append(
            f"| `{run['run']}` | " + " | ".join(str(run["counts"].get(c, 0)) for c in classes) + " |"
        )
    out.append("")
    out.append(
        "`hallucinated` is separated from `wrong` deliberately. A misreading is a mistake; a "
        "confident figure on a page that does not carry one is an invention presented as a "
        "reading, and it is the single failure this pipeline is built to prevent."
    )
    out.append("")

    out.append("## Mess cases")
    out.append("")
    out.append("| # | Case | " + " | ".join(f"`{r['run']}`" for r in runs) + " |")
    out.append("|---|---|" + "---|" * len(runs))
    for number, name in CASES.items():
        verdicts = " | ".join(r.get("mess_cases", {}).get(number, "—") for r in runs)
        out.append(f"| {number} | {name} | {verdicts} |")
    out.append("")
    out.append(
        "**held** is the only good outcome. **guessed** is the worst: a figure the document "
        "does not carry, asserted with confidence. **over-flagged** is case 2 doing its job — "
        "the control is legible, so flagging it is wrong, and without it a run that flagged "
        "everything would score perfectly on recall while being useless."
    )
    out.append("")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------------- png


def chart(runs: list[dict[str, Any]], out: Path) -> None:
    fig = plt.figure(figsize=(13, 5.4), dpi=160)
    grid = fig.add_gridspec(1, 2, width_ratios=[1.05, 1], wspace=0.22, left=0.06, right=0.985, top=0.80, bottom=0.14)
    fig.patch.set_facecolor("white")

    palette = ["#1F3864", "#8B1E1E", "#2E7D5B", "#B58A2B", "#5B4B8A"]

    # --- the four metrics, all on one axis ------------------------------------------
    ax = fig.add_subplot(grid[0, 0])
    # Grouped by configuration, not one bar per run. Two runs of the same Skills on the same
    # folder scored 100% and 28.6% flag precision, so a chart with a bar per run invites the
    # reading that something changed between them when nothing did. The bar is the mean and the
    # whisker is the range, and where a group holds one run there is no whisker to draw.
    #
    # This is what makes a Skill edit legible: an improvement that clears the whisker is an
    # improvement, and one that sits inside it is a sample of one.
    groups = bars_for(runs)
    width = 0.8 / max(len(groups), 1)
    crowded = len(groups) > 3
    for index, group in enumerate(groups):
        positions = [n + index * width - 0.4 + width / 2 for n in range(len(METRICS))]
        means = [group["mean"][key] for key, _, _, _ in METRICS]
        bars = ax.bar(
            positions, [m or 0 for m in means], width * 0.9,
            label=group["label"], color=palette[index % len(palette)], zorder=3,
        )
        if group["n"] > 1:
            lows = [group["low"][key] or 0 for key, _, _, _ in METRICS]
            highs = [group["high"][key] or 0 for key, _, _, _ in METRICS]
            ax.errorbar(
                positions,
                [m or 0 for m in means],
                yerr=[
                    [(m or 0) - lo for m, lo in zip(means, lows)],
                    [hi - (m or 0) for m, hi in zip(means, highs)],
                ],
                fmt="none", ecolor="#333", elinewidth=1.1, capsize=3, capthick=1.1, zorder=4,
            )
        for bar, value, (key, _, _, _) in zip(bars, means, METRICS):
            top = (group["high"][key] if group["n"] > 1 else value) or 0
            ax.text(
                bar.get_x() + bar.get_width() / 2, top + 1.4,
                "n/a" if value is None else f"{value:.1f}",
                ha="center", va="bottom", fontsize=7.5 if crowded else 8.5, color="#333",
                rotation=90 if crowded else 0,
            )

    # A shared axis, on purpose. Giving accuracy its own scale would hide the point.
    ax.set_ylim(0, 122 if len(runs) > 3 else 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0", "25", "50", "75", "100%"], fontsize=9)
    ax.set_xticks(range(len(METRICS)))
    ax.set_xticklabels([label for _, label, _, _ in METRICS], fontsize=9.5)
    ax.grid(axis="y", color="#E4E4E8", zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#BBB")
    # One row, always, and clear of the subtitle. At ncol=3 a fourth run wrapped the legend onto
    # a second line and printed it straight through the sentence underneath.
    ax.legend(frameon=False, fontsize=8.5, ncol=max(len(groups), 1), loc="lower left", bbox_to_anchor=(0, 1.13))
    ax.set_title(
        "Accuracy barely moves. The flag metrics are where a run is actually judged.",
        fontsize=9.5, color="#555", loc="left", pad=10, style="italic",
    )

    # --- the per-case grid ------------------------------------------------------------
    ax2 = fig.add_subplot(grid[0, 1])
    numbers = list(CASES)
    # Per run here, not per configuration. The bars answer "how good is this setup"; the grid
    # answers "what did this particular run do", and collapsing two runs into one column would
    # hide that one of them over-flagged case 6 and the other did not. Superseded scorings are
    # dropped for the same reason they are dropped from the bars.
    shown = [r for r in runs if r.get("kind") != "superseded"]
    for column, run in enumerate(shown):
        for row, number in enumerate(numbers):
            v = run.get("mess_cases", {}).get(number, "—")
            ax2.add_patch(
                mpatches.FancyBboxPatch(
                    (column + 0.04, len(numbers) - row - 0.94), 0.92, 0.88,
                    boxstyle="round,pad=0,rounding_size=0.06",
                    facecolor=VERDICT_COLOUR.get(v, "#CCC"), edgecolor="white", linewidth=1.4,
                )
            )
            ax2.text(
                column + 0.5, len(numbers) - row - 0.5, v,
                ha="center", va="center", fontsize=7.6, color="white",
            )

    ax2.set_xlim(0, len(shown))
    ax2.set_ylim(0, len(numbers))
    ax2.set_xticks([n + 0.5 for n in range(len(shown))])
    # Run names are long and the columns are narrow, so past four of them the labels run
    # together -- `reference-perfectreference-flawed` reads as one word and is the kind of thing
    # that only becomes obvious once the chart is full screen in a recording.
    ax2.set_xticklabels(
        [r["run"] for r in shown],
        fontsize=9 if len(shown) <= 4 else 7.6,
        rotation=0 if len(shown) <= 4 else 18,
        ha="center" if len(shown) <= 4 else "right",
    )
    ax2.set_yticks([len(numbers) - n - 0.5 for n in range(len(numbers))])
    ax2.set_yticklabels([f"{n}  {CASES[n]}" for n in numbers], fontsize=8.6)
    ax2.tick_params(length=0)
    for side in ("top", "right", "left", "bottom"):
        ax2.spines[side].set_visible(False)
    ax2.set_title(
        "Ten deliberate defects. 'held' is the only good outcome.",
        fontsize=9.5, color="#555", loc="left", pad=26, style="italic",
    )

    fig.suptitle(
        "DocMess — extraction scorecard",
        fontsize=14, weight="bold", x=0.06, ha="left", y=0.955, color="#1F3864",
    )
    # The PNG travels on its own -- into a README, into a recording -- so anything needed to read
    # it honestly has to be printed on it. A held-out group is measured against a different
    # corpus, and bars of the same height are not the same claim: the seed 42 groups were scored
    # on the documents the Skills were edited while looking at, and the held-out group was not.
    if any("held-out" in g["label"] for g in groups):
        fig.text(
            0.06, 0.025,
            "Held-out is a different corpus (seed 7777), unseen while the Skills were edited. "
            "Compare it with the seed 42 groups cautiously: the same height is not the same claim.",
            fontsize=8, color="#666", ha="left", style="italic",
        )
    fig.savefig(out, facecolor="white")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, default=SCORECARD)
    ap.add_argument("--scores", type=Path, help="Defaults to scores.jsonl inside --out.")
    args = ap.parse_args()

    runs = load(args.scores or args.out / SCORES.name)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scorecard.md").write_text(markdown(runs))
    chart(runs, args.out / "scorecard.png")

    print(f"{args.out}/scorecard.md")
    print(f"{args.out}/scorecard.png")
    print(f"  {len(runs)} runs: " + ", ".join(r["run"] for r in runs))
    for run in runs:
        print(
            f"  {run['run']:<22} "
            + "  ".join(f"{short} {pct(run['metrics'].get(key)):>6}" for key, _, short, _ in METRICS)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
