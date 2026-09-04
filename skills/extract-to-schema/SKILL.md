---
name: extract-to-schema
description: Read the figures off a classified document into a strict JSON schema, marking every field as high confidence, low confidence, or unreadable with a reason. Use when extracting invoice totals, SOW ceilings, dates or payment terms from scanned or degraded paperwork, and whenever a figure might be cropped, ambiguous, contradicted by a handwritten note, or written in an unfamiliar number format.
---

# Extract a document to the schema

Read what the document says. Not what it probably says, and not what would make the row add up.

The output is judged against what the documents were generated from, field by field, so a
plausible wrong answer scores worse than an honest gap. **A figure you cannot read is
`unreadable` with a reason, never a best guess.** This is the rule the whole thing exists for.

## The shape of a field

Every field is an object, and there are exactly three legal shapes:

```json
"total_due":  { "value": "20373.40", "confidence": "high", "evidence": "page 1, totals block, bottom right" }
"issue_date": { "value": "2026-09-03", "confidence": "low", "evidence": "page 1, header block, right",
                "reason": "written 03/09/2026; resolved day-first from the August billing period" }
"total_due":  { "value": null, "confidence": "unreadable",
                "reason": "the totals block is below the scan margin and is not on the page" }
```

| Confidence | When | value | evidence | reason |
|---|---|---|---|---|
| `high` | Read directly off the page and legible | required, non-null | **required** | **forbidden** |
| `low` | Read, but the reading depends on an inference | required, non-null | **required** | **required** |
| `unreadable` | Could not be read at all | must be `null` | **forbidden** | **required** |

The three shapes are mutually exclusive, so the confidence can be recovered from the shape.
That is deliberate: a record whose stated confidence disagrees with its own shape is rejected
rather than interpreted.

`confidence` is one of those three words. **Never a number.** `0.82` reads as precision and is
a vibe; three named states force a decision that can be checked.

**A `low` needs its inference written down.** "The value is present and the caveat travels with
it" is what the middle state is for — a `low` with no stated reason is indistinguishable from a
`high` and the field is rejected. Conversely, a `high` carrying an explanation is a `low` that
did not admit it.

**`evidence` is where on the page you read it**, precise enough that a person can go and look.
A human reviews the flagged fields against it. "The document" is not evidence. "Page 2, totals
block, bottom right" is.

**A field the document does not assert is left out entirely.** There is no fourth state for
missing. Do not emit a `null` value with `high` confidence, and do not invent a field because
similar documents have one — absence is measured separately from error, and a fabricated empty
field is scored as a wrong answer rather than as a gap.

## Values

- **Money as a decimal string**, no separators and no currency symbol: `"20373.40"`. Never a
  float — `0.1 + 0.2` is not `0.3`, and a lost cent is scored as a wrong answer.
- **Dates as ISO**, `YYYY-MM-DD`, whatever the page uses.
- **Periods as `YYYY-MM`.**
- Text as it appears, without tidying.

## Read the total. Never derive it.

These invoices carry a **retainer credit** between the subtotal and the net, so:

- the total due is **not** the sum of the amount column;
- the total due is sometimes **smaller** than the subtotal above it;
- neither the largest figure on the page nor the last one is reliably the total.

Read `total_due` from the line that says TOTAL DUE. If that line is not on the page, the answer
is `unreadable` — **not** `net + VAT`, even when both are visible and the arithmetic works.

If the arithmetic on a document does not reconcile, that is worth reporting, but do not
"correct" any figure to make it. Record what is printed.

**This is not a rule about invoices. It is the rule.** A figure the document does not state is
not yours to supply, on any document, however easy it is to obtain — including when the
document contains every number needed to compute it, and including when another document in
the same folder states it outright. Deriving it, summing it, or copying it across is the same
answer wearing three different disguises, and all three are scored as inventions.

The test is not "can I get to this number." It is "does this document assert it." If the answer
is no, the field is `unreadable` and the reason says where the figure went. Anything you worked
out that a reviewer would want belongs in the reason, not in the value.

## Eight things these documents do

**No text layer.** Roughly a third are image-only scans and a text extractor returns nothing at
all from them. An empty text extraction is not an empty document — look at the page. If you
cannot see the page, say so; do not emit a document full of nulls.

**Degraded but perfectly legible.** Skewed, speckled, grey. Degradation is not illegibility. If
you can read the figure, `high` is the correct answer, and flagging a legible field is scored
as a false positive exactly like a wrong value.

**A total cut off by the scanner margin.** The page ends mid-document. `unreadable`, with a
reason saying what happened to it.

**Do not enumerate the rest of the block.** A cut page invites listing every field that ought to
have been below the fold — `subtotal`, `tax_amount`, `net_amount`, the credit line — each one
`unreadable` for the same reason. Report `total_due` and stop.

The objection is not that four rows are tedious where one would do, though they are: they say the
same thing four times and send a reviewer to a document whose single problem the first row
already stated. It is that **the list is a claim about what was cropped, and the crop is the one
thing you cannot see.** You are not reading those fields off the page. You are reciting what an
invoice usually carries, on a page that stops mid-line-item — so there may equally have been a
fourth line item, a discount, a late fee, a note. Naming four absences and not those is a guess
wearing the costume of thoroughness, and it is the template-reciting this Skill forbids two
sections up, arriving from the direction nobody watches.

The honest statement about a truncated page is that it is truncated and the extent of the loss is
unknown. Put that in the reason for the field you do report:

```json
"total_due": { "value": null, "confidence": "unreadable",
               "reason": "the page ends mid-line-item and the totals block is below the margin; the extent of what is missing cannot be determined from the scan" }
```

One field, and a reason that does not pretend to know the shape of what it cannot see.

**Handwriting contradicting the printed terms.** A biro note beside the payment terms saying
something different. Record the **printed** value, set `low`, and put the conflict in the
reason, quoting what the handwriting says. Do not silently prefer either one, and do not drop
the printed value in favour of the note.

**`1.234,56` instead of `1,234.56`.** One client's invoices use a full stop for thousands and a
comma for decimals. Normalise to `"1234.56"` and round-trip to the cent. The tell is a number
with a comma followed by exactly two digits, or a full stop followed by exactly three.

**These are `high`.** An unfamiliar notation is not an uncertain reading: once the separators
resolve one way and not the other, the digits are as plain as any other figure on the page.
Converting a format you are certain of is not an inference, so it needs no `reason` and must
not be marked `low`. Flagging a whole invoice because its punctuation is foreign sends a
reviewer to five fields that were never in doubt, and costs exactly what a wrong answer costs.
Reserve `low` for a reading that could genuinely have gone another way.

**A spreadsheet cell that holds a formula and no result.** A statement of account shows
`TOTAL OUTSTANDING` against a cell containing `=SUM(E13:E15)` — and, if nothing ever calculated
it, no stored value at all. The label is on the page; the figure is not. The document is
referring to a total it does not contain.

Do not add up the rows. This is the previous section's rule in its least obvious form: every
number needed is right there, the arithmetic is trivial, and the answer would almost certainly
be correct — and it would still be a figure you produced rather than one the statement asserts.
`unreadable`, with a reason saying the cell holds an uncalculated formula and no balance is
stated in the file. Say in the reason that it can be recomputed from the rows. Do not put it in
the value.

**`03/09/2026`.** Day-first or month-first, and nothing in the number settles it. Resolve it
against the billing period printed on the same document, set `low`, and say in the reason which
way you read it and why. Only if there is nothing to resolve it against is it `unreadable`.

## Validate before you hand anything back

The Skill bundles `scripts/validate.py`. Run it on your output:

```bash
python scripts/validate.py extractions.json
```

It exits non-zero and names the exact JSON pointer of every record that breaks the contract.
**Fix the records; do not adjust the validator, and do not write a replacement for it.** It
prints a fingerprint line beginning `docmess-validate/1` — a run that does not show that line
did not run this script, and the contract has not actually been checked.

If the script is missing, stop and say so rather than validating by inspection.

## Do not

- Do not guess a figure you cannot read, however obvious it seems.
- Do not compute a figure that is missing from the page, even from that page's own rows.
- Do not carry a figure across from another document that happens to state it.
- Do not mark a field `low` for its notation. Format is not uncertainty.
- Do not list the fields of a block that is off the page. You cannot see what was cropped.
- Do not use a numeric confidence.
- Do not emit `evidence` on an `unreadable`, or omit `reason` on a `low`.
- Do not invent a field the document does not carry.
- Do not put values in `fields` on an `out_of_scope` document; it has none.
- Do not round money, or write it as a float.
