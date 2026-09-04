# The mess register

Ten deliberate document defects. The order is the decision record's and does not change,
because `scorecard.md` has a column per case and the column headings are these numbers.

**A case that does not map to a line in a Skill is decoration. Cut it.** Ten is a cap rather
than a target. This matters more than it sounds: documents rendered faithfully out of a
database are trivial to extract, the pipeline would score perfectly, and the score would mean
nothing because there would be no way for it to fail. The register is what makes the corpus a
test rather than a demonstration.

| # | Case | The rule it forces | Skill | Planted by |
|---|---|---|---|---|
| 1 | Image-only scan, no text layer | Empty text extraction is not an empty document. Escalate to vision rather than emit nulls | `extract-to-schema` | `degrade.py` |
| 2 | Skewed, speckled, grayscale — but legible | Degraded is not unreadable. `high` is the correct answer here | `extract-to-schema` | `degrade.py` |
| 3 | Two documents in one PDF, an invoice with a remittance behind it | A file is not a document. One record per document, with a page range | `classify-document` | `corpus.py` |
| 4 | An amendment supersedes the original, both in the folder | Set `supersedes`. The stale ceiling must not reach the asset | `classify-document` | `corpus.py` |
| 5 | The same invoice under a second filename | Dedupe on the natural key, never the filename. Totals must not double-count | `classify-document` | `corpus.py` |
| 6 | One client's invoices in `1.234,56` format | Normalise the locale, and round-trip to the cent | `extract-to-schema` | `corpus.py` |
| 7 | **The total cut off by the scanner margin** | `unreadable`, with a reason. Never a guess | `extract-to-schema` | `degrade.py` |
| 8 | Handwriting contradicting the printed payment terms | Record both, flag the conflict, escalate. Do not silently prefer one | `extract-to-schema` | `degrade.py` |
| 9 | `03/09/2026`, ambiguous without context | Resolve from the invoice period if it can be resolved; flag it if it cannot | `extract-to-schema` | `corpus.py` |
| 10 | A lunch receipt that does not belong in the folder | `out_of_scope`. Do not force-fit it to the schema | `classify-document` | `corpus.py` |

## Why these ten and not others

Case 7 is the one the brief is really about — flag low-confidence extractions rather than
guessing — and case 10 is the one that reads as senior, because a pipeline that cannot say
"this isn't one of mine" will confidently put a sandwich in the accounts receivable.

**Case 2 is the control, and it is there deliberately.** Nine of these reward caution. A model
that flagged everything would pass all nine while being useless, and the scorecard would call
it excellent. Case 2 is legible under degradation, so flagging it is the wrong answer and
scores as a false positive against flag precision. Without it, decision 6's calibration only
measures one direction and flag recall can be bought at no cost.

## Where each case is planted, and why the split

Six cases are made by the renderer and four by the degradation pass. The split is not
arbitrary: cases 7 and 8 are raster operations. A total cut off by a scanner margin is a crop
of a rendered page, and a handwritten annotation is ink laid over pixels. Neither can be
expressed by the thing that writes the PDF, so neither belongs in `documents.py`.

The execution plan says step 2 plants "3 through 10" and then lists 7 and 8 under step 3.
This table is the resolution: **`corpus.py` plants 3, 4, 5, 6, 9 and 10; `degrade.py` plants
1, 2, 7 and 8.**

## What the manifest records about them

Every planted case is recorded in `corpus/manifest.json` under `mess_cases`, keyed by case
number, carrying the documents it touches and the field it turns on. `check_corpus.py` and
`check_degrade.py` assert that each case is present and locatable; `reconcile.py` reads the
same block to produce the per-case column in the scorecard.

Case 2 additionally records `expected_confidence: "high"` on its fields. It is the only case
that declares what the *model* should say rather than what the *document* says, and step 8
scores a flag on it as a false positive rather than as caution.

## The case that is not a case

Corpus invoices carry a retainer credit between subtotal and total, so the total due is not
the sum of the amount column and not the largest figure on the page. This is invoice
structure, not an eleventh mess case, and it earns its place by mapping to a line
`extract-to-schema` already has to carry: read the total, never derive it.

It came out of the step-0 spike, where the run reconciled the arithmetic on all eleven
documents rather than reading the totals block alone, and caught that the subtotal exceeded
the total due on one of them. See `docs/notes/step-0-vision-spike.md`.
