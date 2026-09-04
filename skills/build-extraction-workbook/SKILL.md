---
name: build-extraction-workbook
description: Turn validated extraction records into an Excel workbook a finance team can audit, with live formulas, a QA sheet citing page evidence for every flagged field, and an exceptions list. Use when handing extracted document data to somebody who works in Excel, or when a reviewer needs to see which figures were uncertain and why before trusting a total.
---

# Build the extraction workbook

The deliverable is not the JSON. It is a workbook somebody in finance opens, clicks a total in,
and sees a formula.

Run the bundled script. Do not build the workbook by hand and do not rewrite the script:

```bash
python scripts/build_workbook.py extractions.json --out extraction-review.xlsx
```

## Five tabs, in this order

| Tab | What it holds |
|---|---|
| **Summary** | Counts by document type, the total invoiced, how many fields were flagged, how many need review |
| **Invoices** | One row per invoice: number, client, engagement, dates, subtotal, credit, net, tax, total |
| **Engagements** | One row per SOW, with the amendment applied and the original shown beside it |
| **Exceptions** | Every document that needs a human: unreadable fields, conflicts, duplicates, out of scope |
| **QA** | Every flagged field, one row each, with its evidence, its reason, and a blank verdict column |

## Every number a reader might check is a formula

The Summary total is `=SUM(Invoices!L2:L37)`, not a number the script computed and typed in.
The count of flagged fields is a `COUNTIF` over the QA tab.

This is the point of the whole tab, and it is worth being blunt about why. **A workbook of
computed constants is a screenshot with extra steps.** A reviewer who wants to know where a
figure came from clicks it, sees `1284630.55`, and has learned nothing; the workbook is only as
trustworthy as their willingness to take it on faith. A reviewer who clicks and sees
`=SUM(Invoices!L2:L37)` can follow it to thirty-six rows, each citing a page. That is the
difference between a report and something auditable.

It also means the workbook survives contact with its reader. Delete a row you know to be a
duplicate, and every total updates. Constants would go stale silently and the sheet would be
wrong in a way nobody could see.

**So the script does no arithmetic.** It writes formula strings. If you find yourself computing
a subtotal in Python to put in a cell, that cell should hold a formula instead. The one
exception is the row and column indices the formulas refer to, which the script obviously has
to work out.

## Flagged fields are visible without hunting

- Any cell whose field came back `low` or `unreadable` is filled amber.
- `unreadable` cells carry the reason as a cell comment, so hovering explains the gap.
- The QA tab lists every one of them with its `evidence` string, so a reviewer can go to the
  page rather than guessing which page to open.
- The QA tab has an empty **Verdict** column. That is where the human works. Leave it blank.

Conditional formatting rather than static fills where the rule can be expressed as one, so the
colour follows the data if a reviewer edits it.

## Check before you hand it over

- Open it. Click the Summary total. It shows a formula in the bar, not a number.
- The Exceptions tab is not empty. A run of real client paperwork with nothing to report means
  the flagging is broken, not that the folder was clean.
- Every row on the QA tab has a non-empty evidence string. A flagged field a reviewer cannot
  locate is a flag they will ignore.
- Row counts on the Invoices tab match the Summary count of invoices.

## Do not

- Do not compute totals in Python and write them as values.
- Do not merge cells. A merged cell breaks sorting and filtering, which is most of what a
  finance reader will do to this.
- Do not hide the flagged rows or sort them to the bottom. They are the interesting ones.
- Do not fill in the Verdict column, even with "OK".
- Do not add a tab that repeats another tab's contents in a different arrangement.
