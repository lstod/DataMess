# DocMess

**Turn a messy folder of business documents into workable data with Cowork.** Point it at a
folder of statements of work, invoices, remittances and statements (scanned, skewed, duplicated,
a third of them image-only) and get back a spreadsheet a finance team can work from. Every figure
it could not read is flagged with the page it came from, not guessed.

## What it does

- **In:** a folder of mixed files (PDFs, many with no text layer, plus Word documents,
  spreadsheets and emails) with the mess real folders have: two documents in one file, the same
  invoice filed twice, an amendment that replaces an earlier ceiling, a total cut off by the
  scanner, a lunch receipt that does not belong.
- **Cowork, with three Skills:** `classify-document` works out what each file is and where one
  document ends and the next begins. `extract-to-schema` reads every figure into a strict schema
  and marks each one `high`, `low` or `unreadable`, with a reason. `build-extraction-workbook`
  turns the result into a workbook.
- **Out:** an `.xlsx` with live formulas across Summary, Invoices, Engagements, Exceptions and QA
  sheets. The QA sheet cites the file and page behind every flagged field, and the Exceptions
  sheet lists duplicates, superseded amendments and files that do not belong. The same data can
  optionally be loaded into Postgres or exported as CSV.

The rest of this README shows how the project checks that it does this honestly. It measures two
things: whether it read the figures right, and whether it knew when it could not.

## What's in the folder

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

Case 2 is the control: degraded but legible, so flagging it is wrong. Without it, a run that
flagged every field would score perfectly on recall while being useless.
[Why that matters](docs/writeup.md#why-case-2-is-a-control).

## The scorecard

![scorecard](scorecard/scorecard.png)

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

`reference-perfect` is the ground truth fed straight back in, to prove the scorer can score a
perfect input perfectly. Rows marked *superseded* were scored against a ground truth that was
later corrected, and are kept rather than deleted.

**Accuracy is the wrong headline.** `reference-flawed` invents a cropped total, misreads another
figure and files a lunch receipt as an invoice, and loses three tenths of one percentage point of
accuracy, because 735 of 743 fields sit on legible pages. Flag precision and flag recall are the
numbers that catch it: they measure whether the pipeline knows when it cannot read something.
Runs of the same Skills on the same folder also vary (two v1 runs scored 100% and 22.2% flag
precision), so the chart groups bars by configuration and shows the range as a whisker.

The run-by-run story, including what each Skill edit changed and whether its prediction held, is
in [the writeup](docs/writeup.md#the-scorecard-run-by-run).

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

## How it is tested

Most document-extraction projects start with a folder of documents and spend most of their budget
hand-labelling the right answers. DocMess starts from the answers: it takes a seeded portfolio of
engagements, statements of work and invoices from the sibling BizData generator, renders the paper
those rows would have come from, degrades it, and asks Cowork to reconstruct the rows. The
generator wrote the truth, so there is no labelling step, and every extracted field is scored
against it. More in [the writeup](docs/writeup.md#the-corpus-is-generated-backwards).

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
              writeup.md             — the long version: every run, every decision, what broke
              in-a-client-environment.md — what ports, what doesn't, and the ground-truth problem
              recording-shot-list.md — six shots, every on-screen figure checked against seed 42
```

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export DOCMESS_BIZDATA_ROOT=../BizData        # a checkout of the sibling generator

.venv/bin/python scripts/corpus.py --seed 42 --period 2026-08   # 74 files + the manifest
.venv/bin/python scripts/degrade.py                             # 22 of them become scans

# Then either bank a Cowork run into extractions.json, or:
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

## Further reading

The corpus is synthetic, so the mess is the mess I planted: ten defects chosen by someone who
knew what the extraction would find hard, which is not the distribution of a real
accounts-payable folder. [More on that](docs/writeup.md#honest-about-the-shape-of-this).

- [The full writeup](docs/writeup.md)
  - [The scorecard, run by run](docs/writeup.md#the-scorecard-run-by-run)
  - [Decisions](docs/writeup.md#decisions): what was chosen, over what, and why
  - [The edits were all made looking at one corpus](docs/writeup.md#the-edits-were-all-made-looking-at-one-corpus):
    the held-out corpus test
  - [What broke](docs/writeup.md#what-broke): seventeen failures found during the build. Start here
  - [The two things a schema cannot catch](docs/writeup.md#the-two-things-a-schema-cannot-catch)
  - [The four metrics, computed twice](docs/writeup.md#the-four-metrics-computed-twice)
  - [Honest about the shape of this](docs/writeup.md#honest-about-the-shape-of-this)
- [docs/in-a-client-environment.md](docs/in-a-client-environment.md): what ports unchanged, what
  has to be rebuilt, and how you get ground truth when no generator planted it
- [docs/recording-shot-list.md](docs/recording-shot-list.md): six shots, three minutes. It opens
  on the 34,906.40 exception rather than on the extraction working
