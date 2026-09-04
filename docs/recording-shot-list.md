# Recording shot list

Three minutes, six shots. Written before the take, and every figure on screen is checkable from
seed 42 — the numbers below are what will actually be visible, so if the screen disagrees with
this page, stop and find out why before recording again.

**The temptation is to show the extraction working. Don't.** A number on screen could have been
invented and the audience knows it, so a pipeline reading 36 invoices correctly is thirty seconds
of nothing. The whole recording is built around one moment: the total that is missing on purpose.
Shot 4 is the take; everything else exists to set it up and to prove it afterwards.

**Shot 6 was fifteen seconds and is now forty-five**, taken five seconds at a time off the shots
around it. The scorecard stopped being a closing card once it carried a measured Skill edge and a
noise floor to judge it against — it is the shot that answers "how do you know it worked", which
is the question the rest of the recording sets up and cannot answer on its own. If a take runs
long, cut from shot 2. The fan-out is the least load-bearing thirty seconds in the video: it looks
impressive and proves nothing, which is the exact failure mode this list exists to avoid.

Setup before rolling:

```bash
.venv/bin/python scripts/corpus.py --seed 42 --period 2026-08
.venv/bin/python scripts/degrade.py
.venv/bin/python scripts/reference_run.py --perfect      # or the banked Cowork run
.venv/bin/python skills/build-extraction-workbook/scripts/build_workbook.py
```

Have `out/extraction-review.xlsx` closed, `corpus/inbox/` open in Finder on icon view sorted by
name, and the terminal at a readable font size. Close the file explorer sidebar.

---

## 1 · The folder of mess — 0:00 to 0:20

Finder on `corpus/inbox/`, icon view, scrolling slowly once top to bottom.

What has to be legible on screen:

- 74 files, mixed icons — `.pdf`, `.docx`, `.xlsx`, one `.eml`
- the block of `scan_20260814_*.pdf` thumbnails, visibly skewed and grey
- `INV-2026-1614.pdf` and `INV-2026-1614 (1).pdf` adjacent — pause half a second here
- `receipt_20260804_lunch.pdf` and `FW_ Your Meridian Print account.eml`

Say: seventy-four documents, four formats, twenty-two of them image-only scans with no text layer
at all. Note that the duplicate and the lunch receipt are planted, and that the folder is
generated backwards out of a portfolio generator — so the answers are already written down in
`corpus/manifest.json`, which is what makes any of the rest measurable.

Do not open a document yet.

## 2 · The fan-out running — 0:20 to 0:45

Cowork, the run in progress. Subagents working the folder in parallel, the main thread merging.

What has to be visible:

- more than one subagent active at once
- a subagent that has escalated a page to vision — the scans have no text layer, so this is not
  optional and it is worth pointing at
- the merge step, and the validation against `schema/extraction.schema.json`

Say: 75 document instances found, merged to 74 records. Three different numbers — 74 files, 75
instances, 74 records — because one file holds two documents and one document is filed under two
names. A file is not a document, and three of the ten planted defects exist only because those
are different things.

If the run is slow, cut on the merge rather than speeding the footage up.

## 3 · The cut-off invoice — 0:45 to 1:10

Open `corpus/inbox/scan_20260814_119189.pdf` full screen. Scroll to the bottom of page 1.

The totals block is not there. The scanner margin took it.

Say: this is `INV-2026-1500`. The total is not faint or ambiguous — it is physically not on the
page, and it cannot be recovered by arithmetic either, because the net and the VAT went with it.
There is exactly one correct answer, and it is not a number.

Hold on the cropped edge for two full seconds before cutting. This shot is the setup for the next
one and it only works if the audience has seen that the figure genuinely is absent.

## 4 · The total that is short, and knows it — 1:10 to 1:50

**The take.** Open `out/extraction-review.xlsx` on the Summary tab.

Point at, in this order:

| Cell | Reads | What to say |
|---|---|---|
| `B15` Total invoiced | `1,968,886.88` | click it — the formula bar shows `='Invoices'!L39`, not a number somebody typed |
| `B17` Invoices missing a total | `1` | one invoice has no total. The cell is empty, not zero — a zero would be a lie that balances |
| `B20` of which unreadable | `5` | the pipeline is asserting it could not read five figures — this invoice total, and two totals on each statement of account |

Then say the subtraction out loud, and put it on screen if editing allows:

```
true total, from the manifest       2,003,793.28
this workbook's Summary             1,968,886.88
                                    ────────────
                                       34,906.40
```

That difference is exactly `INV-2026-1500` — the invoice from shot 3. The workbook is short by one
invoice, the shortfall has a name, and anyone can find out why in two clicks. A pipeline that
guessed would have shown a total that balanced and been wrong by £34,906.40 with nothing on the
page to indicate it.

This is the argument. Do not rush it and do not talk over the pause after the number.

## 5 · The QA sheet — 1:50 to 2:15

The QA tab. Eight rows, one per flagged field.

Walk the `INV-2026-1500` row across its columns: field `total_due`, confidence `unreadable`, value
blank, and the Reason column — *the totals block is below the scanner margin and is not on the
page.* Then the Source file column naming `scan_20260814_119189.pdf`, the file from shot 3.

Then the `INV-2026-1596` row, because it is a different kind of hard: a handwritten annotation
reading *net 14 days not 30 — agreed w/ MJ 12/8* contradicts the printed terms. The pipeline
recorded the printed value, flagged the conflict, and did not silently prefer either.

Say: every flagged field cites the file and the page it came from, and the Verdict column is empty
because a human has not looked yet. The sheet is built to be worked down, not to be admired.

If there is time, the two `STM-2026-10x` rows are the best pair on the sheet. Both statements
report `total_outstanding` unreadable, and the reason is that the cell holds an uncalculated
`=SUM()` with no stored result — the spreadsheet refers to a total that was never computed and
so does not contain one. Nobody planted that. The generator wrote a formula, openpyxl never
calculated it, and the corpus produced a defect its own manifest did not know about.

Then the Exceptions tab, briefly — 17 rows: the duplicate at row 5, six supersessions, four
out-of-scope documents, and the five unreadable figures.

> **Insert, if the run was recorded before 3 September.** Shoot this tab from
> `extraction-review.xlsx` in the repo root, not from the workbook the recorded run produced.
> The run's own copy has **16** rows and no duplicate row, because it was built by the version of
> `build_workbook.py` that filtered `duplicate_of` records out before the tab was written — the
> bug the run itself found and reported. Everything else in that recording is still exact: the
> documents are byte-identical across the fix, so only this one tab is stale.
>
> Check before rolling: **17 rows**, and row 5 reads `INV-2026-1614  duplicate  a second copy of
> INV-2026-1614; excluded from totals`. If row 5 is a supersession, it is the old workbook.

## 6 · The scorecard — 2:15 to 3:00

`scorecard/scorecard.png` full screen.

Three beats, and the chart carries all of them. **First, why accuracy is the wrong number:** the
flawed run guesses at that cropped total,
misreads another figure, over-flags the legible control and misses the duplicate — and it loses
three tenths of one percentage point of accuracy. It loses twelve and a half points of flag recall.
Accuracy is the number every extraction vendor quotes and it is the number least able to see the
failure that matters.

**Second, the whiskers — the part nobody else's chart has.** The green bar is two runs of the
same Skills on the same folder, and flag precision came back 100% once and 22.2% the other time.
Nothing changed between them. That spread is the noise floor, and it is drawn on the chart rather
than left in a footnote — because without it, any two bars of different heights can be sold as an
improvement.

The gold bar is one Skill edit against that. Run 2 flagged ten legible figures for having
continental separators, and computed two statement totals off a cell holding an uncalculated
`=SUM()`. Both were the Skill under-specifying: it said to normalise `1.234,56` without saying
that leaves a `high`, and it said not to compute a missing figure without covering the case where
the cell is present, labelled, and empty. The prediction — which fields would move, and that
coverage and case 7 must not — was written down before the run, and every line of it held.

**Third, and this is the shot.** The gold bar has a whisker too. The second v2 run scored 66.7%,
because on the cropped invoice it listed four more fields of the totals block it could not see —
five rows in a reviewer's queue where one would do. So v2 varies as well: the floor moves 22.2% to
66.7% and the mean 61.1% to 83.3%, and **the bands still overlap.** Say the words: this is a
raised floor, not a solved problem.

Then the part worth the whole recording. Both v2 runs first scored an identical 100% on all four
metrics while handing a reviewer 8 rows and 12 — the extra four sat in an outcome class that was
neutral to every metric, so fifty percent more review work was invisible. That is the same failure
as the accuracy point at the top of this shot, one level up and in my own measurement rather than
in the model's output. It is fixed: those rows now cost flag precision, which is why the gold bar
is not at 100.

If asked about the two `cowork-01` rows on the table, the honest version is worth thirty seconds:
the real run scored 89.5% the first time, and every one of those failures was the manifest being
wrong about its own documents — an unsigned figure the page printed signed, an `Original` column
the page showed and the ground truth omitted, two totals the ground truth asserted and the file
does not contain. The run read the documents more carefully than the generator described them.

Do not present that as a win. It is the finding: the measurement apparatus was the least reliable
part of the build, and the only reason anyone knows is that the run cited its evidence well enough
to be checked against the page.

End on the chart. No outro.

---

## Figures that must match the screen

Checked against seed 42 before the take. If any of these is different, the corpus was regenerated
with another seed or the manifest changed, and the script above is wrong rather than the build.

| Figure | Value | Where |
|---|---|---|
| Files in the inbox | 74 | Finder |
| Document instances found | 75 | the Cowork run |
| Records after merge | 74 | Summary `A3`, and `B12` sums the census to it |
| Total invoiced | 1,968,886.88 | Summary `B15` |
| True total | 2,003,793.28 | `corpus/manifest.json` |
| Shortfall | 34,906.40 | the subtraction, and `INV-2026-1500`'s declared total |
| Invoices missing a total | 1 | Summary `B17` |
| Fields flagged | 8 | Summary `B19`, and 8 rows on QA |
| of which unreadable | 5 | Summary `B20` |
| Exceptions | 17 | Summary `B22`, and 17 rows on Exceptions |
| Flawed run accuracy / precision | 99.7% / 87.5% | `scorecard/scorecard.png` |
| Real run, as first scored | 89.5% / 50.0% | table only — `cowork-01`, superseded, not in the bars |
| Real run, ground truth corrected | 100% / 100% | `cowork-01-fixed` |
| Skills v1 flag precision, mean and range | 61.1%, 22.2–100% | the green bar and its whisker |
| Skills v2 flag precision, mean and range | 83.3%, 66.7–100% | the gold bar and its whisker |
| Northfield fields flagged for notation, v1 run 2 → v2 | 10 → 0 | the case 6 row of the grid |
| QA rows, `cowork-03` vs `cowork-04` | 8 vs 12 | the two workbooks in `docs/evidence/step-5/` |
| Bars on the chart | 4 — two references, Skills v1, Skills v2 | grouped by configuration, not by run |
| Columns on the case grid | 6 | one per live run; `cowork-01` is excluded |

## Do not show

- The reference run being generated. It is a projection of the manifest, not an extraction, and
  showing it invites exactly the misreading that the pipeline scored itself. If the Cowork run is
  not banked, cut shot 2 and say the extraction is not in this recording rather than implying a
  projection is one.
- Any `check_*.py` output. 823 assertions passing is reassuring to a reader of the repo and is
  dead air on video.
- The generator source. The backwards-corpus idea is one sentence in shot 1; the code is not
  interesting to watch.
