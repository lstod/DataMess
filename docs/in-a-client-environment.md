# In a client environment

What ports across unchanged, what has to be rebuilt, and the part that does not port at all.

---

## Ports unchanged

**The schema.** `schema/extraction.schema.json` is the most transferable thing here. The
three-state confidence contract — `high`, `low`, `unreadable`, with a null value obliged to carry
a reason and forbidden from carrying evidence — is not specific to invoices or to this corpus. The
field-name vocabulary changes; the shape does not, and the shape is what stops a run putting an
awkward value somewhere plausible.

**The Skills, minus their vocabulary.** `classify-document`'s rules are about documents in
general: a file is not a document, an amendment supersedes, dedupe on the natural key rather than
the filename, refuse to force-fit. Those hold for any folder. What changes is the list of document
types and the natural key, which for this corpus is the invoice number and for a client will be
whatever their finance system actually keys on — ask, do not infer.

**The reconciler's structure.** Six outcome classes, four metrics, typed comparison, and the
separation of `hallucinated` from `wrong` are all independent of what is being extracted. So is
the `_ratio` decision to return null rather than zero when a run flagged nothing.

**The workbook builder.** Tabs change, the no-arithmetic-in-the-builder rule does not. Every
derived figure is a formula, so a controller who does not trust the pipeline can click a total and
see where it came from. That property is worth more in a client environment than here, because
there the audience has a reason to be suspicious.

**The check harness convention.** Standalone scripts that collect every result, shell out to the
real implementation, and restate their constants rather than importing them. The restating is what
lets a harness disagree with the code it checks, and it is how most of the failures in the
README's What broke were caught.

## Has to be rebuilt

**The corpus generator, obviously — and the point is that you do not rebuild it.** `documents.py`
and `corpus.py` exist to manufacture a measurable problem. A client has the problem already.

**Vision capacity planning.** 22 image-only PDFs is a demo. A real folder is tens of thousands,
and the cost and latency of a vision call per page is a line item rather than a footnote. That
number has to be established before anything is promised, on a sample of the client's actual
scans — theirs will be worse than these, because these were degraded by a script that was trying
to be fair.

**The document type list and the field vocabulary.** Both are closed sets in the schema, on
purpose. Both are discovery work, and both will be wrong on the first pass.

**Postgres.** DocMess's container on 5434 holds extractions only, because ground truth lives in a
JSON file next to it. In a client environment the extraction has to land next to data that already
exists, which turns a standalone table into a join against a system of record — and that join is
where the natural-key question stops being academic.

**Everything about permissions.** This repo reads a folder it created. A client's document store
has access controls, retention rules, and documents the pipeline must not read at all. None of
that is modelled here.

## The uncomfortable part: ground truth

**No client has a generator that planted the answers.** The single design decision that makes this
repo's scorecard trustworthy is the one thing that cannot be carried across, and every number in
the README depends on it. This section is the one that matters.

What does not work:

- **Extracting the truth with a second model.** Two models sharing a training distribution share
  blind spots. A cropped total is unreadable to both, and agreement gets recorded as
  confirmation. This is worse than no ground truth, because it produces a number.
- **Using the client's existing system as truth.** Tempting, since the invoices were presumably
  keyed into an ERP already. But those records are the output of a manual process with its own
  error rate, and where they disagree with the document the document is usually right. You end up
  measuring agreement with a data-entry team, and reporting it as accuracy.
- **Accepting the pipeline's own confidence as a proxy.** This is circular in the exact way the
  four-metric scorecard exists to expose. Flag precision and recall are only meaningful against an
  independent judgement of which fields were genuinely hard.

What does work, in the order to try it:

**1. A stratified hand-labelled sample, and budget for it properly.** Not a random sample —
random sampling of a document folder buys you a lot of easy pages. Stratify on the things that
predict difficulty: has a text layer or does not, page count, document type, and whether the
pipeline flagged anything. Over-sample the flagged stratum heavily, because that is the population
the two metrics that matter are computed over, and it is small. Two hundred documents labelled
carefully beats two thousand labelled quickly, and the labelling has to be done by someone who
knows the domain, blind to what the pipeline said.

**2. Double-key the sample and measure your labellers first.** Two independent labellers on the
same subset, and look at where they disagree before looking at the model at all. Their
disagreement rate is the ceiling on any accuracy figure you can honestly report — you cannot
measure a pipeline to a precision finer than your ground truth. On messy documents that ceiling is
routinely lower than the accuracy people expect to claim, and finding that out early changes what
gets promised. Disagreements are also the most informative documents in the set: they are usually
genuine ambiguity in the source, which means the correct pipeline behaviour is `low` confidence
rather than a value.

**3. Reconcile against arithmetic the documents already carry.** This is the cheapest real signal
and it needs no labels. Invoice line items sum to a subtotal; a statement sums to its invoices; a
remittance matches an invoice total. Where the document is internally consistent and the
extraction is not, the extraction is wrong — for free, at full corpus scale, with no sampling
error. It cannot tell you a field was read *right*, only that a set of fields is mutually
impossible, so it complements the sample rather than replacing it. On this corpus that check is
sharper than usual by construction: invoices carry a retainer credit line between subtotal and
net, so the total has to be *read* rather than summed from the amount column.

**4. Treat production disagreements as a growing label set.** Every field a human corrects
downstream is a label somebody already paid for. Capture the correction, the original, and the
page, and the sample grows in exactly the region where the pipeline is weak. This is the only
mechanism here that improves over time, and it is worth building the capture on day one even
though it returns nothing for months.

**What to say about it out loud.** The honest framing is that you can measure calibration well and
accuracy approximately. Whether the pipeline knows when it is unsure is answerable from a few
hundred stratified labels with tight confidence intervals, because it is a question about the
flagged population and the flagged population is small. Whether it is 97% or 98% accurate across
the whole folder is not answerable at any budget a client will approve, and quoting it anyway is
how extraction projects lose trust in month three. Lead with the calibration number and give the
accuracy figure an interval.

## What this repo would want first

If the next step were a real folder rather than a generated one:

1. A sample of fifty of the client's actual scans, before any commitment, to see how much worse
   than `degrade.py` reality is.
2. The natural key their finance system joins on, from the person who maintains it.
3. Whether the documents contain anything that must not leave the environment, because that
   decides whether a hosted model is available at all and therefore whether vision is on the
   table.
4. The manual error rate on the existing process. Not to beat it — to know what the ground-truth
   ceiling is, and to know what "good enough" was already being tolerated.
