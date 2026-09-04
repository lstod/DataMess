# DocMess

**The corpus is generated backwards.** Most document-extraction projects start with a folder of
documents and spend most of their budget hand-labelling what the right answers are. DocMess starts
from the answers: it takes a seeded portfolio of engagements, statements of work and invoices from
a sibling generator, renders the paper those rows *would have come from* — scanned, skewed,
cropped, duplicated, handwritten on — and then asks an agent to reconstruct the rows from the
paper. The generator knows the truth because it wrote it. So there is no labelling step, and no
labelling error correlated with the pipeline's own blind spots.

That inversion is the whole design. Everything else follows from it, including the thing worth
reading, which is [What broke](#what-broke).

```
BizData generate(seed) ──> Portfolio (rows)
                              │
                              ├──> documents.py + corpus.py ──> corpus/inbox/     74 files
                              └──────────────────────────────> corpus/manifest.json   ground truth
                                                                    │
        degrade.py ──> 22 of them become image-only scans           │
                                                                    │
        Cowork Skills ──> extractions.json ──> build_workbook.py ──> out/*.xlsx
                                                    │
                                                    └──> reconcile.py ──┴──> scorecard/
```

---

## The scorecard

![scorecard](scorecard/scorecard.png)

Seed 42's corpus. `reference-perfect` is the manifest projected into a flawless extraction — it
exists to prove the reconciler can score a perfect input perfectly, because until it can, no
figure it produces is evidence. `reference-flawed` has one of each failure class injected
deliberately. The two `cowork-01` rows are the same real run, scored twice.

**The bars group by configuration, not by run.** Two runs of the same Skills on the same folder
scored 100% and 28.6% flag precision, so a bar per run would invite the reading that something
changed between them when nothing did. Each bar is the mean of its group and the whisker is the
range, which puts the noise floor on the chart instead of in a footnote. `cowork-01` is on the
table but not in the bars: it was measured against a manifest that has since been corrected, so
pooling it with runs measured against the current one would average two different questions.

| Run | Field accuracy | Coverage | Flag precision | Flag recall |
|---|---|---|---|---|
| `reference-perfect` | 100.0% | 100.0% | 100.0% | 100.0% |
| `reference-flawed` | 99.7% | 99.9% | **87.5%** | **87.5%** |
| `cowork-01` *(superseded)* | 89.5% | 100.0% | **50.0%** | 100.0% |
| `cowork-01-fixed` | 100.0% | 100.0% | 100.0% | 100.0% |
| `cowork-02` | 98.4% | 100.0% | **22.2%** | 100.0% |
| `cowork-03` *(Skills v2)* | 100.0% | 100.0% | 100.0% | 100.0% |
| `cowork-04` *(Skills v2)* | 100.0% | 100.0% | **66.7%** | 100.0% |
| `holdout-01` *(seed 7777, superseded)* | 100.0% | 100.0% | 100.0% | **87.5%** |
| `holdout-01-fixed` *(seed 7777)* | 100.0% | 100.0% | 100.0% | 100.0% |

**The gap between the first two rows is the argument.** The flawed run guesses at a total that is
physically cropped off the page, misreads another, drops a field, over-flags the legible control,
misses a duplicate invoice, and files a lunch receipt as an invoice. That costs it **three tenths
of one percentage point** of accuracy.

It is not a quirk of this corpus. 735 of 743 fields sit on perfectly legible pages, so one
invented figure will always be a rounding error against the denominator — and the bigger the
corpus, the smaller it gets. Any extraction demo reporting a single accuracy number is reporting
the number least able to detect the failure that matters.

**The gap between the last two rows is the more uncomfortable one, and it is not about the
model.** They are one run, scored against the ground truth before and after the ground truth was
corrected. Every failure in the 89.5% was the manifest being wrong about its own documents — an
unsigned figure the page printed signed, an `Original` column the page showed and the manifest
omitted, two totals the manifest asserted that the file does not contain. The run read the
documents more carefully than the generator described them. See
[What broke](#what-broke).

**`cowork-02` is a second real run on the same folder, and it is the argument for running more
than one.** Identical documents, byte for byte. It raised its hand on all eight hard fields, so
its recall is also 100% — but it flagged ten legible figures it had no need to, and on both
statements of account it supplied a total the document does not state, computing `165515.10` from
the rows and reporting it at `low` with an accurate note that the cell holds an uncalculated
`=SUM()`. Honest hedging rather than silent invention, and still an assertion the document does
not make, which is why accuracy and precision move and recall does not. The behaviour does not
reproduce between runs, and a single run is a sample of one.

**Both of those failures were the Skill under-specifying, and the fix is written down before the
next run rather than after it.** The Skill said to normalise `1.234,56` without saying what
confidence that leaves behind, and it said not to compute a missing figure without covering the
case where the cell is present, labelled, and holds an uncalculated formula — which is the one
place the rule was needed and the one place it did not reach. With a band that wide, an edit can
be made to look like it worked by running until it does, so the prediction and what would falsify
it are recorded in
[the runbook](docs/notes/step-5-cowork-runbook.md#the-third-run--a-skill-edit-predicted-before-it-runs)
in advance: precision to 100%, coverage and recall unmoved, and case 7 still held — because both
changes push toward saying less, and a Skill frightened into declining everything would post a
better precision while being worse.

**`cowork-03` is that run, and every line of the prediction held.** The aggregate is the weaker
half of it: the edit named which fields had to move, and they are checkable individually. The ten
Northfield money fields flagged for their punctuation went to zero. Both statements went from a
computed `165515.10` at `low` to `null` at `unreadable`, now citing the cell that should have held
the figure — *"the TOTAL OUTSTANDING label is present but cell E17 holds the uncalculated formula
`=SUM(E13:E15)` with no stored result"*. Case 7 and coverage held, so none of it was bought by
going quiet.

**`cowork-04` is the second v2 run, and it is the reason the v2 bar has a whisker rather than a
victory.** It scored 100% on accuracy, coverage and recall like its predecessor — and 66.7% on
flag precision, because it did something run 3 did not. On the one invoice whose totals block is
below the scan margin, it reported `subtotal`, `tax_amount`, `net_amount` and `retainer_credit`
as unreadable alongside `total_due`: five rows in a reviewer's queue where one would do, each
saying the same thing.

**Both v2 runs originally scored an identical 100% on all four metrics, and that was the finding.**
One handed a reviewer 8 QA rows and the other 12 — fifty percent more work — and no metric moved,
because those four fields land in the `declined` class (undeclared by the manifest, reported with
no value) which was neutral to everything. A measurement that cannot see fifty percent more review
is not measuring review. `declined` now counts in flag precision's denominator: it asserts no
value, so accuracy is untouched, but the row still costs someone a look.

**The deeper fault is not reviewer load, and it is why this is a bug rather than a preference.**
Naming the four fields of a block you cannot see is a claim about what was cropped, and the crop
is precisely what cannot be observed. That page stops mid-line-item — there may equally have been
a fourth line item, a discount, a late fee. A run listing exactly the fields a *typical* invoice
carries is not reading the page, it is reciting the template, which is the one thing the Skill
already forbade and which arrived from the direction nobody was watching. The Skill now says to
report the total and stop, and to say in the reason that the extent of the loss is unknown.

**So the honest read of the edit is a raised floor, not a fix.** Across the four real runs, Skills
v1 spans 22.2–100% on flag precision and v2 spans 66.7–100%. The mean moves from 61.1% to 83.3%
and the worst case roughly triples, which is a real improvement in the number that matters — and
the two bands still overlap, so on n=2 apiece this is not yet a configuration that has stopped
varying. The chart is drawn to make that overlap impossible to miss rather than to flatter the
edit.

The two that move are flag precision and flag recall: does the pipeline know when it cannot read
something. A finance team can work with a system that says "I could not read this figure, here is
the page". It cannot work with one that quietly supplies a plausible number.

## The 34,906.40 that is missing on purpose

The clearest thing in the repo is a subtraction:

```
true total, from the manifest       2,003,793.28
total on the workbook's Summary     1,968,886.88
                                    ────────────
                                       34,906.40
```

That difference is exactly one invoice: `INV-2026-1500`, whose total the scanner cut off at the
margin. The correct behaviour is not to produce a number. So the cell is empty, the QA sheet cites
the file and page it could not read, the workbook total is knowably short, and the shortfall has a
name. A pipeline that had guessed would have shown a total that balanced and been wrong by
£34,906.40 with nothing to indicate it.

That is the thirty seconds worth recording, and it is why the recording shot list opens on the
exception rather than on the extraction working.

## What it is

Seed 42, period 2026-08. Every figure below is reproducible with `scripts/corpus.py --seed 42`.

| | |
|---|---|
| Documents | 74 — 18 SOWs, 6 amendments, 36 invoices, 8 remittances, 2 statements, 4 out of scope |
| Files | 74 — one file holds two documents, one document is filed under two names |
| Document instances | 75, merging to 74 records. The three numbers being different is the point |
| Formats | 47 `.pdf`, 24 `.docx`, 2 `.xlsx`, 1 `.eml` |
| Image-only scans | 22 of the 47 PDFs. No text layer at all — vision or nothing |
| Declared fields | 743, of which 8 are deliberately hard |
| Mess cases | 10, each mapped to a named rule in a Skill |
| Assertions | 821 across seven `check_*.py` harnesses |

Ten deliberate defects, each of which forces a specific rule rather than existing to make the
corpus look difficult:

| # | Case | The rule it forces |
|---|---|---|
| 1 | Image-only scan, no text layer | An empty text extraction is not an empty document |
| 2 | Skewed, speckled, grey — but legible | Degraded is not unreadable. `high` is the correct answer |
| 3 | Two documents in one PDF | A file is not a document. One record each, with page ranges |
| 4 | An amendment supersedes the original | Set `supersedes`. The stale ceiling must not reach the asset |
| 5 | The same invoice under a second filename | Dedupe on the natural key, never the filename |
| 6 | One client billing in `1.234,56` | Normalise the locale, round-trip to the cent |
| 7 | **The total cut off by the scanner margin** | `unreadable`, with a reason. Never a guess |
| 8 | Handwriting contradicting the printed terms | Record both, flag the conflict, escalate |
| 9 | `03/09/2026`, ambiguous | Resolve from the billing period, or flag that you cannot |
| 10 | A lunch receipt in the folder | `out_of_scope`. Do not force-fit it to the schema |

Case 2 is a control, and it is the only reason the calibration numbers mean anything. Nine of the
ten cases reward flagging, so a run that flagged every field would score 100% on recall while
being useless. Case 2 is legible, so flagging it is wrong, and flag precision is the only number
that falls when a run hedges everything. Asserted:

```
30  t  flagging everything scores full recall -- caution is not free elsewhere   recall 100.0%
31  t  ...and flag precision is the number that collapses                        precision 0.6%
```

## Decisions

| Decision | Rather than | Why |
|---|---|---|
| Corpus generated backwards from a seeded portfolio | Hand-labelling a folder of real documents | Ground truth is free, exact, and not correlated with the pipeline's blind spots. It is the only reason a four-figure scorecard is trustworthy at all |
| In-process `import generate()` | A DSN, or a transcribed copy of the schema | Nothing to configure and nothing to keep in sync. The generator is pure and stdlib-only, so a portfolio costs 0.3s and no container. A transcribed copy would drift; this one cannot, because there is only one |
| The manifest is written by the renderer, in the same pass | Deriving truth by reading the documents back | A truth file produced by a reader measures the reader against itself. This is the guardrail that outranks every other rule in the build |
| Vision in scope, 30% of the corpus with no text layer | A text-only corpus with `pdfplumber` | It is the difference between a parsing exercise and the problem the brief describes. Also the one assumption that could invalidate the design, so it was spiked against the real product on day one, before any generator code existed |
| The document as the unit of extraction | The file | A file may hold two documents, or the same document may sit in two files, or it may hold nothing that belongs. Three of the ten cases only exist because these are different |
| Three confidence states, with `unreadable` carrying a reason and a null value | A confidence float | `0.34` is not actionable and cannot be validated. A schema can enforce that a null value arrives with a reason and no evidence block; it cannot enforce that a float was honest |
| Four metrics, accuracy and calibration | The accuracy figure every vendor quotes | Accuracy moved 0.14 points between a run that declined a cropped total and one that invented it. Flag recall moved 25 |
| Typed comparison in the reconciler, including for identifiers | String equality | `I` and `l` are the same bitmap in Helvetica-Bold. Naive equality reports the corpus's own typography as a model error — see below, because this is exactly what happened |
| DocMess owns its Postgres on 5434 | Borrowing the sibling project's container | Once ground truth is in `manifest.json` there is no cross-database join to preserve, and a shared container means a teardown in one repo destroying data in the other |
| Standalone `check_*.py` collecting every result | pytest stopping at the first failure | Whether one thing broke or ten is the first question after a red run, and a harness that exits early cannot answer it. The assertions are also read as prose more often than they are run |
| Append-only `scores.jsonl` | Keeping the best run | A scorecard that only kept the good result would be marketing. The before-and-after is the entire value of the file |

## The edits were all made looking at one corpus

Three versions of the extraction Skill, and every change in them was written while staring at seed
42: the separator rule at Northfield's two invoices, the uncalculated-formula rule at these two
statements of account, the cropped-block rule at this one scan. That is fitting to the test set.
Run it enough times and you get a Skill that scores beautifully on these seventy-four documents
and no better than the first version anywhere else, with a scorecard that cannot tell you which
has happened — every number on it comes from the corpus the edits were tuned against.

It is the most likely thing wrong with this repo, so it should not have to be discovered by a
reader.

`corpus-holdout/` is seed 7777, generated from a different BizData portfolio through the same
pipeline: 74 documents, 36 invoices, 8 genuinely hard fields, the same ten defects, and entirely
different clients and invoice numbers. It was never rendered while any Skill edit was being
written, and it is outside `FIXTURE_SEEDS`, so nothing in the check suite has looked at it either.
Scoring a run against it is the only measurement here that can distinguish a Skill that got better
from one that memorised:

```bash
scripts/corpus.py  --seed 7777 --corpus corpus-holdout
scripts/degrade.py --seed 7777 --corpus corpus-holdout
scripts/reconcile.py --run holdout-01 --skill v3 \
    --manifest corpus-holdout/manifest.json --in <banked>/extractions.json
```

A held-out score materially below seed 42's was the expected result. It did not happen.
`holdout-01` scored **100% on accuracy, coverage and flag precision**, held all ten defects, and
produced no wrong value, no invention, no over-flag and no decline. Whatever the three Skill
versions learned, it was not the particular clients and invoice numbers of seed 42.

### The one miss was the ground truth, and only a held-out corpus could have found it

Flag recall came back 87.5% — seven of eight hard fields. The eighth was case 9, the ambiguous
date, planted on an invoice whose issue date printed as **`07/07/2026`**. Day-first and month-first
give the same date. Nothing on that page is ambiguous, the run read it correctly and confidently,
and the scorer docked it a full share of recall for declining to hedge about nothing.

The guard in `documents.py` states its own purpose exactly: it exists so a caller "cannot plant a
case that reads unambiguously and then be scored for failing to flag it". It then tested only
whether the day was 12 or less. Seed 42's case 9 is `03/06/2026`, so **seed 42 could not have
exposed this defect at any number of runs.** The selector and the guard now require `day != month`,
`check_corpus.py` asserts it, and seed 42's manifest is byte-identical after the change — checked,
not assumed, because five banked runs and a recording depend on it.

### The uncomfortable part of that correction

Changing the answer key after seeing the run disagree with it is the exact move a held-out test
exists to prevent. It is worth being plain about why this is a defect rather than a disagreement,
and about the fact that a reader should verify the reasoning rather than take it:
`07/07/2026` denotes 7 July under either convention, which is checkable from the page in the time
it takes to read this sentence, and the field's *value* was never in dispute — the run and the
manifest agreed on `2026-07-07`. Only the expected confidence changed.

That is a weaker justification than a corpus that was right the first time, and it is the second
occasion in this project where the generator described its own documents wrongly. Both scorings
are kept: `holdout-01` at 87.5% as measured, `holdout-01-fixed` at 100% against the corrected
expectation, marked in `scores.jsonl` and excluded from the chart's bars in the same way
`cowork-01` is.

Regenerating seed 7777 with the fix moves case 9 to a different invoice, so `corpus-holdout/` is
pinned to the documents that were actually run and carries `manifest-corrected.json` alongside its
original, with the single change recorded in a `corrections` block inside the file. A genuinely
untouched held-out number needs a fresh corpus and a fresh run, and this one has now been looked
at, which is what makes it no longer held out.

## What broke

The part worth reading. Seventeen failures found during the build, each caught by something that
runs rather than by inspection, and each one a thing that would have shipped looking correct.

The last several are different from the rest, and they are the ones I would read first. Everything
above them was caught by a harness. These were caught by the pipeline under test, and every one of
them was a defect in the thing doing the measuring.

**A mess case was planted where there was no mess, and the guard against it checked the wrong
half of the condition.** Case 9 is an ambiguous date, and it is only ambiguous when day-first and
month-first disagree. The selector required a day of 12 or less and stopped there, so seed 7777
planted it on `07/07/2026`, which reads as 7 July either way. A run that read it correctly and
confidently was scored as having failed to flag it. The guard in `documents.py` existed precisely
to prevent this and said so in its comment, then tested only the day. Seed 42's instance is
`03/06/2026`, which is genuinely ambiguous, so no number of seed 42 runs could have surfaced it --
it took the first corpus the Skills had never seen, on the first run against it.

**The relocation flag aimed every path at the new corpus except the one that matters.** Adding
`--corpus` so a held-out seed could be built without disturbing seed 42 rebound the inbox and the
manifest and missed `PAGES_SHA`, which is derived from the corpus root at import time. Building
the holdout therefore wrote seed 7777's page hashes over seed 42's — the drift-detection file,
drifted, which is the exact failure its own guard was added to prevent, arriving through the one
door the guard does not watch. Caught because the script prints the path it writes to and the path
was wrong. Every path the script writes is now rebound in one place.

**And then the fix for that made fifty percent more review work invisible.** `declined` was put in
no metric's numerator and no metric's denominator, which was right for accuracy and wrong for
everything else. Two runs of the same Skills scored an identical 100% on all four metrics while
one handed a reviewer 8 QA rows and the other 12 — the extra four being the rest of the totals
block on the cropped invoice, each row restating what the fifth already said. Four runs split
two-two on that behaviour, which looked like model noise and was a contract that never said which
of its own rules wins when a page is cut.

The measurement fix is that `declined` now counts in flag precision's denominator: no value
asserted, so accuracy is untouched, but the row still costs somebody a look. The contract fix is
the sharper half — listing the fields of a block that is off the page is a claim about what was
cropped, and the crop is the one thing that cannot be observed. That page stops mid-line-item, so
there may equally have been a fourth line item or a discount. A run naming exactly the fields a
typical invoice carries is reciting the template, which the Skill already forbade in a section it
never thought to apply here. Two assertions now fail if `declined` goes neutral again, checked by
reverting the line and watching them go red.

**The scorecard called a run a guesser for saying it could not read something.** The second Cowork
run reported `subtotal`, `net_amount`, `tax_amount` and `retainer_credit` as unreadable on the
cropped invoice, with the correct reason. The manifest does not declare those fields — precisely
*because* they are cropped off the page — so the reconciler classed each one as `hallucinated`,
the class reserved for inventing a figure, and four miscounts dragged mess case 7 to a verdict of
`guessed`. The run had held case 7 perfectly. The run and the corpus agreed there was nothing to
read; they expressed it differently, the run by declining and the manifest by omission. There is
now a seventh outcome class, `declined`, in no metric's numerator or denominator, because a field
with no value asserts nothing and nothing cannot be a hallucination.

**The schema declined to have an opinion about types, so two runs disagreed and only the scorer
noticed.** `value` carried a description and no `type`. One run emitted a remittance's settled
invoices as `["INV-1", "INV-2"]` and another as `"INV-1, INV-2"`; both passed validation, and the
disagreement surfaced two steps downstream as eight fields scored `wrong` for content that was
character-for-character identical. A shape the scorer will mark wrong is a shape the validator
should refuse. `value` is now typed and `invoices_settled` must be an array — a list of one is
still a list — and the validator rejects the flattened form at the door.

**The gate that proves the SQL agrees with Python was not reading the SQL.** The README calls this
"the four metrics, computed twice", and `db/checks/reconciliation.sql` is the second computation —
the file a client is handed. `check_load.py` ran it, checked it exited without error, and then
compared the database against a *third* copy of the same metrics restated inside the harness. The
deliverable SQL's actual output was never compared to anything and could have returned any number.
Found when the recall definition changed: the SQL was updated, the restatement was not, and the
gate reported a disagreement that was between two copies of the check rather than between SQL and
Python. The gate now parses the figures `reconciliation.sql` prints and asserts them against the
`runs` table.

**And a constraint in the schema file was not the constraint in the database.** `create table if
not exists` cannot evolve a table that already exists, so adding `declined` to the outcome
vocabulary left the running Postgres enforcing the old list. The load passed on a fresh volume and
failed on a day-old one. The check constraints are now dropped and re-added on every load, which
is idempotent, costs milliseconds, and means the file and the database cannot disagree.

**The ground truth was wrong about its own documents, in four places, and 818 assertions did not
notice.** The first real Cowork run scored 89.5% accuracy and 50% flag precision. Every one of its
81 scored failures was a defect in the manifest or the contract, and not one was a misreading:

- **The retainer credit was declared unsigned and printed signed.** The invoice says
  `Less retainer applied on account  -9,814.23`; the manifest declared `9814.23`, because the
  renderer applied the minus at draw time and the manifest recorded the portfolio's figure. The
  run read the page. 35 fields scored wrong.
- **`fee_type` was declared as an internal enum.** The page says `Fixed fee`, the manifest said
  `fixed`, and nothing in any Skill or in the schema ever asked for that normalisation. An
  expectation that lives only in the manifest is not a contract; it is a preference being scored
  as though it were a fact. 18 fields.
- **The amendments print an `Original` column that the manifest did not declare.** Case 4's
  documents carry a three-column table — label, Original, Revised — and only the revised three
  were declared. Reading a labelled figure out of a labelled table scored as `hallucinated`, the
  worst class on the scorecard, awarded for the most defensible behaviour available. 24 fields.
- **Two statement totals are asserted by the manifest and absent from the file.** The cells hold
  `=SUM(...)` with no cached result, so the figure is genuinely not in the bytes. The run reported
  them unreadable and was right; the manifest called them readable at high confidence, so being
  right cost it half its flag precision. 4 fields.

Corrected, the same run scores 100% on all four metrics. Nothing about the run changed and
`corpus/pages.sha256` is byte-identical across the fix — every rendered page is the same, and only
the claims about them moved.

The reason none of this was caught is a single structural gap: **every assertion compared the
manifest to itself.** Its schema, its arithmetic, its counts, its determinism — all self-referential,
all passing. Nothing opened a PDF and compared a declared value to the text on the page. That
check now exists, and its mutation test is in the harness: it catches all four gaps, and a total
altered by one penny.

**And the workbook builder hid the duplicate it correctly excluded.** `main()` filtered
`duplicate_of` records out before calling `build()`, so the duplicate branch in `write_exceptions`
could never fire. The comment at the call site said the Exceptions tab *had* to show it. Excluding
the duplicate from the totals was right; never telling the reviewer it existed was not — two files
on disk, one invoice in the register, and nothing on the sheet explaining the difference. Twenty
assertions checked that the duplicate was excluded and none checked that it was reported. The run
found it by reading its own output workbook and reporting the discrepancy against the code
comment.

**The confusable fold ran in the wrong order and mis-scored 70 identifiers.** The reconciler folds
glyphs that share a bitmap before comparing identifiers, because day one established that `I` and
`l` are indistinguishable in Helvetica-Bold. The fold was `a.upper().translate(CONFUSABLE)` —
and `.upper()` turns a misread `l` into `L`, which is not in the table. So `INV-2026-09l2` never
folded onto `INV-2026-0912`, and 33 invoice numbers plus 37 other references were scored as model
errors caused entirely by two method calls in the wrong order. Invisible by inspection: the line
reads correctly and any test you would think to write passes. What caught it was taking a
*correct* run and re-rendering every value the way a different reader would have typed it — money
as `1,200.00`, dates as `03/07/2026`, `I` typed as `l` — then asserting it still scores 100%.

**Minted invoice numbers collided on seed 9015.** BizData invoices carry an integer id and nothing
else, so DocMess mints `INV-2026-NNNN` itself. The first version derived each number from its own
id independently, which is not injective across a set — two ids mapped to one number, and on a
corpus whose entire dedupe case turns on the invoice number being a unique natural key. Fixed by
drawing one stride and one base per run, making the mapping affine and provably collision-free.
Found by a fixture seed, not by seed 42, which is the argument for running seventeen.

**Case 2 rendered illegibly, which is the one thing it must not do.** The large-print control is
meant to prove that degraded is not unreadable, so it has to be *readable*. At the larger type
size the fixed column widths overlapped and the description column ran through the amounts.
`check_degrade` was passing at the time, because it measured ink coverage. It took looking at the
PNG. Fixed with dynamic column widths measured from `stringWidth`, plus a runtime `ValueError`
that refuses to render a colliding page at all — a corpus that silently produces an illegible
control produces a scorecard that lies in the direction that flatters it.

**The obvious legibility metric measured the wrong thing.** Case 2's check used stroke width to
prove large print is larger. Stroke width barely moves with type size and is dominated by the
speckle noise the degradation adds, so it separated nothing. Replaced with the median height of
text-bearing pixel bands, which gives a clean 1.36× ratio against the other scans.

**The handwriting overlapped the printed terms it was meant to contradict.** Case 8's annotation
anchored to the payment-terms block, but to its *first* line rather than its last, so it landed on
top of the text. The check that should have caught it compared ink density before and after
degradation — confounded, because degradation changes ink density everywhere. Replaced with an
assertion that a band of clear paper separates the printed terms from the handwriting on the
degraded page, which is the actual claim.

**Case 5's duplicate was not being hashed, twice.** The duplicate is a second *file*, not a second
document, so it has no manifest entry of its own and is reachable only through `also_filed_as`.
Both `check_corpus.py` and `degrade.py` built their file sets from `source_file` alone: 46 files
hashed against 47 present, silently. The same gap in two places, three days apart.

**Three mess cases reported a false all-clear.** Cases 3, 5 and 10 are not about a field's value —
they are about page ranges, deduplication and classification, and none of the documents involved
has a field that would come out wrong. Scored from the field loop alone they read `held` no matter
what the run actually did with them. A per-case column showing ten `held` verdicts when three were
never examined is worse than omitting them.

**`remittance_ref` was missing from the schema's field-name vocabulary.** The schema closes the
set of field names, deliberately — an open vocabulary lets a run invent a field to put an
awkward value in. It also means the generator can emit a field the contract does not permit, which
is what happened, and `check_corpus.py` caught it by validating the manifest against the same
schema the extraction is held to.

**The scored-run artifact listed only the failures, and so could not be audited.** Writing seven
hundred rows saying *fine* seemed like noise, so `reconcile.py` recorded only the fields that went
wrong. Then step 7 tried to recompute the four metrics in SQL and could not compute a single one:
**a denominator cannot be rebuilt from a list of numerators.** The only way to check the file was
to trust the summary counts it had written itself, which is not a check. The per-field list is now
complete — 743 rows, sorted failures-first so the interesting twenty are still at the top. Worth
noticing that the artifact was not wrong, it was just unfalsifiable, and nothing but a second
implementation asking to read it would have revealed that.

**The BizData seam needed the module in `sys.modules` before executing it.** Both repos have a
`scripts/` package, so a `sys.path` import collides. `importlib.util` avoids that, but under
Python 3.12 `@dataclass` resolves field types by looking the module up in `sys.modules` — which
has to happen before `exec_module`, not after. The failure is an `AttributeError` on `NoneType`
several frames from the cause.

## The two things a schema cannot catch

`reference-flawed` is fully schema-valid. The bundled validator exits 0 on it:

```
19  t  flawed: it is schema-valid, so only the reconciler can catch it   validator exits 0
```

Every injected error has the right *shape* — correct confidence enum, evidence present where
required, page numbers ascending. A schema constrains form. Only the reconciler can see that a
well-formed figure is the wrong figure, and only the manifest lets it know which. That is why both
exist, and why the schema is not the deliverable.

## The four metrics, computed twice

`db/checks/reconciliation.sql` recomputes all four in SQL from the loaded rows and diffs them
against what `reconcile.py` produced:

```
 n_disagreements | runs_compared
-----------------+---------------
               0 |             2
```

What the second implementation is for is not the arithmetic — one division each, nobody gets that
wrong — but the **denominators**, which are four unforced choices. Coverage is over what the
corpus declares, so a run cannot improve it by producing fields nobody asked for. Flag recall is
over what the corpus says was hard rather than what the run thought was hard, which would make it
100% by construction. A metric with nothing to divide by is `null`, not `0.0`. Transcribing those
into SQL either agrees or proves one of the two wrong, and it only counts as a check because the
SQL reads the stored figures exactly once, in the last query, to diff them.

Ground truth is not in the database. `field_outcomes` records that a field was *expected* to be
unreadable without recording what the true value was — a fact about the run rather than about the
world — which is enough for all four metrics and keeps the schema portable to an environment that
has no manifest. Which is every environment except this one.

## Layout

```
schema/       extraction.schema.json — the contract, with the confidence rules in the schema itself
              mess-cases.md          — the ten cases and the rule each one forces
skills/       classify-document/     — a file is not a document
              extract-to-schema/     — the confidence contract, with a bundled validator
              build-extraction-workbook/
docker-compose.yml                   — Postgres 16.10 on 5434, its own volume
scripts/      bizdata.py             — the in-process seam to the generator
              documents.py corpus.py — the renderer, and the manifest writer
              degrade.py             — rasterise, skew, speckle, crop, annotate
              reference_run.py       — --perfect and --flawed, so steps 6-8 run without a Cowork run
              reconcile.py           — seven outcome classes, four metrics
              scorecard.py           — the md and the png
              load.py                — into Postgres, idempotent, or --csv
              check_*.py             — 823 assertions
corpus/       manifest.json          — ground truth. Committed; the documents are not
              pages.sha256           — the determinism contract for the rendered pages
scorecard/    scores.jsonl           — append-only. The bad runs stay
db/           schema.sql             — the asset, and the measurement layer, kept separate
              checks/reconciliation.sql — the four metrics again, in SQL that cannot see the answer
docs/         evidence/              — screenshots and transcripts the README refers to
              in-a-client-environment.md — what ports, what doesn't, and the ground-truth problem
              recording-shot-list.md — six shots, every on-screen figure checked against seed 42
```

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export DOCMESS_BIZDATA_ROOT=../BizData        # a checkout of the sibling generator

.venv/bin/python scripts/corpus.py --seed 42 --period 2026-08   # 74 files + the manifest
.venv/bin/python scripts/degrade.py                             # 22 of them become scans

# Then either bank a Cowork run into extractions.json — see the runbook — or:
.venv/bin/python scripts/reference_run.py --perfect

.venv/bin/python skills/build-extraction-workbook/scripts/build_workbook.py
.venv/bin/python scripts/reconcile.py --run my-run
.venv/bin/python scripts/scorecard.py

# Optional. The load is the most cuttable part of the build, and --csv is why.
docker compose up -d
.venv/bin/python scripts/load.py
docker exec -i docmess-postgres psql -U docmess -d docmess -f - < db/checks/reconciliation.sql
.venv/bin/python scripts/load.py --csv out/csv/     # the same six tables, no database

for f in scripts/check_*.py; do .venv/bin/python "$f"; done
```

Everything is deterministic from `--seed`. `manifest.json` is byte-identical across runs and
`pages.sha256` pins the rendered pages pixel for pixel; the PDF container bytes are deliberately
not asserted, because reportlab stamps a document id and Pillow a creation date, and chasing those
would buy nothing the page hashes do not already give.

## Honest about the shape of this

The corpus is synthetic, and it is synthetic in a way that matters: **the mess is the mess I
thought to plant.** Ten defects chosen by someone who knew what the extraction would find hard is
not the same distribution as a real accounts-payable folder, which will contain a defect nobody
listed. The scorecard is a real measurement against a corpus whose difficulty was chosen, and
that is a weaker claim than it looks at four figures.

That caveat got tested sooner than expected, and in both directions.

The corpus produced a defect nobody listed. The statements were written with `=SUM()` formulas
and openpyxl never calculates one, so two documents ended up asserting totals they do not contain
— the same failure as case 7, arrived at by a completely different route, and planted by
accident. It is now declared rather than removed, because a spreadsheet whose totals were never
calculated is an ordinary thing to find in a client's folder and a more honest test than anything
I would have designed.

And the corpus was harder to get right than the pipeline reading it. Four of the manifest's
claims disagreed with the documents, and the run that read them scored 89.5% for being correct.
**A generated ground truth is only as good as the renderer's agreement with it, and that
agreement is itself a thing that has to be asserted** — which is the same lesson as
[docs/in-a-client-environment.md](docs/in-a-client-environment.md), where the answers have to come
from somewhere other than a generator, arriving a step earlier than expected.

What this does establish is that the pipeline flags what it cannot read rather than inventing it —
including one figure it could have justified inventing, since a remittance in the same folder
names the exact total that is cropped off INV-2026-1500, and it reported the number, refused to
use it, and said to chase the uncropped scan. And that the measurement apparatus is checkable: a
perfect input scores perfectly, a hedged input loses precision, and the four places where the
apparatus was wrong were found because the run cited its evidence well enough for its
disagreements to be checked against the page.
